# PLC / web UI integration review (2026-10-05)

Six read-only reviews of the boundary between the PLC program
(codesys_code/Application) and the web UI (PluginHello.tsx, components/,
lib/) plus the Python tools, one per angle: protocol contract, flow control
and timing, failure and recovery, state ownership, safety and authority,
observability and testing. Follows plc_architecture_review_2026-10-04.md
(PLC only) and does not repeat it.

One thread runs through all six: the PLC is the real authority and the
source of truth, but the contract, the guards, the state and the error
semantics are re-implemented in each client, by hand, and they drift.

## 1. Priorities across the six angles

| # | Sev | Finding | Angle | Where |
|---|---|---|---|---|
| 1 | P0 | `DIRECT` (raw CiA402 on EAxis0) is allowed exactly when EAxis0 is virtual in SoftMotion -- the normal "delta virtual" state. `step` / `maxfe` come from the client unbounded, travel cap is a writable GVL, state 0 sends a fault reset on its own. Any client can give joint 0 a 3 deg step in one cycle. | safety | DrainHostPackets.st:538-557, PRG_EventLog.st:705-768 |
| 2 | P0 | Port 8125 has no authentication, first connection wins; `site_clear` (operator confirmed) is a field the client fills in; `DELTA_MODE real` never expires. | safety | TCP_MSGPAK_Server.st:9-23, DrainHostPackets.st:940-942, xplc.py:274 |
| 3 | P0 | `tools/bus_watch.py` re-downloads up to 20 times with no owner gate, through the path without the slow-download refusal, never saves the PLC log first, and only detects dropouts by lost frames (the 10-05 09:10 one had Frames Lost 0). | tooling | bus_watch.py:46-80 |
| 4 | P1 | The harness (:8127) answers `Access-Control-Allow-Origin: *`, no token, accepts `text/plain` POSTs: any web page in a browser on the UI PC can send raw PLC packets (`plc_send`). | safety | remote_harness.py:103-150, MotorTestPage.tsx:96 |
| 5 | P1 | `DRV_SDO` writes any index on any station in any FSM state (ASDA writes go to EEPROM); `SET_AXIS_LIMITS` has no upper bound and is the only speed guard (G1 F/ACC/JERK unchecked). | safety | DrainHostPackets.st:501-527, 636-643 |
| 6 | P1 | Resume (">") after a PLC fault continues mid-cycle on a stale arm model: `MotionPose` is zeroed when the PLC leaves Ready, the cycle sends partial G1s (`{Z}` only), so the nozzle comes down at X0 Y0. It also skips RUN's recovery (flushed triggers treated as done, open reel move). | state, failure | CheckAxisGroupReady.st:31-37, ProcessMotionPacket.st:745-756, CalibPage.tsx:2275-2442 |
| 7 | P1 | Five pages each run their own "climb to Ready" loop on old reads, no busy guard; a double click can re-home under queued moves. `plcReady` is a latch, not derived from the PLC. | state | MiscControlsPage.tsx:31-91,170, OperationPage.tsx:171-235, ControlPage.tsx:64-84 |
| 8 | P1 | The UI never reconnects and ignores a stale heartbeat; a half-open link shows only 5 s reply timeouts. | failure | PluginHello.tsx:550-567, 785 |
| 9 | P1 | `TAPE_CYCLE` encodes to ~251 B against the 255 B host packet limit; one more stage or a fractional value makes every tape step NAK `packet_too_long` mid-run. No host-side size check. | contract | protocol.ts:539-547, GVL.st:1089 |
| 10 | P1 | Deferred replies have no owner of the deadline: a trigger TTL expiry only pushes TRIGGER_ERR (never NAKs the waiting id); host waits 5 s, PLC TTL 10 s / override; an untimed WAIT_FOR_MOTION_STOP stays registered after the host gives up, the next wait NAKs `wait_busy`. | contract | ProcessFlyEventsAndIo.st:34-117, ProcessMotionPacket.st:55-71, feeder.ts:65-103 |
| 11 | P1 | The motion buffer is ~1.07 s (12 queued + 8 in flight), counted in moves not time. The 34 unplanned stops of the 6 h soak all sit exactly where SoftMotion ran out of the next move (bottom dwell extended 14x, top of next corner 11x, after the rise 9x; the resume legs match rest-to-rest move times): a supply interruption > ~1.1 s before the PLC accepts the move, not a refusal or a drive fault. | timing | ProcessMotionPacket.st:3, AxisGroupSM.st:12, dwell_tri6h.csv |
| 12 | P1 | `seq` cannot detect gaps: incremented from two tasks non-atomically, diag replies bypass the ring, nobody reads it. No common clock: `RuntimeMs` counts scans, PLC RTC ~4 min ahead, one offset for a 6 h soak. | observability | MsgPakInfoWrapup.st:15, UpdateRuntimeAndInputEvent.st:8-13 |
| 13 | P1 | Diagnostic CSV strings are `STRING(255)`: DWELL (8 stops, ~41 chars each when unsettled) and SETTLE (20 entries) overflow and are cut silently; the host parser accepts cut rows; a length mismatch blanks the new stop-cause columns for every row. | contract, observability | PRG_DiagReply.st:30,115-177, circle_soak.py:110-117 |

