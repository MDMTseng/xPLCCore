// The production cycle: one part per pass, from the plan to the tape.
//
// Moved out of CalibPage's runAllObjects (2026-09-25). The page keeps the
// set-up (plan sync, start pose, bottom-camera calibration, input
// watchdog) and the checkpoint handler (STOP, step mode, error hold, plan
// bookkeeping); this module is the loop:
//
//   cycle_start ─┬─ plan done ─────────────────────────────── end
//                ├─ empty segment: tape step (not awaited) ──┐
//                └─ pack segment:                            │
//                     tape step (advance the OK run, shoot)  │
//                     segment already full in the tape?      │
//                       └─ settle: wait, advance, next cycle │
//                     feeder: refill when empty              │
//                     pick → inspect (side, bottom, rotate,  │
//                            rectified side)                 │
//                     top result → judge → place | toss      │
//                     NG in the tape → pick it out, bin it ──┘
//
// Checkpoint names and payloads are unchanged: the page's handler, the
// step mode and the harness depend on them.

import { cmd } from '../protocol';
import { FEEDER, GEOMETRY, INSPECTION, NOZZLE } from './params';
import { EVT, IO_PINS, camTrig, bit } from './io';
import type { Machine } from './machine';
import { nextCycleAction, leadingOkRun } from './plan';
import { tapeStep, type TapeStepResult, type TopView } from './tape';
import { refillFeeder, type FeederPart } from './feeder';
import { pickFromFeeder, pickFromTape, placePart, tossTo } from './nozzle';
import { judgePlacement, placePose, type Bin } from './judge';

export type Checkpoint = (name: string, data: unknown) => Promise<any>;

export type SideResult = { status: number; facing: number; measure?: { status: number; OK_vec?: number[] } };
export type BtmResult = {
  status: number;
  obj_pose: { x: number; y: number; ang: number; status: number };
  nozzle_pose?: { x: number; y: number; ang: number; status: number };
  mmpp?: number;
};

export type CycleContext = {
  m: Machine;
  checkpoint: Checkpoint;
  /** The live plan (the checkpoint handler updates it on every advance). */
  plan: () => number[];
  /** Feeder-camera part -> robot pose (the camera calibration). */
  predict: (part: FeederPart) => { X: number; Y: number; Z: number };
  /** Bottom-camera part position -> arm correction at `angleDeg`. */
  armOffset: (partPx: { X: number; Y: number }, angleDeg: number) => { X: number; Y: number };
  /** Bottom-camera calibration centre, used when its reply timed out. */
  btmCenter: { X: number; Y: number };
  /** Every tape step's result (plan tracking on the PLC). */
  onTapeStep?: (r: TapeStepResult, kind: 'pack' | 'empty', cells: number) => void;
};

export type CycleResult = { packed: number; ended: 'plan_done' | 'error_stop' };

type CycleState = {
  /** Cells the next packed tape step advances (the OK run seen last). */
  nextAdvance: number;
  /** Parts placed in the tape but not yet advanced past. */
  placedUncounted: number;
  /** Cells packed so far (the page's speed display). */
  packed: number;
  /** Top-camera view of the next cycle, when a tape step already made it. */
  pendingView?: Promise<TopView | undefined>;
  /** A feeder refill in flight. */
  pendingFeeder?: Promise<FeederPart[]>;
  /** Parts the feeder camera found that are still to be picked. */
  parts: FeederPart[];
  /** The arm is at travel height (after a toss or an NG pick). */
  armAtSafeZ: boolean;
  /** Failures in a row, each capped (params): refills that found no part,
   *  parts tossed instead of placed, NG pick-outs from the tape. */
  emptyRefills: number;
  tossesInARow: number;
  ngPicksInARow: number;
};

const BIN_LOCATION: Record<Bin, { X: number; Y: number; Z: number }> = {
  feeder: GEOMETRY.TOSS_FEEDER,
  tape_ng: GEOMETRY.TOSS_TAPE_NG,
  part_ng: GEOMETRY.TOSS_PART_NG,
};
/** NG_COUNT class per bin (the page's NG counters). */
const BIN_CLASS: Record<Bin, number> = { feeder: 0, tape_ng: 1, part_ng: 2 };

