"""Run circle_soak.py back to back for a long soak (one stream of hours of
packets is too big), optionally recovering from EtherCAT dropouts.

    python tools/soak_segments.py --owner-ok --segments 3 -- --shape dip --speed 70 --minutes 60
    python tools/soak_segments.py --owner-ok --hours 6 --recover 10 -- --shape zud --speed 10 --minutes 60

Everything after "--" goes to circle_soak.py. --segments N runs N segments
and stops after one that did not end on time. --hours H runs segments until
H hours have passed; with --recover N a segment that ended early is
followed by a re-download (tools/safe_install.py, delta already virtual and
off) and the soak goes on, up to N times -- to measure how often the bus
drops (2026-10-03). Meant to run detached (Start-Process) with its output
in a log file, since background tasks of the session end after 2 h.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def opt(own, name, default, conv):
    return conv(own[own.index(name) + 1]) if name in own else default


def run_segment(rest, minutes):
    args = list(rest)
    if "--minutes" in args:
        args[args.index("--minutes") + 1] = "%g" % minutes
    else:
        args += ["--minutes", "%g" % minutes]
    p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "circle_soak.py"), "--owner-ok"] + args,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    reason = None
    for line in p.stdout:
        print(line.rstrip(), flush=True)
        if " END (" in line:
            reason = line.split(" END (", 1)[1].split(")", 1)[0]
    p.wait()
    return reason, p.returncode


def main():
    argv = sys.argv[1:]
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    own = argv[:argv.index("--")] if "--" in argv else argv
    if "--owner-ok" not in own:
        raise SystemExit("REFUSED: pass --owner-ok (the real delta moves)")
    segments = opt(own, "--segments", 3, int)
    hours = opt(own, "--hours", 0.0, float)
    recover = opt(own, "--recover", 0, int)
    seg_min = float(rest[rest.index("--minutes") + 1]) if "--minutes" in rest else 60.0
    t0 = time.time()
    seg = drops = 0
    while True:
        left = hours * 60 - (time.time() - t0) / 60 if hours else None
        if hours and left < 1:
            break
        if not hours and seg >= segments:
            break
        seg += 1
        minutes = min(seg_min, left) if hours else seg_min
        log("=== segment %d (%.0f min)%s ===" % (seg, minutes, "" if not hours else ", %.0f min left" % left))
        reason, rc = run_segment(rest, minutes)
        if reason == "time":
            continue
        if reason == "stream ended early" and drops < recover:
            drops += 1
            log("=== DROPOUT %d at %.1f min into the soak: re-download and go on ===" % (drops, (time.time() - t0) / 60))
            r = subprocess.run([sys.executable, "-u", os.path.join(HERE, "safe_install.py"), "--no-log"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            out = (r.stdout + r.stderr).strip().splitlines()
            for line in out[-3:]:
                log("   install:", line)
            if not any("All slaves in operational" in l for l in out):
                # 2026-10-04: the install itself went through but its last
                # check hit the daemon's 4 h budget; ask the master again
                # (machine.rpc restarts a retired daemon).
                sys.path.insert(0, HERE)
                import machine as mc
                try:
                    ec = mc.ethercat_state()
                except Exception as e:
                    ec = {"error": str(e)}
                log("   EtherCAT asked again: %s" % ec)
                if "operational" not in str(ec.get("LastMessage", "")):
                    log("=== stopped: the bus did not come back after the re-download")
                    return
            continue
        log("=== stopped after segment %d (%s, rc %s; dropouts %d)" % (seg, reason, rc, drops))
        return
    log("=== all segments done (%d, dropouts %d)" % (seg, drops))


if __name__ == "__main__":
    main()
