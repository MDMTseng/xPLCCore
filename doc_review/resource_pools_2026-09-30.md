# PLC queues and resource pools: what frees them, what can be left over (2026-09-30)

## Inventory

| pool | size | freed by | left over? |
|---|---|---|---|
| FlyEvent buffer (`AxisGroupSM.FlyEventBuffer`) | 32; new ones NAK `flyevent_buffer_full` at <= 3 free | firing, TTL expiry (`TRIGGER_ERR 100`; default TTL, fix 2), `FlushFlyEvents` on Error / UnInited (notified, `TRIGGER_ERR 101`), host disconnect, SYS `FLUSH` (fix 2) | fixed |
| host -> PLC packets (`GVL.minfo_buf` 16, `sysinfo_buf` 8) | 16 + 8 | processed; NAK-drained (`group_not_ready`) while not Ready; dropped on host disconnect (fix 1) | fixed |
| PLC -> host replies (`GVL.reMP_info`) | 32 | sent; dropped on disconnect; a reply unsendable for 100 ms is dropped; 5 in a row force a socket reset | no |
| duplicate filter (`RecentCmdIds`) | 16, overwritten | cleared on Error / UnInited and host disconnect | no |
| waits (`WAIT_FOR_*`, `BLOCK_FOR_*`) | 1 per kind | condition, timeout, FSM leaving Ready, host disconnect, SYS `FLUSH` | no |
| TAPE_CYCLE sequence | 1 | done, `tape_timeout` (6 s), not Ready, host disconnect, SYS `FLUSH` | no |
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
   FIN). First read as unhandled; it was not: the socket's idle watchdog
   reset the link after 7 s without a byte, which runs the disconnect
   cleanup. But the heartbeat supervisor (W1 A3) put the FSM in Error
   after 5 s of silence with motion queued, stopping the motion: against
   the owner's rule that a lost host's motion runs to its end (and it hit
   clean disconnects too, when more than 5 s of motion was left). See
   fix 3.

## Ways to reset today

| how | clears |
|---|---|
| host disconnect | fly events (no notify), duplicate filter, waits, TAPE sequence, DI watch, replies, unprocessed packets (fix 1) |
| FSM reset (`GA_EV 8` -> UnInited) | the above plus the SoftMotion queue (stop / reset), motion packets NAKed, coordinate frame re-applied |
| SYS `FLUSH` (the UI at every run start and end) | fly events (`TRIGGER_ERR 101`), waits and the TAPE sequence (NAK `flushed`); FSM untouched |
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

### 2. Default TTL for fly events, SYS FLUSH (done)

- `GVL.FlyEventDefaultTtlMs` (10 000 ms): the TTL of an M4 sent without
  `ttl_ms`. Before, such an M4 never expired. An explicit negative
  `ttl_ms` still means never. SYS `SET_FLY_TTL {ttl_ms}` sets it
  (100..3 600 000, or negative for never) and replies with the value in
  force. The UI sets `FLY_EVENT.DEFAULT_TTL_MS` (`params.ts`), divided by
  the speed override, at every run start and on every speed change.
- SYS `FLUSH`: drops the fly events (an unfired pin operation reports
  `TRIGGER_ERR 101`; a pending trigger wait gets NAK `flushed`), the
  pending waits and the TAPE sequence (NAK `flushed`). The FSM is not
  touched. It is ordered with the motion packets (not in `RouteToSys`),
  so it only drops what was sent before it. The reply `fly_flushed` is
  the count; the flush itself runs after the reply is committed. A first
  version flushed while packing the reply: the flushed events' own
  reply slots interleaved with it and garbled the stream, and the
  client dropped the link. `runCycles` sends it when a run starts and
  when it ends (a failure is only logged).

`tools/fly_leftover_test.py` (machine): an M4 aimed at a movement never
sent expires after the set 2 s with `TRIGGER_ERR 100` (2.03 s). FLUSH
drops two (default TTL and `ttl_ms -1`): `fly_flushed 2`, both
`TRIGGER_ERR 101`, slots free. A pending `WAIT_FOR_MOTION_STOP` -> NAK
`flushed`. FSM Ready throughout: PASS. Production flow
(`run_virtual.py --plc 192.168.1.70 --cycles 20`, virtual arms): no
error, place to next place median 1164 ms.

### 3. A lost host: the motion runs on, the link is reset (done)

- W1 A3 (`AxisGroupSM`, host silent `UI_HEARTBEAT_TIMEOUT_MS` with motion
  queued) now only counts `GVL.UiHeartbeatStaleCount`, once per silent
  spell. It no longer raises the FSM error. The queue holds at most 12
  moves, so the motion ends by itself.
- The idle watchdog of `FB_TcpMsgPakServer` is the host-loss detector:
  `GVL.HostIdleTimeoutMs` (5000, >= 2000; was a constant 7 s) without a
  byte from the host -> link reset (`GVL.IdleResetCount`) -> the
  disconnect cleanup (fix 1, and the fly events / waits / TAPE). The UI
  pings every 1 s from a worker, so a healthy UI never trips it.

`tools/host_gone_test.py --silent` (machine): 12 slow G1s queued, then
the host silent with the socket open. The link reset came before 6.6 s
(`IdleResetCount` +1, `UiHeartbeatStaleCount` +1). The motion ran on: 8
moves still queued at 6.6 s, done at 13.2 s. FSM Ready throughout: PASS.
The clean-disconnect test again: 13 run + 7 dropped = 20, PASS.

## Status

| gap | fix | machine test |
|---|---|---|
| 1 unprocessed packets after disconnect | dropped on the disconnect edge | `host_gone_test.py` PASS |
| 2 M4 without TTL | default TTL 10 s (`SET_FLY_TTL`), SYS `FLUSH` at run start / end | `fly_leftover_test.py` PASS |
| 3 lost host | idle reset `HostIdleTimeoutMs` 5 s; A3 counts only, motion runs on | `host_gone_test.py --silent` PASS |