const SAFE_Z = GEOMETRY.SAFE_Z;
const INSP = { ...GEOMETRY.INSP_LOCATION, Z: GEOMETRY.INSP_LOCATION.Z + GEOMETRY.OBJECT_HEIGHT };

async function timed<T>(p: Promise<T>, name: string): Promise<T> {
  const t0 = Date.now();
  const v = await p;
  console.log('waitTime', name, Date.now() - t0);
  return v;
}

export async function runCycles(ctx: CycleContext): Promise<CycleResult> {
  const { m, checkpoint } = ctx;
  const s: CycleState = { nextAdvance: 0, placedUncounted: 0, packed: 0, parts: [], armAtSafeZ: false,
    emptyRefills: 0, tossesInARow: 0, ngPicksInARow: 0 };
  // Stop the run on something that would otherwise repeat forever.
  const stop = async (errorString: string): Promise<CycleResult> => {
    try { await checkpoint('ERROR', { errorString }); } catch { /* the page ends the run */ }
    return { packed: s.packed, ended: 'error_stop' };
  };

  const tape = async (cells: number, kind: 'pack' | 'empty',
      trigger?: { motion_id_offset: number; motion_progress: number }) => {
    const r = await tapeStep(m, { cells, kind, trigger });
    ctx.onTapeStep?.(r, kind, cells);
    return r.view;
  };

  for (let i = 0; ; i++) {
    const startData = await checkpoint('cycle_start', i);
    console.log('[DBG]cycle_start_data', JSON.stringify(startData), s.nextAdvance);
    const plan: number[] | undefined = startData?.production_plan;
    if (plan === undefined || plan.length === 0) return { packed: s.packed, ended: 'plan_done' };

    // ── Empty segment: one tape step for the whole segment (was 2 cells per
    // pass, ~0.5 s each). Not awaited: the arm picks and inspects the next
    // part while the tape moves and the top camera shoots; the next cycle
    // awaits the view where it needs it. The PLC runs one TAPE_CYCLE at a
    // time, so a pending one is finished first.
    if (plan[0] < 0) {
      s.nextAdvance = 0;
      const action = nextCycleAction(plan, s.placedUncounted);
      const cells = action.kind === 'skip_empty' ? action.cells : Math.min(2, -plan[0]);
      if (s.pendingView) await s.pendingView;
      const step = tape(cells, 'empty');
      step.catch(() => {});              // a rejection surfaces where it is awaited
      await checkpoint('[STEP][REEL ADV]', { adv_count: cells, type: 'empty' });
      s.pendingView = step;
      continue;
    }

    // ── Tape step for this cycle: advance the OK run seen last cycle and
    // shoot. It needs the arm off the tape, not the feeder, so it starts
    // before any feeder wait.
    let view = s.pendingView;
    await checkpoint('TOP_CAM check slot', i);
    if (view === undefined) {
      const cells = Math.min(s.nextAdvance, 2);
      s.packed += cells;
      s.placedUncounted = Math.max(0, s.placedUncounted - cells);
      view = tape(cells, 'pack', { motion_id_offset: 0, motion_progress: 0 });
      await checkpoint('[STEP][REEL ADV]', { adv_count: cells, type: 'pack', packCounter: s.packed });
      s.nextAdvance = 0;
    }
    await checkpoint('_PACK_INFO_', { packCounter: s.packed });
    s.pendingView = view;

    // Plan done by that step: let it finish, then stop (no extra pick).
    if (ctx.plan().length === 0) {
      await view;
      return { packed: s.packed, ended: 'plan_done' };
    }

    // ── The parts already in the tape may complete this segment: picking
    // another one would inspect it and toss it back. Wait for the top view
    // instead and advance the segment without a pick.
    if (nextCycleAction(ctx.plan(), s.placedUncounted).kind === 'settle_segment') {
      const settled = await settleSegment(ctx, s, view, tape);
      if (settled === 'plan_done') return { packed: s.packed, ended: 'plan_done' };
      if (settled === 'advanced') continue;
    }

    // ── Feeder: take the result of a refill in flight, or refill now.
    if (s.pendingFeeder) {
      await m.send(cmd.G1({ Z: SAFE_Z }));
      await m.send(cmd.G1({ X: GEOMETRY.FEEDER_STANDBY.X, Y: GEOMETRY.FEEDER_STANDBY.Y }));
      s.parts = await s.pendingFeeder;
      s.pendingFeeder = undefined;
    }
    if (s.parts.length === 0) {
      if (++s.emptyRefills > FEEDER.MAX_EMPTY_REFILLS) {
        return stop(`供料盤連續 ${FEEDER.MAX_EMPTY_REFILLS} 次找不到可取的料 / no pickable part after ${FEEDER.MAX_EMPTY_REFILLS} refills`);
      }
      s.parts = await refillFeeder(m);
      continue;
    }
    s.emptyRefills = 0;

    // ── Pick.
    const part = s.parts[0];
    await checkpoint('fetch one item on FF', i);
    s.parts.shift();
    if (part.surround_clear === 0) continue;
    if (!s.armAtSafeZ) await m.send(cmd.G1({ Z: SAFE_Z, A: 0 }));
    const at = ctx.predict(part);
    s.armAtSafeZ = false;
    await checkpoint('go to predicted location', i);
    await pickFromFeeder(m, { X: at.X, Y: at.Y, Z: at.Z, A: -part.angle_deg });
    // Last part off the plate: refill while this one is inspected and placed.
    if (s.parts.length === 0) s.pendingFeeder = refillFeeder(m);
    await checkpoint('[STEP] object picked', i);

    // ── Inspect, then decide with the top view.
    const insp = await inspectHeldPart(ctx, i);
    const top = await timed(view as Promise<TopView | undefined>, 'TOP CAM report');
    s.pendingView = undefined;
    const armOffset = ctx.armOffset({ X: insp.btm.obj_pose.x, Y: insp.btm.obj_pose.y }, insp.angleOffset);
    const planNow = (await checkpoint('GetProductionPlan', i)).production_plan as number[];
    const J = judgePlacement({
      reasons: insp.reasons,
      partBin: insp.partBin,
      sideStatus: insp.side.status,
      btmPoseStatus: insp.btm.obj_pose.status,
      armOffset,
      top,
      plan: planNow,
    });
    console.log('[JUDGE]', JSON.stringify({ place: J.place, reasons: J.reasons, bin: J.partBin, slot: J.placeSlot, ng: J.ngSlot, next: J.nextAdvance }));
    s.nextAdvance = J.nextAdvance;

    // Image bookkeeping for vision; nothing depends on its reply
    // (vision_contract.md: fire-and-forget).
    void (async () => m.sendVision({ type: 'TopInsp', cmd_type: 'save_target',
      t0: J.saveNames[0], t1: J.saveNames[1], t2: J.saveNames[2] }))()
      .catch((e) => console.warn('save_target failed', e?.message ?? e));

    if (J.holeTooFar) {
      try {
        await checkpoint('ERROR', { errorString: 'slotHoleOffset is too far', slotHoleOffset: J.holeOffset,
          distance: Math.hypot(J.holeOffset.X, J.holeOffset.Y) });
      } catch {
        return { packed: s.packed, ended: 'error_stop' };
      }
    }

    // ── Place or toss the held part.
    const angle = INSPECTION.BASE_ANGLE_DEG + insp.angleOffset;
    if (J.place) {
      s.tossesInARow = 0;
      m.mark(EVT.PLACE);
      s.placedUncounted++;
      await checkpoint('place object', i);
      await placePart(m, placePose(J.placeSlot, armOffset, J.holeOffset, angle),
        () => checkpoint('[STEP] place object', i));
    } else {
      m.mark(EVT.TOSS);
      await checkpoint('[TOSS] object', { tossReasons: J.reasons });
      console.log('toss object', J.reasons);
      await tossTo(m, BIN_LOCATION[J.partBin]);
      await checkpoint('NG_COUNT', { class: BIN_CLASS[J.partBin], count: 1 });
      s.armAtSafeZ = true;
      if (++s.tossesInARow > INSPECTION.MAX_TOSSES_IN_A_ROW) {
        return stop(`連續 ${INSPECTION.MAX_TOSSES_IN_A_ROW} 顆沒放進料帶（最後原因：${J.reasons.join(', ')}）/ ${INSPECTION.MAX_TOSSES_IN_A_ROW} parts in a row tossed`);
      }
    }

    // ── An NG part in the tape: pick it out (only when the corrections
    // can be trusted) and bin it.
    const ngOffset = J.ngSlot * GEOMETRY.SLOT_PITCH_MM;
    if (!Number.isNaN(ngOffset) && !J.compensationNg) {
      await checkpoint('[NG PICK] object', { ng_x_pick_offset: ngOffset, compensationIsNG: J.compensationNg });
      await pickFromTape(m, GEOMETRY.SLOT_LOCATION.X + ngOffset + J.holeOffset.X, GEOMETRY.SLOT_LOCATION.Y + J.holeOffset.Y);
      await tossTo(m, BIN_LOCATION[J.tapeNgBin]);
      await checkpoint('NG_COUNT', { class: BIN_CLASS[J.tapeNgBin], count: 1 });
      if (++s.ngPicksInARow > NOZZLE.MAX_NG_PICKS_IN_A_ROW) {
        return stop(`料帶 NG 連續夾了 ${NOZZLE.MAX_NG_PICKS_IN_A_ROW} 次仍在 / NG part in the tape not removed after ${NOZZLE.MAX_NG_PICKS_IN_A_ROW} picks`);
      }
    } else {
      s.ngPicksInARow = 0;
    }
    s.armAtSafeZ = true;
  }
}

