import React, { useCallback, useEffect, useRef, useState } from 'react';
import { cmd, validateReply } from '../lib/protocol';

// Numeric counters retain the existing { value, Δ-since-last-poll } row
// pattern. server_active (bool) and bind_addr (string) get a separate
// header strip above the table since they're stateful, not cumulative.
type DiagReply = Record<string, number | boolean | string> & { ack?: boolean };

const COUNTER_KEYS: Array<{ key: string; label: string; warnIfNonZero?: boolean }> = [
  { key: 'sm_scans',             label: 'AxisGroupSM scans' },
  { key: 'ui_ping_count',        label: 'UI PINGs received' },
  { key: 'st_chg_event_count',   label: 'ST_CHG events pushed' },
  { key: 'ping_max_gap_ms',      label: 'PING max gap (ms)', warnIfNonZero: false },
  { key: 'last_ui_ping_ms',      label: 'last UI ping (PLC ms)' },
  // TCP listen-health (2026-06-23). client_connect_count is purely
  // informational; server_long_idle_count is informational by default
  // (fires once on every PLC restart before any client connects), but
  // sustained growth alongside ui_ping_count=0 means nobody's reaching us.
  { key: 'client_connect_count',   label: 'TCP client connects (rising-edge)' },
  { key: 'server_long_idle_count', label: 'TCP active-no-client windows (30s)' },
  { key: 'remp_drop',            label: 'reply-ring drops', warnIfNonZero: true },
  { key: 'overlen_drop',         label: 'overlen-packet drops', warnIfNonZero: true },
  { key: 'send_stall_drop',      label: 'send-stall drops', warnIfNonZero: true },
  { key: 'group_not_ready_nak',  label: 'group_not_ready NAKs', warnIfNonZero: true },
  { key: 'missing_type_nak',     label: 'missing_type_field NAKs', warnIfNonZero: true },
  { key: 'coord_not_cfg_nak',    label: 'coord_not_configured NAKs', warnIfNonZero: true },
  { key: 'proto_mismatch_nak',   label: 'protocol_version_mismatch NAKs', warnIfNonZero: true },
  { key: 'idle_reset',           label: 'TCP idle resets', warnIfNonZero: true },
  { key: 'read_err_reset',       label: 'TCP read-error resets', warnIfNonZero: true },
  { key: 'parser_err_reset',     label: 'TCP parser-error resets', warnIfNonZero: true },
  { key: 'write_err_reset',      label: 'TCP write-error resets', warnIfNonZero: true },
  { key: 'ui_hb_stale_count',    label: 'UI heartbeat stale events', warnIfNonZero: true },
  { key: 'group_error_stop_trips', label: 'SoftMotion GroupErrorStop trips', warnIfNonZero: true },
];

