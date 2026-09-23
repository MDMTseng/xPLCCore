# Code review 2026-09-24 -- renderer (TS) and PLC (ST)

Read-only review of `v2-on-main` at `f6ec0c2`: the renderer (`*.ts`,
`*.tsx`, ~10.6k lines) and the PLC source (`codesys_code/**/*.st`, 120
files, ~8.7k lines). Nothing was run against the PLC; the renderer's
tests could not be run (`node_modules` absent, no `node` on PATH).
Owner's framing: the codebase has been refactored step by step toward a
solid foundation and is **functionally not usable** right now -- the
question was why, and what to fix first.

Items marked *(verify live)* depend on SoftMotion behaviour that reading
the code cannot settle.

## Why a production cycle does not run

Three layers, each enough on its own:

1. The real machine most likely cannot reach `Ready` (homing vs the
   ErrorStop supervisor).
2. Once in `Ready`: every digital input reads 0, one waiting command
   freezes the whole inbound queue, and the tape is advanced through an
   output that is not connected on this machine.
3. The renderer swallows every failure (`catch {}`), so the only symptom
   is a cycle that stalls or stops.

## P0 -- blocks running

### PLC

**1. Homing cannot finish on real axes** *(verify live)*
- Per-axis `SMC_Homing` puts the group into ErrorStop -- the code says
  so itself (`Update.st:171-176`, `FB_Homing.st:26-29`).
- The supervisor (bb350d3, 2026-06-23) fires `EV_ERROR` on
  `GroupErrorStop` in every state but Error/UnInited, Homing included
  (`AxisGroupSM.st:697-721`). The 50-scan group reset on Ready entry
  (`Update.st:177-187`) comes after the supervisor has already seen it.
  Expected: every real homing aborts to Error. Both pieces (bb350d3 and
  the rewritten homing, 2bdb118) are marked untested on hardware.
- `FB_Homing` has no abort path: states 10-40 ignore `bExecute` falling
  and the FB is only called in Homing (`FB_Homing.st:96-127`,
  `Update.st:139-165`). After an abort the next attempt fails at once
  (expired timeout timer); the third runs.
- Timeouts disagree: 30 s state watchdog (`AxisGroupManager.st:49`) vs
  30 s + 10 s inside the FB.
- Real motors cannot skip homing (`EV_HOME_GO_FORCE_SKIP` is sim-only,
  `Transition.st:151-159`), and every Error needs EV_RESET, re-power,
  re-home (`Transition.st:174-178`). Any fault, a heartbeat trip
  included, is only recoverable through this path.
- `IOHUB_mot0/1/2_limit` are not declared in any exported `.st`; check
  the device-tree mapping.

**2. Digital inputs are never read**
- The wiring line is commented out (`AxisGroupSM.st:859`), so
  `DigitalInputPointer` stays 0.
- `getDigitalInputFlipCount.raw` and `GET_DIGITAL_INPUT` always return
  0; `BLOCK_FOR_DIGITAL_INPUT` can only time out
  (`ProcessMotionPacket.st:97,112`). protocol.md's "returns 0 / no-op
  when unwired" is wrong -- it waits.
- The inputs are mapped already (`InputCHs[1]/[2]`,
  `ProcessFlyEventsAndIo.st:244-246`); build the word from them.
- Knock-on in the renderer: see R-P0-1.

**3. One waiting motion command blocks the whole inbound queue**
- `BLOCK_FOR_*` and `WAIT_FOR_REEL_STOP` stay at the queue tail until
  satisfied (`ProcessMotionPacket.st:38-106`); the drain loop stops at
  any motion packet at the tail (`DrainHostPackets.st:713-714`); the
  queue has 6 slots (`GVL.st:5-6`).
- Nothing behind a wait runs: not G1, PING, `GET_MACHINE_STATE`, nor
  `GA_EV` (so not even a reset).
- `WAIT_FOR_REEL_STOP` therefore holds arm moves behind the reel and
  breaks the overlap in plc.md's timing diagram. Replacing fixed delays
  with it on the renderer side (the plan in plc.md §Direction, step 2)
  is **not safe until this is fixed**.
- `BLOCK_FOR_DIGITAL_INPUT` without `timeout_ms` (default: forever)
  wedges the PLC permanently, since the inputs never change (P0-2).
- Every drain pass refreshes `LastUiPingMs` from the stuck packet
  (`DrainHostPackets.st:77`), hiding a dead host from the heartbeat.

### Renderer

