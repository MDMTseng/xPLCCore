// InputMonitor -- the IO slave's digital inputs, live (SYS IO_STATE), for
// checking sensors by hand (e.g. the delta's home switches): trigger the
// sensor and watch its lamp. A ring marks a bit that changed since the
// last "Clear", so a sensor passed in a flash still shows.

import React, { useCallback, useEffect, useState } from 'react';
import { IO_PINS } from '../lib/production/io';
import type { Send } from '../lib/motors';

const POLL_MS = 200;

/** Known input pins (machine numbering 0-15), from lib/production/io.ts. */
const PIN_NAMES: Record<number, string> = Object.fromEntries(
  Object.entries(IO_PINS.I).map(([name, pin]) => [pin as number, name]),
);

type IoState = { in0: number; in1: number; in2: number; chg0: number; chg1: number; chg2: number; out: number; sim: boolean };

const ROWS: Array<{ key: 'in1' | 'in2' | 'in0'; chg: 'chg1' | 'chg2' | 'chg0'; label: string; pin0: number | null }> = [
  { key: 'in1', chg: 'chg1', label: 'inputs 0-7 (terminal CH1)', pin0: 0 },
  { key: 'in2', chg: 'chg2', label: 'inputs 8-15 (terminal CH2)', pin0: 8 },
  { key: 'in0', chg: 'chg0', label: 'EC0808DN CH1', pin0: null },
];

export const InputMonitor: React.FC<{ send: Send; active: boolean }> = ({ send, active }) => {
  const [io, setIo] = useState<IoState | null>(null);
  const [err, setErr] = useState('');
  const [updated, setUpdated] = useState(0);

  const poll = useCallback(async (reset = false) => {
    try {
      const r = await send({ type: 'SYS', cmd: 'IO_STATE', ...(reset ? { reset: 1 } : {}) }, 1500);
      setIo(r as IoState);
      setErr('');
      setUpdated(Date.now());
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  }, [send]);

  useEffect(() => {
    if (!active) return undefined;
    let alive = true;
    let busy = false;
    const id = setInterval(() => {
      if (!alive || busy) return;
      busy = true;
      poll().finally(() => { busy = false; });
    }, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [active, poll]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
        <b>Inputs (IO slave, live)</b>
        <button onClick={() => poll(true)}>Clear change marks</button>
        <span style={{ fontSize: 12, color: err ? '#b91c1c' : '#6b7280' }}>
          {err ? `no reading: ${err}` : updated ? `updated ${new Date(updated).toLocaleTimeString()}` : 'waiting'}
        </span>
        {io?.sim && <span style={{ fontSize: 12, color: '#b45309' }}>simulated inputs are ON for the program (lamps show the real ones)</span>}
      </div>
      {ROWS.map((row) => (
        <div key={row.key} style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '4px 0' }}>
          <span style={{ width: 170, fontSize: 12 }}>{row.label}</span>
          {Array.from({ length: 8 }, (_, bit) => {
            const on = io ? ((io[row.key] >> bit) & 1) === 1 : false;
            const changed = io ? ((io[row.chg] >> bit) & 1) === 1 : false;
            const pin = row.pin0 === null ? null : row.pin0 + bit;
            const name = pin !== null ? PIN_NAMES[pin] : undefined;
            return (
              <div key={bit} title={`${pin !== null ? `input ${pin}` : `EC0808DN bit ${bit}`}${name ? ` ${name}` : ''}: ${on ? 'ON' : 'off'}${changed ? ', changed since clear' : ''}`}
                style={{ width: 52, textAlign: 'center', fontSize: 10 }}>
                <div style={{
                  width: 22, height: 22, borderRadius: 11, margin: '0 auto',
                  background: io ? (on ? '#22c55e' : '#d1d5db') : '#f3f4f6',
                  boxShadow: changed ? '0 0 0 3px #f59e0b' : 'none',
                }} />
                <div style={{ fontVariantNumeric: 'tabular-nums' }}>{pin !== null ? pin : bit}</div>
                <div style={{ color: '#6b7280', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name ?? ''}</div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
};
