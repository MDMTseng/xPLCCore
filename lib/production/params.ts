// Every tunable of the production cycle in one place.
//
// Grouped by subsystem; units are in the names or the comments. Values
// were collected from CalibPage.tsx (2026-09-25) unchanged unless a
// comment says otherwise. Geometry is calibrated for the current cell
// layout: if the machine is repositioned, recalibrate and update here.

export type XYZ = { X: number; Y: number; Z: number };

const SAFE_Z = 12;

export const GEOMETRY = {
  /** Travel height (mm) above everything in the cell. */
  SAFE_Z,
  /** Part height (mm) added to the inspection pose while holding a part. */
  OBJECT_HEIGHT: 2.4 + 1,
  /** Pick depth offset (mm) above the predicted part Z. */
  PICK_Z_LIFT: 2.2,
  /** Where the side and bottom cameras see the held part. */
  INSP_LOCATION: { X: 15.618, Y: 10.330, Z: 0.7 + 0.4 } as XYZ,
  /** Slot 0 of the tape under the top camera. */
  SLOT_LOCATION: { X: 41.7, Y: -79.752, Z: -11.400 } as XYZ,
  /** Tape pitch (mm): slot n is SLOT_LOCATION.X + n * SLOT_PITCH_MM. */
  SLOT_PITCH_MM: 8,
  /** Drop back to the feeder (part not NG, just not placeable now). */
  TOSS_FEEDER: { X: -61.074, Y: 60.775, Z: 9 } as XYZ,
  /** NG bin for parts picked back out of the tape (top check NG). */
  TOSS_TAPE_NG: { X: -31, Y: 8.7, Z: 5 } as XYZ,
  /** NG bin for parts failing side/bottom inspection. */
  TOSS_PART_NG: { X: -63.321, Y: 9.870, Z: 5 } as XYZ,
  /** Standby above the feeder while its camera looks. */
  FEEDER_STANDBY: { X: -46.350, Y: 30.181, Z: SAFE_Z } as XYZ,
} as const;

export const MOTION = {
  /** Default feed (mm/s); ACC/DEA/JERK derive from it (feedConfig). */
  FEED: 2000,
  /** Corner blending for the setup moves. */
  CORNER: 45,
};

/** F/ACC/DEA/JERK for a feed rate, the ratios the cell was tuned with. */
export function feedConfig(feed: number = 25) {
  return { F: feed, JERK: feed * 400, ACC: feed * 100, DEA: feed * 100 };
}

export const TAPE = {
  /** Reel axis units per tape cell (matches the bench ReelGo button). */
  REEL_CELL_DISTANCE: 8,
  REEL_MOVE: { F: 5000, ACC: 100000, DEA: 10000, JERK: 100000 },
  REEL_STOP_TIMEOUT_MS: 3000,
  /** After the reel stops, before the top-camera lights. */
  REEL_SETTLE_MS: 20,
  /** The top shots wait until the arm is this far (mm) from above the
   *  middle slot, so the arm is out of the camera's view. */
  TOP_CAM_CLEAR_MM: 40,
  /** Delay (ms) between the top camera's side-light and front-light shot. */
  TOP_SHOT_GAP_MS: 80,
  /** Cells the top camera sees at once; a packed advance never exceeds
   *  the OK run it has seen. */
  VIEW_CELLS: 3,
  /** Max cells per packed advance (the OK run inside the view). */
  MAX_PACK_ADVANCE: 2,
  /** Max cells per empty-segment advance. Was 2 (one tape step + top check
   *  per 2 empty cells, ~0.5 s each); empty cells need no inspection, so a
   *  whole segment now moves in one step (the reel move is ~60 ms). */
  MAX_EMPTY_ADVANCE: 60,
  /** First id of the top-shot events (a counter per shot pair). */
  TOP_SHOT_EVENT_ID_BASE: 900000,
};

export const INSPECTION = {
  /** A-axis angle (deg) at which the part is shown to the cameras. */
  BASE_ANGLE_DEG: 90,
  /** Fixed A trim (deg) added to the bottom-camera angle correction. */
  ANGLE_TRIM_DEG: -8,
  /** While the rectified side result is pending, park this fraction of
   *  the way from inspection toward the slot (0 = stay, 1 = above slot).
   *  Right above the tape made it shake on the machine (2026-09-24). */
  PRE_PLACE_FRACTION: 0.5,
  /** Reject when the bottom-camera part offset exceeds this (mm). */
  MAX_ARM_OFFSET_MM: 5,
  /** Reject (and raise ERROR) when the tape hole offset exceeds this (mm). */
  MAX_SLOT_HOLE_OFFSET_MM: 1.5,
  /** Strobe lengths (ms). */
  SIDE_STROBE_MS: 5,
  BTM_STROBE_MS: 4,
};

export const NOZZLE = {
  /** Dwell (s) after suction on before lifting a part from the feeder. */
  PICK_DWELL_S: 0.02,
  /** Dwell (s) before releasing a part in the tape. */
  PLACE_DWELL_S: 0.01,
  /** Vacuum-break blow (ms) when placing. */
  PLACE_BLOW_MS: 20,
  /** Dwell (s) after the blow before lifting from the tape. */
  PLACE_LIFT_DWELL_S: 0.02,
  /** Vacuum-break blow (ms) when tossing. */
  TOSS_BLOW_MS: 5,
  /** Z below the slot (mm) when picking an NG part out of the tape. */
  NG_PICK_DEPTH_MM: 0.5,
  NG_PICK_DWELL_S: 0.1,
};

export const FEEDER = {
  /** Vibration pulse (ms) that spreads the parts. */
  SPREAD_VIB_MS: 160,
  /** Pause (ms) after the vibration before braking. */
  SETTLE_MS: 120,
  /** Brake time (ms); the camera shoots after it. Tune on the machine. */
  BRAKE_MS: 700,
  /** Light-on delay (ms) before the feeder camera strobe. */
  LIGHT_LEAD_MS: 10,
  CAMERA_STROBE_MS: 50,
  /** Below this many parts seen, also refill the plate from storage. */
  REFILL_BELOW_PARTS: 25,
  REFILL_VIB_MS: 700,
};

export const VISION = {
  /** A vision reply that does not come within this time is a timeout. */
  REPLY_TIMEOUT_MS: 10000,
  /** Consecutive timeouts that stop the run (1 = the first one). */
  TIMEOUT_STOP_COUNT: 1,
};

export const WATCHDOG = {
  /** Input watchdog poll period (ms). */
  POLL_MS: 400,
  /** Reel-wheel feed pulse (ms) while the carrier tape is lacking. */
  REEL_FEED_PULSE_MS: 70,
  /** Polls with the tape lacking before it is an error. */
  REEL_LACKING_LIMIT: 10,
  /** Failed input reads in a row before it is an error. */
  READ_FAILURE_LIMIT: 3,
};
