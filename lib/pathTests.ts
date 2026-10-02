// Test paths for the delta (the same as the Python tools, so UI and tool
// numbers compare): speed in % of F 2000 mm/s, ACC 200000 mm/s^2, JERK
// 800000 mm/s^3 (tools/filter_sweep.py, arrival_phases.py).
//
// - squareDip: the square +-half at Z 0, a dip to `dip` at each corner with
//   a G4 dwell at the bottom; corners blended by `cor` (the move up from a
//   dip is an exact start). Mixes path accelerations into the shock numbers.
// - round: the 4-point square at Z 0 with the corners blended by `cor`
//   (49 of 50 = nearly a circle). A smooth path: torque spikes on it come
//   from the drives' stale targets (doc_review/asda_stale_target_2026-09-30.md).
//
// - zUpDown: X0 Y0, Z 0 <-> `dip` and back, an exact stop (G4 1 ms) at each
//   end. All three joints turn alike, the simplest motion to watch on a
//   drive scope.
//
// Shock is judged on `round`, settling on PnP stops (tools/settle_test.py).

export type PathKind = 'round' | 'squareDip' | 'zUpDown';

export const kinAt = (speedPct: number) => {
  const s = Math.max(0.01, speedPct / 100);
  return { F: 2000 * s, ACC: 200000 * s, DEA: 200000 * s, JERK: 800000 * s };
};

export function buildPath(kind: PathKind, speedPct: number, laps: number,
                          o: { half?: number; dip?: number; cor?: number; dwell?: number } = {}): any[] {
  const h = o.half ?? 50;
  const kin = kinAt(speedPct);
  const corners: [number, number][] = [[-h, -h], [h, -h], [h, h], [-h, h]];
  const pk: any[] = [];
  for (let l = 0; l < laps; l++) {
    if (kind === 'zUpDown') {
      // Gentle ramps for a short stroke: ACC = 10 x F, JERK = 10 x ACC (the
      // circle's 100 x F would be ~14 g at 70 %).
      const zk = { F: kin.F, ACC: kin.F * 10, DEA: kin.F * 10, JERK: kin.F * 100 };
      for (const z of [o.dip ?? -15, 0]) {
        pk.push({ ...zk, type: 'M', cmd: 'G1', X: 0, Y: 0, Z: z, Cor: 0 });
        pk.push({ type: 'M', cmd: 'G4', P: 0.001 });
      }
      continue;
    }
    for (const [x, y] of corners) {
      if (kind === 'round') {
        pk.push({ ...kin, type: 'M', cmd: 'G1', X: x, Y: y, Z: 0, Cor: o.cor ?? 49 });
      } else {
        const cor = o.cor ?? 14;
        pk.push({ ...kin, type: 'M', cmd: 'G1', X: x, Y: y, Z: 0, Cor: cor });
        pk.push({ ...kin, type: 'M', cmd: 'G1', X: x, Y: y, Z: o.dip ?? -15, Cor: cor });
        pk.push({ type: 'M', cmd: 'G4', P: o.dwell ?? 0.01 });
        pk.push({ ...kin, type: 'M', cmd: 'G1', X: x, Y: y, Z: 0, Cor: 0 });
      }
    }
  }
  return pk;
}

// Laps that surely last `seconds` at this speed (the stream is stopped on
// time; extra laps are never sent).
export const lapsFor = (kind: PathKind, speedPct: number, seconds: number) => {
  const f = kinAt(speedPct).F;                       // mm/s
  const perimeter = kind === 'round' ? 330 : kind === 'zUpDown' ? 2 * 15 : 400 + 4 * 2 * 15;
  return Math.max(1, Math.ceil((seconds * f) / perimeter * 1.5) + 2);
};

// The drives' shock / stale numbers (PLC SYS FB_STATS and DEM_STATS).
export type AxisStats = { latePct: number; tqMax: number; ge20: number; ge50: number };

export function parseStats(fb: any, dem: any): AxisStats[] {
  const out: AxisStats[] = [];
  for (let k = 0; k < 3; k++) {
    const ft = String(fb?.[`ft${k}`] ?? '').split(',').filter(Boolean).map(Number);
    const h = String(dem?.[`dl${k}`] ?? '').split(',').filter(Boolean).map(Number);
    const moving = h.slice(1).reduce((a, b) => a + b, 0);
    out.push({
      latePct: moving ? (100 * Number(dem?.[`dlate${k}`] ?? 0)) / moving : NaN,
      tqMax: Number(fb?.[`ftm${k}`] ?? NaN) / 10,
      ge20: (ft[6] ?? 0) + (ft[7] ?? 0),
      ge50: ft[7] ?? 0,
    });
  }
  return out;
}
