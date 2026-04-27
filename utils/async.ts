// `delay()` is used throughout the calibration / motion sequences to pace
// async steps (e.g. wait 200ms after a setForce before the next probe).
// Plain setTimeout works -- until the Electron BrowserWindow is hidden or
// minimized, at which point Chromium throttles main-thread timers to ~1Hz.
// That made multi-step calibration sequences run ~20x slower in the
// background, and the same throttling had previously broken PLC heartbeat
// (see PluginHello keepalive worker).
//
// Drive timers from a shared dedicated Worker so they keep firing at the
// requested cadence regardless of window-visibility throttling. Each
// delay() posts a {id, ms} request; the worker fires setTimeout there and
// posts back {id} when the timer expires; main thread resolves the
// matching promise.
//
// Fallback: if Worker isn't available (jsdom test envs, etc.), fall back
// to plain setTimeout so behaviour is preserved -- just throttle-prone.

type Pending = { resolve: () => void };

const WORKER_SRC =
  "const ts=new Map();onmessage=(e)=>{const d=e.data||{};if(d.cmd==='delay'){const id=d.id;const t=setTimeout(()=>{ts.delete(id);postMessage({id});},d.ms);ts.set(id,t);}else if(d.cmd==='cancel'){const t=ts.get(d.id);if(t){clearTimeout(t);ts.delete(d.id);}}};";

let _worker: Worker | null = null;
let _workerInitTried = false;
let _nextId = 1;
const _pending = new Map<number, Pending>();

function getWorker(): Worker | null {
  if (_worker || _workerInitTried) return _worker;
  _workerInitTried = true;
  if (typeof Worker === 'undefined' || typeof Blob === 'undefined' || typeof URL === 'undefined') {
    return null;
  }
  try {
    const blob = new Blob([WORKER_SRC], { type: 'application/javascript' });
    const url = URL.createObjectURL(blob);
    const w = new Worker(url);
    URL.revokeObjectURL(url);
    w.onmessage = (ev: MessageEvent) => {
      const id = ev.data?.id;
      if (typeof id !== 'number') return;
      const p = _pending.get(id);
      if (p) {
        _pending.delete(id);
        p.resolve();
      }
    };
    w.onerror = () => {
      // If the worker dies, drain pending promises with plain timers so
      // callers don't deadlock. Subsequent calls re-fall through to the
      // setTimeout path because _worker is cleared.
      _worker = null;
      for (const [id, p] of _pending) {
        _pending.delete(id);
        setTimeout(p.resolve, 0);
      }
    };
    _worker = w;
    return w;
  } catch {
    return null;
  }
}

export const delay = (ms: number) =>
  new Promise<void>((resolve) => {
    const w = getWorker();
    if (!w) {
      setTimeout(resolve, ms);
      return;
    }
    const id = _nextId++;
    _pending.set(id, { resolve });
    w.postMessage({ cmd: 'delay', id, ms });
  });