**R-P0-1. The input watchdog stops the cycle within ~400 ms**
- `inputWatchdog` (`CalibPage.tsx:888-948`) reads the always-zero
  inputs (P0-2) as faults: `PackedReelNoProtrusion == false` (905,
  929), `ReelPressRollerInPlace == 0` (906, 931). Its first pass skips
  the delay (898). It sets `_this.current_error`, which holds the RUN
  checkpoint (~2530-2535). ">" and `resume_cycle` clear it for 500 ms;
  the watchdog sets it again; they give up after 10 s.

**R-P0-2. Vision results are dropped unless the PLC connects first**
- `VP_regTcpMsgCB` refuses to register when the **PLC** socket is null
  (`PluginHello.tsx:877`) -- it checks `tcpSocketRef`, not the vision
  link.
- CalibPage is always mounted (gate hard-wired `true||...`,
  `PluginHello.tsx:1081`; ControlPage hides pages with `display:none`),
  and its registration effect (`CalibPage.tsx:213-248`) re-runs only
  when `COMCtrlObj` changes -- on vision status changes, not on PLC
  connect (`PluginHello.tsx:1068-1077`).
- Vision first, PLC second: none of the four check-ID callbacks are
  registered, replies are dropped (`PluginHello.tsx:285-288`), every
  `waitFor*CheckData` waits forever (no timeout).

**R-P0-3. Production never moves the tape** -- *confirmed with the
owner: output 6 is not connected on this machine*
- The tape advances only by pulsing output 6 (`IO_Pins.O.ReelAdv`,
  `CalibPage.tsx:692-693, 730-740`), the "advance" input of the first
  machine's separate tape unit.
- Here the tape is `reelpullmotor`, a servo on the PLC's EtherCAT; it
  moves only on `ReelGo` (`ProcessMotionPacket.st:250`). `ReelGo` and
  `WAIT_FOR_REEL_STOP` appear only on dev/bench buttons
  (`CalibPage.tsx:2876-2878`, BindingTestPage).

The rest of the main path matches the protocol: every command goes
through a `cmd.*` builder, every builder's `cmd` exists in the PLC
dispatcher, no broken imports, the migration commit (08de961) kept the
old packets field-for-field.

## P1 -- wrong behaviour

### Renderer

- **Errors are swallowed.** `catch(error){}` at `CalibPage.tsx:1569-1570`
  catches every NAK, timeout, socket close and stop request. No operator
  message, no motion stop, no park, nozzle possibly still on.
  `run_cycle_stop` stays unset (1574 commented out), so the watchdog
  keeps running and pulsing `ReelWheelFeed` (918).
- **Stop is fake.** STOP and the `stop_cycle` harness action force
  `isRunning=false` after 3 s (~2700-2706, 1902-1907) whether or not the
  loop exited. A loop stuck on a vision wait survives; the next RUN
  resets `run_cycle_stop=false` (~2487) and revives it -- two production
  loops and two watchdogs drive the arm. The old loop then clears the
  new one's `isRunning` (1573).
- **RUN locks up without calibration.** `runAllObjects` sets
  `isRunning=true`, then returns early if `calibParams==null` (652-658);
  every later RUN throws, uncaught.
- **Lost rejections.** Fire-and-forget sends at 737, 1075-1076, 1103,
  1111, 1116, 1478-1482, 1501-1503, 1528-1532. A rejected camera
  trigger (`flyevent_buffer_full`, `group_not_ready`) means the shot
  never fires and its vision wait never ends.
- **The "arm has cleared" gate for the tape camera is switched off.**
  `_waitForReelVisualClearPromise` (1043) is never used -- the
  assignment is commented out (1046); it rejects after 5 s unobserved.
  Tape shots fire off `motion_id_offset:-1, motion_progress:0` plus a
  fixed 100/150 ms (706).
- **No timeouts on vision** (253-287; `VP_sendTcpMsgPack`,
  `PluginHello.tsx:834-874`). `save_target` is awaited (1282) though
  vision_contract.md calls it fire-and-forget; if VisionMaster does not
  echo the id, the loop hangs (not confirmed what it sends).
- **One pending slot per camera.** Each `waitFor*` overwrites the
  previous pending promise without rejecting it (258, 267, 276, 285); a
  late or duplicate reply resolves the next wait with the wrong frame.
- **Feeder-check anchors are nondeterministic.** `goCheckFlexFeederPlate`
  runs beside the main loop; its triggers (791, 802, 825) default to
  "last accepted move", which depends on timing. The feeder-cam trigger
  defaults to `motion_progress=1` (`ProcessMotionPacket.st:324`); the
  light is on for a wall-clock ~60 ms (823-829).
- **5 s client timeout on deferred replies.** `DEFAULT_TCP_REPLY_TIMEOUT_MS`
  (`PluginHello.tsx:595`) also applies to
  `WAIT_FOR_TRIGGER_MOTION_PROGRESS`, which acks only when the trigger
  fires.
