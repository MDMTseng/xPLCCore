// PathTestPanel -- run the delta test paths from the UI and watch the
// drives' shock numbers live (stale-target investigation,
// doc_review/asda_stale_target_2026-09-30.md 7j).
//
// Paths (lib/pathTests.ts): "round" (4-point square, corners 49 mm, nearly
// a circle: the shock test), "square dip" (corners with dips to -15) and
// "Z up/down" (X0 Y0, Z 0 <-> depth with exact stops).
// Speed in % of F 2000 mm/s. The stream runs for the set time (0 = until
// Stop), then the arm goes back to X0 Y0 Z0. Needs the FSM in Ready; with a
// real delta the operator confirms the site is clear for each run.
//
// Delta mode: Virtual / Real switches the delta trio for this PLC run (SYS
// DELTA_MODE, UnInited only; Real needs the "site is clear" tick, which
// the PLC also checks). The FSM is reset (drives off) first, then the
// panel waits for the drives to flip (axes_sim_mask). A download or PLC
// restart brings back the project's virtual delta. Power off resets the
// FSM (all drives off).
//
// Numbers (PLC SYS FB_STATS / DEM_STATS, reset at Start): per drive the
// share of cycles the drive used a one-cycle-old target, the largest torque
// change in one cycle (% rated) and how many exceeded 20 % / 50 %.

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { buildPath, kinAt, lapsFor, parseStats, type AxisStats, type PathKind } from '../lib/pathTests';
import { streamPackets, type StreamState } from '../lib/stream';
import { withFsmLock } from '../lib/fsmLock';
import type { Send } from '../lib/motors';
import { useHarnessAction } from '../harness/registry';

const cell: React.CSSProperties = { padding: '3px 8px', borderBottom: '1px solid #e5e7eb', textAlign: 'right', whiteSpace: 'nowrap' };
const head: React.CSSProperties = { ...cell, fontWeight: 600, background: '#f3f4f6' };
const left: React.CSSProperties = { ...cell, textAlign: 'left' };
const fmt = (x: number, d = 1) => (Number.isFinite(x) ? x.toFixed(d) : '-');
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
const EV = { POWER_ON: 2, GROUP_ENABLE: 4, HOME_GO: 6, RESET: 8 };
const STATIONS = [1003, 1004, 1005];

async function readDriveParam(send: Send, station: number, index: number): Promise<number | undefined> {
  const r = await send({ type: 'SYS', cmd: 'DRV_SDO', station, index, sub: 0, size: 4 });
  const seq = r?.seq_req;
  for (let i = 0; i < 40; i++) {
    const q = await send({ type: 'SYS', cmd: 'DRV_SDO_RESULT' });
    if (!q?.active && q?.seq_res === seq) return q?.ok ? Number(q.value) : undefined;
    await delay(150);
  }
  return undefined;
}

