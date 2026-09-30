"""Comm task profile: where the Comm task's time goes (EC_STATS cs/cb/co/
cscan/ctask/cout/cout500 from TCP_MSGPAK_Server / PRG_EcatEspHttp stamps)
next to TASK_STATS for all tasks. Resets the counters, waits, prints.

    python tools/comm_profile.py [--seconds 30] [--label idle]

cs0..2: worst span of TCP_MSGPAK_Server / PRG_MbProbe / PRG_EcatEspHttp
(wall time, preemption included); cout: the task's time outside its POUs
(IO update, e.g. the Modbus stack: ~1.7 ms once a second while the feeder
link failed, 2026-09-30).
"""

import argparse
import time

import machine as mc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    mc.sys_cmd("EC_STATS", reset=1)
    mc.sys_cmd("TASK_STATS", reset=True)
    time.sleep(a.seconds)
    e = mc.sys_cmd("EC_STATS")
    r = mc.sys_cmd("TASK_STATS")
    print("%s %.0f s" % (a.label, a.seconds))
    for k, n in enumerate(("TCP_MSGPAK_Server", "PRG_MbProbe", "PRG_EcatEspHttp")):
        print("  %-18s worst %7.0f us (bus cycles inside %d)  >1ms %d" % (n, e["cs%d" % k], e["cb%d" % k], e["co%d" % k]))
    print("  POU span worst %.0f us | task time worst %.0f us | outside POUs worst %.0f us, >500us %d scans" % (
        e["cscan"], e["ctask"], e["cout"], e["cout500"]))
    for k in range(r["n"]):
        print("  %-24s avg %4d max %5d us  jitter max %5d" % (
            r["t%d_name" % k], r["t%d_avg" % k], r["t%d_max" % k], r["t%d_jmax" % k]))


if __name__ == "__main__":
    main()
