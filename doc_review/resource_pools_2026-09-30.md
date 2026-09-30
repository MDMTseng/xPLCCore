# PLC queues and resource pools: what frees them, what can be left over (2026-09-30)

## Inventory

| pool | size | freed by | left over? |
|---|---|---|---|
| FlyEvent buffer (`AxisGroupSM.FlyEventBuffer`) | 32; new ones NAK `flyevent_buffer_full` at <= 3 free | firing, TTL expiry (`TRIGGER_ERR 100`), `FlushFlyEvents` on Error / UnInited (notified, `TRIGGER_ERR 101`) and on host disconnect | gap 2 |
| host -> PLC packets (`GVL.minfo_buf` 16, `sysinfo_buf` 8) | 16 + 8 | processed; NAK-drained (`group_not_ready`) while not Ready; dropped on host disconnect (fix 1) | fixed |
| PLC -> host replies (`GVL.reMP_info`) | 32 | sent; dropped on disconnect; a reply unsendable for 100 ms is dropped; 5 in a row force a socket reset | no |
| duplicate filter (`RecentCmdIds`) | 16, overwritten | cleared on Error / UnInited and host disconnect | no |
| waits (`WAIT_FOR_*`, `BLOCK_FOR_*`) | 1 per kind | condition, timeout, FSM leaving Ready, host disconnect | no |
| TAPE_CYCLE sequence | 1 | done, `tape_timeout` (6 s), not Ready, host disconnect | no |
| DI change watch | 16 pins | host disconnect | no |
| SoftMotion movement queue | <= 12 (`MOTION_BUFFER_THRESHOLD`) | runs out; `MC_GroupStop` / reset on Error | no |
| event log (`GVL.Ev*`) | 1024, overwritten | -- | no |
| retained reel move / plan (`ReelMv*`, `PlanSeg`) | -- | on purpose kept over power loss; `REEL_CLEAR`, `PLAN_SET` | by design |

## Gaps

1. **Host disconnect left its unprocessed packets queued.** G1 / M4 /
   TAPE_CYCLE still in `minfo_buf` ran after the host was gone; an M4
   registered after the disconnect flush became an orphan. Fixed (below).
2. **M4 without a TTL** (the UI's camera / nozzle M4s: `ttl_ms` absent =
   never expires) waits for its movement. If the UI's sequence stops
   before that movement is sent (error, STOP) while the FSM stays Ready,
   the event stays and fires at some later, unrelated movement (a jog):
   a wrong shot or nozzle switch. Repeated, it fills the 32 slots.
3. **A host that vanishes silently** (cable pulled, UI killed without
   FIN): the PLC notices only when a send fails. With nothing to send,
   `CLIENT.xActive` can stay TRUE, so none of the disconnect cleanup
   runs and a new client may not get in.

## Ways to reset today

| how | clears |
|---|---|
| host disconnect | fly events (no notify), duplicate filter, waits, TAPE sequence, DI watch, replies, unprocessed packets (fix 1) |
| FSM reset (`GA_EV 8` -> UnInited) | the above plus the SoftMotion queue (stop / reset), motion packets NAKed, coordinate frame re-applied |
| `REEL_CLEAR` | the retained reel move |
| `RESET_DBG_INFO` | diagnostic counters |
| download / cold start | everything but RETAIN |

## Fixes, one at a time

### 1. Host disconnect drops its unprocessed packets (done)

`UpdateRuntimeAndInputEvent`, on the disconnect edge: both host packet
rings are emptied from their consumer side, counted in
`GVL.HostGonePacketDropCount`, before `DrainHostPackets` /
`ProcessMotionPacket` run in that scan. Motion the group already accepted
runs to its end (owner's choice).

`tools/host_gone_test.py` (machine, 2026-09-30): 20 slow G1s queued without
waiting, then the connection closed. 13 moves the group had accepted ran
to the end (11.4 s), 7 packets dropped, 13 + 7 = 20, FSM Ready: PASS.
