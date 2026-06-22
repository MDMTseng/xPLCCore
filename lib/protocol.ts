// Typed builders for UI ↔ PLC msgpack packets.
// Authoritative spec: doc/2-contracts/protocol.md.
//
// Builders only shape the packet — they do not send. Caller still owns
// sendTcpMsgPack(...) including await-tracking and timeouts.
//
// `id` and `protocol_version` are stamped by sendTcpMsgPack; do not set
// them here.

export type PacketType = 'M' | 'SYS';

// Phantom reply-type carrier. The `R` parameter is never set at runtime;
// it exists only so `send<R>` (below) can recover the reply shape for a
// given builder. Builders return `Envelope<TheirReply>`; the legacy plain
// `Envelope` (no parameter) defaults R to `unknown` and behaves exactly
// like before.
export interface Envelope<R = unknown> {
  type: PacketType;
  cmd: string;
  [k: string]: unknown;
  /** @internal phantom — never present at runtime */
  readonly __reply?: R;
}

// ─── Reply shapes ──────────────────────────────────────────────────
// These describe what the PLC sends back for a given command. Used by
// the typed `send<R>` helper to surface reply types at call sites.
// Anything not enumerated here defaults to `unknown` (caller must
// narrow), matching pre-typed behavior.

export interface AckReply {
  ack: boolean;
  err?: string;
  [k: string]: unknown;
}

export interface PingReply {
  pong: true;
  runtime_ms: number;
  [k: string]: unknown;
}

export interface LocationReply {
  X: number; Y: number; Z: number;
  A?: number; B?: number; C?: number;
  [k: string]: unknown;
}

export interface DigitalInputReply {
  raw: number;
  group?: number;
  [k: string]: unknown;
}

export interface DigitalInputFlipCountReply {
  raw: number;
  fc: number[];
  [k: string]: unknown;
}

// G1 ack carries the PLC-assigned movement id (used to anchor fly events
// scheduled with `motion_id_offset`). PLC source: AxisGroupSM.st:1646.
export interface G1Reply extends AckReply {
  movement_id?: number;
}

// M4 ack echoes the caller-supplied `event_id` so multi-pulse schedulers
// can correlate. PLC source: AxisGroupSM.st:1058.
export interface M4Reply extends AckReply {
  event_id?: number;
}

// SYS/GA_EV reply doubles as a state probe: it carries the post-transition
// FSM state and the latched error (cleared on EV_RESET → UnInited).
// PLC source: AxisGroupSM.st:573-589.
export interface GaEvReply extends AckReply {
  st: number;
  st_str: string;
  err_src: string;
  err_id: number;
}

// ─── Motion (type:"M") ───────────────────────────────────────────────

export interface G1Args {
  X?: number; Y?: number; Z?: number;
  A?: number; B?: number; C?: number;
  F?: number;
  Cor?: number;
  ACC?: number; DEA?: number; JERK?: number;
  abort?: boolean;
  // Phase 4 step 2 — coordinate frame tag. 0 = WCS (default), 1 = PCS_1
  // (conveyor-tracking, requires an active Coord1 bind). PLC NAKs other
  // values with err='bad_frame' or, if frame=1 without a bind, NAKs
  // upstream of MoveLinear.
  frame?: 0 | 1;
}

export interface M4Args {
  // pin/state are required for the simple-pulse form, but the multi-pulse
  // `pin_op_seq` form supplies its own per-step pin/state and omits these.
  pin?: number;
  state?: number;
  reset_ms?: number;
  motion_id_offset?: number;
  motion_progress?: number;
  group?: number;
  event_id?: number;
  ttl_ms?: number;
  pin_op_seq?: unknown;
  // Phase 4 step 3 — FlyEvent COORD1_BIND variant. trig=130 (PulseTrigger)
  // + action='coord1_bind' + pulse_target schedules a conveyor bind to fire
  // when ConveyorPulseRaw crosses pulse_target. exit_pulse_offset (>0)
  // defines the pick window — when belt overshoots ref_pulse+offset, PLC
  // emits COORD1_ERROR + transitions to Error + MC_GroupStop.
  trig?: number;
  action?: 'coord1_bind' | 'pin_op';
  pulse_target?: number;
  ref_xyz?: [number, number, number];
  scale?: [number, number, number];
  exit_pulse_offset?: number;
}

