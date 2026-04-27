import React, { useCallback, useEffect, useRef, useState } from 'react';
import { cmd, validateReply } from '../lib/protocol';

// Counters that mean "something is silently going wrong" if non-zero.
// Same set DiagPanel marks `warnIfNonZero` -- kept here as a literal so
// the badge has no dependency on the larger panel.
const WARN_KEYS = [
  'remp_drop',
  'overlen_drop',
  'send_stall_drop',
  'group_not_ready_nak',
  'missing_type_nak',
  'coord_not_cfg_nak',
  'proto_mismatch_nak',
  'idle_reset',
  'read_err_reset',
  'parser_err_reset',
  'ui_hb_stale_count',
] as const;

const POLL_MS = 5000;

// Always-visible PLC-health indicator. Polls GET_DIAG in the background;
// turns red the moment any silent-NAK / drop / reset counter ticks.
// Operators are expected to glance at this; full breakdown lives in the
// DiagPanel embedded in MiscControlsPage.
export const DiagBadge: React.FC<{
  sendTcpMsgPack: (data: any, waitForTracking?: boolean) => boolean | Promise<any>;
  connected: boolean;
}> = ({ sendTcpMsgPack, connected }) => {
  const [worst, setWorst] = useState<{ key: string; value: number } | null>(null);
  const [total, setTotal] = useState<number>(0);
  const [stale, setStale] = useState<boolean>(false);
  // Latency hints for tooltip. ping_max_gap_ms is PLC's worst observed gap
  // between consecutive UI heartbeats (envelope, not current). last_ui_ping_ms
  // is the runtime_ms timestamp of the last PING the PLC saw — comparing it
  // to runtime_ms tells the operator how stale the UI->PLC link is right now.
  const [pingMaxGap, setPingMaxGap] = useState<number | null>(null);
  const [pingFreshness, setPingFreshness] = useState<number | null>(null);
  const inflightRef = useRef(false);

  const poll = useCallback(async () => {
    if (inflightRef.current) return;
    inflightRef.current = true;
    try {
      const reply = await (sendTcpMsgPack(cmd.GetDiag(), true) as Promise<any>);
      if (!reply || reply.ack === false) {
        setStale(true);
        return;
      }
      validateReply('DiagSnapshot', reply);
      let max: { key: string; value: number } | null = null;
      let sum = 0;
      for (const k of WARN_KEYS) {
        const v = Number(reply[k] ?? 0);
        if (!Number.isFinite(v) || v <= 0) continue;
        sum += v;
        if (!max || v > max.value) max = { key: k, value: v };
      }
      setWorst(max);
      setTotal(sum);
      const gap = Number(reply.ping_max_gap_ms);
      setPingMaxGap(Number.isFinite(gap) ? gap : null);
      const lastPing = Number(reply.last_ui_ping_ms);
      const runtime = Number(reply.runtime_ms);
      setPingFreshness(
        Number.isFinite(lastPing) && Number.isFinite(runtime) && lastPing > 0
          ? Math.max(0, runtime - lastPing)
          : null
      );
      setStale(false);
    } catch {
      setStale(true);
    } finally {
      inflightRef.current = false;
    }
  }, [sendTcpMsgPack]);

  useEffect(() => {
    if (!connected) {
      setWorst(null);
      setTotal(0);
      setStale(false);
      return;
    }
    poll();
    const h = setInterval(poll, POLL_MS);
    return () => clearInterval(h);
  }, [connected, poll]);

  const onClick = () => {
    // Surface a window event so MiscControlsPage / DiagPanel can scroll
    // into view if mounted, without DiagBadge having to know about
    // routing or tab state.
    try { window.dispatchEvent(new CustomEvent('diag:open')); } catch (_) {}
  };

  if (!connected) return null;

  const isWarn = total > 0;
  const latencyLine = (() => {
    const parts: string[] = [];
    if (pingMaxGap !== null) parts.push(`PING worst-gap ${pingMaxGap}ms`);
    if (pingFreshness !== null) parts.push(`last PING ${pingFreshness}ms ago`);
    return parts.length ? `\n${parts.join(' · ')}` : '';
  })();
  const tooltip = stale
    ? 'PLC diag poll failed (last reply was NAK/missing). Click to investigate.'
    : isWarn
      ? `${total} silent NAK/drop/reset events on PLC since boot or last clear. Worst: ${worst?.key}=${worst?.value}. Click for details.${latencyLine}`
      : `PLC drop/NAK/reset counters all clean.${latencyLine}`;

  return (
    <button
      type="button"
      title={tooltip}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '3px 9px',
        borderRadius: 999,
        border: `1px solid ${stale ? '#a16207' : isWarn ? '#dc2626' : '#16a34a'}`,
        background: stale ? '#fef3c7' : isWarn ? '#fee2e2' : '#dcfce7',
        color: stale ? '#854d0e' : isWarn ? '#991b1b' : '#166534',
        fontSize: 11,
        fontWeight: 700,
        cursor: 'pointer',
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: stale ? '#ca8a04' : isWarn ? '#dc2626' : '#16a34a',
        }}
      />
      diag
      {isWarn && <span style={{ fontFamily: 'monospace' }}>{total}</span>}
      {stale && !isWarn && <span style={{ fontFamily: 'monospace' }}>?</span>}
    </button>
  );
};
