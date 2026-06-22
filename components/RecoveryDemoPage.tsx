// RecoveryDemoPage -- standalone demo for the §4 (2) crash-and-resume
// flow (decisions_2026-06-22.md). Keeps the existing CalibPage main
// loop untouched while we validate the orchestrator/resume.ts surface
// end-to-end against a live PLC.
//
// Three concerns, mapped to UI sections:
//   1. live machine state (1Hz poll of GET_MACHINE_STATE)
//   2. plan persistence + scratchpad write-ahead actions
//   3. reconcileOnResume + planResumeAction read-out
//
// Not for production -- this page issues real G1 moves with conservative
// Z deltas so a real movement_id flows through writeIntent(). On real
// hardware verify the arm is clear before clicking action buttons.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { cmd, type MachineState, type G1Reply } from '../lib/protocol';
import {
  IntentKind,
  loadPlan,
  savePlan,
  clearPlan,
  writeIntent,
  writeScratchpad,
  reconcileOnResume,
  planResumeAction,
  type PersistedPlan,
  type ResumeDecision,
  type ResumeAction,
} from '../orchestrator/resume';

const EV_POWER_ON = 2;
const EV_GROUP_ENABLE = 4;
const EV_HOME_GO = 6;
const EV_HOME_GO_FORCE_SKIP = 7;
const EV_RESET = 8;

const POLL_MS = 1000;

type LogEntry = { ts: number; line: string };

const fmtNum = (n: number | undefined | null) => (n === undefined || n === null ? '—' : n.toString());

