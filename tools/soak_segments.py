"""Run circle_soak.py several times back to back (a long soak in segments:
one stream of hours of packets is too big). Stops after a segment that did
not end on time (torque limit, FSM error, stream ended, stop file).

    python tools/soak_segments.py --owner-ok --segments 3 -- --shape dip --speed 70 --minutes 60

Everything after "--" goes to circle_soak.py. Meant to run detached
(Start-Process) with its output in a log file, since background tasks of
the session end after 2 h.
"""

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    argv = sys.argv[1:]
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    own = argv[:argv.index("--")] if "--" in argv else argv
    segments = int(own[own.index("--segments") + 1]) if "--segments" in own else 3
    if "--owner-ok" not in own:
        raise SystemExit("REFUSED: pass --owner-ok (the real delta moves)")
    for i in range(segments):
        print(time.strftime("%H:%M:%S"), "=== segment %d of %d ===" % (i + 1, segments), flush=True)
        p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "circle_soak.py"), "--owner-ok"] + rest,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
        ended_on_time = False
        for line in p.stdout:
            print(line.rstrip(), flush=True)
            if "END (time)" in line:
                ended_on_time = True
        p.wait()
        if p.returncode != 0 or not ended_on_time:
            print(time.strftime("%H:%M:%S"), "=== stopped after segment %d (rc %s)" % (i + 1, p.returncode), flush=True)
            return
    print(time.strftime("%H:%M:%S"), "=== all %d segments done" % segments, flush=True)


if __name__ == "__main__":
    main()
