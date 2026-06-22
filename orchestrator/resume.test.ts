// Unit tests for the resume reconciliation pure functions.
// Per implementation_review_2026-06-22.md §4: these pieces are
// load-bearing (state-2 blow-off vs continue-plan vs bowl-back) and
// were previously untested. The §2 movement_id-comparison bug would
// have surfaced immediately as a state-3 test failing.
//
// Run with: npm test

import { describe, it, expect, beforeEach } from 'vitest';
import {
  reconcileOnResume,
  planResumeAction,
  loadPlan,
  savePlan,
  clearPlan,
  IntentKind,
} from './resume';
import type { MachineState, Scratchpad } from '../lib/protocol';

// localStorage shim for the node test environment. Tests touch it
// through save/load/clearPlan.
class LSMock {
  store = new Map<string, string>();
  getItem(k: string) { return this.store.get(k) ?? null; }
  setItem(k: string, v: string) { this.store.set(k, v); }
  removeItem(k: string) { this.store.delete(k); }
  clear() { this.store.clear(); }
}
(globalThis as any).localStorage = new LSMock();

// Test fixtures. Helper builds a MachineState with whatever overrides
// the test needs; the rest get sane defaults that don't matter for
// reconcile logic.
function makeMS(overrides: Partial<MachineState> = {}): MachineState {
  return {
    st: 70, st_str: 'Ready', err_src: '', err_id: 0,
    motion_buffer_size: 0, movement_id: 0, runtime_ms: 1000,
    coord_set: true,
    axes_err_mask: 0, axes_state: 0, axes_err_id: [0, 0, 0, 0],
    axes_labels: ['a', 'b', 'c', 'd'],
    reel_pos: 0,
    boot_epoch_now: 1,
    last_completed_movement_id: 0,
    scratchpad: {
      schema_version: 1, plan_id: 0, plan_index: 0,
      intent_kind: IntentKind.Idle, intent_movement_id: 0,
      last_vision_pulse: 0, boot_epoch: 1,
    },
    ...overrides,
  };
}

function withScratchpad(ms: MachineState, sp: Partial<Scratchpad>): MachineState {
  return { ...ms, scratchpad: { ...ms.scratchpad, ...sp } };
}

beforeEach(() => {
  (globalThis as any).localStorage.clear();
});

// ─── reconcileOnResume ───────────────────────────────────────────────

describe('reconcileOnResume', () => {
  it('cold_start when scratchpad uninitialised (schema_version=0)', () => {
    const ms = withScratchpad(makeMS(), { schema_version: 0 });
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('cold_start');
    if (d.kind === 'cold_start') expect(d.reason).toMatch(/uninitialised/);
  });

  it('cold_start when boot_epoch mismatch (PLC restarted since write)', () => {
    savePlan([1, 2, 3]);
    const persisted = loadPlan()!;
    const ms = makeMS({
      boot_epoch_now: 5,
      scratchpad: {
        schema_version: 1, plan_id: persisted.plan_id, plan_index: 0,
        intent_kind: IntentKind.Idle, intent_movement_id: 0,
        last_vision_pulse: 0, boot_epoch: 4,
      },
    });
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('cold_start');
    if (d.kind === 'cold_start') expect(d.reason).toMatch(/boot_epoch/);
  });

  it('cold_start when no plan on disk', () => {
    const ms = makeMS();
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('cold_start');
    if (d.kind === 'cold_start') expect(d.reason).toMatch(/no plan/);
  });

  it('cold_start when plan_id mismatch (plan edited since cursor was written)', () => {
    const p1 = savePlan([1, 2, 3]);       // plan_id = 1
    const p2 = savePlan([4, 5], p1);      // plan_id = 2
    const ms = withScratchpad(makeMS(), { plan_id: p1.plan_id, intent_kind: IntentKind.Idle });
    expect(p2.plan_id).not.toBe(p1.plan_id);
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('cold_start');
    if (d.kind === 'cold_start') expect(d.reason).toMatch(/plan_id mismatch/);
  });

  it('resume when scratchpad valid, plan matches, no intent in flight', () => {
    const p = savePlan([1, 2, 3]);
    const ms = withScratchpad(makeMS(), {
      plan_id: p.plan_id, plan_index: 1, intent_kind: IntentKind.Idle,
    });
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('resume');
    if (d.kind === 'resume') {
      expect(d.intent_completed).toBe(false);   // idle -> not "completed"
      expect(d.vision_result_unknown).toBe(false);
    }
  });

  // ⚠️ Critical regression test for implementation_review §2: the
  // comparison MUST use last_completed_movement_id, not movement_id.
  // If someone "simplifies" by switching back to movement_id, this
  // test fires.
  it('intent_completed uses last_completed_movement_id, NOT movement_id', () => {
    const p = savePlan([1, 2, 3]);
    // Move that *started* but hasn't completed yet. movement_id=42 means
    // "arm position currently corresponds to move 42" -- but PLC only
    // emits MOVE_DONE when the buffer drains, so completed = 41.
    const ms = makeMS({
      movement_id: 42,
      last_completed_movement_id: 41,
      scratchpad: {
        schema_version: 1, plan_id: p.plan_id, plan_index: 0,
        intent_kind: IntentKind.AdvanceReel, intent_movement_id: 42,
        last_vision_pulse: 0, boot_epoch: 1,
      },
    });
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('resume');
    if (d.kind === 'resume') {
      // If the comparison accidentally used `movement_id` (42 >= 42 -> true)
      // this would be true and state-2 blow-off would never fire.
      expect(d.intent_completed).toBe(false);
    }
  });

  it('intent_completed true once last_completed catches up', () => {
    const p = savePlan([1, 2, 3]);
    const ms = makeMS({
      movement_id: 42,
      last_completed_movement_id: 42,
      scratchpad: {
        schema_version: 1, plan_id: p.plan_id, plan_index: 0,
        intent_kind: IntentKind.AdvanceReel, intent_movement_id: 42,
        last_vision_pulse: 0, boot_epoch: 1,
      },
    });
    const d = reconcileOnResume(ms);
    expect(d.kind).toBe('resume');
    if (d.kind === 'resume') expect(d.intent_completed).toBe(true);
  });

  it('vision_result_unknown only when intent completed AND host says no result seen', () => {
    const p = savePlan([1, 2, 3]);
    const ms = makeMS({
      movement_id: 42,
      last_completed_movement_id: 42,
      scratchpad: {
        schema_version: 1, plan_id: p.plan_id, plan_index: 0,
        intent_kind: IntentKind.Pick, intent_movement_id: 42,
        last_vision_pulse: 0, boot_epoch: 1,
      },
    });
    const seen = reconcileOnResume(ms, { vision_result_seen: true });
    const unseen = reconcileOnResume(ms, { vision_result_seen: false });
    if (seen.kind === 'resume') expect(seen.vision_result_unknown).toBe(false);
    if (unseen.kind === 'resume') expect(unseen.vision_result_unknown).toBe(true);
  });
});

