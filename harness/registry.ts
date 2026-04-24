import { useEffect } from 'react';

export type HarnessAction = (payload: any) => any | Promise<any>;

const actions = new Map<string, HarnessAction>();

export function registerHarnessAction(name: string, fn: HarnessAction): void {
  actions.set(name, fn);
}

export function unregisterHarnessAction(name: string): void {
  actions.delete(name);
}

export function dispatchHarnessAction(name: string, payload: any): any | Promise<any> {
  const fn = actions.get(name);
  if (!fn) throw new Error(`unknown harness action: ${name}`);
  return fn(payload);
}

export function hasHarnessAction(name: string): boolean {
  return actions.has(name);
}

export function listHarnessActions(): string[] {
  return Array.from(actions.keys()).sort();
}

export function useHarnessAction(name: string, fn: HarnessAction, deps: any[]): void {
  useEffect(() => {
    registerHarnessAction(name, fn);
    return () => {
      unregisterHarnessAction(name);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
