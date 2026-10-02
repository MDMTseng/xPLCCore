// Windowed packet stream to the PLC: packets go out in order with up to
// `window` replies outstanding (default 8, production's MAX_IN_FLIGHT, below
// the PLC's motion buffer threshold), so a queued path keeps the motion
// buffer fed. Shared by the harness actions (MotorTestPage: plc_stream_*)
// and the path test panel; `state` is the one stream status both report.

export type StreamState = { sent: number; total: number; running: boolean; err: string };
export type SendFn = (pkt: any, timeoutMs?: number) => Promise<any>;

export const newStreamState = (): StreamState => ({ sent: 0, total: 0, running: false, err: '' });

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
  state.current = { sent: 0, total: pkts.length, running: true, err: '' };
  const pending = new Set<Promise<void>>();
  try {
    for (let i = 0; i < pkts.length && !abort.current; i++) {
      while (pending.size >= win) await Promise.race(pending);
      if (abort.current) break;
      const pr: Promise<void> = send(pkts[i], opt.timeoutMs ?? 30000)
        .then(() => { state.current.sent++; })
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
