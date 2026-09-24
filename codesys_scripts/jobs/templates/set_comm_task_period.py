# -*- coding: ascii -*-
# Set the Comm task (TCP_MSGPAK_Server) period. Offline project edit only:
# no login. Deploy with `rpc.py install --on-site` (a task-config change
# cannot be an online change).
#
#   rpc.py exec --file jobs/templates/set_comm_task_period.py
#
# Why 1 ms (2026-09-25): the Comm task takes in one host packet and sends
# one reply per scan, and a placement exchanges ~50 packets. At 5 ms a
# burst of 4 packets took ~20 ms to even reach the PLC's ring. Measured on
# the PC sim: run 64 s -> 61 s. Scan-counted timeouts in the Comm task
# were made time-based first (TCP_MSGPAK_Server SEND_STALL_TIME).

PERIOD_MS = "1"

tc = projects.primary.find("Task Configuration", True)[0]
done = False
for t in tc.get_children(False):
    if t.get_name() == "Comm":
        print("Comm: %s %s -> %s ms" % (t.interval, t.interval_unit, PERIOD_MS))
        t.interval = PERIOD_MS
        done = True
if not done:
    raise RuntimeError("no task named Comm in the Task Configuration")