export const RecoveryDemoPage: React.FC<{ COMCtrlObj: COMCtrlObj }> = ({ COMCtrlObj }) => {
  const send = COMCtrlObj.sendTcpMsgPack;
  const [snap, setSnap] = useState<MachineState | null>(null);
  const [snapErr, setSnapErr] = useState<string | null>(null);
  const [plan, setPlan] = useState<PersistedPlan | null>(() => loadPlan());
  const [planText, setPlanText] = useState<string>(() => {
    const p = loadPlan();
    return p ? p.plan.join(', ') : '1, -30, 445, -20, 1';
  });
  const [visionSeen, setVisionSeen] = useState<boolean>(true);
  const [decision, setDecision] = useState<ResumeDecision | null>(null);
  const [action, setAction] = useState<ResumeAction | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [busy, setBusy] = useState(false);

  const appendLog = useCallback((line: string) => {
    setLog((prev) => [{ ts: Date.now(), line }, ...prev].slice(0, 40));
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

  // ── helpers ───────────────────────────────────────────────────────
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
      appendLog(`${label} threw: ${e?.message ?? e}`);
      return null;
    }
  }, [send, appendLog]);

  const sendEv = useCallback(async (ev: number, label: string) => {
    setBusy(true);
    try {
      await sendTracked(cmd.GA_EV(ev), `GA_EV(${label})`);
    } finally {
      setBusy(false);
    }
  }, [sendTracked]);

  // Drive FSM in stages, aborting between stages if state hasn't
  // progressed -- so a wedged Homing transition doesn't drag the next
  // step into the swamp. Bounded poll for state advance between
  // events. If `stopAtGroupEnabled` is true, omit HOME_FSK -- useful
  // when the homing FB itself is broken on this rig and we just need
  // a state high enough to verify other things.
  const driveToReady = useCallback(async (stopAtGroupEnabled: boolean) => {
    setBusy(true);
    try {
      const steps: Array<[number, string, number]> = [
        [EV_RESET,        'RESET',        10],   // -> 10 UnInited
        [EV_POWER_ON,     'POWER_ON',     30],   // -> 30 Powered (via 20)
        [EV_GROUP_ENABLE, 'GROUP_ENABLE', 50],   // -> 50 GroupEnabled (via 40)
      ];
      if (!stopAtGroupEnabled) steps.push([EV_HOME_GO_FORCE_SKIP, 'HOME_GO_FORCE_SKIP', 70]);
      for (const [ev, label, target] of steps) {
        await sendTracked(cmd.GA_EV(ev), `GA_EV(${label})`);
        // Poll for the target state. Bail out at 3s -- typical
        // transitions complete in <300ms; if we're still not there
        // after 3s something is wedged.
        const reached = await pollUntilState(target);
        if (!reached) {
          appendLog(`drive aborted: ${label} did not advance to st=${target} within 3s`);
          return;
        }
      }
      if (!stopAtGroupEnabled) {
        await sendTracked(cmd.SetCoord0(), 'SetCoord0');
      }
    } finally {
      setBusy(false);
    }
  }, [sendTracked, appendLog]);

  const pollUntilState = useCallback(async (target: number, timeoutMs = 3000): Promise<boolean> => {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const r = await send(cmd.GetMachineState(), true, 1000);
      if (r && typeof r === 'object' && (r as any).st === target) return true;
      await new Promise((res) => setTimeout(res, 200));
    }
    return false;
  }, [send]);

  // ── plan persistence ──────────────────────────────────────────────
  const onSavePlan = useCallback(() => {
    const parts = planText
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => Number(s));
    if (parts.some((n) => Number.isNaN(n))) {
      appendLog('plan parse error: non-numeric entries');
      return;
    }
    const next = savePlan(parts, plan);
    setPlan(next);
    appendLog(`plan saved: id=${next.plan_id} len=${parts.length}`);
    // Stamp the new plan_id into PLC scratchpad immediately so the
    // disk-vs-scratchpad badge goes green. intent_kind=Idle,
    // intent_movement_id=0 -- "plan loaded, nothing in flight yet".
    // Best-effort: if PLC is unreachable the disk side is still saved
    // and the next reconcile will detect the mismatch correctly.
    (async () => {
      try {
        const ok = await writeScratchpad(send, {
          plan_id: next.plan_id,
          plan_index: 0,
          intent_kind: IntentKind.Idle,
          intent_movement_id: 0,
          last_vision_pulse: 0,
        });
        appendLog(`scratchpad stamped with new plan_id=${next.plan_id} -> ack=${ok}`);
      } catch (e: any) {
        appendLog(`scratchpad stamp failed (disk save still ok): ${e?.message ?? e}`);
      }
    })();
  }, [planText, plan, appendLog, send]);

  const onClearPlan = useCallback(() => {
    clearPlan();
    setPlan(null);
    appendLog('plan cleared');
  }, [appendLog]);

  // ── write-ahead actions ───────────────────────────────────────────
  // Contract per resume.ts writeIntent docstring:
  //   1. issue motion command, await ack
  //   2. read reply.movement_id
  //   3. writeIntent(... intent_movement_id = reply.movement_id)
  // Each handler implements that 3-step shape so the demo matches the
  // documented timing exactly. Production code must do the same.
  const doActionWithMotion = useCallback(async (kind: number, label: string, deltaZ: number) => {
    if (!plan) { appendLog('no plan saved; save one first'); return; }
    if (!snap || snap.st !== 70 || !snap.coord_set) {
      appendLog('not Ready or coord_set=false; drive FSM first');
      return;
    }
    setBusy(true);
    try {
      const r = await sendTracked<G1Reply>(
        cmd.G1({ Z: deltaZ, F: 30 }),
        `${label} G1`,
      );
      if (!r || r.ack !== true) {
        appendLog(`${label}: G1 not accepted, skipping intent write`);
        return;
      }
      const mid = r.movement_id ?? 0;
      if (mid === 0) {
        appendLog(`${label}: G1 ack carried no movement_id, skipping intent write`);
        return;
      }
      // STEP 3 -- write intent AFTER ack, with this move's id.
      const ok = await writeIntent(send, {
        plan_id: plan.plan_id,
        plan_index: snap.scratchpad.plan_index,
        intent_kind: kind as any,
        intent_movement_id: mid,
        last_vision_pulse: snap.scratchpad.last_vision_pulse,
      });
      appendLog(`${label}: writeIntent(kind=${kind}, mid=${mid}) -> ack=${ok}`);
    } finally {
      setBusy(false);
    }
  }, [plan, snap, sendTracked, send, appendLog]);

  const doIdleIntent = useCallback(async () => {
    if (!plan) { appendLog('no plan saved; save one first'); return; }
    setBusy(true);
    try {
      const ok = await writeScratchpad(send, {
        plan_id: plan.plan_id,
        plan_index: snap?.scratchpad.plan_index ?? 0,
        intent_kind: IntentKind.Idle,
        intent_movement_id: 0,
        last_vision_pulse: snap?.scratchpad.last_vision_pulse ?? 0,
      });
      appendLog(`idle scratchpad write -> ack=${ok}`);
    } finally {
      setBusy(false);
    }
  }, [plan, snap, send, appendLog]);

  // ── reconcile ─────────────────────────────────────────────────────
  const onReconcile = useCallback(() => {
    if (!snap) { appendLog('no machine snapshot yet'); return; }
    // Re-read plan from disk so the "I just crashed" scenario maps
    // correctly even if local state cached an older plan.
    const fresh = loadPlan();
    setPlan(fresh);
    const d = reconcileOnResume(snap, { vision_result_seen: visionSeen });
    const a = planResumeAction(d);
    setDecision(d);
    setAction(a);
    appendLog(`reconcile -> ${d.kind}${d.kind === 'cold_start' ? ' (' + d.reason + ')' : ''}`);
    appendLog(`planResumeAction -> ${a.kind}`);
  }, [snap, visionSeen, appendLog]);

  // ── plan_id correlation ───────────────────────────────────────────
  const planIdCorrelation = useMemo(() => {
    if (!plan || !snap) return null;
    const same = plan.plan_id === snap.scratchpad.plan_id;
    return same
      ? { label: '✓ disk plan_id matches scratchpad', color: '#16a34a' }
      : { label: `✗ mismatch: disk=${plan.plan_id} scratchpad=${snap.scratchpad.plan_id}`, color: '#dc2626' };
  }, [plan, snap]);

  const epochCorrelation = useMemo(() => {
    if (!snap) return null;
    const same = snap.boot_epoch_now === snap.scratchpad.boot_epoch;
    return same
      ? { label: '✓ boot_epoch matches (no restart since last write)', color: '#16a34a' }
      : { label: `✗ boot_epoch stale: scratchpad=${snap.scratchpad.boot_epoch} now=${snap.boot_epoch_now}`, color: '#dc2626' };
  }, [snap]);

  // ── render ────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 320px', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Banner */}
        <div style={{ padding: 10, background: '#fff7ed', border: '1px solid #f59e0b', borderRadius: 8, color: '#92400e', fontSize: 12 }}>
          <b>Recovery demo page.</b> Sandbox for the §4 (2) scratchpad / write-ahead / resume flow. Action buttons issue real G1 moves (Z ±5 mm). Clear the arm before clicking.
        </div>

        {/* Machine state panel */}
        <Section title="Live machine state (1 Hz)">
          {snapErr && <div style={{ color: '#dc2626', fontSize: 12 }}>err: {snapErr}</div>}
          {!snap && <div style={{ color: '#6b7280' }}>waiting for first snapshot…</div>}
          {snap && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6, fontSize: 12, fontFamily: 'monospace' }}>
              <KV label="st" value={`${snap.st_str} (${snap.st})`} />
              <KV label="motion_buffer_size" value={fmtNum(snap.motion_buffer_size)} />
              <KV label="movement_id" value={fmtNum(snap.movement_id)} />
              <KV label="last_completed_movement_id" value={fmtNum(snap.last_completed_movement_id)} />
              <KV label="coord_set" value={String(snap.coord_set)} />
              <KV label="reel_pos" value={snap.reel_pos.toFixed(3)} />
              <KV label="boot_epoch_now" value={fmtNum(snap.boot_epoch_now)} />
              <KV label="err_src" value={snap.err_src || '(none)'} />
              <KV label="scratchpad.schema_version" value={fmtNum(snap.scratchpad.schema_version)} />
              <KV label="scratchpad.boot_epoch" value={fmtNum(snap.scratchpad.boot_epoch)} />
              <KV label="scratchpad.plan_id" value={fmtNum(snap.scratchpad.plan_id)} />
              <KV label="scratchpad.plan_index" value={fmtNum(snap.scratchpad.plan_index)} />
              <KV label="scratchpad.intent_kind" value={`${intentName(snap.scratchpad.intent_kind)} (${snap.scratchpad.intent_kind})`} />
              <KV label="scratchpad.intent_movement_id" value={fmtNum(snap.scratchpad.intent_movement_id)} />
            </div>
          )}
          <div style={{ marginTop: 8, fontSize: 12 }}>
            {planIdCorrelation && <div style={{ color: planIdCorrelation.color }}>{planIdCorrelation.label}</div>}
            {epochCorrelation && <div style={{ color: epochCorrelation.color }}>{epochCorrelation.label}</div>}
          </div>
        </Section>

        {/* Plan */}
        <Section title="Plan (host disk via localStorage)">
          <textarea
            value={planText}
            onChange={(e) => setPlanText(e.target.value)}
            rows={2}
            style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, padding: 6, border: '1px solid #d1d5db', borderRadius: 6 }}
          />
          <div style={{ marginTop: 6, fontSize: 12, color: '#6b7280' }}>
            saved: {plan ? `id=${plan.plan_id} len=${plan.plan.length} [${plan.plan.join(', ')}]` : '(none)'}
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
            <button type="button" onClick={onSavePlan} disabled={busy}>Save plan</button>
            <button type="button" onClick={onClearPlan} disabled={busy}>Clear plan</button>
          </div>
        </Section>

        {/* FSM helpers */}
        <Section title="FSM helpers">
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>
            Two drive variants. <b>Full walk</b> uses <code>HOME_GO_FORCE_SKIP</code> (ev=7) which bypasses the home routine and jumps straight to Ready -- needed when the rig can't physically home (virtual motors not forced, home sensor unwired, etc.). <b>Stop at GroupEnabled</b> doesn't issue any home event at all -- useful when even FORCE_SKIP wedges. Individual step buttons below let you nudge state manually if the drive aborts.
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
            <button type="button" onClick={() => driveToReady(false)} disabled={busy}>Drive to Ready (RESET → POWER → GROUP → HOME_FORCE_SKIP → SetCoord0)</button>
            <button type="button" onClick={() => driveToReady(true)} disabled={busy}>Drive to GroupEnabled (no home event)</button>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, fontSize: 12 }}>
            <button type="button" onClick={() => sendEv(EV_RESET, 'RESET')} disabled={busy}>RESET (ev=8)</button>
            <button type="button" onClick={() => sendEv(EV_POWER_ON, 'POWER_ON')} disabled={busy}>POWER_ON (ev=2)</button>
            <button type="button" onClick={() => sendEv(EV_GROUP_ENABLE, 'GROUP_ENABLE')} disabled={busy}>GROUP_ENABLE (ev=4)</button>
            <button type="button" onClick={() => sendEv(EV_HOME_GO, 'HOME_GO')} disabled={busy} title="Real homing -- only works if the rig can physically home">HOME_GO (ev=6)</button>
            <button type="button" onClick={() => sendEv(EV_HOME_GO_FORCE_SKIP, 'HOME_GO_FORCE_SKIP')} disabled={busy} title="Bypass home routine, jump to Ready">HOME_GO_FORCE_SKIP (ev=7)</button>
            <button type="button" onClick={() => sendTracked(cmd.SetCoord0(), 'SetCoord0')} disabled={busy}>SetCoord0</button>
          </div>
        </Section>

        {/* Actions */}
        <Section title="Write-ahead actions">
          <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 6 }}>
            Each action: (1) issue G1, (2) await ack, (3) writeIntent with the ack's movement_id. Idle action is scratchpad-only.
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <button type="button" onClick={doIdleIntent} disabled={busy}>Idle (no motion)</button>
            <button type="button" onClick={() => doActionWithMotion(IntentKind.AdvanceReel, 'advance_reel', -5)} disabled={busy}>Advance reel (Z-5)</button>
            <button type="button" onClick={() => doActionWithMotion(IntentKind.Pick, 'pick', +5)} disabled={busy}>Pick (Z+5)</button>
          </div>
        </Section>

        {/* Reconcile */}
        <Section title="Reconcile (simulate resume)">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <label style={{ fontSize: 12 }}>
              <input type="checkbox" checked={visionSeen} onChange={(e) => setVisionSeen(e.target.checked)} /> vision_result_seen
            </label>
            <button type="button" onClick={onReconcile} disabled={busy}>Run reconcile</button>
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap' }}>
            {decision ? `decision: ${JSON.stringify(decision, null, 2)}` : '(not run yet)'}
          </div>
          <div style={{ fontFamily: 'monospace', fontSize: 12, whiteSpace: 'pre-wrap', marginTop: 6 }}>
            {action ? `action: ${JSON.stringify(action, null, 2)}` : ''}
          </div>
        </Section>
      </div>

      {/* Log panel */}
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, background: '#ffffff', padding: 10, fontSize: 12, fontFamily: 'monospace', height: 'fit-content', maxHeight: '80vh', overflowY: 'auto' }}>
        <div style={{ fontWeight: 700, marginBottom: 6 }}>Activity log</div>
        {log.length === 0 && <div style={{ color: '#9ca3af' }}>(empty)</div>}
        {log.map((e, i) => (
          <div key={i} style={{ marginBottom: 4 }}>
            <span style={{ color: '#6b7280' }}>{new Date(e.ts).toLocaleTimeString()}</span>{' '}
            <span>{e.line}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div style={{ border: '1px solid #e5e7eb', borderRadius: 12, background: '#ffffff', padding: 12 }}>
    <div style={{ fontWeight: 700, marginBottom: 8, fontSize: 13 }}>{title}</div>
    {children}
  </div>
);

const KV: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, padding: '2px 6px', background: '#f9fafb', borderRadius: 4 }}>
    <span style={{ color: '#6b7280' }}>{label}</span>
    <span>{value}</span>
  </div>
);

function intentName(k: number): string {
  switch (k) {
    case IntentKind.Idle: return 'idle';
    case IntentKind.AdvanceReel: return 'advance_reel';
    case IntentKind.Pick: return 'pick';
    case IntentKind.BowlBack: return 'bowl_back';
    default: return 'unknown';
  }
}
