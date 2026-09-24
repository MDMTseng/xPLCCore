# Whole-flow analysis and improvement plan (2026-09-25)

Branch `flow-analysis`. Scope: the production cycle end to end (renderer
loop, PLC program, command set) and the dev/test/deploy loop. It builds
on, and does not repeat, `doc/1-concepts/solidification.md`,
`doc_review/code_review_2026-09-24.md` and `doc/3-subsystems/plc.md`
§Direction. Line numbers are as of commit 45e249c. "Verified" means it
was checked against code or measured in this session; otherwise the
finding comes from a read-through and should be confirmed before fixing.

## 1. Measured baseline

Virtual scene (delta trio virtual, vision mock), plan `1,-20,20,-20,1`,
10 % NG per camera. Real PLC runs `gap_50..54`, PC soft-PLC sim runs
`simgap_50..54` (`tools/sim/compare_runs.py --pairs "gap_5*:simgap_5*"`).

| | real PLC | PC sim |
|---|---|---|
| run time, no stops (seed 50) | 62 s | 51 s |
| place to next place, median | 1.1-1.2 s | 0.83 s |
| cycles / parts packed | 39 / 22 | 39 / 22 |
| arm moving / waiting | 64 % / 18 % | 74 % / 20 % |
| host packets per placement | - | ~50 (551 for 10 parts, incl. heartbeat and polling) |

Where the time goes (gantt of `gap_50`, verified):

- **1/3 of cycles end in a toss.** Of 13 tosses, 5 were plan-driven
  ("segment count reached, drop back", "slot past remaining count"): a
  good part is picked, inspected by both cameras, and only then checked
  against the plan (`GetProductionPlan`, `CalibPage.tsx:1572`). About
  1.1 s each.
- **Each empty segment stalls the arm ~5 s.** `-20` is advanced 2 cells
  per loop pass (`Math.min(2,…)`, `CalibPage.tsx:1062`), each pass a full
  TAPE_CYCLE + top-result wait + fixed `delay(100)` (1063-1066). 10
  passes x ~0.5 s, with the arm idle. Two such stalls = 10 s of the 62 s.
- The arm also stops twice per cycle for vision: side+bottom results
  (1271, 1280) and the rectified side result at the half-way park
  (`PRE_PLACE_FRACTION=0.5`, 1340-1345).
- Feeder refill is ~1.1-1.4 s of host-timed Modbus writes and fixed
  delays (846-891): 160 + 120 + 700 ms brake + light delays.

The sim is 15-30 % faster (loopback vision, EAXIS_A virtual, different
scheduler). It is trustworthy for sequence/plan logic, not for timing or
load limits. Axis parameters are otherwise identical (2196 compared).

## 2. Bugs found (fix first)

| # | Where | Problem | Status |
|---|---|---|---|
| B1 | `CalibPage.tsx:2763-2769`, 2089, 2929 | Error hold parks *any* checkpoint and "resume" resolves it with `undefined`. Parked at `cycle_start` or `GetProductionPlan` the loop then reads `undefined.production_plan` (1055, 1572) and the run aborts, at 1572 with a part on the nozzle. Parked at `[STEP][REEL ADV]` the plan decrement is skipped. | verified |
| B2 | `CalibPage.tsx:961`, 1768 | Input watchdog stops only on `run_cycle_stop`; a plan end or exception leaves it pulsing `ReelWheelFeed`, and each RUN adds another. | read |
| B3 | `ProcessMotionPacket.st:313-320` | `ReelGo` acks TRUE when both reel FBs are busy and nothing moves. | read |
| B4 | `AxisGroupSM.st:1033-1043` | TAPE_CYCLE claims its fly-event slot only after the reel moved; a full buffer means tape advanced without top shots. | read |
| B5 | `AxisGroupSM.st:1007-1017` vs `ProcessMotionPacket.st:305-317` | TAPE_CYCLE and ReelGo share `reelGo*` parameters; a ReelGo in the same scan rewrites the tape move. | read |
| B6 | `AxisGroupSM.st:964`, `ProcessMotionPacket.st:66` | host-supplied digital-input `group` index not bounds-checked. | read |
| B7 | `TCP_MSGPAK_Server.st:99-125` | packets > 255 bytes and ring-full routing are dropped with no NAK. | read |
| B8 | fly-event buffer, outputs | nothing flushes fly events, pending waits or `DigitalOutputBits` on Error/UnInited/disconnect; stale triggers can fire after recovery, a light can stay on. | read |
| B9 | `CalibPage.tsx:1530-1531` | X offset uses the tape camera mm/px, Y the bottom camera's (also in the 09-24 review). | read |
| B10 | `PluginHello.tsx:597` | `sendTcpMsgPack` returns `false` with no socket; `await false` looks like success. | read |