export interface M4BindArgs {
  pulse_target: number;
  ref_xyz: [number, number, number];
  exit_pulse_offset: number;
  event_id: number;
  scale?: [number, number, number];   // default [100, 0, 0]
  ttl_ms?: number;
}

export interface ReelGoArgs {
  Distance: number;
  F?: number;
  ACC?: number;
  DEA?: number;
  JERK?: number;
}

export interface BlockArgs {
  timeout_ms?: number;
}

export interface DigitalInputWaitArgs {
  pin: number;
  state: number;
  group?: number;
  timeout_ms?: number;
}

export interface TriggerWaitArgs {
  motion_progress?: number;
  motion_id_offset?: number;
}

// ─── Replies (shape hints; runtime is `any`) ─────────────────────────

export interface MachineState {
  st: number;
  st_str: string;
  err_src: string;
  err_id: number;
  motion_buffer_size: number;
  movement_id: number;
  runtime_ms: number;
  coord_set: boolean;
  axes_err_mask: number;
  axes_state: number;
  // Per-axis DS402 uiDriveInterfaceError mirror, same ordering as
  // axes_err_mask bits 0..3 (EAxis0/1/2/reelpullmotor). 0 = no fault.
  axes_err_id: number[];
  // Axis labels in the same order as axes_err_id / axes_err_mask bits.
  // Source of truth for axis ordering -- UI must consume this rather
  // than hardcoding, so a PLC reorder propagates automatically.
  axes_labels: string[];
  // Reel axis absolute position in axis-scaled user units (typically
  // mm under current GenericDSP402 scaling). Renderer derives the
  // carrier-tape cell number on resume:
  //   cell = round((reel_pos - origin) / pitch)
  // PLC is origin/pitch-agnostic -- it just reports the raw axis
  // position. See doc_review/decisions_2026-06-22.md §4 (1).
  reel_pos: number;
}

export interface DiagSnapshot {
  runtime_ms: number;
  sm_scans: number;
  remp_drop: number;
  overlen_drop: number;
  send_stall_drop: number;
  group_not_ready_nak: number;
  missing_type_nak: number;
  coord_not_cfg_nak: number;
  proto_mismatch_nak: number;
  idle_reset: number;
  read_err_reset: number;
  parser_err_reset: number;
  ui_ping_count: number;
  ui_hb_stale_count: number;
  ping_max_gap_ms: number;
  last_ui_ping_ms: number;
  st_chg_event_count: number;
}

export interface PushEvent {
  kind: 'event';
  name: 'ST_CHG' | 'COORD_SET' | 'MOVE_DONE' | 'COORD1_ERROR';
  runtime_ms: number;
  // COORD_SET only: BOOL gate state on this edge (true = SetCoord0/1
  // just raised it; false = UnInited entry just cleared it).
  value?: boolean;
  [k: string]: unknown;
}

// Phase 4 step 3 — COORD1_ERROR payload. Fired when the conveyor pulse
// overshoots Coord1ExitPulse during a bound tracking move, or when a
// bind is rejected (rebind_active / malformed scale). PLC source:
// AxisGroupSM.st window-exit detector.
export interface Coord1ErrorEvent extends PushEvent {
  name: 'COORD1_ERROR';
  event_id: number;
  ref_pulse: number;
  exit_pulse: number;
  pulse_at_err: number;
  movement_id: number;
  mv_progress: number;
  arm_x: number; arm_y: number; arm_z: number;
  pcs_x: number; pcs_y: number; pcs_z: number;
}

// Strip undefined keys so the wire packet doesn't carry phantom fields.
function compact<T extends object>(o: T): T {
  const out: any = {};
  for (const k of Object.keys(o)) {
    const v = (o as any)[k];
    if (v !== undefined) out[k] = v;
  }
  return out;
}

