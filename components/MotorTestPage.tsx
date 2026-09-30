// MotorTestPage -- the motors' settings as the PLC has them, and a limit
// test for the A axis (open-loop stepper: a lost step is not seen by the
// PLC, so the operator checks A against a mark after each test).
//
// Logic in lib/motors.ts. Limits set here are run-time only (SYS
// SET_AXIS_LIMITS; a download or PLC restart brings back the configured
// ones); make chosen values permanent in codesys_scripts/jobs/templates/
// set_axis_limits.py. Setting limits resets the FSM (the group takes them
// when enabled). Only A moves in the test; the arm holds its position.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { useHarnessAction } from '../harness/registry';
import {
  A_AXIS, MOTORS, applyLimits, readAxis, runALadder, runATest, toAxisUnits, toShowUnits, turnsPerUnit,
  type AxisInfo, type LadderStep, type Limits, type Send, type TestResult,
} from '../lib/motors';

type Row = { at: number; label: string; limitsShow: Limits; result?: TestResult; verdict?: string; error?: string };

const fmt = (x: number | undefined, d = 0) => (x === undefined || !Number.isFinite(x) ? '-' : x.toFixed(d));
const AXIS_STATE = ['power off', 'error stop', 'stand still', 'homing', 'discrete', 'continuous', 'synchronized', 'stopping'];

const cell: React.CSSProperties = { padding: '3px 8px', borderBottom: '1px solid #e5e7eb', textAlign: 'right', whiteSpace: 'nowrap' };
const head: React.CSSProperties = { ...cell, fontWeight: 600, background: '#f3f4f6' };
const left: React.CSSProperties = { ...cell, textAlign: 'left' };
const box: React.CSSProperties = { border: '1px solid #d1d5db', borderRadius: 6, padding: 12, marginBottom: 12, background: '#fff' };
const input: React.CSSProperties = { width: 90, marginLeft: 4, marginRight: 12 };