export const PathTestPanel: React.FC<{
  send: Send;
  active: boolean;
  log: (s: string) => void;
  stream: { current: StreamState };
  abort: { current: boolean };
}> = ({ send, active, log, stream, abort }) => {
  const [kind, setKind] = useState<PathKind>('round');
  const [speed, setSpeed] = useState(70);
  const [seconds, setSeconds] = useState(60);
  const [fsm, setFsm] = useState('');
  const [fsmErr, setFsmErr] = useState('');
  const [depth, setDepth] = useState(15);
  const [real, setReal] = useState<boolean | undefined>(undefined);
  const [confirm, setConfirm] = useState(false);
  const [running, setRunning] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [stats, setStats] = useState<AxisStats[]>([]);
  const [filters, setFilters] = useState<string>('');
  const [results, setResults] = useState<{ at: number; label: string; s: AxisStats[] }[]>([]);
  const stopReq = useRef(false);

  const readState = useCallback(async () => {
    try {
      const g = await send({ type: 'SYS', cmd: 'GA_EV', ev: 0 });
      setFsm(String(g?.st_str ?? ''));
      setFsmErr(g?.err_src ? `${g.err_src}${g.err_id ? ` (${g.err_id})` : ''}` : '');
      const m = await send({ type: 'SYS', cmd: 'GET_MACHINE_STATE' });
      setReal(((Number(m?.axes_sim_mask ?? 7) & 7) !== 7));
    } catch { /* not connected */ }
  }, [send]);

  const readStats = useCallback(async () => {
    const fb = await send({ type: 'SYS', cmd: 'FB_STATS' });
    const dem = await send({ type: 'SYS', cmd: 'DEM_STATS' });
    const s = parseStats(fb, dem);
    setStats(s);
    return s;
  }, [send]);

  useEffect(() => {
    if (!active) return;
    readState();
    const t = setInterval(() => { if (!running) readState(); }, 2000);
    return () => clearInterval(t);
  }, [active, running, readState]);

  const toReady = async () => {
    setRunning('homing');
    try {
      await withFsmLock('Motors path test', async () => {
      const end = Date.now() + 120000;
      for (;;) {
        const rep: any = await send({ type: 'SYS', cmd: 'GA_EV', ev: 0 });
        const st = String(rep?.st_str ?? '');
        setFsm(st);
        if (st === 'Ready') break;
        if (Date.now() > end) throw new Error(`FSM did not reach Ready (${st})`);
        const ev = ({ UnInited: EV.POWER_ON, Powered: EV.GROUP_ENABLE, GroupEnabled: EV.HOME_GO, Error: EV.RESET } as Record<string, number>)[st];
        // expect_st: refused (state_changed) if the FSM moved on meanwhile
        if (ev !== undefined) { try { await send({ type: 'SYS', cmd: 'GA_EV', ev, expect_st: rep?.st }); } catch { /* shows in the state */ } }
        await delay(400);
      }
      });
      log('FSM Ready');
    } catch (e: any) {
      log(`to Ready failed: ${e?.message ?? e}`);
    } finally {
      setRunning('');
    }
  };

  const toUnInited = async () => {
    const end = Date.now() + 20000;
    for (;;) {
      const st = String((await send({ type: 'SYS', cmd: 'GA_EV', ev: 0 }))?.st_str ?? '');
      setFsm(st);
      if (st === 'UnInited') return;
      if (Date.now() > end) throw new Error(`FSM did not reach UnInited (${st})`);
      try { await send({ type: 'SYS', cmd: 'GA_EV', ev: EV.RESET }); } catch { /* shows in the state */ }
      await delay(400);
    }
  };

  const powerOff = async () => {
    setRunning('power off');
    try { await toUnInited(); log('power off: FSM UnInited'); }
    catch (e: any) { log(`power off failed: ${e?.message ?? e}`); }
    finally { setRunning(''); readState(); }
  };

  const setMode = async (wantReal: boolean) => {
    if (wantReal && !confirm) { log('tick "the site is clear" first'); return; }
    setRunning(wantReal ? 'switching to REAL' : 'switching to virtual');
    try {
      await toUnInited();
      const pkt: any = { type: 'SYS', cmd: 'DELTA_MODE', real: wantReal ? 1 : 0 };
      if (wantReal) {
        pkt.site_clear = 1;
        // the PLC's maintenance gate (2026-10-05): the ticked "site is
        // clear" box is the operator's intent; it closes again after
        // 10 min, on disconnect or on Error
        await send({ type: 'SYS', cmd: 'MAINT_ARM', ttl_s: 600 });
      }
      for (let i = 0; ; i++) {
        try { await send(pkt); break; }
        catch (e: any) {
          if (!String(e?.message ?? e).includes('sim_apply_busy') || i > 30) throw e;
          await delay(1000);
        }
      }
      const end = Date.now() + 40000;
      for (;;) {
        const m = await send({ type: 'SYS', cmd: 'GET_MACHINE_STATE' });
        const mask = Number(m?.axes_sim_mask ?? -1) & 7;
        if (mask === (wantReal ? 0 : 7)) break;
        if (Date.now() > end) throw new Error(`drives did not switch (mask ${mask})`);
        await delay(500);
      }
      log(wantReal ? 'delta REAL (for this PLC run)' : 'delta virtual');
    } catch (e: any) {
      log(`delta mode failed: ${e?.message ?? e}`);
    } finally {
      setRunning('');
      setConfirm(false);
      readState();
    }
  };

  const readFilters = async () => {
    try {
      const out: string[] = [];
      for (const [k, st] of STATIONS.entries()) {
        const a = await readDriveParam(send, st, 0x2144);   // P1.068
        const b = await readDriveParam(send, st, 0x2108);   // P1.008
        out.push(`EAxis${k}: P1.068 ${a ?? '?'} ms, P1.008 ${b === undefined ? '?' : b * 10} ms`);
      }
      setFilters(out.join('   '));
    } catch (e: any) {
      setFilters(`read failed: ${e?.message ?? e}`);
    }
  };

  const start = async () => {
    if (running) return;
    const label = `${kind === 'round' ? 'circle' : kind === 'zUpDown' ? `Z 0/-${depth}` : 'square dip'} ${speed}%`;
    stopReq.current = false;
    setRunning(label);
    setElapsed(0);
    try {
      await send({ type: 'SYS', cmd: 'FB_STATS', reset: 1 });
      await send({ type: 'SYS', cmd: 'DEM_STATS', reset: 1 });
      const dur = seconds > 0 ? seconds : 3600;
      const pkts = buildPath(kind, speed, lapsFor(kind, speed, dur), kind === 'zUpDown' ? { dip: -Math.abs(depth) } : {});
      log(`${label}: ${seconds > 0 ? `${seconds} s` : 'until Stop'} (F ${fmt(kinAt(speed).F, 0)} mm/s)`);
      const t0 = Date.now();
      const done = streamPackets(send, pkts, stream, abort);
      let lastStats = 0;
      while (stream.current.running) {
        await delay(250);
        const el = (Date.now() - t0) / 1000;
        setElapsed(el);
        if (stopReq.current || (seconds > 0 && el >= seconds)) abort.current = true;
        if (Date.now() - lastStats > 1000) {
          lastStats = Date.now();
          readStats().catch(() => {});
        }
      }
      const st = await done;
      if (st.err) log(`stream: ${st.err}`);
      await send({ type: 'M', cmd: 'WAIT_FOR_MOTION_STOP', timeout: 30, timeout_ms: 25000 }, 30000);
      const s = await readStats();
      setResults((r) => [...r, { at: Date.now(), label: `${label}, ${fmt((Date.now() - t0) / 1000, 0)} s`, s }]);
      const k = kinAt(Math.min(speed, 10));
      await send({ ...k, type: 'M', cmd: 'G1', X: 0, Y: 0, Z: 0, Cor: 0 });
      await send({ type: 'M', cmd: 'WAIT_FOR_MOTION_STOP', timeout: 30, timeout_ms: 25000 }, 30000);
      log(`${label}: done, back at X0 Y0 Z0`);
    } catch (e: any) {
      abort.current = true;
      log(`${label} failed: ${e?.message ?? e}`);
    } finally {
      setRunning('');
      setConfirm(false);
      readState();
    }
  };

  // Harness (read-only): what the panel shows, to check it is loaded.
  useHarnessAction('path_panel_state', async () => ({ fsm, real, kind, speed, seconds, running, stats }),
    [fsm, real, kind, speed, seconds, running, stats]);

  const canStart = !running && fsm === 'Ready' && !stream.current.running && (real === false || confirm);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <b>Path test (shock)</b>
        <span style={{ color: '#6b7280', fontSize: 12 }}>
          FSM {fsm || '?'} · delta {real === undefined ? '?' : real ? 'REAL' : 'virtual'}
        </span>
        {fsm === 'Error' && fsmErr && <span style={{ color: '#b91c1c', fontSize: 12 }}>error: {fsmErr}
          {fsmErr.includes('11000') ? ' -- group enable refused after a virtual/real switch: needs a download' : ''}</span>}
        <span style={{ marginLeft: 8 }}>Delta:</span>
        <button disabled={!!running || real === false} onClick={() => setMode(false)}
          style={{ fontWeight: real === false ? 700 : 400 }}>Virtual</button>
        <button disabled={!!running || real === true || !confirm} onClick={() => setMode(true)}
          title="tick 'the site is clear' first" style={{ fontWeight: real ? 700 : 400, color: '#b91c1c' }}>Real</button>
        <button disabled={!!running || fsm === 'UnInited'} onClick={powerOff}>Power off</button>
        <button disabled={!!running || fsm === 'Ready'} onClick={toReady}>Home → Ready</button>
        <button disabled={!!running} onClick={readFilters}>Read drive filters</button>
      </div>
      {filters && <div style={{ fontSize: 12, color: '#374151', marginBottom: 6 }}>{filters}</div>}

      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <label><input type="radio" checked={kind === 'round'} disabled={!!running} onChange={() => setKind('round')} /> Circle (shock test)</label>
        <label><input type="radio" checked={kind === 'squareDip'} disabled={!!running} onChange={() => setKind('squareDip')} /> Square with dips</label>
        <label><input type="radio" checked={kind === 'zUpDown'} disabled={!!running} onChange={() => setKind('zUpDown')} /> Z up/down</label>
        {kind === 'zUpDown' && (
          <span>depth <input type="number" min={1} max={30} step={1} value={depth} style={{ width: 50 }} disabled={!!running}
            onChange={(e) => setDepth(Math.min(30, Math.max(1, Number(e.target.value))))} /> mm</span>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
        <span>Speed</span>
        <input type="range" min={5} max={100} step={5} value={speed} style={{ width: 280 }}
          disabled={!!running} onChange={(e) => setSpeed(Number(e.target.value))} />
        <b style={{ fontSize: 18, width: 60, fontVariantNumeric: 'tabular-nums' }}>{speed}%</b>
        <span style={{ color: '#6b7280', fontSize: 12 }}>F {fmt(kinAt(speed).F, 0)} mm/s</span>
        <span style={{ marginLeft: 12 }}>Time</span>
        <input type="number" min={0} step={10} value={seconds} style={{ width: 70 }} disabled={!!running}
          onChange={(e) => setSeconds(Math.max(0, Number(e.target.value)))} />
        <span style={{ color: '#6b7280', fontSize: 12 }}>s (0 = until Stop)</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
        <label style={{ color: '#b91c1c' }}>
          <input type="checkbox" checked={confirm} disabled={!!running} onChange={(e) => setConfirm(e.target.checked)} />
          {' '}The site is clear (needed for Real and for a run on the real delta)
        </label>
        <button style={{ fontSize: 15, padding: '4px 18px' }} disabled={!canStart} onClick={start}>Start</button>
        <button style={{ fontSize: 15, padding: '4px 18px' }} disabled={!running || running === 'homing'}
          onClick={() => { stopReq.current = true; abort.current = true; }}>Stop</button>
        {running && <span style={{ color: '#b45309' }}>{running} · {fmt(elapsed, 0)} s</span>}
        {!running && fsm && fsm !== 'Ready' && <span style={{ color: '#6b7280', fontSize: 12 }}>needs Ready</span>}
      </div>

      <table style={{ borderCollapse: 'collapse', fontSize: 12, marginTop: 10, fontVariantNumeric: 'tabular-nums' }}>
        <thead><tr>
          <th style={{ ...head, textAlign: 'left' }}>{running ? 'live' : 'last run'}</th>
          <th style={head}>stale (1 cycle late)</th><th style={head}>torque change max</th>
          <th style={head}>≥ 20 %</th><th style={head}>≥ 50 %</th>
        </tr></thead>
        <tbody>
          {[0, 1, 2].map((k) => {
            const s = stats[k];
            return (
              <tr key={k}>
                <td style={left}>EAxis{k}</td>
                <td style={cell}>{s ? `${fmt(s.latePct, 2)} %` : '-'}</td>
                <td style={cell}>{s ? `${fmt(s.tqMax)} %` : '-'}</td>
                <td style={cell}>{s ? s.ge20 : '-'}</td>
                <td style={cell}>{s ? s.ge50 : '-'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div style={{ color: '#6b7280', fontSize: 12, marginTop: 4 }}>
        Torque change: actual torque (0x6077) between two 1 ms cycles, % of rated. Counts since Start.
      </div>

      {results.length > 0 && (
        <table style={{ borderCollapse: 'collapse', fontSize: 12, marginTop: 10, fontVariantNumeric: 'tabular-nums' }}>
          <thead><tr>
            <th style={{ ...head, textAlign: 'left' }}>run</th>
            <th style={head}>stale % 0/1/2</th><th style={head}>torque max % 0/1/2</th>
            <th style={head}>≥ 20 % 0/1/2</th><th style={head}>≥ 50 % 0/1/2</th>
          </tr></thead>
          <tbody>
            {results.map((r, i) => (
              <tr key={i}>
                <td style={left}>{new Date(r.at).toLocaleTimeString()} {r.label}</td>
                <td style={cell}>{r.s.map((x) => fmt(x.latePct, 2)).join(' / ')}</td>
                <td style={cell}>{r.s.map((x) => fmt(x.tqMax)).join(' / ')}</td>
                <td style={cell}>{r.s.map((x) => x.ge20).join(' / ')}</td>
                <td style={cell}>{r.s.map((x) => x.ge50).join(' / ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};
