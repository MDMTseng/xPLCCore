import React, { useEffect, useRef, useState } from 'react';

// Capacity. 100 covers a normal operator session (a typical Ready->G1->idle
// loop emits 1-3 events per cycle); enough to debug a fault sequence
// without keeping arbitrarily-old history pinned in memory.
const RING_SIZE = 100;

type LogEntry = {
  seq: number;
  at: number;       // wall-clock ms
  name: string;     // ST_CHG / COORD_SET / MOVE_DONE / ...
  payload: any;
};

// Collapsible viewer for the last RING_SIZE PLC push events. Subscribes
// to the `plc:event` window event (same channel PluginHello dispatches
// to). Lets an operator inspect "what events fired in the last minute"
// without opening DevTools.
//
// Dropped (oldest-first) entries are silently overwritten -- the badge
// `seq` column makes gaps visible if anyone needs to confirm what was
// dropped.
export const PlcEventLog: React.FC<{ defaultOpen?: boolean }> = ({ defaultOpen = false }) => {
  const [open, setOpen] = useState(defaultOpen);
  const [, setTick] = useState(0);
  const ringRef = useRef<LogEntry[]>([]);
  const seqRef = useRef(0);

  useEffect(() => {
    const handler = (ev: globalThis.Event) => {
      const detail = (ev as CustomEvent).detail;
      if (!detail || typeof detail !== 'object') return;
      seqRef.current += 1;
      const entry: LogEntry = {
        seq: seqRef.current,
        at: Date.now(),
        name: String(detail.name ?? '<unnamed>'),
        payload: detail,
      };
      const ring = ringRef.current;
      ring.push(entry);
      if (ring.length > RING_SIZE) ring.shift();
      setTick((n) => n + 1);
    };
    window.addEventListener('plc:event', handler as EventListener);
    return () => window.removeEventListener('plc:event', handler as EventListener);
  }, []);

  const entries = [...ringRef.current].reverse(); // newest first
  const count = entries.length;

  return (
    <div
      style={{
        marginTop: 10,
        border: '1px solid #cbd5e1',
        borderRadius: 12,
        background: '#ffffff',
        padding: 10,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          cursor: 'pointer',
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <div style={{ fontWeight: 800, color: '#111827', fontSize: 13 }}>
          PLC push events ({count}/{RING_SIZE})
          {count > 0 && (
            <span style={{ color: '#6b7280', fontWeight: 500, marginLeft: 6 }}>
              latest: {entries[0].name} @ {new Date(entries[0].at).toLocaleTimeString()}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); ringRef.current = []; setTick((n) => n + 1); }}
          style={{
            fontSize: 11,
            padding: '2px 8px',
            background: '#f3f4f6',
            border: '1px solid #d1d5db',
            borderRadius: 6,
            cursor: 'pointer',
            marginRight: 8,
          }}
        >
          clear
        </button>
        <span style={{ fontSize: 11, color: '#6b7280' }}>{open ? '▼' : '▶'}</span>
      </div>

      {open && (
        <div
          style={{
            marginTop: 8,
            maxHeight: 240,
            overflowY: 'auto',
            border: '1px solid #e5e7eb',
            borderRadius: 8,
            background: '#f8fafc',
          }}
        >
          {count === 0 ? (
            <div style={{ padding: 10, fontSize: 12, color: '#9ca3af' }}>
              No events yet. ST_CHG fires on every FSM transition, COORD_SET on
              every coord-gate edge, MOVE_DONE on every G1 completion.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead style={{ background: '#e2e8f0', position: 'sticky', top: 0 }}>
                <tr>
                  <th style={{ textAlign: 'right', padding: '3px 6px', width: 40 }}>#</th>
                  <th style={{ textAlign: 'left',  padding: '3px 6px', width: 80 }}>time</th>
                  <th style={{ textAlign: 'left',  padding: '3px 6px', width: 90 }}>name</th>
                  <th style={{ textAlign: 'left',  padding: '3px 6px' }}>payload</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.seq} style={{ borderTop: '1px solid #e5e7eb' }}>
                    <td style={{ padding: '3px 6px', textAlign: 'right', color: '#9ca3af', fontFamily: 'monospace' }}>{e.seq}</td>
                    <td style={{ padding: '3px 6px', color: '#6b7280', fontFamily: 'monospace' }}>
                      {new Date(e.at).toLocaleTimeString(undefined, { hour12: false })}
                    </td>
                    <td style={{ padding: '3px 6px', fontWeight: 700, color: '#0f172a' }}>{e.name}</td>
                    <td style={{ padding: '3px 6px', fontFamily: 'monospace', color: '#334155', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 0 }}>
                      {summarisePayload(e.payload)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
};

function summarisePayload(p: any): string {
  if (!p || typeof p !== 'object') return String(p);
  // Drop kind/name (already in column) and runtime_ms (low signal in the
  // brief view); keep the rest as a one-line key=val sequence.
  const skip = new Set(['kind', 'name', 'runtime_ms']);
  const parts: string[] = [];
  for (const [k, v] of Object.entries(p)) {
    if (skip.has(k)) continue;
    let rendered: string;
    if (typeof v === 'object') rendered = JSON.stringify(v);
    else rendered = String(v);
    parts.push(`${k}=${rendered}`);
  }
  return parts.join('  ');
}
