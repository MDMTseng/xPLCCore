// One FSM walk at a time (2026-10-05 integration review, state ownership).
//
// The Welcome, Operation and Motors (path test) pages each have a "climb to
// Ready" loop that reads the PLC state, decides an event, waits and sends
// it. Two of them running together act on each other's stale reads: a
// double click could post HOME_GO after the first loop had reached Ready and
// queued moves, re-homing the arm under them. They also must not run while
// a production run is on (Operation's init ends with a blind G1 to the
// standby pose).
//
// withFsmLock(owner, fn) runs fn when no other walk and no production run
// is active, and throws `fsm_walk_busy` otherwise; the caller shows it.

let owner: string | null = null;
let runProbe: (() => boolean) | null = null;

export class FsmWalkBusy extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = 'FsmWalkBusy';
  }
}

/** CalibPage registers how to tell that a production run is on. */
export function registerRunProbe(probe: () => boolean): void {
  runProbe = probe;
}

export function fsmWalkOwner(): string | null {
  return owner;
}

export async function withFsmLock<T>(who: string, fn: () => Promise<T>): Promise<T> {
  if (owner !== null) throw new FsmWalkBusy(`fsm_walk_busy: "${owner}" is already walking the FSM`);
  if (runProbe?.()) throw new FsmWalkBusy('fsm_walk_busy: a production run is on; STOP it first');
  owner = who;
  try {
    return await fn();
  } finally {
    owner = null;
  }
}
