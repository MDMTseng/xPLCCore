// BindingTestPage -- hardware bench test for tape-on-reel binding:
// pull the reel forward a set distance, actuate a heat press via two
// digital outputs for a set press time, release, repeat.
//
// Follows the RecoveryDemoPage precedent: standalone page registered in
// ControlPage's tabs array, receives COMCtrlObj, left column of controls
// + right log panel, 1Hz GET_MACHINE_STATE poll.
//
// The bind CYCLE lives entirely here (PLC stays generic): the PLC only
// sees a ReelGo move and an M4 immediate pin op. The timed press is a
// single M4 whose release stage is PLC-timed -- if the UI crashes
// mid-press, the PLC still releases the press on schedule.
//
// IO module: the EtherCAT DO module's device mapping binds OutputCHs[1]
// (%QB8, CH1 = physical outputs 0-7) and OutputCHs[2] (%QB9, CH2 =
// physical outputs 8-15), so DigitalOutputBits bit N -> physical output
// N (see ProcessFlyEventsAndIo.st). The device-tree node is labelled
// C1616DN (16/16) but the physical part is still being confirmed by the
// user -- pin indices are clamped to 0..7 (valid under either part);
// raise MAX_PIN to 15 once a 16-out module is confirmed.
//
// Never auto-starts anything: every actuation is behind an explicit click.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { cmd, type MachineState, type M4Reply, type AckReply, type SetAxisSimReply } from '../lib/protocol';

const EV_POWER_ON = 2;
const EV_GROUP_ENABLE = 4;
const EV_HOME_GO_FORCE_SKIP = 7;
const EV_RESET = 8;

const POLL_MS = 1000;
const SETTLE_POLL_MS = 300;
const SETTLE_STABLE_POLLS = 3;
const SETTLE_POS_TOL = 0.005;      // "not moving" tolerance between polls (mm)
const SETTLE_TARGET_TOL = 0.05;    // "reached start+Distance" tolerance (mm)
const REEL_SETTLE_TIMEOUT_MS = 30000;
const PRESS_TTL_MS = 2000;         // FlyEvent trigger TTL; timeout push = press did not fire
const MIN_JERK = 10000;            // NEVER send JERK=0 -- SMC_MR_INVALID_VELACC_VALUES
const MAX_PIN = 7;                 // safe subset: phys outputs 0-7 (CH1); raise to 15 once the 16-out part is confirmed

// Per-axis simulation masks (SYS SET_AXIS_SIM, bit0-2 = delta trio,
// bit3 = reelpullmotor; 1 = simulated).
const SIM_MASK_BENCH = 0x07;       // delta simulated, reel REAL
const SIM_MASK_ALL_REAL = 0x00;
const SIM_MASK_DELTA = 0x07;
const SIM_MASK_REEL = 0x08;

const LS_KEY = 'bindingTestPage.v1';

type LogEntry = { ts: number; line: string; warn?: boolean };

interface PersistedFields {
  distance: string;
  feed: string;
  acc: string;
  dea: string;
  advanced: boolean;
  pinA: string;          // press-down solenoid DO pin
  pinB: string;          // lift solenoid DO pin
  pressTimeMs: string;   // how long the press-down output is held
  liftTimeMs: string;    // how long the lift output is held (after press)
  cycleCount: string;
  // Bench mode: delta trio simulated, reel real (SET_AXIS_SIM 0x07
  // before the FSM climb). Default ON -- this tab exists for the bench.
  benchMode: boolean;
}

const DEFAULT_FIELDS: PersistedFields = {
  distance: '4',
  feed: '20',
  acc: '100',
  dea: '100',
  advanced: false,
  pinA: '0',
  pinB: '1',
  pressTimeMs: '500',
  liftTimeMs: '500',
  cycleCount: '3',
  benchMode: true,
};

function loadFields(): PersistedFields {
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    if (!raw) return DEFAULT_FIELDS;
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_FIELDS, ...parsed };
  } catch {
    return DEFAULT_FIELDS;
  }
}

const sleep = (ms: number) => new Promise<void>((res) => setTimeout(res, ms));

const stateColor = (st: number | undefined): string => {
  if (st === 70) return '#16a34a';   // Ready
  if (st === 990) return '#dc2626';  // Error
  return '#d97706';                  // in between
};