P2 / P3, by angle, in section 2.

## 2. Findings by angle

### 2.1 Protocol contract
- P2: Python `Machine.send` resends after LinkDown; the UI gives the packet a new id and the PLC clears its G1 dedupe ring on disconnect, so a G1 / ReelGo / TAPE_CYCLE that already arrived runs twice. Retry only idempotent commands (xplc.py:188-197).
- P2: old-PLC fallback `isUnknownCommand` (`/unknown_cmd|^nak \(/`) never matches the PLC's `unknown SYS cmd`, but matches any NAK without `err` (CalibPage.tsx:799-802).
- P2: versioning only on paper: replies never carry `protocol_version`, diag commands skip the check, `VERSION` is never called.
- P2: docs wrong: replies do not carry `runtime_ms`; COORD_SET fires on both edges with `value`; GET_DIGITAL_INPUT reply key is `value` (doc) / `raw` (TS) / `state` (PLC); HEARTBEAT and COORD1_ERROR missing from the event table.
- P3: fields sent and ignored (GET_DIGITAL_INPUT / M4 `group`, `timeout:30`), typed and never sent (`M4Reply.event_id`), ST-only (`FAC`, `dedup`), TS `Event` lists values GA_EV rejects.
- Design: `contract/protocol.yaml` (limits, error table with code + class, per command: ring, FSM gate, reply mode with deadline, idempotent flag, params, reply fields, errors) -> `tools/gen_protocol.py` generates ST constants + `ProtoLookup` (replaces the RouteTo* OR chains, enables a CASE dispatch), `lib/protocol.gen.ts`, `tools/xplc_proto.py`, and the doc tables; `tools/proto_lint.py` diffs every `CommandType` branch's TryRead / PackKv / err literals against the YAML. Migration: host only first (extract YAML from ST, generate docs and types, size guard), then one PLC download (lookup, numeric `ec`, capabilities in VERSION, TTL NAK), then a major bump.

