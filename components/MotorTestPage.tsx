// MotorTestPage -- the motors' settings as the PLC has them, and a limit
// test for the A axis (open-loop stepper: a lost step is not seen by the
// PLC, so the operator checks A against a mark after each test).
//
// Logic in lib/motors.ts. Limits set here are run-time only (SYS
// SET_AXIS_LIMITS; a download or PLC restart brings back the configured
// ones); make chosen values permanent in codesys_scripts/jobs/templates/
// set_axis_limits.py. Setting limits resets the FSM (the group takes them
// when enabled). Only A moves in the test; the arm holds its position.

import React, { useCallback, useEffect, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { useHarnessAction } from '../harness/registry';
import { InputMonitor } from './InputMonitor';
import { JointPanel } from './JointPanel';
import {
  A_AXIS, MOTORS, applyLimits, readAxis, runATest, scaleLimits, toAxisUnits, toShowUnits, turnsPerUnit,
  type AxisInfo, type Limits, type Send, type TestResult,
} from '../lib/motors';

type Row = { at: number; label: string; limitsShow: Limits; result?: TestResult; error?: string };

const fmt = (x: number | undefined, d = 0) => (x === undefined || !Number.isFinite(x) ? '-' : x.toFixed(d));
const AXIS_STATE = ['power off', 'error stop', 'stand still', 'homing', 'discrete', 'continuous', 'synchronized', 'stopping'];

const cell: React.CSSProperties = { padding: '3px 8px', borderBottom: '1px solid #e5e7eb', textAlign: 'right', whiteSpace: 'nowrap' };
const head: React.CSSProperties = { ...cell, fontWeight: 600, background: '#f3f4f6' };
const left: React.CSSProperties = { ...cell, textAlign: 'left' };
const box: React.CSSProperties = { border: '1px solid #d1d5db', borderRadius: 6, padding: 12, marginBottom: 12, background: '#fff' };
const SWING = 180;      // deg per move
const CYCLES = 20;      // back-and-forth pairs per run

export const MotorTestPage: React.FC<{ COMCtrlObj: COMCtrlObj; active?: boolean }> = ({ COMCtrlObj, active = true }) => {
  const [infos, setInfos] = useState<(AxisInfo | undefined)[]>([]);
  const [busy, setBusy] = useState<string>('');
  const [log, setLog] = useState<string[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [factor, setFactor] = useState(1);
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

  const guarded = useCallback(async <T,>(what: string, fn: () => Promise<T>): Promise<T | undefined> => {
    if (busy) { addLog(`busy (${busy}): ${what} ignored`); return undefined; }
    setBusy(what);
    try { return await fn(); }
    catch (e: any) { addLog(`${what} failed: ${e?.message ?? e}`); throw e; }
    finally { setBusy(''); refresh().catch(() => {}); }
  }, [busy, addLog, refresh]);

  // The downloaded A limits in degrees, scaled by the slider's factor as
  // the same motion f times faster (v x f, a x f^2, j x f^3).
  const aBase = (i: AxisInfo | undefined): Limits | undefined =>
    i ? toShowUnits(MOTORS[A_AXIS], { v: i.cv, a: i.ca, d: i.cd, j: i.cj }) : undefined;
  const aLimits = (f: number, i = infos[A_AXIS]) => {
    const base = aBase(i);
    return base ? scaleLimits(base, f, 'time') : undefined;
  };
  const doRun = (f: number) => guarded(`A test x${f}`, async () => {
    const i = infos[A_AXIS] ?? await readAxis(send, A_AXIS);
    const l = aLimits(f, i);
    if (!l) throw new Error('A limits unknown');
    await applyLimits(send, A_AXIS, toAxisUnits(MOTORS[A_AXIS], l));
    addLog(`x${f}: v ${fmt(l.v)} deg/s (${fmt(l.v / 6)} rpm), a ${fmt(l.a)} deg/s², j ${fmt(l.j)} deg/s³`);
    const res = await runATest(send, { amplitude: SWING, cycles: CYCLES }, addLog);
    setRows((r) => [...r, { at: Date.now(), label: `x${f}`, limitsShow: l, result: res }]);
    return res;
  });

  useEffect(() => { refresh().catch(() => {}); }, [refresh]);

  // Harness (remote test driver): the same actions, answers given up front.
  useHarnessAction('motors_info', async () => await refresh(), [refresh]);
  useHarnessAction('motors_a_test', async (p: any) => await doRun(Number(p?.factor ?? 1)), [doRun]);
  // Test driver: any PLC command through this page's link (JOINT_* etc.).
  useHarnessAction('plc_send', async (p: any) => await send(p?.pkt, p?.timeoutMs ?? 5000), [send]);
  useHarnessAction('io_state', async (p: any) => await send({ type: 'SYS', cmd: 'IO_STATE', ...(p?.reset ? { reset: 1 } : {}) }), [send]);
  useHarnessAction('motors_restore', async () => await applyLimits(send, A_AXIS, 'restore'), [send]);

  const a = infos[A_AXIS];
  return (
    <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: 8 }}>
      <div style={{ flex: 3, minWidth: 0 }}>
        <div style={box}>
          <InputMonitor send={send} active={active} />
        </div>
        <div style={box}>
          <JointPanel send={send} active={active} log={addLog} />
        </div>
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
            Factor on the downloaded limits (same motion f times faster: v x f, a x f², j x f³). Run: sets the limits
            (FSM reset, motors off), brings the FSM to Ready, swings A {SWING} deg back and forth {CYCLES} times and stops
            at 0 -- check the mark. Only A moves.
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <input type="range" min={0.5} max={3} step={0.05} value={factor} style={{ width: 320 }}
              onChange={(e) => setFactor(Number(e.target.value))} disabled={!!busy} />
            <b style={{ fontSize: 18, fontVariantNumeric: 'tabular-nums', width: 60 }}>x{factor.toFixed(2)}</b>
            <button style={{ fontSize: 15, padding: '4px 18px' }} disabled={!!busy} onClick={() => doRun(factor).catch(() => {})}>Run</button>
          </div>
          {(() => {
            const l = aLimits(factor);
            return l && (
              <div style={{ marginTop: 6, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                v {fmt(l.v)} deg/s ({fmt(l.v / 6)} rpm) &nbsp; a {fmt(l.a)} deg/s² &nbsp; j {fmt(l.j)} deg/s³
              </div>
            );
          })()}
          {busy && <div style={{ marginTop: 8, color: '#b45309' }}>running: {busy}</div>}
        </div>

        <div style={box}>
          <b>Results</b>
          <table style={{ borderCollapse: 'collapse', fontSize: 12, marginTop: 6, fontVariantNumeric: 'tabular-nums' }}>
            <thead><tr>
              <th style={{ ...head, textAlign: 'left' }}>run</th><th style={head}>v deg/s</th><th style={head}>a deg/s²</th><th style={head}>j deg/s³</th>
              <th style={head}>moves</th><th style={head}>s / move</th><th style={head}>peak v %</th><th style={head}>peak a %</th><th style={head}>note</th>
            </tr></thead>
            <tbody>
              {rows.map((r, k) => (
                <tr key={k}>
                  <td style={left}>{new Date(r.at).toLocaleTimeString()} {r.label}</td>
                  <td style={cell}>{fmt(r.limitsShow.v)}</td><td style={cell}>{fmt(r.limitsShow.a)}</td><td style={cell}>{fmt(r.limitsShow.j)}</td>
                  <td style={cell}>{r.result?.moves ?? '-'}</td><td style={cell}>{fmt(r.result?.perMoveS, 3)}</td>
                  <td style={cell}>{fmt(r.result?.peakPct.v)}</td><td style={cell}>{fmt(r.result?.peakPct.a)}</td>
                  <td style={cell}>{r.error ? `error: ${r.error}` : ''}</td>
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
