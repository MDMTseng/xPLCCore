// Windowed packet stream to the PLC: packets go out in order with up to
// `window` replies outstanding (default 8, production's MAX_IN_FLIGHT, below
// the PLC's motion buffer threshold), so a queued path keeps the motion
// buffer fed. Shared by the harness actions (MotorTestPage: plc_stream_*)
// and the path test panel; `state` is the one stream status both report.
//
// Timing (2026-10-05, the unplanned pauses on the triangle soak): the reply
// latency of every packet (send -> ack) and the gaps between two sends, so a
// pause of the delta can be matched to a late ack or a host that did not
// send. Times are ms since `t0` (Date.now() at the start).

export type StreamTiming = {
  t0: number;
  /** Max reply latency, ms, and a histogram with edges 50 100 250 500 1000 2000 ms. */
  latMax: number;
  latHist: number[];
  /** Max gap between two sends, ms. */
  gapMax: number;
  /** The last 200 replies slower than SLOW_MS: [t_ms, packet index, latency_ms, in flight when sent]. */
  slow: number[][];
  /** The last 200 send gaps over SLOW_MS: [t_ms, packet index, gap_ms, in flight while waiting]. */
  gaps: number[][];
};

export type StreamState = { sent: number; total: number; running: boolean; err: string; timing?: StreamTiming };
export type SendFn = (pkt: any, timeoutMs?: number) => Promise<any>;

export const newStreamState = (): StreamState => ({ sent: 0, total: 0, running: false, err: '' });

const LAT_EDGES = [50, 100, 250, 500, 1000, 2000];
const SLOW_MS = 150;
const KEEP = 200;

function push(list: number[][], row: number[]) {
  list.push(row);
  if (list.length > KEEP) list.shift();
}

// Sends `pkts`; resolves when all replies are in or `abort.current` is set
// (moves already queued in the PLC still run). Throws nothing: an error ends
// the stream and lands in state.err.
export async function streamPackets(
  send: SendFn,
  pkts: any[],
  state: { current: StreamState },
  abort: { current: boolean },
  opt: { window?: number; timeoutMs?: number } = {},
): Promise<StreamState> {
  if (state.current.running) throw new Error('stream already running');
  const win = Math.max(1, Number(opt.window ?? 8));
  abort.current = false;
  const tm: StreamTiming = { t0: Date.now(), latMax: 0, latHist: new Array(LAT_EDGES.length + 1).fill(0), gapMax: 0, slow: [], gaps: [] };
  state.current = { sent: 0, total: pkts.length, running: true, err: '', timing: tm };
  const pending = new Set<Promise<void>>();
  let lastSend = 0;
  try {
    for (let i = 0; i < pkts.length && !abort.current; i++) {
      const waited = pending.size;
      while (pending.size >= win) await Promise.race(pending);
      if (abort.current) break;
      const now = Date.now() - tm.t0;
      if (i > 0) {
        const gap = now - lastSend;
        if (gap > tm.gapMax) tm.gapMax = gap;
        if (gap > SLOW_MS) push(tm.gaps, [lastSend, i, gap, waited]);
      }
      lastSend = now;
      const inflight = pending.size;
      const pr: Promise<void> = send(pkts[i], opt.timeoutMs ?? 30000)
        .then(() => {
          state.current.sent++;
          const lat = Date.now() - tm.t0 - now;
          if (lat > tm.latMax) tm.latMax = lat;
          let b = 0;
          while (b < LAT_EDGES.length && lat >= LAT_EDGES[b]) b++;
          tm.latHist[b]++;
          if (lat > SLOW_MS) push(tm.slow, [now, i, lat, inflight]);
        })
        .finally(() => { pending.delete(pr); });
      pending.add(pr);
    }
    await Promise.all(pending);
  } catch (e: any) {
    state.current.err = String(e?.message ?? e);
  } finally {
    state.current.running = false;
  }
  return { ...state.current };
}