// ─── planResumeAction ────────────────────────────────────────────────

describe('planResumeAction', () => {
  function setupResume(opts: {
    intent_kind: number;
    intent_movement_id: number;
    last_completed_movement_id: number;
    vision_result_seen?: boolean;
  }) {
    const p = savePlan([1, 2, 3]);
    const ms = makeMS({
      movement_id: opts.intent_movement_id,
      last_completed_movement_id: opts.last_completed_movement_id,
      scratchpad: {
        schema_version: 1, plan_id: p.plan_id, plan_index: 0,
        intent_kind: opts.intent_kind,
        intent_movement_id: opts.intent_movement_id,
        last_vision_pulse: 0, boot_epoch: 1,
      },
    });
    return reconcileOnResume(ms, { vision_result_seen: opts.vision_result_seen });
  }

  it('state 1 (idle) -> continue_plan', () => {
    const decision = setupResume({
      intent_kind: IntentKind.Idle, intent_movement_id: 0,
      last_completed_movement_id: 0,
    });
    const action = planResumeAction(decision);
    expect(action.kind).toBe('continue_plan');
  });

  // State 2 is the most important test -- if this fires bowl_back or
  // continue_plan instead of blow_off, scrap.
  it('state 2 (intent unfinished) -> blow_off', () => {
    const decision = setupResume({
      intent_kind: IntentKind.AdvanceReel,
      intent_movement_id: 42,
      last_completed_movement_id: 41,   // didn't catch up
    });
    const action = planResumeAction(decision);
    expect(action.kind).toBe('blow_off');
    if (action.kind === 'blow_off') {
      expect(action.reason).toMatch(/not yet completed/);
    }
  });

  it('state 3 (intent done, vision OK) -> continue_plan', () => {
    const decision = setupResume({
      intent_kind: IntentKind.Pick,
      intent_movement_id: 42,
      last_completed_movement_id: 42,
      vision_result_seen: true,
    });
    const action = planResumeAction(decision);
    expect(action.kind).toBe('continue_plan');
  });

  it('state 4 (intent done, vision unknown) -> bowl_back', () => {
    const decision = setupResume({
      intent_kind: IntentKind.Pick,
      intent_movement_id: 42,
      last_completed_movement_id: 42,
      vision_result_seen: false,
    });
    const action = planResumeAction(decision);
    expect(action.kind).toBe('bowl_back');
    if (action.kind === 'bowl_back') {
      expect(action.reason).toMatch(/possible misjudgment|vision result missing/);
    }
  });

  it('cold_start decision -> cold_start action', () => {
    clearPlan();
    const decision = setupResume({
      intent_kind: IntentKind.Idle, intent_movement_id: 0,
      last_completed_movement_id: 0,
    });
    clearPlan();   // drop the plan so reconcile returns cold_start
    const decision2 = reconcileOnResume(makeMS({ scratchpad: { schema_version: 1, plan_id: 1, plan_index: 0, intent_kind: 0, intent_movement_id: 0, last_vision_pulse: 0, boot_epoch: 1 }, boot_epoch_now: 1 }));
    const action = planResumeAction(decision2);
    expect(action.kind).toBe('cold_start');
  });
});

// ─── plan persistence ────────────────────────────────────────────────

describe('plan persistence', () => {
  it('roundtrip preserves contents', () => {
    const p = savePlan([1, -30, 445, -20, 1]);
    const loaded = loadPlan();
    expect(loaded).not.toBeNull();
    expect(loaded!.plan).toEqual([1, -30, 445, -20, 1]);
    expect(loaded!.plan_id).toBe(p.plan_id);
  });

  it('savePlan bumps plan_id every write', () => {
    const a = savePlan([1, 2]);
    const b = savePlan([3, 4], a);
    expect(b.plan_id).toBeGreaterThan(a.plan_id);
  });

  it('clearPlan removes everything', () => {
    savePlan([1, 2]);
    clearPlan();
    expect(loadPlan()).toBeNull();
  });

  it('loadPlan returns null on missing / corrupted entry', () => {
    expect(loadPlan()).toBeNull();
    (globalThis as any).localStorage.setItem('plan_v1', 'not json');
    expect(loadPlan()).toBeNull();
  });
});