/**
 * The parts in the tape may complete the head segment. Clear the camera's
 * view (it only shoots once the arm has left, and nothing else is queued
 * while we wait), look, and advance the segment if the OK run fills it.
 */
async function settleSegment(ctx: CycleContext, s: CycleState, view: Promise<TopView | undefined>,
    tape: (cells: number, kind: 'pack' | 'empty', trigger?: { motion_id_offset: number; motion_progress: number }) => Promise<TopView | undefined>,
): Promise<'advanced' | 'plan_done' | 'not_yet'> {
  const { m, checkpoint } = ctx;
  const head = ctx.plan()[0];
  await m.send(cmd.G1({ Z: SAFE_Z }));
  await m.send(cmd.G1({ X: GEOMETRY.FEEDER_STANDBY.X, Y: GEOMETRY.FEEDER_STANDBY.Y }));
  const st = await view;
  const okRun = st ? leadingOkRun({ isOk: st.is_OK, isClear: st.is_clear }) : 0;
  if (okRun < head) return 'not_yet';            // an NG among them: pick a replacement
  s.packed += head;
  await checkpoint('[STEP][REEL ADV]', { adv_count: head, type: 'pack', packCounter: s.packed });
  await checkpoint('_PACK_INFO_', { packCounter: s.packed });
  s.placedUncounted = Math.max(0, s.placedUncounted - head);
  const after = await tape(head, 'pack', { motion_id_offset: 0, motion_progress: 0 });
  if (ctx.plan().length === 0) { s.pendingView = undefined; return 'plan_done'; }
  // More plan (an empty segment next): this view is the next cycle's.
  s.pendingView = after ? Promise.resolve(after) : undefined;
  s.armAtSafeZ = true;
  return 'advanced';
}

