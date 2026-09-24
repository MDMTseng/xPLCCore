// Place or toss: the decision after the top camera has reported.
//
// Pure function over the inspection results, the top-camera view and the
// plan; the cycle does the motion. Extracted unchanged from CalibPage's
// runAllObjects (2026-09-25): the checks run in the same order and later
// ones override the bin exactly as before (tests pin that down).

import { INSPECTION, GEOMETRY } from './params';
import { leadingOkRun, firstClearSlot, firstNgSlot } from './plan';
import type { TopView } from './tape';

/** Where a part goes when it is not placed. */
export type Bin =
  /** Back to the feeder: the part is fine, it just cannot go in now. */
  | 'feeder'
  /** The NG bin for parts picked back out of the tape. */
  | 'tape_ng'
  /** The NG bin for parts that failed inspection. */
  | 'part_ng';

export type JudgeInput = {
  /** Reasons already collected (vision timeouts, first-shot failures,
   *  the rectified side measure). */
  reasons: string[];
  /** Bin chosen so far for the held part. */
  partBin: Bin;
  /** First side shot: status 1 = OK. */
  sideStatus: number;
  /** Bottom shot: part pose status 1 = OK. */
  btmPoseStatus: number;
  /** Arm correction from the bottom shot (mm). */
  armOffset: { X: number; Y: number };
  /** Bottom camera scale (mm/px). */
  btmMmpp: number;
  /** Top camera view after this cycle's tape step; undefined = timed out. */
  top: TopView | undefined;
  /** Remaining plan. */
  plan: number[];
};

export type Judgement = {
  place: boolean;
  reasons: string[];
  /** Bin for the held part when it is not placed. */
  partBin: Bin;
  /** Bin for an NG part picked out of the tape. */
  tapeNgBin: Bin;
  /** Slot to place into (NaN: none). */
  placeSlot: number;
  /** Slot holding an NG part to pick out (NaN: none). */
  ngSlot: number;
  /** Cells the next tape step may advance (the OK run, or the plan
   *  segment's remainder when that is smaller). */
  nextAdvance: number;
  /** Placement correction from the tape hole (mm); NaN when not found. */
  holeOffset: { X: number; Y: number };
  /** The hole is further off than INSPECTION.MAX_SLOT_HOLE_OFFSET_MM: the
   *  cycle raises ERROR (the tape or the camera needs a look). */
  holeTooFar: boolean;
  /** The correction cannot be trusted: do not place, do not pick from
   *  the tape. */
  compensationNg: boolean;
  /** Image names for vision's save_target (slot 0..2). */
  saveNames: (string | undefined)[];
};

export function judgePlacement(i: JudgeInput, now: number = Date.now()): Judgement {
  const reasons = [...i.reasons];
  let partBin = i.partBin;
  let tapeNgBin: Bin = 'feeder';
  let compensationNg = false;

  const topTimedOut = i.top === undefined;
  const view = i.top ?? { is_clear: [0, 0, 0], is_OK: [0, 0, 0], post_check_advCount: 0, locHole: { status: 0, x: 0, y: 0, mmpp: 0 } };
  if (topTimedOut) {
    // The slots are unknown: do not place, do not pick an "NG" out of the
    // tape, do not advance; the part goes back to the feeder and the next
    // cycle checks the tape again.
    reasons.push('vision timeout: top');
    partBin = 'feeder';
  }

  const slots = { isOk: view.is_OK.slice(view.post_check_advCount), isClear: view.is_clear.slice(view.post_check_advCount) };
  let nextAdvance = leadingOkRun(slots);
  const saveNames: (string | undefined)[] = [undefined, undefined, undefined];
  for (let k = 0; k < nextAdvance; k++) saveNames[k] = 'OK_' + now;

  let placeSlot = firstClearSlot(slots);
  let ngSlot = firstNgSlot(slots);
  placeSlot = placeSlot < 0 ? NaN : placeSlot;
  ngSlot = ngSlot < 0 ? NaN : ngSlot;
  if (topTimedOut) { placeSlot = NaN; ngSlot = NaN; nextAdvance = 0; }
  if (!Number.isNaN(ngSlot)) {
    tapeNgBin = 'tape_ng';
    saveNames[ngSlot] = 'NG_pick_' + now;
  }

  if (i.sideStatus !== 1) {
    reasons.push('SideCheck failed');
    partBin = 'part_ng';
  }
  if (i.btmPoseStatus !== 1) {
    compensationNg = true;
    reasons.push('btm check failed');
    partBin = 'part_ng';
  }
  if (Math.hypot(i.armOffset.X, i.armOffset.Y) > INSPECTION.MAX_ARM_OFFSET_MM) {
    reasons.push('armOffset is too far');
    partBin = 'feeder';
    compensationNg = true;
  }

  // NOTE: X uses the top camera's scale, Y the bottom camera's. That is
  // how it has always been; flagged in the 2026-09-24 review as a likely
  // bug (both should be the top camera's). Kept until checked on the
  // machine.
  const holeOffset = { X: NaN, Y: NaN };
  if (view.locHole.status === 1) {
    holeOffset.X = view.locHole.x * view.locHole.mmpp;
    holeOffset.Y = -view.locHole.y * i.btmMmpp;
  }
  if (Number.isNaN(holeOffset.X)) {
    reasons.push('slotHoleOffset is NaN');
    partBin = 'feeder';
    compensationNg = true;
  }
  let holeTooFar = false;
  if (Math.hypot(holeOffset.X, holeOffset.Y) > INSPECTION.MAX_SLOT_HOLE_OFFSET_MM) {
    reasons.push('slotHoleOffset is too far');
    partBin = 'feeder';
    holeTooFar = true;
    compensationNg = true;
  }

  if (Number.isNaN(placeSlot)) {
    reasons.push('no slot to place');
    partBin = 'feeder';
  }

  const plan = i.plan;
  if (plan.length > 0 && plan[0] <= nextAdvance) {
    // The OK run already in the tape completes the segment.
    reasons.push('production plan place count hit' + plan[0] + ' ' + nextAdvance);
    partBin = 'feeder';
    if (plan[0] > 0) nextAdvance = plan[0];
  }
  // The reel only advances past a run of OK cells from slot 0, so every
  // cell up to the target slot ends up packed: a slot past the segment's
  // remaining count would pack one too many (chaos seed 38).
  if (plan.length > 0 && plan[0] > 0 && reasons.length === 0
      && !Number.isNaN(placeSlot) && placeSlot + 1 > plan[0]) {
    reasons.push('production plan: slot ' + placeSlot + ' past count ' + plan[0]);
    partBin = 'feeder';
  }
  if (plan.length === 0) {
    reasons.push('production plan is empty');
    partBin = 'feeder';
  }

  const place = i.sideStatus === 1 && !Number.isNaN(placeSlot) && !compensationNg && reasons.length === 0;
  return { place, reasons, partBin, tapeNgBin, placeSlot, ngSlot, nextAdvance, holeOffset, holeTooFar, compensationNg, saveNames };
}

/** Where to put a part into `slot` given the corrections. */
export function placePose(slot: number, armOffset: { X: number; Y: number }, holeOffset: { X: number; Y: number }, angleDeg: number) {
  return {
    X: GEOMETRY.SLOT_LOCATION.X + slot * GEOMETRY.SLOT_PITCH_MM - armOffset.X + holeOffset.X,
    Y: GEOMETRY.SLOT_LOCATION.Y - armOffset.Y + holeOffset.Y,
    Z: GEOMETRY.SLOT_LOCATION.Z,
    A: angleDeg,
  };
}
