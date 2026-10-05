// Which homing event to send from GroupEnabled (2026-10-06, owner: the UI
// should see a simulation and skip homing).
//
// A delta trio that is virtual cannot home: on the local soft PLC every
// axis is virtual and the homing FB fails (fault 'Homing:homing_fb'); on the
// machine a virtual-delta run (soaks, bench) has nothing to home either.
// The PLC accepts HOME_GO_FORCE_SKIP only in that case -- it checks the
// drives' own virtual flags (AxisSimMaskApplied, Transition.st) -- and
// publishes the same mask as `axes_sim_mask` (bits 0-2 = EAxis0/1/2). So the
// UI asks the PLC and follows it; tools/machine.py fsm_to does the same.

import { cmd, Event } from './protocol';

type Send = (pkt: any, ...rest: any[]) => Promise<any> | any;

export const DELTA_VIRTUAL_MASK = 0x07;

export function deltaVirtual(axesSimMask: unknown): boolean {
  return typeof axesSimMask === 'number' && (axesSimMask & DELTA_VIRTUAL_MASK) === DELTA_VIRTUAL_MASK;
}

/** The event for GroupEnabled: HOME_GO, or HOME_GO_FORCE_SKIP when the PLC
 *  reports the delta virtual. If the state cannot be read, HOME_GO (the
 *  PLC would refuse the skip anyway for a real delta). */
export async function homingEvent(send: Send): Promise<{ ev: number; skip: boolean }> {
  try {
    const ms: any = await send(cmd.GetMachineState());
    if (deltaVirtual(ms?.axes_sim_mask)) return { ev: Event.HOME_GO_FORCE_SKIP, skip: true };
  } catch { /* fall back to a real homing */ }
  return { ev: Event.HOME_GO, skip: false };
}
