// What the production cycle needs from the cell, as one typed interface.
//
// The cycle modules (tape, feeder, nozzle) talk to the machine only
// through this, so they carry no React state and can be driven by a fake
// in unit tests. CalibPage builds the real one from COMCtrlObj.

import type { VisionCheck } from './io';

/** The Modbus bridge to the flex feeder (vibration channels, top light). */
export interface FeederIO {
  von(channel: number): unknown;
  voff(channel: number): unknown;
  top_light_on(): unknown;
  top_light_off(): unknown;
}

export interface Machine {
  /** PLC command, awaiting its reply. Rejects on NAK, timeout or no socket. */
  send(pkt: unknown, track?: boolean, timeoutMs?: number): Promise<any>;
  /** PLC command the cycle does not wait on; a failure raises the cycle
   *  error (the run holds at the next checkpoint) instead of being lost. */
  sendNoWait(pkt: unknown): void;
  /** Host timestamp in the PLC event log; never throws. */
  mark(code: number | undefined): void;
  /** Register for the next result of a camera. Resolves undefined when
   *  the reply times out and the cycle should skip the part. Call BEFORE
   *  triggering the shot. */
  waitVision(check: VisionCheck): Promise<any>;
  /** Request to the vision process. */
  sendVision(pkt: unknown): Promise<any>;
  feeder: FeederIO;
  delay(ms: number): Promise<void>;
}
