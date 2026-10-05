// Windowed packet stream to the PLC: packets go out in order with up to
// `window` replies outstanding (default 8, production's MAX_IN_FLIGHT, below
// the PLC's motion buffer threshold), so a queued path keeps the motion
// buffer fed. Shared by the harness actions (MotorTestPage: plc_stream_*)
// and the path test panel; `state` is the one stream status both report.
//
// Timing (2026-10-05, the unplanned pauses on the triangle soak). In steady
// state the PLC holds every packet until SoftMotion has room, so each reply
// takes ~430 ms BY DESIGN and per-reply latency says nothing (integration
// review, flow control). What does show an interruption of the supply:
//   - replyGap: time between two consecutive replies. Steady state: one per
//     accepted move, ~50-200 ms. A long gap = the PLC accepted nothing (it was
//     not fed, or did not take what it had).
//   - sendGap with the window NOT full: the host could send and did not.
//   - loopLag: how late a 20 ms timer fires = the UI event loop was busy
//     (GC, rendering) and could neither read replies nor send.
// Rows are [t_ms, packet index, ms, in flight]; times are ms since `t0`.

export type StreamTiming = {
  t0: number;
  /** Reply latency histogram (info only), edges 50 100 250 500 1000 2000 ms, and its max. */
  latHist: number[];
  latMax: number;
  replyGapMax: number;
  sendGapMax: number;
  loopLagMax: number;
  /** The last ROWS rows of each kind over GAP_MS. */
  replyGaps: number[][];
  sendGaps: number[][];
  loopLags: number[][];
};

export type StreamState = { sent: number; total: number; running: boolean; err: string; timing?: StreamTiming };
export type SendFn = (pkt: any, timeoutMs?: number) => Promise<any>;

export const newStreamState = (): StreamState => ({ sent: 0, total: 0, running: false, err: '' });

const LAT_EDGES = [50, 100, 250, 500, 1000, 2000];
const GAP_MS = 300;
const LAG_MS = 100;
const TICK_MS = 20;
const ROWS = 500;

function keep(list: number[][], row: number[]) {
  list.push(row);
  if (list.length > ROWS) list.shift();
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
  const tm: StreamTiming = {
    t0: Date.now(), latHist: new Array(LAT_EDGES.length + 1).fill(0), latMax: 0,
    replyGapMax: 0, sendGapMax: 0, loopLagMax: 0, replyGaps: [], sendGaps: [], loopLags: [],
  };
  state.current = { sent: 0, total: pkts.length, running: true, err: '', timing: tm };
  const pending = new Set<Promise<void>>();
  const now = () => Date.now() - tm.t0;
  let lastSend = -1;
  let lastReply = -1;
  // Event-loop lag: a timer that should fire every TICK_MS.
  let expect = now() + TICK_MS;
  const tick = setInterval(() => {
    const t = now();
    const lag = t - expect;
    expect = t + TICK_MS;
    if (lag > tm.loopLagMax) tm.loopLagMax = lag;
    if (lag > LAG_MS) keep(tm.loopLags, [t - lag, state.current.sent, lag, pending.size]);
  }, TICK_MS);
  try {
    for (let i = 0; i < pkts.length && !abort.current; i++) {
      while (pending.size >= win) await Promise.race(pending);
      if (abort.current) break;
      const t = now();
      // A send gap only counts when the window had room the whole time,
      // i.e. the host itself was late (a full window waits for replies).
      if (lastSend >= 0) {
        const gap = t - lastSend;
        if (gap > tm.sendGapMax) tm.sendGapMax = gap;
        if (gap > GAP_MS && pending.size < win - 1) keep(tm.sendGaps, [lastSend, i, gap, pending.size]);
      }
      lastSend = t;
      const pr: Promise<void> = send(pkts[i], opt.timeoutMs ?? 30000)
        .then(() => {
          state.current.sent++;
          const r = now();
          const lat = r - t;
          if (lat > tm.latMax) tm.latMax = lat;
          let b = 0;
          while (b < LAT_EDGES.length && lat >= LAT_EDGES[b]) b++;
          tm.latHist[b]++;
          if (lastReply >= 0) {
            const gap = r - lastReply;
            if (gap > tm.replyGapMax) tm.replyGapMax = gap;
            if (gap > GAP_MS) keep(tm.replyGaps, [lastReply, i, gap, pending.size]);
          }
          lastReply = r;
        })
        .finally(() => { pending.delete(pr); });
      pending.add(pr);
    }
    await Promise.all(pending);
  } catch (e: any) {
    state.current.err = String(e?.message ?? e);
  } finally {
    clearInterval(tick);
    state.current.running = false;
  }
  return { ...state.current };
}