## 3. Command set

Today one placement sends ~40 functional packets: ~20 `G1`, 4-5 `M4`
variants, 5 `G4`, one `TAPE_CYCLE`, ~6 `EVT_MARK`, plus heartbeat and two
independent input pollers (221-238 every 500 ms and the watchdog every
400 ms). The PLC side takes **one packet in and one reply out per 5 ms
Comm tick** (`FB_TcpMsgPakServer.st:247` EXIT after one packet;
`TCP_MSGPAK_Server.st:134-182`). A 4-packet burst therefore costs ~20 ms
before the last one is even in the ring, and every ack, event, heartbeat
and MOVE_DONE queues behind the same 5 ms egress.

Proposals, in order:

1. **Composite commands for the fixed sequences** (direction already set
   in plc.md, TAPE_CYCLE done). Next candidates, each replacing 5-10
   packets and the host-side timing between them:
   - `PICK {x,y,z,a, dwell_ms, lift}`: approach, Z down, vacuum on,
     dwell, Z up. Reply on accept; event when lifted.
   - `INSPECT {pose, shots:[side,btm], rotate}`: move to inspection, fire
     the side shot at end of move, bottom shot, rotate for the rectified
     shot. Shots stay fly events.
   - `PLACE {slot pose, blow_ms}` and `TOSS {bin}`.
   - `FEEDER_CYCLE` (plan.md step 4): vibration, brake, light, camera
     trigger as one PLC sequence instead of Modbus writes with host
     delays. Removes ~1 s host jitter from every refill.
   The renderer still decides *what* to do; the PLC only runs *how*.
2. **Batch frame**: allow one msgpack array of commands per frame, parsed
   and dispatched in one scan (keeps per-command ids and replies). Cheap
   win for the remaining G1 chains without new semantics.
3. **Comm throughput**: parse up to N packets and send several reply
   slots per Comm scan, or run Comm at 1-2 ms. Needed before (2) pays
   off fully.
4. **Unify the trigger commands.** `M4`, `M4Bind`, `M4ImmediatePinOp`,
   `M4DistancePinOp`, `WaitForTriggerMotionProgress` are one concept:
   *when* (immediately / at motion progress / at distance / at movement
   id) plus *what* (`pin_op_seq`). One `TRIG {when, ops, ttl}` with a
   typed `when` removes three parsers (`ProcessMotionPacket.st:268-294`
   and 437-481 are copies) and the magic trigger codes 20/120/130.
5. **Acks by exception for motion.** A `G1` ack only says "queued"
   (`protocol.md:94`); make it opt-in (`ack:false` default inside
   composites) and NAK on failure. Halves egress for motion.
6. **Diagnostics off the control path.** `EVT_MARK` (6/cycle) travels as
   an ordered command. Piggy-back an optional `mark` field on the next
   command, or let the renderer log its own marks with the PLC time from
   the last reply.
7. **Explicit failure semantics everywhere**: every command either acts
   or NAKs with a reason (fixes B3, B7); replies carry the first fault,
   not the last writer of `LastErrorSource`.
8. **Order-free reads on the SYS ring**: `GET_DIGITAL_INPUT`,
   `READ_LATEST_CMD_LOCATION`, flip counts currently wait behind motion
   back-pressure in `minfo_buf`. Route them to `sysinfo_buf`, and merge
   the two host input pollers into one subscription/event.

## 4. Cycle-flow changes (renderer)

