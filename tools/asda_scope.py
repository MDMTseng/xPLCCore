"""Find stale-target events in ASDA-Soft scope recordings (B3-E, 8 kHz).

A stale target (the drive's control loop reused last cycle's target, the
delta vibration of 2026-09-30) shows in "Cmd Pos. [PUU]" as one bus cycle
whose interpolated steps repeat the previous cycle's, then a first-sample
jump against the motion and a catch-up at ~2x. Detected on the second
difference taken one bus cycle apart: |d2| > --thresh.

    python tools/asda_scope.py FILE [FILE ...] [--cycle-ms 1] [--thresh 154530]
                               [--detail MS] [--top 8]

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--cycle-ms", type=float, default=1.0, help="EtherCAT cycle (1 or 2)")
    ap.add_argument("--thresh", type=float, default=154530.0,
                    help="|d2| in PUU; 154530 suits 50%% speed on the square path at 1 ms "
                         "(normal p99 ~50000); use ~100 for tools/pulse_test.py")
    ap.add_argument("--detail", type=float, help="print the per-sample increments around this time (ms)")
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    stride = int(round(a.cycle_ms * FS / 1000))
    for f in a.files:
        names, (ch0, cmd, fe, cur) = load(f)
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