export const DiagPanel: React.FC<{
  sendTcpMsgPack: (data: any, waitForTracking?: boolean) => boolean | Promise<any>;
}> = ({ sendTcpMsgPack }) => {
  const [enabled, setEnabled] = useState(false);
  const [intervalMs, setIntervalMs] = useState(2000);
  const [latest, setLatest] = useState<DiagReply | null>(null);
  const [prev, setPrev] = useState<DiagReply | null>(null);
  const [lastFetchAt, setLastFetchAt] = useState<number | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const inflightRef = useRef(false);

  const fetchDiag = useCallback(async () => {
    if (inflightRef.current) return;
    inflightRef.current = true;
    try {
      const reply = await (sendTcpMsgPack(cmd.GetDiag(), true) as Promise<any>);
      if (reply && reply.ack !== false) {
        validateReply('DiagSnapshot', reply);
        setPrev(latest);
        setLatest(reply as DiagReply);
        setLastFetchAt(Date.now());
        setLastError(null);
      } else {
        setLastError('NAK from PLC');
      }
    } catch (e: any) {
      setLastError(String(e?.message ?? e));
    } finally {
      inflightRef.current = false;
    }
  }, [sendTcpMsgPack, latest]);

  useEffect(() => {
    if (!enabled) return;
    fetchDiag();
    const h = setInterval(fetchDiag, intervalMs);
    return () => clearInterval(h);
  }, [enabled, intervalMs, fetchDiag]);

  const onReset = useCallback(async () => {
    try {
      const reply = await (sendTcpMsgPack(cmd.ResetDbgInfo(), true) as Promise<any>);
      if (reply && reply.ack !== false) {
        setPrev(null);
        setLastError(null);
        await fetchDiag();
      } else {
        setLastError('reset NAK');
      }
    } catch (e: any) {
      setLastError(String(e?.message ?? e));
    }
  }, [sendTcpMsgPack, fetchDiag]);

  const fmt = (v: unknown): string => {
    if (v === undefined || v === null) return '—';
    if (typeof v === 'number') return v.toLocaleString();
    return String(v);
  };

  const delta = (key: string): number | null => {
    if (!latest || !prev) return null;
    const a = latest[key];
    const b = prev[key];
    if (typeof a !== 'number' || typeof b !== 'number') return null;
    return a - b;
  };

  const numericValue = (key: string): number | undefined => {
    const v = latest?.[key];
    return typeof v === 'number' ? v : undefined;
  };

  return (
    <div
      style={{
        border: '1px solid #cbd5e1',
        borderRadius: 12,
        background: '#ffffff',
        padding: 12,
        marginTop: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontWeight: 800, color: '#111827' }}>PLC Diagnostics (SYS/GET_DIAG)</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              style={{ marginRight: 4 }}
            />
            poll
          </label>
          <select
            value={intervalMs}
            onChange={(e) => setIntervalMs(Number(e.target.value))}
            disabled={!enabled}
          >
            <option value={500}>500ms</option>
            <option value={1000}>1s</option>
            <option value={2000}>2s</option>
            <option value={5000}>5s</option>
          </select>
          <button onClick={fetchDiag} disabled={enabled}>refresh</button>
          <button onClick={onReset} style={{ color: '#b91c1c' }}>reset counters</button>
        </div>
      </div>

      <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 6 }}>
        {lastFetchAt
          ? `last fetch: ${new Date(lastFetchAt).toLocaleTimeString()}  ·  PLC runtime: ${fmt(latest?.runtime_ms)}ms`
          : 'idle — toggle "poll" or click refresh'}
        {lastError && <span style={{ color: '#b91c1c', marginLeft: 8 }}>err: {lastError}</span>}
      </div>

      {latest && (latest.server_active !== undefined || latest.bind_addr !== undefined) && (
        <div
          style={{
            display: 'flex',
            gap: 12,
            alignItems: 'center',
            fontSize: 11,
            color: '#374151',
            padding: '4px 8px',
            marginBottom: 6,
            background: '#f8fafc',
            borderRadius: 6,
            border: '1px solid #e2e8f0',
          }}
        >
          <span>
            bind:&nbsp;
            <code style={{ background: '#fff', padding: '0 4px', borderRadius: 3 }}>
              {String(latest.bind_addr ?? '?')}
            </code>
          </span>
          <span style={{ color: latest.server_active ? '#15803d' : '#b91c1c', fontWeight: 600 }}>
            server: {latest.server_active ? 'active' : 'down'}
          </span>
          {/* Configuration-drift hint: bind looks healthy but nobody's
              actually connected -- usually firewall, wrong IP on the
              caller side, or a runtime network misconfig. */}
          {latest.server_active &&
            typeof latest.client_connect_count === 'number' &&
            latest.client_connect_count === 0 &&
            typeof latest.server_long_idle_count === 'number' &&
            latest.server_long_idle_count > 0 && (
              <span style={{ color: '#b45309' }}>
                ⚠ bind up, no client has connected since boot
              </span>
            )}
        </div>
      )}

      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr style={{ background: '#f3f4f6' }}>
            <th style={{ textAlign: 'left',  padding: '4px 8px' }}>counter</th>
            <th style={{ textAlign: 'right', padding: '4px 8px' }}>value</th>
            <th style={{ textAlign: 'right', padding: '4px 8px' }}>Δ since last poll</th>
          </tr>
        </thead>
        <tbody>
          {COUNTER_KEYS.map(({ key, label, warnIfNonZero }) => {
            const v = numericValue(key);
            const d = delta(key);
            const isWarn = warnIfNonZero && typeof v === 'number' && v > 0;
            return (
              <tr key={key} style={{ borderTop: '1px solid #f1f5f9' }}>
                <td style={{ padding: '3px 8px', color: isWarn ? '#b91c1c' : '#374151' }}>{label}</td>
                <td style={{ padding: '3px 8px', textAlign: 'right', fontFamily: 'monospace', fontWeight: isWarn ? 700 : 400, color: isWarn ? '#b91c1c' : '#111827' }}>
                  {fmt(v)}
                </td>
                <td style={{ padding: '3px 8px', textAlign: 'right', fontFamily: 'monospace', color: d && d > 0 ? '#0369a1' : '#9ca3af' }}>
                  {d === null ? '—' : (d > 0 ? `+${d}` : String(d))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 6 }}>
        Δ shows change since previous successful poll. Red counters mean the PLC has logged at least one drop / NAK / reset since boot or last clear.
        "ping_max_gap_ms" is the worst PING-to-PING gap observed on the PLC clock — high values mean the UI heartbeat stalled at some point.
        "TCP client connects" rises on every TCP rising edge; sustained 0 with growing "active-no-client windows" means the bind is up but nobody's reaching us (firewall / wrong IP / runtime misconfig).
      </div>
    </div>
  );
};