export const MotorTestPage: React.FC<{ COMCtrlObj: COMCtrlObj }> = ({ COMCtrlObj }) => {
  const [infos, setInfos] = useState<(AxisInfo | undefined)[]>([]);
  const [busy, setBusy] = useState<string>('');
  const [log, setLog] = useState<string[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [lim, setLim] = useState({ v: '', a: '', j: '' });
  const [amp, setAmp] = useState('180');
  const [cycles, setCycles] = useState('20');
  const [factors, setFactors] = useState('1, 1.25, 1.5, 2');
  const [mode, setMode] = useState<'time' | 'all'>('time');
  const [ask, setAsk] = useState<LadderStep | null>(null);
  const answer = useRef<((ok: boolean) => void) | null>(null);

  const addLog = useCallback((s: string) => {
    setLog((l) => [...l.slice(-200), `${new Date().toLocaleTimeString()}  ${s}`]);
  }, []);

  const send: Send = useCallback(async (pkt: any, timeoutMs?: number) => {
    const r = COMCtrlObj.sendTcpMsgPack(pkt, true, timeoutMs ?? 5000);
    if (typeof r === 'boolean') throw new Error('PLC not connected');
    const rep: any = await r;
    if (rep?.ack === false) throw new Error(`${pkt?.cmd}: ${rep?.err ?? 'NAK'}`);
    return rep;
  }, [COMCtrlObj.sendTcpMsgPack]);

  const refresh = useCallback(async (resetPeaks = false) => {
    const out: (AxisInfo | undefined)[] = [];
    for (const m of MOTORS) {
      try { out[m.axis] = await readAxis(send, m.axis, resetPeaks && m.axis === 0); }
      catch (e: any) { addLog(`AXIS_INFO ${m.name}: ${e?.message ?? e}`); }
    }
    setInfos(out);
    return out;
  }, [send, addLog]);

  // Prefill the A test limits (degrees) from the PLC once.
  useEffect(() => {
    const a = infos[A_AXIS];
    if (a && lim.v === '') {
      const s = toShowUnits(MOTORS[A_AXIS], { v: a.lv, a: a.la, d: a.ld, j: a.lj });
      setLim({ v: String(Math.round(s.v)), a: String(Math.round(s.a)), j: String(Math.round(s.j)) });
    }
  }, [infos, lim.v]);

  const guarded = useCallback(async <T,>(what: string, fn: () => Promise<T>): Promise<T | undefined> => {
    if (busy) { addLog(`busy (${busy}): ${what} ignored`); return undefined; }
    setBusy(what);
    try { return await fn(); }
    catch (e: any) { addLog(`${what} failed: ${e?.message ?? e}`); throw e; }
    finally { setBusy(''); refresh().catch(() => {}); }
  }, [busy, addLog, refresh]);

  const limitsFromInputs = (): Limits => {
    const v = Number(lim.v), a = Number(lim.a), j = Number(lim.j);
    if (!(v > 0 && a > 0 && j > 0)) throw new Error('limits must be > 0');
    return { v, a, d: a, j };
  };
  const testOpts = () => {
    const amplitude = Number(amp), n = Math.round(Number(cycles));
    if (!(amplitude > 0 && amplitude <= 1000) || !(n >= 1 && n <= 500)) throw new Error('amplitude 0-1000 deg, cycles 1-500');
    return { amplitude, cycles: n };
  };

  const doApply = () => guarded('apply limits', async () => {
    const l = limitsFromInputs();
    const r = await applyLimits(send, A_AXIS, toAxisUnits(MOTORS[A_AXIS], l));
    addLog(`A limits set (u): v ${r.v} a ${r.a} d ${r.d} j ${r.j}; the FSM is in UnInited`);
    return r;
  });
  const doRestore = () => guarded('restore limits', async () => {
    const r = await applyLimits(send, A_AXIS, 'restore');
    addLog(`A limits restored (u): v ${r.v} a ${r.a} j ${r.j}`);
    return r;
  });
  const doTest = () => guarded('A test', async () => {
    const l = limitsFromInputs();
    await applyLimits(send, A_AXIS, toAxisUnits(MOTORS[A_AXIS], l));
    const res = await runATest(send, testOpts(), addLog);
    setRows((r) => [...r, { at: Date.now(), label: 'single', limitsShow: l, result: res }]);
    return res;
  });
  const confirmStep = (s: LadderStep) => new Promise<boolean>((resolve) => {
    setAsk(s);
    answer.current = (ok) => { setAsk(null); answer.current = null; resolve(ok); };
  });
  const doLadder = (auto?: (s: LadderStep, k: number) => boolean) => guarded('A ladder', async () => {
    const fs = factors.split(/[ ,]+/).map(Number).filter((f) => f > 0);
    if (fs.length === 0) throw new Error('no factors');
    let k = 0;
    const steps = await runALadder(send, {
      base: limitsFromInputs(), factors: fs, mode, test: testOpts(), log: addLog,
      confirm: auto ? async (s) => auto(s, k++) : confirmStep,
    });
    setRows((r) => [...r, ...steps.filter((s) => s.verdict).map((s) => ({
      at: Date.now(), label: `x${s.factor} (${mode})`, limitsShow: s.limitsShow, result: s.result, verdict: s.verdict, error: s.error,
    }))]);
    return steps;
  });

  useEffect(() => { refresh().catch(() => {}); }, [refresh]);

  // Harness (remote test driver): the same actions, answers given up front.
  useHarnessAction('motors_info', async () => await refresh(), [refresh]);
  useHarnessAction('motors_a_test', async (p: any) => {
    if (p?.v) setLim({ v: String(p.v), a: String(p.a), j: String(p.j) });
    const l = p?.v ? { v: +p.v, a: +p.a, d: +p.a, j: +p.j } : limitsFromInputs();
    await applyLimits(send, A_AXIS, toAxisUnits(MOTORS[A_AXIS], l));
    return await runATest(send, { amplitude: +(p?.amplitude ?? 180), cycles: +(p?.cycles ?? 5) }, addLog);
  }, [send, addLog, lim]);
  useHarnessAction('motors_restore', async () => await applyLimits(send, A_AXIS, 'restore'), [send]);

  const a = infos[A_AXIS];
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: 8 }}>
      <div style={{ flex: 3, minWidth: 0 }}>
        <div style={box}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <b>Motors (as the PLC has them)</b>
            <button disabled={!!busy} onClick={() => refresh().catch(() => {})}>Refresh</button>
            <button disabled={!!busy} onClick={() => refresh(true).catch(() => {})}>Reset peaks</button>
            <span style={{ color: '#6b7280', fontSize: 12 }}>limits in force / downloaded; peaks of the set values since the reset</span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
              <thead><tr>
                <th style={{ ...head, textAlign: 'left' }}>axis</th><th style={head}>mode</th><th style={head}>state</th>
                <th style={head}>position</th><th style={head}>unit / motor turn</th>
                <th style={head}>max vel</th><th style={head}>= motor rpm</th><th style={head}>max acc</th><th style={head}>max jerk</th>
                <th style={head}>peak vel</th><th style={head}>peak acc</th>
              </tr></thead>
              <tbody>
                {MOTORS.map((m) => {
                  const i = infos[m.axis];
                  if (!i) return <tr key={m.axis}><td style={left}>{m.label}</td><td style={cell} colSpan={10}>-</td></tr>;
                  const s = m.show.per, u = m.show.unit, tpu = turnsPerUnit(m, i.sf);
                  const changed = i.lv !== i.cv || i.la !== i.ca || i.lj !== i.cj;
                  return (
                    <tr key={m.axis} style={{ background: changed ? '#fef3c7' : undefined }}>
                      <td style={left} title={m.name}>{m.label}</td>
                      <td style={cell}>{i.virt ? 'virtual' : 'real'}{i.err_flag ? ' ERR' : ''}</td>
                      <td style={cell}>{AXIS_STATE[i.st] ?? i.st}</td>
                      <td style={cell}>{fmt(i.pos * s, 2)} {u}</td>
                      <td style={cell}>{fmt(tpu > 0 ? s / tpu : NaN, 2)} {u}</td>
                      <td style={cell}>{fmt(i.lv * s)}{changed ? ` (${fmt(i.cv * s)})` : ''} {u}/s</td>
                      <td style={cell}>{fmt(i.lv * tpu * 60)}</td>
                      <td style={cell}>{fmt(i.la * s)}{changed ? ` (${fmt(i.ca * s)})` : ''} {u}/s²</td>
                      <td style={cell}>{fmt(i.lj * s)}{changed ? ` (${fmt(i.cj * s)})` : ''} {u}/s³</td>
                      <td style={cell}>{fmt(i.pv * s)} ({fmt(i.lv ? (100 * i.pv) / i.lv : NaN)}%)</td>
                      <td style={cell}>{fmt(i.pa * s)} ({fmt(i.la ? (100 * i.pa) / i.la : NaN)}%)</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div style={{ color: '#6b7280', fontSize: 12, marginTop: 6 }}>
            Yellow: run-time limits differ from the downloaded ones (in brackets). A: 1 u = 10 deg (Kin_CAxis /10 wrap), shown in deg.
          </div>
        </div>

        <div style={box}>
          <b>A axis limit test</b>
          <div style={{ color: '#6b7280', fontSize: 12, margin: '4px 0 8px' }}>
            Open-loop stepper: the PLC cannot see a lost step. Put a mark on the nozzle at A = 0 before testing; after each
            test A is back at 0 -- check the mark. Setting limits resets the FSM (motors off), then the test brings it to Ready.
            Only A moves.
          </div>
          <div>
            v<input style={input} value={lim.v} onChange={(e) => setLim({ ...lim, v: e.target.value })} />deg/s
            {'  '}a<input style={input} value={lim.a} onChange={(e) => setLim({ ...lim, a: e.target.value })} />deg/s²
            {'  '}j<input style={input} value={lim.j} onChange={(e) => setLim({ ...lim, j: e.target.value })} />deg/s³
          </div>
          <div style={{ marginTop: 6 }}>
            swing<input style={input} value={amp} onChange={(e) => setAmp(e.target.value)} />deg
            cycles<input style={input} value={cycles} onChange={(e) => setCycles(e.target.value)} />
            {a && <span style={{ color: '#6b7280', fontSize: 12 }}>motor at v: {fmt((Number(lim.v) / 360) * 60)} rpm, {fmt(Number(lim.v) / 360, 1)} turns/s</span>}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button disabled={!!busy} onClick={() => doTest().catch(() => {})}>Run test at these limits</button>
            <button disabled={!!busy} onClick={() => doApply().catch(() => {})}>Only set limits</button>
            <button disabled={!!busy} onClick={() => doRestore().catch(() => {})}>Restore downloaded limits</button>
          </div>
          <div style={{ marginTop: 10 }}>
            ladder factors<input style={{ ...input, width: 160 }} value={factors} onChange={(e) => setFactors(e.target.value)} />
            <select value={mode} onChange={(e) => setMode(e.target.value as 'time' | 'all')}>
              <option value="time">time scale: v x f, a x f², j x f³</option>
              <option value="all">all limits x f</option>
            </select>
            <button style={{ marginLeft: 8 }} disabled={!!busy} onClick={() => doLadder().catch(() => {})}>Run ladder</button>
          </div>
          {ask && (
            <div style={{ marginTop: 10, padding: 10, background: '#eff6ff', border: '1px solid #93c5fd', borderRadius: 6 }}>
              Step x{ask.factor} done ({ask.result?.moves} moves, {fmt(ask.result?.perMoveS, 3)} s each).
              Is A back on its 0 mark?
              <button style={{ marginLeft: 8 }} onClick={() => answer.current?.(true)}>Yes, on the mark</button>
              <button style={{ marginLeft: 8 }} onClick={() => answer.current?.(false)}>No, steps lost</button>
            </div>
          )}
          {busy && <div style={{ marginTop: 8, color: '#b45309' }}>running: {busy}</div>}
        </div>

        <div style={box}>
          <b>Results</b>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>
            <thead><tr>
              <th style={{ ...head, textAlign: 'left' }}>run</th><th style={head}>v deg/s</th><th style={head}>a deg/s²</th><th style={head}>j deg/s³</th>
              <th style={head}>moves</th><th style={head}>s / move</th><th style={head}>peak v %</th><th style={head}>peak a %</th><th style={head}>A on mark</th>
            </tr></thead>
            <tbody>
              {rows.map((r, k) => (
                <tr key={k}>
                  <td style={left}>{new Date(r.at).toLocaleTimeString()} {r.label}</td>
                  <td style={cell}>{fmt(r.limitsShow.v)}</td><td style={cell}>{fmt(r.limitsShow.a)}</td><td style={cell}>{fmt(r.limitsShow.j)}</td>
                  <td style={cell}>{r.result?.moves ?? '-'}</td><td style={cell}>{fmt(r.result?.perMoveS, 3)}</td>
                  <td style={cell}>{fmt(r.result?.peakPct.v)}</td><td style={cell}>{fmt(r.result?.peakPct.a)}</td>
                  <td style={cell}>{r.error ? `error: ${r.error}` : r.verdict ?? '(check)'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ color: '#6b7280', fontSize: 12, marginTop: 6 }}>
            To keep a setting: set_axis_limits.py, SM_Drive_GenericDSP402 in u (deg / 10), then download.
          </div>
        </div>
      </div>
      <div style={{ flex: 2, minWidth: 0, ...box, fontFamily: 'monospace', fontSize: 11, maxHeight: 640, overflowY: 'auto', whiteSpace: 'pre-wrap' }}>
        {log.join('\n') || 'log'}
      </div>
    </div>
  );
};
