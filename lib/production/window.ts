// A send window: fire commands without waiting a round trip each, but with
// at most `max` replies outstanding. Machine.queue is built on this.

export type SendFn = (pkt: unknown) => Promise<unknown>;

export function createSendWindow(send: SendFn, max: number, onError: (pkt: unknown, e: unknown) => void) {
  const inFlight: Promise<unknown>[] = [];
  let gate: Promise<void> = Promise.resolve();

  /** Resolves once `pkt` has been sent: at once, or after the oldest
   *  outstanding reply when the window is full. Calls are serialised, so
   *  packets go out in call order. */
  function queue(pkt: unknown): Promise<void> {
    const sent = gate.then(async () => {
      while (inFlight.length >= max) {
        await Promise.race(inFlight).catch(() => {});
      }
      const reply = send(pkt).catch((e) => { onError(pkt, e); });
      inFlight.push(reply);
      void reply.then(() => { inFlight.splice(inFlight.indexOf(reply), 1); });
    });
    gate = sent;
    return sent;
  }

  /** Resolves when every reply sent through the window has arrived. */
  async function drain(): Promise<void> {
    await gate;
    while (inFlight.length > 0) await Promise.race(inFlight);
  }

  return { queue, drain, inFlight: () => inFlight.length };
}
