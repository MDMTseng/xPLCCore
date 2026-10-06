# Open work (living list, started 2026-10-06)

Everything still to do from the reviews and tests of 2026-10-03..06, in one
place. Each item names its source; tick it off here when it is done (with
the commit). Rules that apply to all of it:

- PLC changes go to the local soft PLC first (`codesys_env.sim.json`), then
  to the machine only with the owner's go, through `tools/deploy.py`
  (owner's write sequence), one download per change set.
- Real-delta motion only with the owner's OK for that run (`--owner-ok`).
- Tags: **[UI]** UI / host only, **[PLC]** needs a download, **[M]** needs
  the machine (and someone at it), **[S]** can be checked on the sim.

## 1. Machine health (do first)

| # | Item | Tags | Source |
|---|---|---|---|
| 1.1 | EtherCAT dropout recurred 2026-10-06 at standstill, 22 min after a power cycle (QEC + reel leave OP, ~2.5 s lost). Check the QEC / reel supply and cables again; long standstill watch with `tools/bus_watch.py --owner-ok` (stops and keeps evidence) to get the pattern | M | ethercat_dropout_2026-10-03.md (reopened) |
| 1.2 | Unplanned 40-877 ms pauses (~1 per 10 min): run a triangle soak on the machine with the new per-stop fields (queued moves, host packets waiting, cooldown, Z, ms since last accept) and the host timing (reply gaps, UI loop lag, late sends); confirm "supply interrupted > ~1.1 s" and find where (TCP retransmission, UI GC, Comm task) | M, owner OK | plc_ui_integration_review §2.2, #11 |
| 1.3 | 75 % triangle 6 h soak (stopped on 2026-10-05 to debug the pauses) | M, owner OK | soak campaign |
| 1.4 | Verify RETAIN (plan, cells done, open tape move) survives a power loss on the Intewell target | M | plan_counting_audit #9 |

## 2. Production plan counts

| # | Item | Tags | Source |
|---|---|---|---|
| 2.1 | Before each empty-segment advance and the first step of a new plan: the top view must show the cells about to leave clear, else hold "remove part" | UI, S | plan_counting_audit #4 / fix 3 |
| 2.2 | Tape moved outside the plan (manual ReelGo, pulled by hand): keep the reel odometer at each counted close in RETAIN, compare at RUN, refuse on a difference; or refuse ReelGo while a plan is unfinished | PLC, UI, S | audit #3 / fix 4 |
| 2.3 | "Position lost" recovery: UI to jog to the hole and send REEL_CLEAR, count suggested from the odometer; REEL_CLEAR's count checked | UI, PLC | audit #6 / fix 6 |
| 2.4 | Count / speed display: from the PLC ledger, not added per run | UI | audit #7 |
| 2.5 | PLAN_SET zeroes PlanCellsPacked / PlanCellsEmpty even with cells_done > 0 | PLC | audit #7 |
| 2.6 | Tests: STOP / hold at each checkpoint then a second run on the same tape; plan change with leftover parts; stray part before an empty segment; failed last step (offline, fake PLC) | UI | audit tests |

## 3. Safety and authority

| # | Item | Tags | Source |
|---|---|---|---|
| 3.1 | Transport authority: IP allowlist or dedicated NIC, HELLO with a session token, one controlling session (MAINT_ARM is a gate, not an authentication) | PLC, UI | integration review #2, §2.5 |
| 3.2 | Operating modes PRODUCTION / SETUP / MAINTENANCE (`GVL.OpMode`, key-switch DI or MAINT_ARM with a DI pulse); every command classed READ / PROD / SETUP / MAINT | PLC | §2.5 |
| 3.3 | Workspace check for G1 poses (NAK `out_of_workspace`); joint software limits; JOINT_MOVE on an unhomed real axis limited | PLC, S | §2.5 P2 |
| 3.4 | COORD1_UNBIND guard against motion in flight | PLC, S | §2.5 P2, motion_review rec. 9 |
| 3.5 | E-stop input in the PLC (refuse power-on, DIRECT, JOINT_MOVE, REEL_RESUME, resets while pressed); UI "STOP" labelled "stop after cycle" + a "Stop now" (GA_EV 9); no safe-Z lift after a fault | PLC, UI, M | §2.5 P2 |
| 3.6 | No harness / `plc_send` in production builds; relay allowlist of read-only commands while a run is on | UI | #4, failure F6 |
| 3.7 | DELTA_MODE real expires back to virtual (TTL) | PLC | #2 |

## 4. Protocol and failure handling

| # | Item | Tags | Source |
|---|---|---|---|
| 4.1 | `contract/protocol.yaml` single source + `tools/gen_protocol.py` (ST constants / ProtoLookup / CASE dispatch, `lib/protocol.gen.ts`, `tools/xplc_proto.py`, doc tables) + `tools/proto_lint.py` | PLC, UI | §2.1 design |
| 4.2 | Numeric error codes with a class (busy / fatal / ...); busy-class NAKs retried with backoff; one classifier for UI and Python | PLC, UI | F3, §2.1 |
| 4.3 | VERSION with proto major/minor + capabilities, called on connect; replace error-text sniffing | PLC, UI | P2-5 |
| 4.4 | Link supervisor in the UI (connecting / up / suspect / down, backoff, reconnect, reconcile with GET_MACHINE_STATE); act on a stale HEARTBEAT | UI | F1 |
| 4.5 | Timeouts: scale motion timeouts by queue depth and override; on a timeout reconcile by movement id; host deadline = PLC deadline + margin for every deferred command | UI | F4, P1-2 |
| 4.6 | `seq` stamped once at send time in the Comm task; UI counts seq gaps and late replies | PLC, UI | #12 |
| 4.7 | Host packet room: bigger PLC slots or shorter keys (TAPE_CYCLE still ~258 B worst case at override 0.01) | PLC, UI | #9 |
| 4.8 | When minfo_buf is full, PINGs stop too (PKT_ALLOW_NEXT needs room in both rings) while raw bytes keep the idle watchdog fed: gate each ring separately | PLC, S | F5 |
| 4.9 | Plan ledger: `applyAdvance` at send is kept for the pipelined cycle; consider counting from the TAPE_CYCLE ack | UI | F8 |

## 5. Flow control and timing

| # | Item | Tags | Source |
|---|---|---|---|
| 5.1 | Motion buffer counted in time (ms of queued motion) or ack-on-receipt with credits, instead of 12 moves + window 8 (~1.07 s) | PLC, UI | §2.2 P1 |
| 5.2 | Probe the axis group's real queue capacity; raise MOTION_BUFFER_THRESHOLD / the UI window (minfo_buf has 16) | PLC, S | §2.2 |
| 5.3 | PLC accept / receive timestamps in every motion ack | PLC | §2.2 P1, step 2 leftover |
| 5.4 | Check a refused-then-retried G1 is not queued twice (movement ids where G1RetryCount > 0) | S | §2.2 P2-4 |
| 5.5 | PLC-side Nagle on port 8125 (capture with Wireshark) | M | §2.2 P3 |

## 6. State ownership (UI)

| # | Item | Tags | Source |
|---|---|---|---|
| 6.1 | PLC `MachineView` snapshot with `rev`; one store in PluginHello; pages read it instead of polling | PLC, UI | §2.4 design |
| 6.2 | A run records `fault_seq` at start and becomes unresumable when it changes (the PLC now publishes fault_seq) | UI | §2.4 |
| 6.3 | Error cause shown from the fault record (fault_src / fault_seq) instead of err_src | UI | §2.4 P2 |
| 6.4 | Override in the PLC snapshot; drop the redundant SetCoord1 + 100 ms; move-id "dropped" warning fixed; HEARTBEAT seq used | UI | §2.4 P3 |
| 6.5 | Decide the scratchpad / RecoveryDemo resume model: adopt in production or remove | UI | §2.4 P3 |

## 7. Observability and tests

| # | Item | Tags | Source |
|---|---|---|---|
| 7.1 | Event ring types: EtherCAT master / slave state, NAK, error id, connect / disconnect, motion queue empty; drain it to a file during runs | PLC | §2.6 P1 |
| 7.2 | UI JSONL log of every request (id, latency, timeout, NAK, gap) | UI | §2.6 |
| 7.3 | Common clock: PING with LTIME / RTC, host logs (send, recv, plc) per ping | PLC, UI | #12 |
| 7.4 | VERSION stamped with git sha + fingerprint on every deploy (deploy.py), checked after it | tools | §2.6 P2 |
| 7.5 | DiagPanel / DiagBadge: the missing drop / NAK counters, diag_send_drop, comm gap, maint counters | UI | §2.6 P2 |
| 7.6 | Extract `lib/transport.ts` from PluginHello; Node fake PLC (from `_archive/mock_plc.py`); golden replies recorded once from the PLC; tests for stream.ts | UI | §2.6 P2 |
| 7.7 | Sim generated from the real project (manifest of libraries, tasks, slaves, IO channels) with a drift check; today it needs GVL_SimEsp, the MAX_TRANS_PARAMS patch and SimNoFieldbus by hand, and runs SM3 4.20 vs 4.18 on the machine | tools | §2.6 P2 |
| 7.8 | `192.168.1.70` hard-coded in ~55 files; `tools/sim/queue_test.py` defaults to the machine; `topology.py` ignores XPLC_CONFIG | tools | §2.6 P3 |

## 8. PLC structure (from the 2026-10-04 review)

| # | Item | Tags | Source |
|---|---|---|---|
| 8.1 | `Stopping` state: stop to standstill (or timeout) before power-off on Error | PLC, M | plc_architecture_review_2026-10-04 |
| 8.2 | Fault classes: a belt-window / Coord1 fault should not force a full re-home | PLC | 10-04 review |
| 8.3 | DrainHostPackets command table + CASE dispatch (with 4.1) | PLC | 10-04 review |
| 8.4 | Split AxisGroupSM; split GVL | PLC | 10-04 review |
| 8.5 | plc_direct tools onto `xplc.Machine(Direct())` | tools | 10-04 review |
| 8.6 | First-cycle "Axis variable is not an AXIS_REF" from the reel FBs; the 3 new build warnings; CmpLog `Logger.0.MaxEntries` bigger than 500 | PLC | 10-04 review, PLC log |

## Done recently (for reference)

- Integration review step 1 (host) d203055, step 2 (PLC: MAINT_ARM gate, DIRECT caps, cooldown, strings, trigger TTL NAK, fault record, GA_EV expect_st, stop-cause fields, comm gap) f771c6a, deployed to the machine 2026-10-06 08:33 with deploy.py.
- UI skips homing when the PLC reports the delta virtual 0034b17.
- Plan counts: one ledger, re-apply continues, confirmations efcf169.
