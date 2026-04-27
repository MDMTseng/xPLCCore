// Typed builders for UI ↔ PLC msgpack packets.
// Authoritative spec: doc/protocol.md.
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
  name: 'ST_CHG' | 'COORD_SET' | 'MOVE_DONE';
  runtime_ms: number;
  // COORD_SET only: BOOL gate state on this edge (true = SetCoord0/1
  // just raised it; false = UnInited entry just cleared it).
  value?: boolean;
  [k: string]: unknown;
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
