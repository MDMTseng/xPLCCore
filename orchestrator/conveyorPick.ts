// Phase 6 — conveyor pick-and-place orchestrator.
//
// Consumes the Phase 4 step 3 PLC FlyEvent COORD1_BIND protocol. Pure
// async function — no React, no globals. Caller injects the TCP send
// primitive and the event subscription, so this module is unit-testable
// against a fake transport.
//
// Cycle:
//   1. Schedule M4Bind to fire at pulse_target. PLC arms the bind in the
//      FlyEvent ring and replies with ack + event_id.
//   2. Issue G1 frame=coord1 to the pick point (relative to the moving
//      object). PLC executes once bind fires; arm chases belt.
//   3. Fire pick action (gripper close / vacuum on) via M4 pin pulse
//      latched to motion progress.
//   4. Issue G1 frame=0 to lift + place point (untrack).
//   5. Resolve.
//
// Fault path: a COORD1_ERROR event with our event_id aborts the run,
// causing the returned promise to reject with the PLC diagnostic payload
// already in hand. The PLC has put itself in Error + MC_GroupStop, so no
// further motion commands will succeed until host clears + re-homes.

import { cmd, Envelope, M4Reply, G1Reply, AckReply, Coord1ErrorEvent, PushEvent } from '../lib/protocol';

export type SendFn = (
  data: any,
  waitForTracking?: boolean,
  timeout_ms?: number,
) => Promise<any> | boolean;

export interface PickPoint {
  X?: number; Y?: number; Z?: number;
  A?: number; B?: number; C?: number;
  F?: number;
}

export interface ConveyorPickRequest {
  // Bind input — when ConveyorPulseRaw hits pulse_target, PLC binds
  // PCS_1 origin to ref_xyz. ref_xyz is the object origin in WCS at
  // the moment the trigger fires (host-computed from vision pose +
  // belt-pulse-at-detection).
  pulse_target: number;
  ref_xyz: [number, number, number];
  exit_pulse_offset: number;
  event_id: number;
  scale?: [number, number, number];
  bind_ttl_ms?: number;

  // Pick action — relative to bound coord1 frame (object-local).
  pick_point: PickPoint;          // approach above object (frame=coord1)
  pick_action: () => Promise<void>; // gripper close / vacuum on / etc.

  // Place action — absolute WCS. Issued frame=0 to leave tracking.
  lift_z: number;                  // safe height in WCS before untrack
  place_point: PickPoint;          // drop location (frame=0)
  place_action: () => Promise<void>; // gripper open / vacuum off

  // Optional per-G1 motion params.
  feed?: number;
}

export type ConveyorPickPhase =
  | 'idle'
  | 'binding'
  | 'tracking'
  | 'picking'
  | 'untracking'
  | 'placing'
  | 'done'
  | 'error';

export interface ConveyorPickResult {
  ok: true;
  phase: 'done';
  pick_movement_id?: number;
  place_movement_id?: number;
}

export interface ConveyorPickFailure {
  ok: false;
  phase: ConveyorPickPhase;
  reason: string;
  coord1_error?: Coord1ErrorEvent;
  detail?: unknown;
}

export interface OrchestratorDeps {
  send: SendFn;
  // Subscribe to PLC push events. Returns an unsubscribe fn.
  subscribeEvents: (cb: (ev: PushEvent) => void) => () => void;
  // Optional progress hook — called on phase transitions.
  onPhase?: (phase: ConveyorPickPhase) => void;
  // Optional log hook.
  log?: (msg: string, extra?: unknown) => void;
}

// Sentinel thrown internally when a COORD1_ERROR event with our id
// arrives; the top-level try/catch turns it into a structured failure.
class Coord1Abort extends Error {
  constructor(public readonly event: Coord1ErrorEvent) {
    super(`COORD1_ERROR (event_id=${event.event_id}, ref=${event.ref_pulse}, exit=${event.exit_pulse}, at=${event.pulse_at_err})`);
  }
}

async function sendTyped<R>(send: SendFn, env: Envelope<R>, timeout_ms = 5000): Promise<R> {
  const result = send(env, true, timeout_ms);
  if (typeof result === 'boolean') {
    throw new Error('orchestrator: send returned boolean (fire-and-forget); awaitable required');
  }
  return (await result) as R;
}

function requireAck<R extends AckReply>(reply: R, where: string): R {
  if (!reply || reply.ack !== true) {
    throw new Error(`${where}: NAK ${reply?.err ?? '<no-reason>'}`);
  }
  return reply;
}