type Inspection = {
  side: SideResult;
  btm: BtmResult;
  /** A-axis correction (deg) on top of INSPECTION.BASE_ANGLE_DEG. */
  angleOffset: number;
  reasons: string[];
  partBin: Bin;
};

/**
 * Show the held part to the side and bottom cameras, turn it to the
 * corrected angle, and take the rectified side shot on the way toward
 * the tape (parked PRE_PLACE_FRACTION of the way: right above the tape
 * made it shake on the machine).
 */
async function inspectHeldPart(ctx: CycleContext, i: number): Promise<Inspection> {
  const { m, checkpoint } = ctx;
  const base = INSPECTION.BASE_ANGLE_DEG;

  await m.send(cmd.G1({ X: INSP.X, Y: INSP.Y, Z: INSP.Z + 1, A: base }));
  await checkpoint('SideCam check', i);
  const sidePending = m.waitVision('side');
  await m.send(cmd.G1({ Z: INSP.Z }));
  m.sendNoWait(camTrig(IO_PINS.O.CAM_Side, IO_PINS.O.CAM_Side_Light0,
    { reset_ms: INSPECTION.SIDE_STROBE_MS, motion_id_offset: 0, motion_progress: 1 }));

  await checkpoint('[STEP]go to BTM insp', i);
  m.sendNoWait(cmd.G4(0.01));
  const btmPending = m.waitVision('btm');
  await m.send(camTrig(IO_PINS.O.CAM_Btm, IO_PINS.O.CAM_Btm_Light0, { reset_ms: INSPECTION.BTM_STROBE_MS }));
  m.sendNoWait(cmd.G4(0.001));
  await m.send(cmd.G1({ A: base }));
  await m.send(cmd.G1({ Z: INSP.Z + 1 }));

  const reasons: string[] = [];
  let partBin: Bin = 'part_ng';
  let side = await timed(sidePending, 'SideCam report') as SideResult | undefined;
  if (side === undefined) {
    reasons.push('vision timeout: side');
    side = { status: 0, facing: 0, measure: { status: 0, OK_vec: [0, 0, 0] } };
  }
  let btm = await timed(btmPending, 'BTM report') as BtmResult | undefined;
  if (btm === undefined) {
    reasons.push('vision timeout: bottom');
    btm = { status: 0, obj_pose: { x: ctx.btmCenter.X, y: ctx.btmCenter.Y, ang: 0, status: 0 } };
  }
  if (btm.status !== 1 || side.status !== 1) {
    await m.send(cmd.G1({ Z: SAFE_Z }));
    reasons.push('BTM or SideCam check failed');
    partBin = 'feeder';
  }

  // Face the part the right way round, then correct by the bottom
  // camera's angle and the fixed trim.
  const angleOffset = (side.facing !== 0 ? 180 : 0) + btm.obj_pose.ang + INSPECTION.ANGLE_TRIM_DEG;
  await m.send(cmd.G1({ A: base + angleOffset }));
  await checkpoint('[STEP]', i);

  if (reasons.length === 0) {
    const rectifiedPending = m.waitVision('side');
    const trig = bit(IO_PINS.O.CAM_Side) | bit(IO_PINS.O.CAM_Side_Light0);
    await m.send(cmd.M4({ pin: trig, state: trig, reset_ms: INSPECTION.SIDE_STROBE_MS, motion_progress: 1 }));
    const toward = { X: GEOMETRY.SLOT_LOCATION.X + GEOMETRY.SLOT_PITCH_MM, Y: GEOMETRY.SLOT_LOCATION.Y };
    const f = INSPECTION.PRE_PLACE_FRACTION;
    await m.send(cmd.G1({ X: INSP.X + (toward.X - INSP.X) * f, Y: INSP.Y + (toward.Y - INSP.Y) * f, Z: SAFE_Z }));
    const rectified = await timed(rectifiedPending, 'SideCam rectified report') as SideResult | undefined;
    if (rectified?.measure?.status !== 1) {
      reasons.push('SideCam measure failed' + rectified?.measure?.OK_vec);
    }
  }
  await checkpoint('[STEP]', i);
  return { side, btm, angleOffset, reasons, partBin };
}