- **A dropped socket looks like success.** `sendTcpMsgPack` returns
  `false` when the socket is null (`PluginHello.tsx:597`); `await false`
  continues.
- **No reconnect.** `close`/`error` handlers (538-550) never reconnect
  (protocol.md says they do). If the decoder throws (517-522) the
  receive loop ends, the socket stays open, `tcpConnected` stays true,
  every request times out.
- **`FlexVibCtrl` can be undefined** on first render
  (`PluginHello.tsx:264`); `FVib` (1837-1841) and the feeder check call
  it unchecked; its Modbus errors are lost (822-830, 858).
- **Likely calculation bug**: 1369-1370 mix cameras --
  `slotHoleOffset.X` uses the tape camera's `locHole.mmpp`, Y the
  bottom camera's `btmCheckCalibInfo.mmpp`.
- Heartbeat is not blockable in practice (Worker ticks; the PLC
  refreshes on any packet). Residual: a >5 s main-thread stall still
  trips it, and the renderer's own `stale` flag shows false alarms
  during busy production (707-711).

### PLC

- **Motion-buffer count ignores the move in progress** *(verify live)*.
  `MotionBufferSize = LastAccepted - MovementId`
  (`UpdateMotionProgress.st:20-21`) is 0 with one move in flight.
  `MOVE_DONE` / `last_completed_movement_id` fire when the last move
  *starts*, never for a lone move (`:61-74`) -- resume reconcile treats
  unfinished moves as done. The "motion in flight" guards on SetCoord0/1
  (`ProcessMotionPacket.st:178,201`) and COORD1 bind
  (`Coord1CommitBind.st:49`) let changes through mid-move; the
  heartbeat trip (`AxisGroupSM.st:727`) misses a single long move.
- **Nothing is cleaned up on Error, reset or disconnect.** Fly events
  are never flushed (only writes: `ProcessMotionPacket.st:449,532`) and
  their timeouts stop counting outside Ready (fly-event code runs after
  the early return, `AxisGroupSM.st:879-894`) -- stale pin ops and
  deferred acks fire on the next Ready. Outputs freeze through Error
  (`ProcessFlyEventsAndIo.st:247-248`). The inbound queue is not flushed
  on disconnect (`TCP_MSGPAK_Server.st:94-99`). The G1 duplicate filter
  (`ProcessMotionPacket.st:567-583`) is keyed on id only; the renderer
  restarts ids at 1 (`PluginHello.tsx:178,608`), so after a reconnect up
  to 16 G1s can be acked `dedup:true` without moving.
- **Commanded pose zeroed on every non-Ready scan**
  (`CheckAxisGroupReady.st:31-36`). After recovery a partial G1 drives
  omitted axes (A too) to 0; protocol says omitted axes hold.
- **Error cuts drive power in the same scan** (`Update.st:193-218`)
  while `GroupStopFb` (`AxisGroupSM.st:775-790`) is described as a
  controlled deceleration. Every fault is in practice an immediate
  power-off stop. Decide which is intended.
- **`WAIT_FOR_TRIGGER_MOTION_PROGRESS` reuses the previous M4's stage
  data** (`IoStageCount`/`IoStageDelay` not reset,
  `ProcessMotionPacket.st:505-521`); the fire logic waits that stale
  first-stage delay before acking (`ArmFiredTrigger.st:17-23`). On the
  tape-and-shot timing path.
- **ReelGo**: acked and silently dropped when both reel move FBs are busy
  (`ProcessMotionPacket.st:249-257`); the reel FBs are only called in
  Ready (return at `AxisGroupSM.st:879` precedes `:908`), breaking
  call-every-scan; `BlendingHigh` in code (`:917,:927`) vs `Buffered` in
  plc.md.
- **TCP reset race**: the reset branch sets `iParseIdx := 0`
  (`FB_TcpMsgPakServer.st:158`), the next step subtracts the previous
  packet length (`:182-185`), the index goes negative and the parse loop
  reads before `aInternalBuffer` (`:219-223`). `MoveLeft` copies
  overlapping memory with MemCpy (memmove commented out,
  `MoveLeft.st:14-16`).
