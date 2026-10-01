"""Find stale-target events in ASDA-Soft scope recordings (B3-E, 8 kHz).

A stale target (the drive's control loop reused last cycle's target, the
delta vibration of 2026-09-30) shows in "Cmd Pos. [PUU]" as one bus cycle
whose interpolated steps repeat the previous cycle's, then a first-sample
jump against the motion and a catch-up at ~2x. Detected on the second
difference taken one bus cycle apart: |d2| > --thresh.

    python tools/asda_scope.py FILE [FILE ...] [--cycle-ms 1] [--thresh 154530]
                               [--detail MS] [--top 8]
    python tools/asda_scope.py FILE --ramp [--table MS0 MS1]

--ramp is for constant-speed runs (tools/direct_test.py, pulse_test.py): a
cycle is bad when its command increment differs from the nominal |step| by
more than a third; bad cycles less than 300 ms apart form one burst. It
prints every burst and the intervals between them (2026-10-01: every
2.4-3.6 s). --table prints, per bus cycle, the increment, the 8 sub-sample
steps of Cmd Pos and the following error from MS0 to MS1.

FILE: a .parscp (7-zip holding scpTemp.scp) or a bare .scp / .parscp.scp.
Format (ASDA-Soft V7.2.14, learned 2026-09-30): uint32 sample count at byte
420; float64 data from byte 1000, one block per channel (N values each),
channels in the order configured (here Fdbk Pos, Cmd Pos, Following Error,
Motor Current); 8 kHz sampling ("8K" in the scope). Data parsed from the end
of the file is misaligned -- the first analysis did that and got wrong times.
Needs numpy (and py7zr for .parscp).
"""

import argparse
import os
import struct
import sys
import tempfile

import numpy as np

FS = 8000.0


def load(path):
    if path.lower().endswith(".parscp"):
        import py7zr
        d = tempfile.mkdtemp()
        with py7zr.SevenZipFile(path) as z:
            z.extractall(d)
        path = os.path.join(d, "scpTemp.scp")
    b = open(path, "rb").read()
    n = struct.unpack_from("<I", b, 420)[0]
    names = []
    for off in (674, 770, 866, 962):
        s = b[off:off + 40].split(b"\0")[0].decode("latin-1").strip()
        names.append(s)
    data = np.frombuffer(b[1000:1000 + 32 * n], dtype="<f8").reshape(4, n)
    return names, data


def events(cmd, stride, thresh):
    d = np.diff(cmd)
    ad = np.abs(d[2 * stride:] - 2 * d[stride:-stride] + d[:-2 * stride])
    idx = np.nonzero(ad > thresh)[0] + stride
    ev = []
    for i in idx:
        if not ev or i - ev[-1] > stride:
            ev.append(int(i))
    ev = [i for i in ev if i > stride]
    stale = []
    for i in ev:
        w = d[max(0, i - 2 * stride):i + 3 * stride]
        j = np.argmax(np.abs(w - np.median(w)))
        if np.sign(w[j]) != np.sign(np.median(w)) and abs(w[j]) > 3 * abs(np.median(w)):
            stale.append(i)
    return d, stale


def ramp(f, names, cmd, fe, stride, a):
    inc = cmd[stride::stride] - cmd[:-stride:stride]        # per bus cycle
    # in motion: the 21-cycle sum moves, so a single stalled (0) cycle counts
    win = np.convolve(inc, np.ones(21), "same")
    moving = np.abs(win) > 5 * 21
    nom = float(np.median(np.abs(inc[moving]))) if moving.any() else 0.0
    print("%s: %s | %.1f s, nominal |step| %.0f PUU per cycle" % (
        os.path.basename(f), "/".join(names), cmd.size / FS, nom))
    if a.ramp and nom > 0:
        bad = np.nonzero(moving & (np.abs(np.abs(inc) - nom) > nom / 3))[0]
        gap = int(300 / a.cycle_ms)
        bursts = []
        for i in bad:
            if bursts and i - bursts[-1][-1] < gap:
                bursts[-1].append(int(i))
            else:
                bursts.append([int(i)])
        print("  bad cycles %d of %d moving (%.2f %%), bursts %d" % (
            len(bad), int(moving.sum()), 100.0 * len(bad) / max(1, moving.sum()), len(bursts)))
        for b in bursts:
            print("  %9.0f ms  length %5.0f ms  bad cycles %3d" % (
                b[0] * a.cycle_ms, (b[-1] - b[0] + 1) * a.cycle_ms, len(b)))
        iv = np.diff([b[0] * a.cycle_ms for b in bursts])
        if iv.size:
            print("  intervals (s): %s" % " ".join("%.2f" % (x / 1000) for x in iv))
            print("  interval min / mean / max: %.2f / %.2f / %.2f s" % (
                iv.min() / 1000, iv.mean() / 1000, iv.max() / 1000))
    if a.table:
        print("  %7s %7s   %-*s %7s" % ("ms", "cmd/cyc", 5 * stride, "sub-steps of Cmd Pos", "FE"))
        for m in range(int(a.table[0] / a.cycle_ms), int(a.table[1] / a.cycle_ms) + 1):
            k = m * stride
            sub = np.diff(cmd[k:k + stride + 1])
            print("  %7.0f %7d   %s %7d" % (m * a.cycle_ms, int(inc[m]),
                                          " ".join("%4d" % x for x in sub), int(fe[k])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--cycle-ms", type=float, default=1.0, help="EtherCAT cycle (1 or 2)")
    ap.add_argument("--thresh", type=float, default=154530.0,
                    help="|d2| in PUU; 154530 suits 50%% speed on the square path at 1 ms "
                         "(normal p99 ~50000); use ~100 for tools/pulse_test.py")
    ap.add_argument("--detail", type=float, help="print the per-sample increments around this time (ms)")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--ramp", action="store_true", help="constant-speed run: burst list and intervals")
    ap.add_argument("--table", type=int, nargs=2, metavar=("MS0", "MS1"),
                    help="per-cycle table of Cmd Pos sub-steps and following error")
    a = ap.parse_args()
    stride = int(round(a.cycle_ms * FS / 1000))
    for f in a.files:
        names, (ch0, cmd, fe, cur) = load(f)
        if a.ramp or a.table:
            ramp(f, names, cmd, fe, stride, a)
            continue
        n = cmd.size
        d, st = events(cmd, stride, a.thresh)
        moving = np.abs(d) > 1000
        mv = max(1e-9, n / FS * moving.mean())
        bursts = []
        for i in st:
            if not bursts or i - bursts[-1][-1] > 800:
                bursts.append([i])
            else:
                bursts[-1].append(i)
        top = sorted(sorted(bursts, key=len, reverse=True)[:a.top])
        print("%s: %s | %.1f s, moving %.0f%% | stale %d (%.2f/s moving), bursts %d" % (
            os.path.basename(f), "/".join(names), n / FS, moving.mean() * 100, len(st), len(st) / mv, len(bursts)))
        print("  largest bursts (ms, count): " + ", ".join("%.1f (%d)" % (b[0] * 1000 / FS, len(b)) for b in top))
        if a.detail is not None:
            i0 = int(a.detail * FS / 1000)
            for r in range(i0 - 2 * stride, i0 + 8 * stride, stride):
                print("  %9.3f ms: %s" % (r * 1000 / FS, " ".join("%7d" % x for x in d[r:r + stride])))


if __name__ == "__main__":
    main()