// Helper so each builder can declare its reply type with one cast point
// instead of repeating `as Envelope<R>` at every literal.
function env<R = unknown>(o: { type: PacketType; cmd: string; [k: string]: unknown }): Envelope<R> {
  return o as Envelope<R>;
}

export const cmd = {
  // Motion — most motion commands reply with an ack/nack envelope.
  G1: (a: G1Args = {}) => env<G1Reply>({ type: 'M', cmd: 'G1', ...compact(a) }),
  G4: (P: number) => env<AckReply>({ type: 'M', cmd: 'G4', P }),
  M4: (a: M4Args) => env<M4Reply>({ type: 'M', cmd: 'M4', ...compact(a) }),
  // Convenience builder for the FlyEvent COORD1_BIND variant.
  // trig=130 (PulseTrigger), action='coord1_bind', scale defaults to
  // [100, 0, 0] (X-only conveyor); exit_pulse_offset is the pick window
  // — PLC fires COORD1_ERROR + Error state when ConveyorPulseRaw
  // exceeds ref_pulse + exit_pulse_offset.
  M4Bind: (a: M4BindArgs) => env<M4Reply>({
    type: 'M', cmd: 'M4',
    ...compact({
      trig: 130,
      action: 'coord1_bind' as const,
      pulse_target: a.pulse_target,
      ref_xyz: a.ref_xyz,
      scale: a.scale ?? [100, 0, 0],
      exit_pulse_offset: a.exit_pulse_offset,
      event_id: a.event_id,
      ttl_ms: a.ttl_ms,
    }),
  }),
  ReelGo: (a: ReelGoArgs) => env<AckReply>({ type: 'M', cmd: 'ReelGo', ...compact(a) }),
  SetCoord0: () => env<AckReply>({ type: 'M', cmd: 'SetCoord0' }),
  SetCoord1: () => env<AckReply>({ type: 'M', cmd: 'SetCoord1' }),
  WaitForMotionStop: (a: BlockArgs = {}) =>
    env<AckReply>({ type: 'M', cmd: 'WAIT_FOR_MOTION_STOP', ...compact(a) }),
  BlockForDigitalInput: (a: DigitalInputWaitArgs) =>
    env<AckReply>({ type: 'M', cmd: 'BLOCK_FOR_DIGITAL_INPUT', ...compact(a) }),
  WaitForTriggerMotionProgress: (a: TriggerWaitArgs) =>
    env<AckReply>({ type: 'M', cmd: 'WAIT_FOR_TRIGGER_MOTION_PROGRESS', ...compact(a) }),
  ReadLatestCmdLocation: () =>
    env<LocationReply>({ type: 'M', cmd: 'READ_LATEST_CMD_LOCATION' }),
  GetDigitalInput: (group?: number) =>
    env<DigitalInputReply>({ type: 'M', cmd: 'GET_DIGITAL_INPUT', ...compact({ group }) }),
  GetDigitalInputFlipCount: () =>
    env<DigitalInputFlipCountReply>({ type: 'M', cmd: 'getDigitalInputFlipCount' }),
  ResetDbgInfoM: () =>
    env<AckReply>({ type: 'M', cmd: 'RESET_DBG_INFO' }),

  // System
  Ping: () => env<PingReply>({ type: 'SYS', cmd: 'PING' }),
  GetMachineState: () => env<MachineState>({ type: 'SYS', cmd: 'GET_MACHINE_STATE' }),
  GetDiag: () => env<DiagSnapshot>({ type: 'SYS', cmd: 'GET_DIAG' }),
  ResetDbgInfo: () => env<AckReply>({ type: 'SYS', cmd: 'RESET_DBG_INFO' }),
  GA_EV: (ev: number) => env<GaEvReply>({ type: 'SYS', cmd: 'GA_EV', ev }),
} as const;