- **Size limits**: inbound packets over 255 bytes dropped with a counter
  only, no NAK (`TCP_MSGPAK_Server.st:92-93`) -- a 9-stage `pin_op_seq`
  with float64 values is ~200-250 bytes. Reply buffers are 1 KB, the
  packer is unchecked (msgpack.md #10 open); `GET_DIAG` is ~750 bytes.

## P2 -- structure

- `runAllObjects` is one ~925-line closure (`CalibPage.tsx:650-1575`);
  calibpage.md fix #1 (extract step functions) still pending.
  `checkSlot_and_reelAdv` exists three times (670, 1726, 2784),
  `init_plc_motion` twice (`MiscControlsPage.tsx:31`,
  `OperationPage.tsx:171`); `_this` is `[k:string]:any` (144).
- `orchestrator/resume.ts` is used only by RecoveryDemoPage; its
  `plan_v1` is separate from CalibPage's plan. `conveyorPick.ts` has no
  consumer and targets conveyor tracking this machine does not use.
- `AxisGroupSM` is ~2,100 lines over 20 methods sharing ~150 program
  variables; shared scratch (`FlyEventTempData`, `Index`,
  `ResponsePacketPointer`, unpacker instances) caused the stale-stage
  bug above. Split by ownership: host link, motion queue, fly events + a
  single writer of outputs, reel driver, conveyor.
- Conveyor/COORD1 code runs every 1 ms and can raise `EV_ERROR` on a
  machine with no conveyor (`AxisGroupSM.st:458-587, 499-506, 761-772`);
  put it behind a feature flag.
- `CheckAxisGroupReady`'s "legacy no-op" loop NAKs leftover packets, SYS
  included, as `group_not_ready` with id -1 (fix R6 made the rest 0)
  (`:12-29`).
- `RuntimeMs` counts scans, not time (`UpdateRuntimeAndInputEvent.st:2`);
  some counters are 16-bit and wrap (`AxisGroupSM.st:196-199`); conveyor
  pulse comparisons are not wrap-safe.
- Status string rebuilt every scan during motion
  (`UpdateMotionProgress.st:33-40`); the EtherCAT master FB is called
  from application code in UnInited (`Update.st:65`) -- unusual, safety
  uncertain; stale comment at `AxisGroupSM.st:430`.
- Side projects (2026-09-23): an EasyCAT/ESP32 slave dropping off the
  production bus makes the supervisor stop the machine
  (`AxisGroupSM.st:699`); `PRG_EcatEspHttp` ignores write errors and
  partial sends and does one unframed read. Keep them behind a switch.
- Renderer typing: the send result is `Promise|boolean`; the typed
  `send<R>` helper is unused; push events cannot be matched to requests
  (only `TRIGGER_ERR` carries an `event_id`) -- composite commands will
  need that.

## IEC pitfalls

- `UnpackNext` copies up to 255 bytes into `sLastString : STRING(80)`
  (`UnpackNext.st:227-236`, `FB_MpUnpacker.st:30`): a key over 80
  characters overflows a local FB instance (`FindValueByPath.st:62`).
- `TO_INT` on a `pin_op_seq` delay wraps above 32767 ms
  (`ProcessMotionPacket.st:387`).
- Loop bound `(uiLastCount/3)-1` works only because an empty array wraps
  to -1 in the INT loop variable (`:380`).
- `CONCAT` into `LastErrorSource : STRING(40)` truncates
  (`ProcessFlyEventsAndIo.st:210`).
- Retain handling of the scratchpad and boot counter looks correct.

## plc.md items marked fixed that are not

- **A3 heartbeat**: trips only when Ready *and* the motion buffer is
  non-zero, and any packet (a stuck one included) resets the timer
  (`AxisGroupSM.st:726-729`, `DrainHostPackets.st:77`).
- **P0 #1**: only the null guard was added; inputs are still unwired.
- Stale details: line numbers (`:1197/:1218`, "~1720 lines"); ring
  indexes are UINT not UDINT; `COORD_SET` fires on both edges with a
  `value` field; protocol.md is missing the `HEARTBEAT` and
  `COORD1_ERROR` events, SYS `VERSION`/`COORD1_*`, G1 `frame`/`FAC`;
  protocol.md claims renderer auto-reconnect; calibpage.md anchors are
  stale (`#L595` is now 670).
- Confirmed present: A1, A2, R1-R5, R11, the PH#3 wrap fix; every
  command in `lib/protocol.ts` is handled by the dispatcher.

## Composite commands (plc.md §Direction)

Not as a motion command that waits at the queue tail -- that stalls the
arm for the whole sequence (P0-3). Follow the `WAIT_FOR_TRIGGER` shape:
validate, remove from the queue, ack, then advance a sequence block every
scan that pushes milestone events and a final reply. Prerequisites:
non-blocking waits, a single owner for the reel, a single writer for
outputs, flushing pending operations on Error, and a general table of
pending replies. On the renderer side there is no seam yet where a
`TAPE_CYCLE` could replace the step-by-step code: commands are built
inline among decisions, nothing is cancellable, stop is a flag checked at
checkpoints.

## Owner's decision on how to proceed

Target first: **a full cycle running in an all-virtual scene** (all axes
simulated, inputs, vision, feeder and tape stubbed), then hook real
hardware in one piece at a time. See the plan that follows this review.