export const BindingTestPage: React.FC<{ COMCtrlObj: COMCtrlObj }> = ({ COMCtrlObj }) => {
  const send = COMCtrlObj.sendTcpMsgPack;
  const [snap, setSnap] = useState<MachineState | null>(null);
  const [snapErr, setSnapErr] = useState<string | null>(null);
  const [fields, setFields] = useState<PersistedFields>(() => loadFields());
  const [log, setLog] = useState<LogEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [cycleRunning, setCycleRunning] = useState(false);
  const abortRef = useRef(false);
  // event_id -> {label, timer}. TTL-timeout pushes carry our event_id;
  // arrival within PRESS_TTL_MS means the pin op did NOT fire.
  const pendingPressRef = useRef<Map<number, { label: string; timer: number }>>(new Map());
  const eventIdRef = useRef(80000 + Math.floor(Math.random() * 1000));

  // ── field persistence ─────────────────────────────────────────────
  useEffect(() => {
    try { window.localStorage.setItem(LS_KEY, JSON.stringify(fields)); } catch { /* quota */ }
  }, [fields]);
  const setField = useCallback(<K extends keyof PersistedFields>(k: K, v: PersistedFields[K]) => {
    setFields((prev) => ({ ...prev, [k]: v }));
  }, []);

  const appendLog = useCallback((line: string, warn = false) => {
    setLog((prev) => [{ ts: Date.now(), line, warn }, ...prev].slice(0, 200));
  }, []);

  // ── 1 Hz poll of GET_MACHINE_STATE ────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const reply = await send(cmd.GetMachineState(), true, 1500);
        if (cancelled) return;
        if (reply && typeof reply === 'object' && 'st' in reply) {
          setSnap(reply as MachineState);
          setSnapErr(null);
        }
      } catch (e: any) {
        if (!cancelled) setSnapErr(e?.message ?? String(e));
      }
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => { cancelled = true; window.clearInterval(id); };
  }, [send]);

  // ── press-fire watchdog: TTL-timeout pushes carry our event_id ────
  useEffect(() => {
    const handler = (ev: Event) => {
      const msg = (ev as CustomEvent).detail;
      if (!msg || typeof msg !== 'object') return;
      if ((msg as any).name !== 'TRIGGER_ERR') return;
      const eid = (msg as any).event_id;
      if (typeof eid !== 'number') return;
      const pending = pendingPressRef.current.get(eid);
      if (!pending) return;
      window.clearTimeout(pending.timer);
      pendingPressRef.current.delete(eid);
      appendLog(
        `${pending.label} (event_id=${eid}): PLC pushed trigger-TIMEOUT -- the press did NOT fire. ` +
        `The M4 ack only means the FlyEvent was registered; firing requires a valid group position ` +
        `(group enabled / FSM Ready). Drive FSM to Ready and retry.`,
        true,
      );
    };
    window.addEventListener('plc:event', handler as EventListener);
    return () => {
      window.removeEventListener('plc:event', handler as EventListener);
      // Clear any armed watchdog timers on unmount.
      for (const p of pendingPressRef.current.values()) window.clearTimeout(p.timer);
      pendingPressRef.current.clear();
    };
  }, [appendLog]);

  // ── send helpers ──────────────────────────────────────────────────
  const sendTracked = useCallback(async <R,>(env: any, label: string, timeout = 3000): Promise<R | null> => {
    const result = send(env, true, timeout);
    if (typeof result === 'boolean') {
      appendLog(`${label}: fire-and-forget (no reply)`);
      return null;
    }
    try {
      const r = await result;
      appendLog(`${label}: ${JSON.stringify(r).slice(0, 220)}`);
      return r as R;
    } catch (e: any) {
      appendLog(`${label} threw: ${e?.message ?? e}`, true);
      return null;
    }
  }, [send, appendLog]);

  const pollUntilState = useCallback(async (target: number, timeoutMs = 3000): Promise<boolean> => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const r = await send(cmd.GetMachineState(), true, 1000);
        if (r && typeof r === 'object' && (r as any).st === target) return true;
      } catch { /* poll again */ }
      await sleep(200);
    }
    return false;
  }, [send]);

  // ── FSM helper ────────────────────────────────────────────────────
  // RESET first (SET_AXIS_SIM is only honored in UnInited), then stamp
  // the axis-sim mask for the session, then climb to Ready.
  const driveToReady = useCallback(async () => {
    setBusy(true);
    try {
      await sendTracked(cmd.GA_EV(EV_RESET), 'GA_EV(RESET)');
      if (!(await pollUntilState(10))) {
        appendLog('drive aborted: RESET did not reach UnInited (st=10) within 3s', true);
        return;
      }
      const wantMask = fields.benchMode ? SIM_MASK_BENCH : SIM_MASK_ALL_REAL;
      const simReply = await sendTracked<SetAxisSimReply>(
        cmd.SetAxisSim(wantMask),
        `SET_AXIS_SIM(0x${wantMask.toString(16).padStart(2, '0')})`,
      );
      if (!simReply || simReply.ack !== true) {
        appendLog('drive aborted: SET_AXIS_SIM was not acked -- axis sim state unknown, not powering on', true);
        return;
      }
      // Handshake: the PLC publishes axes_sim_mask only once the drive
      // reinit sequence completes. Wait for it before POWER_ON so we
      // never power half-switched drives.
      const effective = simReply.mask ?? wantMask;
      const maskDeadline = Date.now() + 15000;
      let maskApplied = false;
      while (Date.now() < maskDeadline) {
        try {
          const r = await send(cmd.GetMachineState(), true, 1500);
          if (r && typeof r === 'object' && (r as MachineState).axes_sim_mask === effective) {
            maskApplied = true;
            break;
          }
        } catch { /* poll again */ }
        await sleep(300);
      }
      if (!maskApplied) {
        appendLog(`drive aborted: axes_sim_mask did not reach 0x${effective.toString(16).padStart(2, '0')} within 15s (drive reinit failed?)`, true);
        return;
      }
      appendLog(`axis sim mask applied: 0x${effective.toString(16).padStart(2, '0')} `
        + (fields.benchMode ? '(delta simulated, reel REAL)' : '(ALL AXES REAL)'));
      const steps: Array<[number, string, number]> = [
        [EV_POWER_ON,           'POWER_ON',           30],
        [EV_GROUP_ENABLE,       'GROUP_ENABLE',       50],
        [EV_HOME_GO_FORCE_SKIP, 'HOME_GO_FORCE_SKIP', 70],
      ];
      for (const [ev, label, target] of steps) {
        await sendTracked(cmd.GA_EV(ev), `GA_EV(${label})`);
        const reached = await pollUntilState(target);
        if (!reached) {
          appendLog(`drive aborted: ${label} did not advance to st=${target} within 3s`, true);
          return;
        }
      }
      appendLog('FSM is Ready -- ReelGo / M4 will dispatch (no SetCoord0 needed for these).');
    } finally {
      setBusy(false);
    }
  }, [sendTracked, pollUntilState, appendLog, fields.benchMode, send]);

  // ── parsing helpers ───────────────────────────────────────────────
  const numOr = (s: string, fallback: number): number => {
    const n = Number(s);
    return Number.isFinite(n) ? n : fallback;
  };

  // press = pinA (press-down solenoid), lift = pinB (lift solenoid).
  // They are DISTINCT outputs driven in separate phases, never together,
  // so a shared pin is a config error (would press and lift the same
  // bit). maskBoth is only for the all-off safety release.
  const parsePins = useCallback((): {
    press: number; lift: number;
    maskPress: number; maskLift: number; maskBoth: number;
  } | null => {
    const a = Math.trunc(numOr(fields.pinA, -1));
    const b = Math.trunc(numOr(fields.pinB, -1));
    if (a < 0 || a > MAX_PIN || b < 0 || b > MAX_PIN) {
      appendLog(`pin indices must be 0..${MAX_PIN} (CH1 physical outputs); got press=${fields.pinA} lift=${fields.pinB}`, true);
      return null;
    }
    if (a === b) {
      appendLog(`press pin and lift pin must differ (both = ${a}); they are separate solenoids`, true);
      return null;
    }
    return { press: a, lift: b, maskPress: 1 << a, maskLift: 1 << b, maskBoth: (1 << a) | (1 << b) };
  }, [fields.pinA, fields.pinB, appendLog]);

  const requireReady = useCallback((): boolean => {
    if (!snap || snap.st !== 70) {
      appendLog(`FSM not Ready (st=${snap?.st ?? '?'}) -- type:'M' packets only dispatch in Ready. Use the FSM helper first.`, true);
      return false;
    }
    return true;
  }, [snap, appendLog]);

  // ── reel pull ─────────────────────────────────────────────────────
  const readReelPos = useCallback(async (): Promise<number | null> => {
    try {
      const r = await send(cmd.GetMachineState(), true, 1500);
      if (r && typeof r === 'object' && 'reel_pos' in r) return (r as MachineState).reel_pos;
    } catch { /* transient */ }
    return null;
  }, [send]);

  // ReelGo acks immediately; there is no movement_id for the reel axis.
  // Completion = reel_pos reaches start+Distance within tolerance, or
  // stops changing for SETTLE_STABLE_POLLS consecutive polls.
  const waitReelSettle = useCallback(async (startPos: number, distance: number): Promise<{ ok: boolean; pos: number | null; why: string }> => {
    const target = startPos + distance;
    const deadline = Date.now() + REEL_SETTLE_TIMEOUT_MS;
    let last: number | null = null;
    let stable = 0;
    await sleep(SETTLE_POLL_MS); // let the move start before sampling "stable"
    while (Date.now() < deadline) {
      if (abortRef.current) return { ok: false, pos: last, why: 'aborted' };
      const p = await readReelPos();
      if (p !== null) {
        if (Math.abs(p - target) <= SETTLE_TARGET_TOL) {
          return { ok: true, pos: p, why: `reached target ${target.toFixed(3)}` };
        }
        if (last !== null && Math.abs(p - last) <= SETTLE_POS_TOL) {
          stable += 1;
          if (stable >= SETTLE_STABLE_POLLS) {
            return { ok: true, pos: p, why: `settled (${SETTLE_STABLE_POLLS} stable polls, off-target by ${(p - target).toFixed(3)})` };
          }
        } else {
          stable = 0;
        }
        last = p;
      }
      await sleep(SETTLE_POLL_MS);
    }
    return { ok: false, pos: last, why: 'settle timeout' };
  }, [readReelPos]);

  const buildReelGo = useCallback(() => {
    const Distance = numOr(fields.distance, 0);
    const F = numOr(fields.feed, 20);
    const args: { Distance: number; F: number; JERK: number; ACC?: number; DEA?: number } = {
      Distance,
      F,
      JERK: MIN_JERK, // hidden; never 0 (SMC_MR_INVALID_VELACC_VALUES)
    };
    if (fields.advanced) {
      args.ACC = numOr(fields.acc, 100);
      args.DEA = numOr(fields.dea, 100);
    }
    return args;
  }, [fields]);

  // Returns true when pull + settle succeeded (used by the cycle loop).
  const pullReelOnce = useCallback(async (): Promise<boolean> => {
    if (!requireReady()) return false;
    const args = buildReelGo();
    if (args.Distance === 0) { appendLog('distance is 0 -- nothing to pull', true); return false; }
    const start = await readReelPos();
    if (start === null) { appendLog('could not read reel_pos before pull', true); return false; }
    const r = await sendTracked<AckReply>(cmd.ReelGo(args), `ReelGo(D=${args.Distance}, F=${args.F})`);
    if (!r || r.ack !== true) { appendLog('ReelGo not acked -- pull skipped', true); return false; }
    const settle = await waitReelSettle(start, args.Distance);
    appendLog(
      `reel ${settle.ok ? 'settled' : 'DID NOT settle'}: start=${start.toFixed(3)} now=${settle.pos?.toFixed(3) ?? '?'} (${settle.why})`,
      !settle.ok,
    );
    return settle.ok;
  }, [requireReady, buildReelGo, readReelPos, sendTracked, waitReelSettle, appendLog]);

  const onPullReel = useCallback(async () => {
    setBusy(true);
    abortRef.current = false;
    try { await pullReelOnce(); } finally { setBusy(false); }
  }, [pullReelOnce]);

  // ── heat press ────────────────────────────────────────────────────
  // Sends ONE immediate-fire M4 and arms the fire watchdog. The M4 ack
  // only proves registration; if the trigger can't evaluate (invalid
  // group position) the PLC pushes a TTL-timeout event instead of firing.
  const sendPressM4 = useCallback(async (pinOpSeq: number[], label: string): Promise<boolean> => {
    const eid = ++eventIdRef.current;
    const r = await sendTracked<M4Reply>(
      cmd.M4ImmediatePinOp({ pin_op_seq: pinOpSeq, event_id: eid, ttl_ms: PRESS_TTL_MS }),
      `${label} M4(event_id=${eid})`,
    );
    if (!r || r.ack !== true) {
      appendLog(`${label}: M4 not acked -- pins untouched`, true);
      return false;
    }
    // Arm the watchdog: if no TTL-timeout push arrives within TTL+margin,
    // the FlyEvent fired on the next scan (there is no explicit "fired"
    // push for pin ops -- absence of the timeout IS the success signal).
    const timer = window.setTimeout(() => {
      if (pendingPressRef.current.delete(eid)) {
        appendLog(`${label} (event_id=${eid}): no trigger-timeout push within ${PRESS_TTL_MS}ms -- pin op fired on the PLC.`);
      }
    }, PRESS_TTL_MS + 500);
    pendingPressRef.current.set(eid, { label, timer });
    return true;
  }, [sendTracked, appendLog]);

  // Two-phase double-acting cycle: press-down (pinA) held press_time_ms,
  // THEN lift (pinB) held lift_time_ms. The two solenoids are mutually
  // exclusive -- press releases before lift engages -- and both end OFF.
  // Encoded as ONE 4-stage pin_op_seq so the whole schedule is PLC-timed:
  // a UI crash mid-cycle still runs press-release, lift, and lift-release
  // on the PLC. Stage = [wait_ms, mask, state]; wait is time held in the
  // previous state before this stage applies.
  const pressLiftOnce = useCallback(async (): Promise<boolean> => {
    if (!requireReady()) return false;
    const pins = parsePins();
    if (!pins) return false;
    const pressMs = Math.max(1, Math.trunc(numOr(fields.pressTimeMs, 500)));
    const liftMs = Math.max(1, Math.trunc(numOr(fields.liftTimeMs, 500)));
    const seq = [
      0,       pins.maskPress, pins.maskPress,  // press-down ON now
      pressMs, pins.maskPress, 0,               // hold press_time, press OFF
      0,       pins.maskLift,  pins.maskLift,   // lift ON (right after press releases)
      liftMs,  pins.maskLift,  0,               // hold lift_time, lift OFF
    ];
    appendLog(`press+lift: press pin ${pins.press} for ${pressMs}ms, then lift pin ${pins.lift} for ${liftMs}ms (all PLC-timed)`);
    return sendPressM4(seq, 'Press+Lift');
  }, [requireReady, parsePins, fields.pressTimeMs, fields.liftTimeMs, appendLog, sendPressM4]);

  const onPressLift = useCallback(async () => {
    setBusy(true);
    try { await pressLiftOnce(); } finally { setBusy(false); }
  }, [pressLiftOnce]);

  // Manual per-solenoid hold, for physical setup/verification of each
  // output independently. ON has no auto-release; OFF is always allowed.
  const onManual = useCallback(async (which: 'press' | 'lift', on: boolean) => {
    const pins = parsePins();
    if (!pins) return;
    if (on && !requireReady()) return;
    const m = which === 'press' ? pins.maskPress : pins.maskLift;
    const pin = which === 'press' ? pins.press : pins.lift;
    setBusy(true);
    try {
      const seq = on ? [0, m, m] : [0, m, 0];
      if (on) appendLog(`MANUAL ${which} ON: pin ${pin} (mask=0x${m.toString(16)}) -- NO AUTO-RELEASE, click "${which} OFF" to release!`, true);
      await sendPressM4(seq, `${which} ${on ? 'ON(manual)' : 'OFF'}`);
    } finally {
      setBusy(false);
    }
  }, [parsePins, requireReady, appendLog, sendPressM4]);

  // ── bind cycle ────────────────────────────────────────────────────
  const onRunCycle = useCallback(async () => {
    if (!requireReady()) return;
    const pins = parsePins();
    if (!pins) return;
    const n = Math.max(1, Math.trunc(numOr(fields.cycleCount, 1)));
    const pressMs = Math.max(1, Math.trunc(numOr(fields.pressTimeMs, 500)));
    const liftMs = Math.max(1, Math.trunc(numOr(fields.liftTimeMs, 500)));
    abortRef.current = false;
    setCycleRunning(true);
    setBusy(true);
    appendLog(`=== bind cycle start: ${n} cycle(s), pull=${fields.distance}mm, press=${pressMs}ms, lift=${liftMs}ms (press pin ${pins.press}, lift pin ${pins.lift}) ===`);
    try {
      for (let i = 1; i <= n; i++) {
        if (abortRef.current) { appendLog(`cycle ${i}/${n}: aborted before pull`); return; }
        appendLog(`cycle ${i}/${n}: pull reel`);
        const pulled = await pullReelOnce();
        if (!pulled) { appendLog(`cycle ${i}/${n}: pull failed -- cycle stopped`, true); return; }
        if (abortRef.current) { appendLog(`cycle ${i}/${n}: aborted before press`); return; }
        appendLog(`cycle ${i}/${n}: press ${pressMs}ms + lift ${liftMs}ms`);
        const pressed = await pressLiftOnce();
        if (!pressed) { appendLog(`cycle ${i}/${n}: press+lift M4 failed -- cycle stopped`, true); return; }
        // Wait out the whole PLC-timed press+lift + margin before the next pull.
        const waitMs = pressMs + liftMs + 400;
        appendLog(`cycle ${i}/${n}: waiting press+lift ${pressMs + liftMs}ms + 400ms margin`);
        const deadline = Date.now() + waitMs;
        while (Date.now() < deadline) {
          if (abortRef.current) { appendLog(`cycle ${i}/${n}: aborted during press/lift wait (schedule is PLC-timed, will still finish)`); return; }
          await sleep(100);
        }
        appendLog(`cycle ${i}/${n}: done`);
      }
      appendLog(`=== bind cycle complete: ${n} cycle(s) ===`);
    } finally {
      setCycleRunning(false);
      setBusy(false);
    }
  }, [requireReady, parsePins, fields.cycleCount, fields.pressTimeMs, fields.liftTimeMs, fields.distance, appendLog, pullReelOnce, pressLiftOnce]);

  const onAbort = useCallback(async () => {
    abortRef.current = true;
    appendLog('ABORT: stopping cycle, sending both outputs OFF (reel stays where it is)', true);
    const pins = parsePins();
    if (pins) {
      await sendPressM4([0, pins.maskBoth, 0], 'Abort all OFF');
    }
  }, [appendLog, parsePins, sendPressM4]);

  // ── render ────────────────────────────────────────────────────────
  const actuationDisabled = busy || cycleRunning;
  const pinsPreview = (() => {
    const a = Math.trunc(numOr(fields.pinA, -1));
    const b = Math.trunc(numOr(fields.pinB, -1));
    if (a < 0 || a > MAX_PIN || b < 0 || b > MAX_PIN || a === b) return null;
    return { press: a, lift: b };
  })();

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 320px', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Banner */}
        <div style={{ padding: 10, background: '#fff7ed', border: '1px solid #f59e0b', borderRadius: 8, color: '#92400e', fontSize: 12 }}>
          <b>Binding bench test.</b> Pulls the physical reel and actuates the heat press via digital
          outputs (pins 0..{MAX_PIN} = CH1 physical outputs). Every actuation is behind an explicit
          click -- nothing runs on page load. Verify the press area is clear before pressing.
        </div>

        {/* FSM helper */}
        <Section title="FSM helper / bench mode">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6, flexWrap: 'wrap' }}>
            <span style={{
              padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700,
              color: '#ffffff', background: stateColor(snap?.st),
            }}>
              {snap ? `${snap.st_str} (${snap.st})` : 'no snapshot'}
            </span>
            {snap && typeof snap.axes_sim_mask === 'number' && (
              <span style={{
                padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700,
                color: '#ffffff',
                background: (snap.axes_sim_mask & SIM_MASK_DELTA) === SIM_MASK_DELTA ? '#7c3aed' : '#374151',
              }}>
                sim_mask=0x{snap.axes_sim_mask.toString(16).padStart(2, '0')}
                {' '}(delta {(snap.axes_sim_mask & SIM_MASK_DELTA) === SIM_MASK_DELTA ? 'SIM' : (snap.axes_sim_mask & SIM_MASK_DELTA) !== 0 ? 'PARTIAL' : 'REAL'},
                {' '}reel {(snap.axes_sim_mask & SIM_MASK_REEL) !== 0 ? 'SIM' : 'REAL'})
              </span>
            )}
            <span style={{ fontSize: 12, fontFamily: 'monospace', color: '#6b7280' }}>
              reel_pos={snap ? snap.reel_pos.toFixed(3) : '—'}  buf={snap?.motion_buffer_size ?? '—'}
            </span>
            {snapErr && <span style={{ color: '#dc2626', fontSize: 12 }}>poll err: {snapErr}</span>}
          </div>
          {snap && typeof snap.axes_sim_mask === 'number' && (snap.axes_sim_mask & SIM_MASK_DELTA) === SIM_MASK_DELTA && (
            <div style={{ fontSize: 12, color: '#6d28d9', marginBottom: 6 }}>
              Delta arm axes are SIMULATED -- G1 will not move the physical arms. Reel is
              {(snap.axes_sim_mask & SIM_MASK_REEL) !== 0 ? ' also simulated (reel_pos is virtual).' : ' REAL: ReelGo moves the physical reel motor.'}
            </div>
          )}
          <label style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
            <input
              type="checkbox"
              checked={fields.benchMode}
              onChange={(e) => setField('benchMode', e.target.checked)}
              disabled={actuationDisabled}
            />{' '}
            <b>Bench mode</b> -- before the climb, send SET_AXIS_SIM 0x07: delta trio simulated,
            reelpullmotor REAL. Uncheck for all-real (full machine) operation.
          </label>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>
            ReelGo and the press M4 are motion-type packets -- they only dispatch when the FSM is
            Ready. One click walks RESET → SET_AXIS_SIM → POWER_ON → GROUP_ENABLE → HOME_GO_FORCE_SKIP.
          </div>
          <button type="button" onClick={driveToReady} disabled={actuationDisabled}>
            Drive to Ready (RESET → SET_AXIS_SIM → POWER → GROUP → HOME_FORCE_SKIP)
          </button>
        </Section>

        {/* Reel pull */}
        <Section title="Reel pull">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end', marginBottom: 6 }}>
            <Field label="distance_mm (may be negative)">
              <input type="number" step="any" value={fields.distance} onChange={(e) => setField('distance', e.target.value)} style={inputStyle} />
            </Field>
            <Field label="F (feed)">
              <input type="number" step="any" min={0} value={fields.feed} onChange={(e) => setField('feed', e.target.value)} style={inputStyle} />
            </Field>
            <label style={{ fontSize: 12 }}>
              <input type="checkbox" checked={fields.advanced} onChange={(e) => setField('advanced', e.target.checked)} /> advanced
            </label>
            {fields.advanced && (
              <>
                <Field label="ACC">
                  <input type="number" step="any" min={0} value={fields.acc} onChange={(e) => setField('acc', e.target.value)} style={inputStyle} />
                </Field>
                <Field label="DEA">
                  <input type="number" step="any" min={0} value={fields.dea} onChange={(e) => setField('dea', e.target.value)} style={inputStyle} />
                </Field>
              </>
            )}
          </div>
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>
            JERK is fixed at {MIN_JERK} (hidden -- zero jerk trips SMC_MR_INVALID_VELACC_VALUES on the
            PLC). Completion is detected by polling reel_pos until it reaches start+distance or stops
            changing for {SETTLE_STABLE_POLLS} polls.
          </div>
          <button type="button" onClick={onPullReel} disabled={actuationDisabled}>Pull reel</button>
        </Section>

        {/* Heat press: two-phase press-down then lift */}
        <Section title="Heat press (press-down + lift, 2 DOs)">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end', marginBottom: 6 }}>
            <Field label={`press-down pin A (0..${MAX_PIN})`}>
              <input type="number" min={0} max={MAX_PIN} step={1} value={fields.pinA} onChange={(e) => setField('pinA', e.target.value)} style={inputStyle} />
            </Field>
            <Field label={`lift pin B (0..${MAX_PIN})`}>
              <input type="number" min={0} max={MAX_PIN} step={1} value={fields.pinB} onChange={(e) => setField('pinB', e.target.value)} style={inputStyle} />
            </Field>
            <Field label="press_time_ms">
              <input type="number" min={1} step={1} value={fields.pressTimeMs} onChange={(e) => setField('pressTimeMs', e.target.value)} style={inputStyle} />
            </Field>
            <Field label="lift_time_ms">
              <input type="number" min={1} step={1} value={fields.liftTimeMs} onChange={(e) => setField('liftTimeMs', e.target.value)} style={inputStyle} />
            </Field>
          </div>
          <div style={{ fontSize: 12, fontFamily: 'monospace', color: '#6b7280', marginBottom: 6 }}>
            {pinsPreview !== null
              ? `sequence: pin ${pinsPreview.press} ON (press) ${fields.pressTimeMs}ms -> OFF, then pin ${pinsPreview.lift} ON (lift) ${fields.liftTimeMs}ms -> OFF`
              : 'invalid pins (press and lift must be different, 0..' + MAX_PIN + ')'}
          </div>
          <div style={{ fontSize: 12, color: '#166534', background: '#f0fdf4', border: '1px solid #bbf7d0', borderRadius: 6, padding: 6, marginBottom: 6 }}>
            Safety: the whole press-then-lift schedule is ONE packet, timed by the PLC -- even if
            this UI crashes mid-cycle, the PLC still releases the press, runs the lift, and releases
            it on schedule. Press and lift never energize together (press releases before lift).
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
            <button type="button" onClick={onPressLift} disabled={actuationDisabled}>Press + Lift cycle</button>
            <span style={{ width: 12 }} />
            <button
              type="button"
              onClick={() => onManual('press', true)}
              disabled={actuationDisabled}
              style={{ border: '1px solid #dc2626', color: '#dc2626' }}
              title="Holds the press-down output ON until you click Press OFF"
            >
              Press ▼ ON
            </button>
            <button type="button" onClick={() => onManual('press', false)}>Press ▼ OFF</button>
            <span style={{ width: 12 }} />
            <button
              type="button"
              onClick={() => onManual('lift', true)}
              disabled={actuationDisabled}
              style={{ border: '1px solid #dc2626', color: '#dc2626' }}
              title="Holds the lift output ON until you click Lift OFF"
            >
              Lift ▲ ON
            </button>
            <button type="button" onClick={() => onManual('lift', false)}>Lift ▲ OFF</button>
          </div>
          <div style={{ fontSize: 12, color: '#991b1b', marginTop: 6 }}>
            Manual ON has no auto-release -- for physically verifying each solenoid alone. Use the
            matching OFF to release. Note: the PLC latches output bits across FSM state changes, and
            pin ops only dispatch in Ready -- if the FSM drops to Error with an output ON, drive back
            to Ready first, then click OFF.
          </div>
        </Section>

        {/* Bind cycle */}
        <Section title="Bind cycle">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-end', marginBottom: 6 }}>
            <Field label="cycle_count">
              <input type="number" min={1} step={1} value={fields.cycleCount} onChange={(e) => setField('cycleCount', e.target.value)} style={inputStyle} />
            </Field>
            <button type="button" onClick={onRunCycle} disabled={actuationDisabled}>Run bind cycle</button>
            <button
              type="button"
              onClick={onAbort}
              disabled={!cycleRunning}
              style={{ border: '1px solid #dc2626', color: cycleRunning ? '#dc2626' : undefined, fontWeight: 700 }}
            >
              Abort
            </button>
          </div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>
            Per cycle: pull reel → wait reel settle → timed press → wait press_time + margin → next.
            Abort stops the loop and sends a press-OFF pin op; the reel stays where it is.
          </div>
        </Section>
      </div>

      {/* Log panel */}
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, background: '#ffffff', padding: 10, fontSize: 12, fontFamily: 'monospace', height: 'fit-content', maxHeight: '80vh', overflowY: 'auto' }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Activity log</div>
        {log.length === 0 && <div style={{ color: '#9ca3af' }}>(empty)</div>}
        {log.map((e, i) => (
          <div key={i} style={{ marginBottom: 4, color: e.warn ? '#dc2626' : undefined }}>
            <span style={{ color: '#6b7280' }}>{new Date(e.ts).toLocaleTimeString()}</span>{' '}
            <span>{e.line}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  width: 110, padding: 4, fontSize: 12, fontFamily: 'monospace',
  border: '1px solid #d1d5db', borderRadius: 6,
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, background: '#ffffff', padding: 12 }}>
    <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>{title}</div>
    {children}
  </div>
);

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <label style={{ display: 'flex', flexDirection: 'column', gap: 2, fontSize: 11, color: '#6b7280' }}>
    {label}
    {children}
  </label>
);
