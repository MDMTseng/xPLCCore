import { describe, it, expect, afterEach } from 'vitest';
import { withFsmLock, registerRunProbe, fsmWalkOwner } from './fsmLock';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('withFsmLock', () => {
  afterEach(() => registerRunProbe(() => false));

  it('runs one walk and refuses a second one meanwhile', async () => {
    const first = withFsmLock('Welcome init', async () => { await sleep(20); return 'done'; });
    expect(fsmWalkOwner()).toBe('Welcome init');
    await expect(withFsmLock('Operation init', async () => 'x')).rejects.toThrow(/fsm_walk_busy/);
    await expect(first).resolves.toBe('done');
    expect(fsmWalkOwner()).toBe(null);
    await expect(withFsmLock('Operation init', async () => 'ok')).resolves.toBe('ok');
  });

  it('releases the lock when the walk throws', async () => {
    await expect(withFsmLock('a', async () => { throw new Error('boom'); })).rejects.toThrow('boom');
    expect(fsmWalkOwner()).toBe(null);
  });

  it('refuses while a production run is on', async () => {
    registerRunProbe(() => true);
    await expect(withFsmLock('a', async () => 1)).rejects.toThrow(/production run/);
  });
});