### 2.2 Flow control and timing
- Queue map: UI window 8 -> TCP -> fbDataBuffer 2 KB (Comm) -> minfo_buf 16 -> EtherCAT task, one motion packet per scan, gated by queued < 12 and cooldown -> SoftMotion queue -> reply ring 128 -> Comm (<= 4 per scan) -> UI. Steady state: all 8 in flight wait in minfo_buf, so each ack takes ~430 ms by design (confirmed on the sim 2026-10-05: acks 250-490 ms, at every stop 11-12 moves queued and 7-9 packets waiting).
- P1 (#11 above). Remedies: window ~14 (minfo_buf has 16); probe the group's real queue capacity (13-39 per motion_review) and raise MOTION_BUFFER_THRESHOLD; better, limit by queued motion time or ack on receipt with a credit count.
- P1: the new host timing (lib/stream.ts) logs almost every ack (> 150 ms is normal), keeps 200 rows (~10 s) but is read once a minute, and a PLC stall also shows up as a host send gap. Put PLC timestamps (Comm received, SoftMotion accepted) into each motion ack; log the UI event-loop lag.
- P2: retry cooldown counts down by BusPeriod but the gate tests `= 0`: at a 2-4 ms bus cycle it goes 1 -> -1 and motion stops for good (ProcessFlyEventsAndIo.st:267-269). Use `<= 0`. Not the cause of the pauses (a 100 ms wait cannot empty a 640 ms queue).
- P2 (to check): a refused G1 retried by a new Execute edge might be queued twice if SoftMotion kept the refused one; check movement ids where G1RetryCount > 0.
- P2: gaps between Comm task runs are not measured; the failing Modbus probe re-initialises every 1 s in the same task.
- P3: the per-stop record is taken at the first stopped cycle only; add "ms since the last host packet arrived" and the queue at the end of the stop. PLC-side Nagle unknown.
- Pause hypotheses, ranked: (1) supply interruption before the PLC (TCP retransmission timeout with one small segment in flight -- >= 300 ms on Windows, longer on the PLC, doubling; or a UI event-loop / GC stall with an 87 000-packet array); (2) Comm task blocked; (3) planner lag; (4) refusal loop. Confirm with the new fields (queued 0 / host waiting 0), `netstat -s` retransmits, a capture on port 8125, Comm gap.

### 2.3 Failure and recovery
| Scenario | Verdict |
|---|---|
| Link drops mid-run | PLC right (idle watchdog 5 s, accepted motion runs out, rest dropped); UI needs a human (no reconnect) |
| Reply lost / late | no duplicates, but ambiguous and never reconciled |
| NAK | every NAK is fatal; busy-class NAKs (`flyevent_buffer_full`, `wait_busy`, `tape_busy`, `reel_busy`, `motion_in_flight`) are retryable |
| FSM Error mid-run | fine with RUN; unsafe with resume (#6) |
| PLC download | RETAIN wiped; UI does not reconnect; renderer's plan count pushed back |
| UI restart | fine apart from the plan ledger |
| Two clients | direct clients kept out (1 connection); tools via the UI relay not gated during a run (a tool's FLUSH drops the run's top shots) |
- P2: flat 5 s timeout for every command; a G1 held at the queue head times out in the UI and still runs; no reconciliation by movement id.
- P2: once minfo_buf fills, PINGs stop too (PKT_ALLOW_NEXT needs space in both rings) while raw bytes keep the idle watchdog fed: the link never resets.
- P3: docs say the PLC goes to Error after 5 s of silence and the UI reconnects; neither is true.
- P3: `applyAdvance` counts cells when TAPE_CYCLE is sent, not acked; re-entering a plan gets a new id and `PLAN_SET(...,0)` silently resets the PLC's progress.
- Design: one link supervisor (connecting / up / suspect / down, backoff, reconcile with GET_MACHINE_STATE); a HELLO handshake with a session nonce (dedupe on (session, id), ownership lease); typed holds; one resume path (merge production and RecoveryDemo's scratchpad model).

### 2.4 State ownership and synchronization
| State | Owner | Copies | Risk |
|---|---|---|---|
| FSM state | PLC | plcReady, plcMotionStatus, 5 pollers | high |
| Modal pose | PLC MotionPose (zeroed when not Ready) | cycle's armAtSafeZ / armA | high |
| Error cause | PLC, cleared on UnInited | page copy; not in ST_CHG | med |
| Plan / cells done | PLC (RETAIN) and UI | production_plan, s.packed | med |
| Move ids, coord_set, belt binding, delta mask, override, reel | PLC | small copies | low |
- P2: hidden tabs stay mounted and poll (~5 requests/s idle), some as motion-ring packets that NAK outside Ready.
- P2: the error cause is lost on any reset from any client; keep a first-fault record + `fault_seq` in ST_CHG.
- P2: two plan ledgers; make the PLC the only one.
- P3: move-id jumps flagged as "move dropped" although MOVE_DONE only fires when the queue empties; HEARTBEAT and event `seq` ignored; PING skipped when other traffic was sent; override only a UI copy; redundant SetCoord1.
- Design: PLC `MachineView` snapshot with a `rev`, events carry `seq`; one UI store in PluginHello; `GA_EV {ev, expect_st}`; one `walkToReady()`; a run records `fault_seq` and becomes unresumable when it changes.

### 2.5 Safety and authority
- Inventory (abridged): G1 / M4 / TAPE / ReelGo (Ready + coord gate, no pose check), GA_EV (checked), JOINT_MOVE (Powered, <= 90 deg, <= 20 deg/s, no real/virtual check), DIRECT (#1), DELTA_MODE real (#2), SET_AXIS_SIM (only adds simulation, TTL: fine), DRV_SDO (#5), SET_AXIS_LIMITS (#5), override / FAC (clamped <= 1: fine), SetCoord (refused in motion: fine), COORD1_UNBIND (no guard).
- P2: no position / workspace limits in the PLC (G1 poses unchecked, JOINT_MOVE +-90 deg on an unhomed real axis); jog limits only in the UI.
- P2: COORD1_UNBIND has no motion-in-flight guard (motion_review rec. 9 still open).
- P2: the UI's STOP ends the loop at the next checkpoint and then moves to safe Z; there is no E-stop input in the PLC; only GA_EV 9 stops at once.
- Design: L0 hardware E-stop / STO (SS1-t); L1 PLC operating modes PRODUCTION / SETUP / MAINTENANCE (`GVL.OpMode`, key switch DI, or `SYS MAINT_ARM` with a DI pulse and a TTL; every command classed READ / PROD / SETUP / MAINT in the command table); PLC-side bounds in every mode (constant caps, joint limits, workspace check, real-delta TTL); L2 transport (IP allowlist, HELLO + token, one controlling session); L3 host conveniences only (owner gate, confirmations, harness token, no `plc_send` in production builds).

### 2.6 Observability and testing
- P1: incident evidence is not kept: the event ring (1024) covers ~10-15 s and only a sim tool drains it; the UI keeps 100 push events in memory; no event types for EtherCAT master / slave state, NAK, error id, connect / disconnect, motion queue empty.
- P2: VERSION returns 'unknown' (stamp_build_info runs only in an unused path); deployed_fp.json is never compared with the PLC.
- P2: DiagBadge / DiagPanel leave out several drop / NAK counters; DiagReplySendDrop is not in GET_DIAG.
- P2: transport untestable (inside a React component; stream.ts untested; the fake PLC archived).
- P2: the sim lives on the Desktop, runs SM3 4.20 + A in the group while the machine runs 4.18 + Kin_CAxis, needs undocumented patches (EasyCAT stand-in GVL, MAX_TRANS_PARAMS), and device tree / tasks / libraries are not in codesys_code.
- P3: 192.168.1.70 hard-coded in ~55 files; tools/sim/queue_test.py defaults to the real machine.
- Test pyramid: unit (extract lib/transport.ts from PluginHello; stream.ts, parsers, deploy.py step order with a fake `mc`); contract (Node fake PLC from mock_plc.py, golden replies recorded once from the PLC, back-pressure / reconnect / seq gaps); sim integration nightly (sim generated from a manifest of the real project with a drift check); hardware-in-the-loop soaks with automatic incident capture.
- Minimum observability: one `seq` stamped at send; PING with host and PLC clocks and RTT logged; the new event types; a UI JSONL log of every request (id, latency, timeout, NAK, gap); VERSION with git sha + fingerprint checked after every deploy; an incident bundle (PLC log, slave states, EC_STATS, event ring, UI log) written before any recovery.

## 3. Suggested order

The owner asked for sim-only testing for now (2026-10-05), so PLC changes go to the local soft PLC first and to the machine only with the owner's go.

1. Host only, no PLC download: bus_watch (owner gate, evidence first, no auto-download); harness token / JSON only / Origin check; UI `plcReady` derived from the PLC, one guarded `walkToReady()`, no polling from hidden tabs; resume blocked after a PLC fault; `isUnknownCommand` fixed; encoded-size guard + test per command; Python retry only for idempotent commands; strict DWELL parsing; host timing reworked (PLC timestamps come in step 2); doc fixes.
2. One PLC change set (sim first): DIRECT / DRV_SDO write / SET_AXIS_LIMITS behind a maintenance gate with constant caps; cooldown `<= 0`; `seq` stamped at send; DWELL / SETTLE strings capped with a `next` index; trigger TTL NAKs the waiting id; first-fault record + `fault_seq` in ST_CHG; `GA_EV expect_st`; accept / receive timestamps in motion acks; Comm gap metric; motion-queue-empty event.
3. Design work: protocol.yaml + generator; operating modes; MachineView store + link supervisor + HELLO; time- or credit-based flow control; generated sim.