// ─── Runtime schema-drift guard ──────────────────────────────────────
// TS reply types are erased at runtime, so a renamed/removed PLC field
// silently surfaces as `undefined` in the UI. This lets each consumer
// call `validateReply('MachineState', reply)` once and get a loud
// console.error + a `plc:schema-drift` window event when keys are
// missing -- caught the moment a coupling-invariants entry rots.
//
// Per-shape required-key lists are kept here next to the interfaces so
// they move together. Field types beyond presence aren't checked --
// presence catches 95% of drift; deeper validation isn't worth the
// runtime cost on every reply.

export const REQUIRED_KEYS = {
  MachineState: [
    'st', 'st_str', 'err_src', 'err_id', 'motion_buffer_size',
    'movement_id', 'runtime_ms', 'coord_set',
    'axes_err_mask', 'axes_state', 'axes_err_id', 'axes_labels',
    'reel_pos',
  ],
  DiagSnapshot: [
    'runtime_ms', 'sm_scans', 'remp_drop', 'overlen_drop', 'send_stall_drop',
    'group_not_ready_nak', 'missing_type_nak', 'coord_not_cfg_nak',
    'proto_mismatch_nak', 'idle_reset', 'read_err_reset', 'parser_err_reset',
    'ui_ping_count', 'ui_hb_stale_count', 'ping_max_gap_ms',
    'last_ui_ping_ms', 'st_chg_event_count',
  ],
  GaEvReply: ['st', 'st_str', 'err_src', 'err_id'],
} as const;

export type ReplyShapeName = keyof typeof REQUIRED_KEYS;

export function validateReply(name: ReplyShapeName, reply: unknown): boolean {
  if (!reply || typeof reply !== 'object') {
    reportSchemaDrift(name, '<not-an-object>', reply);
    return false;
  }
  const missing = REQUIRED_KEYS[name].filter((k) => !(k in (reply as object)));
  if (missing.length > 0) {
    reportSchemaDrift(name, missing.join(','), reply);
    return false;
  }
  return true;
}

function reportSchemaDrift(name: string, missing: string, reply: unknown): void {
  console.error(`[schema-drift] ${name} reply missing keys: ${missing}`, reply);
  if (typeof window !== 'undefined') {
    try {
      window.dispatchEvent(new CustomEvent('plc:schema-drift', {
        detail: { name, missing, reply, at: Date.now() },
      }));
    } catch (_) { /* SSR / no-DOM context */ }
  }
}

// Opt-in typed sender. Wraps an existing `sendTcpMsgPack` so callers
// who want compile-time reply types can write:
//
//   const reply = await send(sendTcpMsgPack, cmd.GetMachineState());
//   reply.st_str  // typed as string
//
// Everywhere else continues calling `await sendTcpMsgPack(cmd.X(...))`
// directly with no behavior change. The phantom `__reply` on Envelope
// carries no runtime data — this is a pure compile-time aid.
type RawSendFn = (
  data: any,
  waitForTracking?: boolean,
  timeout_ms?: number,
) => Promise<any> | boolean;

export async function send<R>(
  fn: RawSendFn,
  envelope: Envelope<R>,
  opts: { waitForTracking?: boolean; timeout_ms?: number } = {},
): Promise<R> {
  const result = fn(envelope, opts.waitForTracking, opts.timeout_ms);
  if (typeof result === 'boolean') {
    throw new Error('send(): underlying sendTcpMsgPack returned boolean (fire-and-forget mode); awaitable required for typed reply');
  }
  return (await result) as R;
}

// E_RobotEvent ordinals. Authoritative source:
// codesys_code/Application/Robot_FBs/E_RobotEvent.st (UDINT enum).
// PLC order is positional, so any reorder there must mirror here.
// See coupling_invariants.md "E_RobotEvent ordinals".
export const Event = {
  NONE: 0,
  BOOT: 1,
  POWER_ON: 2,
  POWER_OFF: 3,
  GROUP_ENABLE: 4,
  GROUP_DISABLE: 5,
  HOME_GO: 6,
  HOME_GO_FORCE_SKIP: 7,
  RESET: 8,
  ERROR: 9,
  OK: 10,
  ER: 11,
} as const;
export type EventOrdinal = (typeof Event)[keyof typeof Event];