export async function runConveyorPick(
  req: ConveyorPickRequest,
  deps: OrchestratorDeps,
): Promise<ConveyorPickResult | ConveyorPickFailure> {
  const { send, subscribeEvents, onPhase, log } = deps;
  let phase: ConveyorPickPhase = 'idle';
  const setPhase = (p: ConveyorPickPhase) => { phase = p; onPhase?.(p); };

  // Latch any COORD1_ERROR for our event_id. Stored even before await
  // points so a race between bind-ack and a synchronous-ish error event
  // is still caught.
  let abortEv: Coord1ErrorEvent | undefined;
  const unsub = subscribeEvents((ev) => {
    if (ev.name === 'COORD1_ERROR' && (ev as Coord1ErrorEvent).event_id === req.event_id) {
      abortEv = ev as Coord1ErrorEvent;
      log?.('[conveyorPick] COORD1_ERROR latched', abortEv);
    }
  });

  const checkAbort = () => {
    if (abortEv) throw new Coord1Abort(abortEv);
  };

  try {
    setPhase('binding');
    log?.('[conveyorPick] M4Bind', req);
    const bindAck = await sendTyped(send, cmd.M4Bind({
      pulse_target: req.pulse_target,
      ref_xyz: req.ref_xyz,
      exit_pulse_offset: req.exit_pulse_offset,
      event_id: req.event_id,
      scale: req.scale,
      ttl_ms: req.bind_ttl_ms,
    }));
    requireAck(bindAck, 'M4Bind');
    checkAbort();

    setPhase('tracking');
    // G1 in coord1 frame — PLC executes once bind fires; arm chases
    // the moving object. Use frame=1 to mark coord1.
    const pickReply = await sendTyped(send, cmd.G1({
      ...req.pick_point,
      F: req.pick_point.F ?? req.feed,
      frame: 1,
    }));
    requireAck(pickReply, 'G1 pick(frame=coord1)');
    checkAbort();

    setPhase('picking');
    await req.pick_action();
    checkAbort();

    setPhase('untracking');
    // Lift above belt in coord1 first so the pickup doesn't drag —
    // host can override by leaving lift_z out and including Z in
    // pick_point. Here we issue a simple lift in coord1 frame.
    const liftReply = await sendTyped(send, cmd.G1({
      ...req.pick_point,
      Z: req.lift_z,
      F: req.feed,
      frame: 1,
    }));
    requireAck(liftReply, 'G1 lift(frame=coord1)');
    checkAbort();

    // Untrack = issue G1 in frame=0 (WCS). PLC implicitly drops the
    // coord1 binding when the next G1 specifies frame=0.
    setPhase('placing');
    const placeReply = await sendTyped(send, cmd.G1({
      ...req.place_point,
      F: req.place_point.F ?? req.feed,
      frame: 0,
    }));
    requireAck(placeReply, 'G1 place(frame=0)');
    // Once we leave coord1 frame the window detector still runs until
    // host issues COORD1_UNBIND or the bind self-clears; the run is
    // logically done from our PoV the moment place_action completes.

    await req.place_action();

    setPhase('done');
    return {
      ok: true,
      phase: 'done',
      pick_movement_id: pickReply.movement_id,
      place_movement_id: placeReply.movement_id,
    };
  } catch (e) {
    if (e instanceof Coord1Abort) {
      setPhase('error');
      return { ok: false, phase, reason: 'coord1_window_exit', coord1_error: e.event };
    }
    setPhase('error');
    return {
      ok: false,
      phase,
      reason: (e instanceof Error ? e.message : String(e)),
      coord1_error: abortEv,
      detail: e,
    };
  } finally {
    unsub();
  }
}

// Convenience adapter — wires the orchestrator into the existing
// `window.addEventListener('plc:event', ...)` channel used by
// PluginHello.tsx.
export function makeWindowEventSubscriber(): OrchestratorDeps['subscribeEvents'] {
  return (cb) => {
    const handler = (ev: Event) => {
      const detail = (ev as CustomEvent).detail;
      if (detail && typeof detail === 'object' && 'kind' in detail && (detail as any).kind === 'event') {
        cb(detail as PushEvent);
      }
    };
    window.addEventListener('plc:event', handler as EventListener);
    return () => window.removeEventListener('plc:event', handler as EventListener);
  };
}