1. **Decide the plan at `cycle_start`, not after inspection.** If the
   current segment is full or the next slot must stay empty, do not
   pick; advance the tape instead. Removes the plan-driven tosses.
2. **Empty segments in one pass**: one reel move of `n` pitches with the
   top shots as distance-triggered fly events (the mechanism exists),
   and pick + inspect the next part while the tape runs. Removes the
   ~5 s stall per segment.
3. **Keep the arm moving through vision waits**: queue the move over the
   slot while the rectified side result is pending and hold back only
   the irreversible Z-down. Needs a machine test (tape vibration note at
   742-745).
4. `save_target` (1443) is awaited with no timeout before every place;
   make it fire-and-forget as `vision_contract.md:135` describes, or add
   a timeout.
5. Stop re-rendering the 3,500-line page on every `_PACK_INFO_`; throttle
   state updates.

## 5. Structure (renderer and PLC)

- `runAllObjects` is ~1,060 lines in one closure with helpers redefined
  per pass; `_this` is an untyped shared bag written by the loop, the
  checkpoint handler, the watchdog and the harness; checkpoints are a
  string protocol whose branch order matters. Plan: `production/{cycle,
  tape,feeder,inspect,place}.ts`, a typed context, a typed checkpoint
  union, plan state owned by one module (fixes the class of bug B1), and
  `orchestrator/resume.ts` wired in so the plan survives a reload
  (solidification W4 #1/#9).
- PLC: `ProcessMotionPacket` 798, `DrainHostPackets` 748, `AxisGroupSM`
  1117 lines, methods coupled through PROGRAM variables. Extract
  `ParsePinOpSeq`, `ResolveTargetId`, `ReadTtl`, `InsertFlyEvent`,
  `DispatchReel` first (they are duplicated today), then the split in
  plc.md P3 #15. Error stop: let GroupStop finish before power off
  (`Update.st:194-217`). `RuntimeMs` counts scans, not time.

## 6. Dev / test / deploy loop

- `rpc.py push` runs the whole live pytest suite, including soak tests
  that run for hours unless a `*_SHORT` env var is set. Add markers
  (`offline`, `live`, `soak`) and default push to `live and not soak`.
- CI (`.github/workflows/ci.yml`) runs tsc, vitest (2 test files) and
  `py_compile` only. Add the offline pytest (`test_plc_guard.py`,
  layout_check, `plan_check.expected()`) to CI.
- **Use the PC soft-PLC sim as a regression gate** (self-hosted runner on
  this PC): install to the sim, run `regression.py --plc 127.0.0.1` and
  the gap/chaos plan tests, compare against the real baseline with
  `compare_runs.py`. Hardware only for timing and real-axis tests.
- Take the PLC host from `config.plc_host` everywhere (12 templates, 3
  tests, every `tools/sim` script hard-code 192.168.1.70), so
  `XPLC_CONFIG` switches the whole toolchain.
- Templates: 78 in `jobs/templates` (README says 15). Archive one-offs,
  move `viz_*` to `tools/viz`, fix the remaining `login(Never)` in
  `probe_modbus_live.py`, add a CI grep for Never/Force outside the
  install templates. `doc/4-dev/scripting.md` still describes the v1
  flow; rewrite from `codesys_scripts/README.md`.

## 7. Roadmap

| Phase | Items | Verify with |
|---|---|---|
| 0 - bugs (days) | B1, B2, B3, B6, B7, B10; offline pytest in CI; push test tiers | unit tests; sim gap/chaos runs incl. an injected error hold |
| 1 - cycle flow (1-2 weeks) | plan at cycle_start; one-pass empty segments; save_target async; B4, B5, B8 | sim plan tests + `compare_runs.py`: plan-driven tosses -> 0, empty-segment stall -> < 1 s |
| 2 - command set (2-4 weeks) | Comm N-per-scan; batch frame; `FEEDER_CYCLE`; `PICK`/`PLACE`; unified `TRIG`; diagnostics off path | packets/placement (`GVL.HostRxCount`) from ~50 to < 15; place-to-place on the real machine |
| 3 - structure (ongoing) | renderer module split + typed checkpoints + resume; PLC helper extraction; sim as CI gate | existing regression + sim gate |

Expected effect of phases 0-1 on the measured run (estimate): about 10 s
of stalls and ~5 wasted cycles removed from a 62 s run, i.e. 20-25 %
shorter for plans with empty segments; ordinary place-to-place is only
affected by phase 2.

## 8. Status (end of 2026-09-25)

Verified on the PC soft-PLC sim, plan `1,-10,60,-11,1` at 0.5 % NG (no
stops, 6 random stops, 6 simulated renderer crashes) and `4,-5,3,-3,2` at
10 % NG. All PASS; the renderer's and the PLC's plan books match.

| Item | Status | Commit |
|---|---|---|
| B1 error hold / resume | fixed | cb5320f |
| B2 watchdog outlives the run | fixed | cb5320f |
| B3 ReelGo acks a dropped move | fixed (NAK reel_busy / tape_busy) | cb5320f |
| B4 TAPE_CYCLE slot claimed late | fixed (Reserved slot at accept) | cb5320f |
| B5 ReelGo overwrites a running TAPE_CYCLE | fixed (tape_busy) | cb5320f |
| B6 digital-input group unchecked | fixed (bad_group) | cb5320f |
| B7 overlength packets dropped silently | fixed (packet_too_long NAK) | cb5320f |
| B8 fly events survive Error | fixed (cleared on Error/UnInited); outputs deliberately kept (vacuum) | cb5320f |
| B9 hole Y in bottom-camera mm/px | fixed, owner: keep X+Y, top-camera scale | eb8022b |
| B10 `await false` on no socket | fixed (send/sendNoWait) | cb5320f |
| MoveLeft MemCpy overlap, TO_INT delay wrap, overflowed reply pushed, Modbus split lines | fixed | 3977a07 |
| WAIT_FOR_TRIGGER inherits stale stages | fixed (FlyEventBlank) | fd666a3 |
| Plan decided before picking; empty segments in one step, overlapped with the pick; save_target async | done: no-stop run 71 s -> 61 s, plan tosses 4 -> 0 | cb5320f, eb8022b |
| PLC keeps the plan (whole plan + cells advanced, packed/empty, kind check), renderer resumes after a crash | done | cb5320f |
| Comm task 1 ms, time-based send stall | done on the sim; `set_comm_task_period.py` for the machine | d8b181c |
| Renderer structure: params.ts, plan/tape/feeder/nozzle/judge modules behind `Machine`, 64 unit tests | done (loop body itself still in CalibPage) | cb5320f..5c316af |
| Offline pytest in CI, HMR off during harness runs | done | cb5320f |

Needs the machine (not changed):
- Error stop sequence (let GroupStop finish before power off, `Update.st`).
- Feeder brake / vibration timings (the largest remaining arm idle in the sim).
- Real-machine latency (top shot 205 vs 121 ms in the sim): measure with
  `compare_runs.py` after installing this build.
- Install checklist for the real PLC (one on-site `install`): the new
  retained plan variables, the TYPE change (Reserved), Comm 1 ms
  (`set_comm_task_period.py` first), FlyEventBufferSize 32.

Also done: the loop body is `lib/production/cycle.ts` (fb8d3d7), with an
offline end-to-end test that runs whole plans against a fake tape.

Tried and dropped: picking the next part while a segment settles and
holding it across the empty segment. Top-result idle fell 1.45 -> 0.49 s,
but the held part then waited ~1.3 s for the empty step's view before
placing; total arm idle did not move (sim, h1_101: 60 s vs 61 s, noise).
Not worth the extra state and the deferred STOP.

Where the sim run goes now (plan 1,-10,60,-11,1, 58 s of arm time): arm
idle ~4.6 s, of which ~2.1 s is the feeder refill (vibration + 700 ms
brake: machine tuning) and ~1.5 s the segment transitions. The rest is
motion. Further speed has to come from the machine side: feeder timings,
motion profiles, and the real-PLC latencies (compare_runs.py).

Next in code: move the loop body of `runAllObjects` into
`lib/production/cycle.ts` (typed context and checkpoints), then the
command-set work of §3 (composite PICK/PLACE/FEEDER_CYCLE, batch frames).
