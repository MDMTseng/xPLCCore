# CalibPage solidification plan

Findings from a review of [CalibPage.tsx](../../components/CalibPage.tsx) (~3200 lines, single
component, `runAllObjects` ~1000 lines of nested closures). This file is
the working list for making the main loop less clunky without changing
runtime behavior. Update the **Status** column as items land.

Scope: the renderer-side main production loop. PLC, vision, and
architecture decisions are out of scope — see
[`plc.md`](./plc.md), [`vision_contract.md`](../2-contracts/vision_contract.md),
[`architecture.md`](../1-concepts/architecture.md), and chat history.

PLC command literals in this file are now built via the typed
`cmd.*` builders from [`lib/protocol.ts`](../../lib/protocol.ts)
(`cmd.G1({...})`, `cmd.M4({...})`, etc.). Hand-rolled `{type:'M', cmd:...}`
literals should not appear in new code.

---

## Why it's clunky (structural diagnosis)

1. **`runAllObjects` is a god-function.**
   [`checkSlot_and_reelAdv`](../../components/CalibPage.tsx#L595),
   [`checkFlexFeederPlate`](../../components/CalibPage.tsx#L712),
   [`goCheckFlexFeederPlate`](../../components/CalibPage.tsx#L778),
   [`waitTime`](../../components/CalibPage.tsx#L1055),
   [`placeObject`](../../components/CalibPage.tsx#L1213) are all defined *inside* the
   main loop and close over its locals. That tangle is the #1 reason
   any edit feels risky.

2. **The pick→inspect→place state machine is implicit.** Each iteration
   of the `for(let i=0;;i++)` loop does pick, side-inspect,
   bottom-inspect, top-slot-inspect, decide, toss-or-place — with no
   explicit state. Business logic (NG classification via
   `tossReasons.push(...)`), motion commands, I/O pulses, and vision
   triggers are all interleaved.

3. **Parallelism uses Promises stashed in local vars.**
   `slotCheckPromise`, `feederCheckPromise`, `waitForReelVisualClearPromise`,
   `slotCheckPromise_BK` pipeline vision with motion correctly, but
   reading-order ≠ execution-order, which is hard to follow.

4. **`_this` ref as untyped mutable bag** ([line 68](../../components/CalibPage.tsx#L68)):
   `_this.isRunning`, `_this.run_cycle_stop`, `_this.current_error`.
   Invisible to React, untyped, used as the thread-stop signal. Every
   field is a landmine.

5. **NaN-as-signal pattern**: `targetPickSlotIdx==targetPickSlotIdx`
   and `slotHoleOffset.X!=slotHoleOffset.X`
   ([lines 1197, 1300](../../components/CalibPage.tsx#L1197)). Works, unreadable.

6. **Command literals repeated with subtle variants** — every
   `{ "type": "M", "cmd": "M4", "pin": 1<<IO_Pins.O.X | 1<<IO_Pins.O.Y, "state": ..., reset_ms: N, ... }`
   is hand-built. Impossible to grep "all places that trigger the
   bottom camera."

7. **Input watchdog is a fire-and-forget IIFE** ([line 809](../../components/CalibPage.tsx#L809)).
   Stop signal is `_this.run_cycle_stop` (not reset anywhere obvious;
   leak-prone on error paths).

8. **Commented-out experiments interleaved with live code** (lines
   612–652 and many more). Slows every cleanup pass — can't tell dead
   from alive at a glance.

9. **Top-level module-scope state** — `let IO_Pins = {...}` and check
   IDs (`FFeederCheckID=104500`, etc.) declared with `let` instead of
   `const`; check IDs re-created every render.

10. **Dense bitmask arithmetic** in `pin_op_seq` constructions — e.g.
    `reelAdvPinOpSeq.push(reelAdvWaitTime, 1<<IO_Pins.O.ReelAdv, 1<<IO_Pins.O.ReelAdv)`
    — a small builder would be much crisper.

---

## Ranked fixes

Priority is leverage ÷ risk. Do in order unless you have a reason not
to.

| # | Fix | Risk | Payoff | Status |
|---|---|---|---|---|
| 1 | **Extract named step functions.** `pickFromFeeder`, `inspectSide`, `inspectBottom`, `inspectTopSlot`, `evaluateInspection`, `placeOrReject`. Closure-captured locals become explicit parameters. Main loop shrinks from ~550 lines to ~60. | Low (mechanical cut-and-paste) | Huge | Open (refactor — held) |
| 2 | **`camTrig(cam, light, opts)` helper** wrapping the `M4` bitmask soup for camera+light pulses. One function, all call sites use it. | Low | High | **Done 2026-04-26** |
| 3 | **Type the `_this` ref.** `type RunCtx = { isRunning: boolean; runCycleStop: boolean; currentError?: {...} }` and `useRef<RunCtx>({...})`. Catches half the latent bugs just by typing. | Low | Medium | **Done 2026-04-26** |
| 4 | **Replace `x !== x` with `Number.isNaN(x)`**. | Zero | Readability | **Done 2026-04-26** |
| 5 | **Split input watchdog into a standalone function.** Lifted out of the IIFE into a named `inputWatchdog()` async function; stop signal still rides on `_this.run_cycle_stop` for now. Long-term this watchdog should move into the PLC (see [`plc.md`](./plc.md) item A5). | Low-medium | Medium | **Done 2026-04-26** (renderer-side lift) |
| 6 | **Unify location/IO constants.** Toss / wait / safe-Z locations now share a single `XYZ` type and module-scope `const` declarations; check IDs are module-scope. Extraction into a separate `constants.ts` file is still open. | Low | Medium | **Partial 2026-04-26** (in-file unified; separate file deferred) |
| 7 | **Delete or archive commented-out experiments.** Removed `VP_regTcpMsgPromise`, `number2bitString`, `postInspPromise`, dead `Flex`/`Splitter`/`PointXYZA` imports/types, and unused `let X = await Y` capture sites. | Zero | Readability | **Done 2026-04-26** |
| 8 | **Data-drive NG classification.** Single `evaluateInspection(results): { ok: true } \| { ok: false, reason: string, bin: TossLocation }`. Replaces scattered `tossReasons.push + ETC_NG_Location = ...`. | Medium | High (this is where bugs hide) | Open (needs product input on taxonomy) |
| 9 | **Reducer / explicit state-machine rewrite** of the main loop. Biggest payoff, biggest disruption. Do not attempt until #1–#8 are done — by then the right state shape is obvious instead of guessed. | High | Huge, later | Deferred |

---

## Rules for working on this file

- **Refactor without behavior change first.** #1–#7 are purely
  mechanical. Verify by running a full production cycle and comparing
  logs before/after.
- **One fix per commit.** Makes bisecting possible if something
  regresses at the machine.
- **Preserve parallelism.** The Promise-stashing pattern
  (`slotCheckPromise`, `feederCheckPromise`,
  `waitForReelVisualClearPromise`) is load-bearing — see
  [`plc.md`](./plc.md) §Machine sequence & parallelism constraint
  for why. Cycle time is the acceptance gate; "it still runs" is
  not sufficient.
- **Don't touch the PLC command shape.** `M4`, `G1`, `M`,
  `WAIT_FOR_TRIGGER_MOTION_PROGRESS` are a stable contract with
  `AxisGroupSM`. Wrap them in helpers; don't change them.
- **Update this file as items land.** Move completed items to a
  "Recently completed" section with a one-line summary and date.

---

## Refactor rules

When extracting named step functions (fix #1) or wrapping triggers
(fix #2), any stage that triggers a camera **must return the Promise
so the caller can `await` it later across cycles** — do not hide the
await inside the stage. This preserves the pipelining shown in
[`plc.md`](./plc.md) §Timing diagram.

```ts
// Good: trigger returns Promise, caller awaits later
function triggerBottomCam(): Promise<BtmData> { ... }

// Bad: hides the parallelism opportunity
async function inspectBottom(): Promise<BtmData> {
  sendTrigger();
  return await waitForBtm();  // caller can't pipeline across cycles
}
```

---

## Recently completed

- **2026-04-26** — Fixes #2, #3, #4, #5, #7 landed; #6 partial (in-file
  unified, separate constants module deferred). All call sites of
  hand-rolled `{type:'M', cmd:...}` in CalibPage now route through
  `cmd.*` builders from [`lib/protocol.ts`](../../lib/protocol.ts).
  Camera+light strobe pulses go through the new `camTrig(camPin,
  lightPin, opts)` helper. `npx tsc --noEmit` clean throughout.
