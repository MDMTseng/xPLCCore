// Renderer side of decisions_2026-06-22.md §4 (2) -- crash-and-resume.
//
// Three pieces (all pure, no React):
//   1. plan persistence (localStorage v1)
//   2. write-ahead scratchpad helper (wraps cmd.ScratchpadWrite)
//   3. resume reconciliation (decides what to do on startup, given the
//      MachineState snapshot the renderer just pulled on reconnect)
//
// Wires into existing renderer plumbing via the same SendFn shape
// orchestrator/conveyorPick.ts uses. Caller owns calling these at the
// right lifecycle points.

import { cmd, Envelope, MachineState, Scratchpad, ScratchpadWriteArgs } from '../lib/protocol';

export type SendFn = (
  data: any,
  waitForTracking?: boolean,
  timeout_ms?: number,
) => Promise<any> | boolean;

// ─── 1. Plan persistence ─────────────────────────────────────────────

export interface PersistedPlan {
  // Bumped every time the plan content is mutated (operator edits, etc).
  // Renderer correlates this with the PLC scratchpad.plan_id on resume.
  plan_id: number;
  // The plan itself. Production code path uses a number[] today
  // (positive = take from feeder, negative = skip cells, etc — see
  // CalibPage). We store as JSON so future schema bumps need only a
  // version field, not a localStorage migration.
  plan: number[];
  // ISO timestamp captured at save time. For audit only; renderer does
  // not act on it.
  saved_at: string;
}

const PLAN_KEY = 'plan_v1';

export function loadPlan(): PersistedPlan | null {
  try {
    const raw = localStorage.getItem(PLAN_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== 'object') return null;
    if (typeof obj.plan_id !== 'number') return null;
    if (!Array.isArray(obj.plan)) return null;
    return obj as PersistedPlan;
  } catch {
    return null;
  }
}

export function savePlan(plan: number[], previous?: PersistedPlan | null): PersistedPlan {
  // Bump plan_id on every save so the PLC scratchpad correlation
  // detects "operator edited the plan since the cursor was written".
  // Wraps at 2^31-1 to stay inside DINT on the wire.
  const next_id = (((previous?.plan_id ?? 0) + 1) % 0x7fffffff) || 1;
  const persisted: PersistedPlan = {
    plan_id: next_id,
    plan: plan.slice(),
    saved_at: new Date().toISOString(),
  };
  localStorage.setItem(PLAN_KEY, JSON.stringify(persisted));
  return persisted;
}

export function clearPlan(): void {
  localStorage.removeItem(PLAN_KEY);
}

// ─── 2. Scratchpad write-ahead helper ────────────────────────────────

export const IntentKind = {
  Idle: 0,
  AdvanceReel: 1,
  Pick: 2,
  BowlBack: 3,
} as const;

export type IntentKindValue = (typeof IntentKind)[keyof typeof IntentKind];

export async function writeScratchpad(
  send: SendFn,
  args: ScratchpadWriteArgs,
  timeout_ms = 2000,
): Promise<boolean> {
  const env = cmd.ScratchpadWrite(args);
  const r = send(env, true, timeout_ms);
  if (typeof r === 'boolean') {
    throw new Error('writeScratchpad: send returned boolean (fire-and-forget); awaitable required');
  }
  const reply = (await r) as { ack?: boolean };
  return reply?.ack === true;
}

// Convenience: stamp the "I'm about to advance reel" intent with the
// PLC's currently-accepted movement id so resume can compare against
// LastCompletedMovementId after the fact.
export async function writeIntent(
  send: SendFn,
  opts: {
    plan_id: number;
    plan_index: number;
    intent_kind: IntentKindValue;
    intent_movement_id: number;
    last_vision_pulse?: number;
  },
): Promise<boolean> {
  return writeScratchpad(send, {
    plan_id: opts.plan_id,
    plan_index: opts.plan_index,
    intent_kind: opts.intent_kind,
    intent_movement_id: opts.intent_movement_id,
    last_vision_pulse: opts.last_vision_pulse ?? 0,
  });
}

// ─── 3. Resume reconciliation ────────────────────────────────────────

export type ResumeDecision =
  // Fresh PLC + fresh renderer. Nothing carried over. Start a new plan.
  | { kind: 'cold_start'; reason: string }
  // Cursor is valid. Caller should continue from intent_kind /
  // plan_index. May still need a state-4 check (intent completed?).
  | {
      kind: 'resume';
      plan: PersistedPlan;
      scratchpad: Scratchpad;
      // True if the PLC's last-completed movement id has reached the
      // intent's movement id. Caller decides per intent_kind what to
      // do (state 3 vs state 4 -- see decisions §B).
      intent_completed: boolean;
      // True if scratchpad.intent_kind is non-idle AND completed but
      // host has no vision result for it -- state 4, "bowl_back" the
      // unit per decisions §B B.6.
      vision_result_unknown: boolean;
    };

export interface ResumeContext {
  // Host's record of whether a vision result was received for the
  // intent currently in scratchpad. If the renderer didn't checkpoint
  // this in localStorage either, treat as unknown -- the safer default.
  vision_result_seen?: boolean;
}

export function reconcileOnResume(
  machineState: MachineState,
  ctx: ResumeContext = {},
): ResumeDecision {
  const sp = machineState.scratchpad;
  if (!sp || sp.schema_version === 0) {
    return { kind: 'cold_start', reason: 'scratchpad uninitialised (cold reset wiped or PLC never had one)' };
  }
  if (sp.boot_epoch !== machineState.boot_epoch_now) {
    return {
      kind: 'cold_start',
      reason: `boot_epoch mismatch (cursor saved at ${sp.boot_epoch}, PLC now at ${machineState.boot_epoch_now}) -- PLC restarted since last write`,
    };
  }
  const plan = loadPlan();
  if (!plan) {
    return { kind: 'cold_start', reason: 'no plan on disk (renderer profile reset or first launch)' };
  }
  if (plan.plan_id !== sp.plan_id) {
    return {
      kind: 'cold_start',
      reason: `plan_id mismatch (disk has ${plan.plan_id}, PLC cursor refers to ${sp.plan_id}) -- plan edited since cursor was written`,
    };
  }

  const intent_completed =
    sp.intent_kind !== IntentKind.Idle &&
    sp.intent_movement_id !== 0 &&
    machineState.movement_id >= sp.intent_movement_id;

  const vision_result_unknown =
    intent_completed && ctx.vision_result_seen === false;

  return {
    kind: 'resume',
    plan,
    scratchpad: sp,
    intent_completed,
    vision_result_unknown,
  };
}
