// JointPanel -- the delta joints during the bench test (SYS JOINT_STATE,
// live): position, test state, home switch, where the switch turned on;
// and a STOP for the test moves (SYS JOINT_STOP). The moves themselves are
// started by the test procedure (SYS JOINT_MOVE, FSM Powered only).

import React, { useCallback, useEffect, useState } from 'react';
import type { Send } from '../lib/motors';

const POLL_MS = 200;
const STATE = ['idle', 'moving', 'done', 'stopped at switch', 'error', 'no switch in travel', 'stopped'];

export const JointPanel: React.FC<{ send: Send; active: boolean; log: (s: string) => void }> = ({ send, active, log }) => {
  const [js, setJs] = useState<any>(null);
  const [fsm, setFsm] = useState('');

  const poll = useCallback(async () => {
    try {
      setJs(await send({ type: 'SYS', cmd: 'JOINT_STATE' }, 1500));
      setFsm(String((await send({ type: 'SYS', cmd: 'GA_EV', ev: 0 }, 1500))?.st_str ?? ''));
    } catch { /* shown as stale */ }
  }, [send]);

  useEffect(() => {
    if (!active) return undefined;
    let busy = false;
    const id = setInterval(() => {
      if (busy) return;
      busy = true;
      poll().finally(() => { busy = false; });
    }, POLL_MS);
    return () => clearInterval(id);
  }, [active, poll]);

  const stop = async () => {
    try { await send({ type: 'SYS', cmd: 'JOINT_STOP' }, 1500); log('JOINT_STOP sent'); }
    catch (e: any) { log(`JOINT_STOP failed: ${e?.message ?? e}`); }
  };

  const cell: React.CSSProperties = { padding: '3px 10px', borderBottom: '1px solid #e5e7eb', textAlign: 'right', fontVariantNumeric: 'tabular-nums' };
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <b>Delta joints (bench test)</b>
        <span style={{ fontSize: 12, color: '#6b7280' }}>FSM {fsm || '?'}</span>
        <button onClick={stop} style={{ marginLeft: 'auto', background: '#dc2626', color: '#fff', fontWeight: 700, padding: '6px 22px', border: 0, borderRadius: 6 }}>
          STOP
        </button>
      </div>
      <table style={{ borderCollapse: 'collapse', fontSize: 13 }}>
        <thead><tr>
          <th style={{ ...cell, textAlign: 'left' }}>joint</th><th style={cell}>mode</th><th style={cell}>position deg</th>
          <th style={cell}>home switch</th><th style={cell}>test</th><th style={cell}>switch turned on at</th>
        </tr></thead>
        <tbody>
          {[0, 1, 2].map((k) => {
            const on = js ? ((js.in >> k) & 1) === 1 : false;
            const st = js ? js[`s${k}`] : undefined;
            return (
              <tr key={k}>
                <td style={{ ...cell, textAlign: 'left' }}>EAxis{k}</td>
                <td style={cell}>{js ? (js[`v${k}`] ? 'virtual' : 'REAL') : '-'}</td>
                <td style={cell}>{js ? Number(js[`p${k}`]).toFixed(3) : '-'}</td>
                <td style={cell}><span style={{ display: 'inline-block', width: 14, height: 14, borderRadius: 7, background: on ? '#22c55e' : '#d1d5db' }} /> {on ? 'ON' : 'off'}</td>
                <td style={cell}>{st === undefined ? '-' : STATE[st] ?? st}{js && js[`e${k}`] ? ` (err ${js[`e${k}`]})` : ''}</td>
                <td style={cell}>{js && st === 3 ? Number(js[`h${k}`]).toFixed(3) : '-'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};
