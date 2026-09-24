import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Divider, Popconfirm, Popover, Typography } from 'antd';
import { JoggingPad } from '../JoggingPad';
import { Modal } from '../Modal';
import type { COMCtrlObj } from '../types';
import { delay } from '../utils/async';
import { t, type UILang } from '../i18n';
import { useHarnessAction } from '../harness/registry';
import { cmd } from '../lib/protocol';
import { GEOMETRY, TAPE, INSPECTION, NOZZLE, FEEDER, VISION, WATCHDOG, MOTION, feedConfig } from '../lib/production/params';
import { applyAdvance, cellsDone, nextCycleAction, remainingPlan } from '../lib/production/plan';
import { VISION_CHECK } from '../lib/production/io';
import type { Machine } from '../lib/production/machine';
import { tapeStep, type TopView } from '../lib/production/tape';
import { refillFeeder, type FeederPart } from '../lib/production/feeder';
import { pickFromFeeder, pickFromTape, placePart, tossTo } from '../lib/production/nozzle';
import { judgePlacement, type Bin } from '../lib/production/judge';
import type { PlanState } from '../lib/protocol';


import {
  buildCalibrationModel,
  predictRobotCoordinates,
  type CalibrationParameters,
  type CalibrationRecord,
} from '../utils/calibration';

const fsPromises = (window as any).require('fs/promises');


// Module-scope config. `as const` keeps the literal types so `IO_Pins.O.X`
// is `number` (specifically the literal), not widened. Single source of
// truth for the I/O bitmap; if you add a pin, add it here, not at the
// call site.
const IO_Pins = {
  I: {
    ReelLacking: 8,                 // 256
    PackedReelNoProtrusion: 11,     // 2048
    ReelTapeHTension: 9,            // 512
    ReelPressRollerInPlace: 10,     // 1024
  },
  O: {
    Nozzle_suck: 0,
    Nozzle_blow: 1,
    CAM_Top_SideLight: 3,
    FlexVib_brake: 5,
    ReelAdv: 6,
    ReelWheelFeed: 7,
    CAM_Side: 8,
    CAM_Side_Light0: 9,
    CAM_Btm: 10,
    CAM_Btm_Light0: 11,
    CAM_FlexFeeder: 12,
    CAM_FlexFeeder_Light0: 13,
    CAM_Top: 14,
    CAM_Top_Light0: 15,
  },
} as const;

// Host marks in the PLC event log (cmd.EvtMark). Keep in step with
// tools/sim/event_log.py MARKS.
const EVT = {
  TOP_RESULT: 1,
  BTM_RESULT: 2,
  SIDE_RESULT: 3,
  FEEDER_RESULT: 4,
  VIB_ON: 5,
  VIB_OFF: 6,
  FEEDER_LIGHT_ON: 7,
  FEEDER_LIGHT_OFF: 8,
  PLACE: 9,
  TOSS: 10,
} as const;
const EVT_BY_RESULT: Record<string, number> = {
  TOPCheckData: EVT.TOP_RESULT,
  BTMCheckData: EVT.BTM_RESULT,
  SideCheckData: EVT.SIDE_RESULT,
  FFeederCheckData: EVT.FEEDER_RESULT,
};

// Vision check IDs — match the IDs the vision plugin replies with.
const FFeederCheckID = 104500;
const SideCheckID    = 114500;
const BTMCheckID     = 124500;
const TOPCheckID     = 134500;

// Cartesian setpoints (mm): see lib/production/params.ts (GEOMETRY).
const SAFE_Z = GEOMETRY.SAFE_Z;
const OBJECT_HEIGHT = GEOMETRY.OBJECT_HEIGHT;
const PICK_Z_LIFT = GEOMETRY.PICK_Z_LIFT;

// camTrig — camera+light strobe pulse. Both pins go high simultaneously,
// PLC auto-resets after reset_ms. Always strobes for the same duration on
// both channels, matching the strobe-driver hardware.
function camTrig(camPinIdx: number, lightPinIdx: number, opts: {
  reset_ms: number;
  motion_progress?: number;
  motion_id_offset?: number;
}) {
  const mask = (1 << camPinIdx) | (1 << lightPinIdx);
  return cmd.M4({ pin: mask, state: mask, ...opts });
}

type XYZ = { X: number; Y: number; Z: number };
const INSP_LOCATION: XYZ = GEOMETRY.INSP_LOCATION;
const SLOT_LOCATION: XYZ = GEOMETRY.SLOT_LOCATION;
const TOSS_LOCATION_0: XYZ = GEOMETRY.TOSS_FEEDER;
const TOSS_LOCATION_1: XYZ = GEOMETRY.TOSS_TAPE_NG;
const TOSS_LOCATION_2: XYZ = GEOMETRY.TOSS_PART_NG;
const WAIT_FLEXFEEDER_LOCATION: XYZ = GEOMETRY.FEEDER_STANDBY;



export const CalibPage: React.FC<{
  COMCtrlObj:COMCtrlObj,
  env_path: string,
  lib_path: string,
  UI_path: string,
  uiLang: UILang,
}> = ({
  COMCtrlObj,
  env_path,
  lib_path,
  UI_path,
  uiLang,
}) => {
  const [calibRecPair, setCalibRecPair] = useState<CalibrationRecord[]>([]);
  const [isJoggingModalOpen, setIsJoggingModalOpen] = useState(false);
  const [latestObjArr, setLatestObjArr] = useState<{x:number,y:number,angle_deg:number,surround_clear:number,center_clear:number}[]>([]);
  const [calibParams, setCalibParams] = useState<CalibrationParameters | null>(null);
  // RunCtx — mutable scratch shared across the runAllObjects pipeline,
  // input watchdog, and harness callbacks. Lives outside React state on
  // purpose: most fields are write-then-read inside a single async tick
  // and don't drive renders. Anything that DOES drive UI goes through
  // setState; this ref only holds in-flight loop state.
  type RunCtx = {
    // Loop control
    isRunning?: boolean;
    run_cycle_stop?: boolean;
    visionTimeouts?: number;      // vision replies timed out in a row (waitForCheckData)
    current_error?: { errorString: string; raw?: any; fc?: any } | undefined;
    BurnRunning?: boolean;
    BurnRunningStopTrigger?: boolean;
    runButtonEl?: HTMLElement | null;
    stepMode?: boolean;
    tossPauseMode?: boolean;
    stepMode_resolve?: ((value?: any) => void) | undefined;
    // Vision-result handoff (Promise pendings filled by RX callback)
    FFeederCheckData_Promise?: { resolve: (v: any) => void; reject: (e: any) => void };
    TOPCheckData?: any;
    TOPCheckData_Promise?: { resolve: (v: any) => void; reject: (e: any) => void };
    SideCheckData?: any;
    SideCheckData_Promise?: { resolve: (v: any) => void; reject: (e: any) => void };
    BTMCheckData?: any;
    BTMCheckData_Promise?: { resolve: (v: any) => void; reject: (e: any) => void };
    // Production-plan walker (initialised before the loop reads them).
    production_plan?: number[];
    production_plan_original?: number[];
    production_plan_stageIndex?: number;
    // Throughput / counters
    lastPackCount?: number;
    packCountOffset?: number;
    packTimestamps?: number[];
    speedStartTime?: number;
    // Revisit (manual recheck) state
    revisit_idx?: number;
    revisit_obj_idx?: number;
    SL_sens_alpha?: number;
    // Jogging helper (set in MiscControlsPage but typed here so the
    // shared shape is single-source)
    jog_base_location?: any;
    // Catch-all for adhoc debug fields used in commented experiments;
    // remove once those are deleted (W4 #7).
    [k: string]: any;
  };
  const _this = useRef<RunCtx>({}).current;
  const sendTcpMsgPack = COMCtrlObj.sendTcpMsgPack;
  // Host mark in the PLC event log: fire-and-forget (no reply awaited, no
  // throw) so timing marks can never disturb the cycle.
  const evtMark = (code: number | undefined) => {
    if (code === undefined) return;
    try { (sendTcpMsgPack as any)(cmd.EvtMark(code), false); } catch {}
  };
  const VP_sendTcpMsgPack = COMCtrlObj.VP_sendTcpMsgPack;
  const FlexVibCtrl = COMCtrlObj.FlexVibCtrl;
  
  const [runningState, setRunningState] = useState<string>("idle");
  const [tossInfo, setTossInfo] = useState<any>();

  const [packInfoString, setPackInfoString] = useState<string>("");
  const [packSpeedInfo, setPackSpeedInfo] = useState<{ count: number; overallHr: number; recentHr: number; ngCount: Record<string, number> } | null>(null);
  const [productionPlanTick, setProductionPlanTick] = useState<number>(0);

  const [stepMode, setStepMode] = useState<boolean>(false);
  const [tossPauseMode, setTossPauseMode] = useState<boolean>(false);
  // F3: per-pin flip counts for the named input pins. PLC counts every
  // edge in `getDigitalInputFlipCount.fc[pin]`; surfacing them lets the
  // operator see "did the press-roller sensor actually toggle on the
  // last reel advance" without scope-probing IO. Polled at 2Hz while
  // mounted; the call is cheap (single msgpack RTT, no PLC side-effect).
  const [flipCountSnapshot, setFlipCountSnapshot] = useState<{ raw: number; fc: number[] } | null>(null);
  // Check IDs are now module-level constants (FFeederCheckID, etc.).

  const loadCalibData = useCallback(async () => {
    const filePath = `${env_path}/calib.json`;

    try {
      const fileContent = await fsPromises.readFile(filePath, 'utf8');
      const tmpCalibRecPair: CalibrationRecord[] = JSON.parse(fileContent);
      const params = buildCalibrationModel(tmpCalibRecPair);

      console.log(params);
      setCalibRecPair(tmpCalibRecPair);
      setCalibParams(params);
    } catch (err) {
      console.error('Error reading file:', err);
    }
  }, [env_path]);


  useEffect(()=>{
    loadCalibData();
  },[loadCalibData]);

  // F3: 2Hz poll of getDigitalInputFlipCount. We pull both `raw` (current
  // input bitmask) and `fc` (per-pin edge counter). The badge below uses
  // these to show live state + edge totals for the four named reel-side
  // input pins. Best-effort: a transient PLC NAK shouldn't break the page.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      // While a run is on, the input watchdog already reads the same
      // counters every WATCHDOG.POLL_MS and publishes them (_this.latestInputs):
      // don't send a second poller's worth of packets to the PLC.
      if (_this.isRunning === true) {
        const w = _this.latestInputs;
        if (w) setFlipCountSnapshot({ raw: w.raw, fc: [...w.fc] });
        return;
      }
      try {
        const rep: any = await COMCtrlObj.sendTcpMsgPack(cmd.GetDigitalInputFlipCount());
        if (!cancelled && rep && Array.isArray(rep.fc)) {
          setFlipCountSnapshot({
            raw: typeof rep.raw === 'number' ? rep.raw : 0,
            fc: rep.fc.map((n: any) => Number(n) | 0),
          });
        }
      } catch { /* best-effort */ }
    };
    tick();
    const id = setInterval(tick, 500);
    return () => { cancelled = true; clearInterval(id); };
  }, [COMCtrlObj.sendTcpMsgPack]);


  useEffect(()=>{



    function registerPromiseTunnel(id:number,name:string){
      _this[name]=undefined;

      if(_this[name+"_Promise"]!=undefined){
        _this[name+"_Promise"].reject(new Error("it's a left over promise of "+name));
        _this[name+"_Promise"]=undefined;
      }
      COMCtrlObj.VP_regTcpMsgCB(id,undefined);//force unload previous leftover
      COMCtrlObj.VP_regTcpMsgCB(id, (data: any) => {
        evtMark(EVT_BY_RESULT[name]);
        console.log("data",data);
        _this[name]=data;
        if(_this[name+"_Promise"]!=undefined){
          _this[name+"_Promise"].resolve(data);
          _this[name+"_Promise"]=undefined;
        }
      });
    }
    registerPromiseTunnel(TOPCheckID,"TOPCheckData");
    registerPromiseTunnel(BTMCheckID,"BTMCheckData");
    registerPromiseTunnel(FFeederCheckID,"FFeederCheckData");
    registerPromiseTunnel(SideCheckID,"SideCheckData");




    return () => {
      COMCtrlObj.VP_regTcpMsgCB(TOPCheckID,undefined);
      COMCtrlObj.VP_regTcpMsgCB(BTMCheckID,undefined);
      COMCtrlObj.VP_regTcpMsgCB(FFeederCheckID,undefined);
      COMCtrlObj.VP_regTcpMsgCB(SideCheckID,undefined);
    }
  },[COMCtrlObj]);



  
  // Resolved by the vision callbacks registered above (registerPromiseTunnel).
  // Bounded: a reply that never comes (vision down, callback not
  // registered, trigger lost) used to stall the cycle forever with no
  // error (review 2026-09-24 R-P0-2).
  // A timed-out reply stops the cycle (VISION_TIMEOUT_STOP = 1). Replies
  // carry only the camera's check ID, not which shot they belong to: a
  // reply that is late rather than lost would be taken as the *next*
  // part's result, and that part judged on the wrong image. Stopping lets
  // someone look (owner, 2026-09-24; it has not happened on the machine).
  // With a per-shot sequence number from vision the replies could be
  // matched exactly; then raise this to skip just the part -- callers
  // already treat undefined as "not inspected" and send it back to the
  // feeder.
  const VISION_REPLY_TIMEOUT_MS=VISION.REPLY_TIMEOUT_MS;
  const VISION_TIMEOUT_STOP=VISION.TIMEOUT_STOP_COUNT;
  function waitForCheckData(name:string):Promise<any>{
    return new Promise((resolve, reject)=>{
      const slot:any={};
      const timer=setTimeout(()=>{
        if(_this[name+"_Promise"]===slot)_this[name+"_Promise"]=undefined;
        _this.visionTimeouts=(_this.visionTimeouts??0)+1;
        if(_this.visionTimeouts>=VISION_TIMEOUT_STOP){
          reject(new Error(`no ${name} reply from vision within ${VISION_REPLY_TIMEOUT_MS} ms (${_this.visionTimeouts} timeouts in a row)`));
        }else{
          console.warn(`vision timeout: ${name} (${_this.visionTimeouts} in a row), part skipped`);
          resolve(undefined);
        }
      },VISION_REPLY_TIMEOUT_MS);
      slot.resolve=(data:any)=>{clearTimeout(timer);_this.visionTimeouts=0;resolve(data);};
      slot.reject=(err:any)=>{clearTimeout(timer);reject(err);};
      _this[name+"_Promise"]=slot;
    });
  }
  let waitForFFeederCheckData=()=>waitForCheckData("FFeederCheckData");
  let waitForTOPCheckData=()=>waitForCheckData("TOPCheckData");
  let waitForSideCheckData=()=>waitForCheckData("SideCheckData");
  let waitForBTMCheckData=()=>waitForCheckData("BTMCheckData");

  let speed = 2000
  let jerk = speed * 800

  let acc = speed * 200
  let dea = acc

  let cor = 45
  // Cartesian setpoints come from module-scope constants now (W4 #6).
  // Local aliases preserve the existing names so the body of this
  // function reads the same.
  const safe_z = SAFE_Z;
  const objectHeight = OBJECT_HEIGHT;
  const pickZ_lift = PICK_Z_LIFT;
  const inspLocation = INSP_LOCATION;
  const inspLocation_withObject = { ...inspLocation, Z: inspLocation.Z + objectHeight };
  const slotLocation = SLOT_LOCATION;
  const tossLocation_0 = TOSS_LOCATION_0;
  const tossLocation_1 = TOSS_LOCATION_1;

  const getFeedSpeedConfig = feedConfig;

  const tossLocation_2 = TOSS_LOCATION_2;

  const wait_flexfeeder_location = WAIT_FLEXFEEDER_LOCATION;


  
  type PointXYZ={X:number,Y:number,Z:number};

  let objn00_location:PointXYZ={...slotLocation};
  let objn10_location:PointXYZ={...slotLocation,X: -39.466, Y: -81.380};


  /*
    BtmCheck is a looking up camera that check the nozzle location(for calibrating) and the object location(for production).
    it's like SMT inspection camera.

    mat_offset_cam2arm is a pre-calculated matrix that convert the check_offset(camera) to arm_offset(robot).


    when in production, a nozzle will pick an object to looking up camera to check the object location.
    the object center and nozzle 0 center will obtain a offset in camera coordinate system.
    then use BtmCheckOffset2ArmOffset to convert the offset to arm coordinate system.

    targetAngleDeg, a param in BtmCheckOffset2ArmOffset, is a placing angle that the arm will rotate to after the check.


  */
  type TYPE_BtmCheckCalibInfo={
    center:{X:number,Y:number},
    mmpp:number,
    mat_offset_cam2arm:[[number,number,number],[number,number,number],[number,number,number]],//3x3 matrix
    // Set when the three calibration shots cannot give a usable matrix
    // (nozzle missing, shots on one line, scale off from the camera's own
    // mmpp). The identity fallback is still filled in, but offsets from it
    // are in pixels, not mm -- callers must stop, not run on it.
    error?:string,
  };

  function BtmCheckOffset2ArmOffset(info:TYPE_BtmCheckCalibInfo,check_offset:{X:number,Y:number},targetAngleDeg:number):{X:number,Y:number}{
    const mat = Array.isArray(info.mat_offset_cam2arm) ? info.mat_offset_cam2arm : [[1,0,0],[0,1,0],[0,0,1]];

    const camOffset = {
      X: check_offset?.X ?? 0,
      Y: check_offset?.Y ?? 0,
    };

    // Treat the offset as a vector (homogeneous coordinate with w = 0 to avoid translating offsets)
    const vec = [camOffset.X, camOffset.Y, 0];

    const rawArmOffset = {
      X:
        ((mat[0]?.[0] ?? 1) * vec[0]) +
        ((mat[0]?.[1] ?? 0) * vec[1]) +
        ((mat[0]?.[2] ?? 0) * vec[2]),
      Y:
        ((mat[1]?.[0] ?? 0) * vec[0]) +
        ((mat[1]?.[1] ?? 1) * vec[1]) +
        ((mat[1]?.[2] ?? 0) * vec[2]),
    };

    // Rotate the offset into the target placement frame
    const angleRad = (targetAngleDeg * Math.PI) / 180;
    const cosTheta = Math.cos(angleRad);
    const sinTheta = Math.sin(angleRad);

    const offset_arm = {
      X: rawArmOffset.X * cosTheta - rawArmOffset.Y * sinTheta,
      Y: rawArmOffset.X * sinTheta + rawArmOffset.Y * cosTheta,
    };

    return offset_arm;
  }
  function BtmCheckObjLoc2ArmOffset(info:TYPE_BtmCheckCalibInfo,objloc:{X:number,Y:number},targetAngleDeg:number):{X:number,Y:number}{
    let check_offset={X:objloc.X-info.center.X,Y:objloc.Y-info.center.Y};
    console.log("check_offset",check_offset);
    return BtmCheckOffset2ArmOffset(info,check_offset,targetAngleDeg);
  }


  async function BtmCheckCalib()
  { 
    
    let armOffset_0_0={X:0,Y:0};
    let armOffset_0_1={X:0,Y:1};
    let armOffset_1_0={X:1,Y:0};
      
    let rep0_0=await checkNozzleLocation(armOffset_0_0);
    let nozzleLoc0_0={X:rep0_0.nozzle_pose.x,Y:rep0_0.nozzle_pose.y};
      
    let rep0_1=await checkNozzleLocation(armOffset_0_1);
    let nozzleLoc0_1={X:rep0_1.nozzle_pose.x,Y:rep0_1.nozzle_pose.y};

    
    let rep1_0=await checkNozzleLocation(armOffset_1_0);
    let nozzleLoc1_0={X:rep1_0.nozzle_pose.x,Y:rep1_0.nozzle_pose.y};

    console.log("nozzleLoc0_0",nozzleLoc0_0);
   console.log("nozzleLoc0_1",nozzleLoc0_1);
   console.log("nozzleLoc1_0",nozzleLoc1_0);

    const delta_cam_y = {
      X: nozzleLoc0_1.X - nozzleLoc0_0.X,
      Y: nozzleLoc0_1.Y - nozzleLoc0_0.Y,
    };
    const delta_cam_x = {
      X: nozzleLoc1_0.X - nozzleLoc0_0.X,
      Y: nozzleLoc1_0.Y - nozzleLoc0_0.Y,
    };

    const det = delta_cam_x.X * delta_cam_y.Y - delta_cam_x.Y * delta_cam_y.X;
    let mat_offset_cam2arm: TYPE_BtmCheckCalibInfo["mat_offset_cam2arm"];
    let calibError: string | undefined;

    // A 1 mm arm step must move the nozzle by a few pixels at least, along
    // two clearly different directions (|sin| of the angle between them).
    const stepX = Math.hypot(delta_cam_x.X, delta_cam_x.Y);
    const stepY = Math.hypot(delta_cam_y.X, delta_cam_y.Y);
    if ([rep0_0, rep0_1, rep1_0].some((r) => r?.nozzle_pose?.status !== 1)) {
      calibError = 'nozzle not found in a calibration shot';
    } else if (stepX < 1 || stepY < 1) {
      calibError = `nozzle did not move between calibration shots (${stepX.toFixed(2)}px, ${stepY.toFixed(2)}px per mm)`;
    } else if (Math.abs(det) < 0.1 * stepX * stepY) {
      calibError = 'calibration shots are (nearly) colinear';
    }

    if (calibError === undefined && Math.abs(det) > 1e-9) {
      const invDet = 1 / det;
      mat_offset_cam2arm = [
        [delta_cam_y.Y * invDet, -delta_cam_y.X * invDet, 0],
        [-delta_cam_x.Y * invDet, delta_cam_x.X * invDet, 0],
        [0, 0, 1],
      ];
    } else {
      console.warn("BtmCheckCalib: " + calibError + "; identity matrix placeholder.");
      mat_offset_cam2arm = [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
      ];
    }

    const mmppFromMatSamples = [
      Math.hypot(mat_offset_cam2arm[0][0], mat_offset_cam2arm[1][0]),
      Math.hypot(mat_offset_cam2arm[0][1], mat_offset_cam2arm[1][1]),
    ].filter((value) => Number.isFinite(value) && value > 0);

    let mmpp =
      mmppFromMatSamples.length > 0
        ? mmppFromMatSamples.reduce((sum, value) => sum + value, 0) / mmppFromMatSamples.length
        : NaN;

    if (!Number.isFinite(mmpp)) {
      const mmppCandidates = [rep0_0.mmpp, rep0_1.mmpp, rep1_0.mmpp].filter(
        (value): value is number => Number.isFinite(value) && value > 0
      );

      if (mmppCandidates.length > 0) {
        mmpp = mmppCandidates.reduce((sum, value) => sum + value, 0) / mmppCandidates.length;
      }
    }

    if (!Number.isFinite(mmpp)) {
      const avgCamStep =
        (Math.hypot(delta_cam_x.X, delta_cam_x.Y) + Math.hypot(delta_cam_y.X, delta_cam_y.Y)) / 2;
      mmpp = avgCamStep > 0 ? 1 / avgCamStep : NaN;
    }

    // Cross-check the derived scale against what the camera reports.
    const camMmpp = [rep0_0.mmpp, rep0_1.mmpp, rep1_0.mmpp].filter((v) => Number.isFinite(v) && v > 0);
    if (calibError === undefined && camMmpp.length > 0) {
      const ref = camMmpp.reduce((a, b) => a + b, 0) / camMmpp.length;
      if (!(Math.abs(mmpp - ref) <= 0.2 * ref)) {
        calibError = `derived mmpp ${mmpp} differs from camera mmpp ${ref} by more than 20%`;
      }
    }

    const btmCheckInfo: TYPE_BtmCheckCalibInfo = {
      center: {...nozzleLoc0_0},
      mmpp,
      mat_offset_cam2arm,
      error: calibError,
    };


    
    // console.log("btmCheckInfo",btmCheckInfo);
    // console.log("BtmCheckObjLoc2ArmOffset",BtmCheckObjLoc2ArmOffset(btmCheckInfo,nozzleLoc1_0,0));
    // console.log("BtmCheckOffset2ArmOffset",BtmCheckOffset2ArmOffset(btmCheckInfo,{X:20,Y:0},180));

    return btmCheckInfo;
  }



  //{
  //     "status": 1,
  //     "obj_pose": {
  //         "x": 685.0157,
  //         "y": 1123.204,
  //         "ang": -16.25761,
  //         "status": 1
  //     },
  //     "nozzle_pose": {
  //         "x": 0,
  //         "y": 0,
  //         "ang": 0,
  //         "status": 0
  //     },
  //     "mmpp": 0.012407
  // }

  type NozzleCheckData={status:number,obj_pose:{x:number,y:number,ang:number,status:number},nozzle_pose:{x:number,y:number,ang:number,status:number},mmpp:number};
  const checkNozzleLocation=async(offset:{X:number,Y:number}={X:0,Y:0}):Promise<NozzleCheckData>=>{


    let rep_promise= waitForBTMCheckData();


    //move to safe_z
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0,F:speed,Cor:cor,ACC:acc,DEA:dea,JERK:jerk }))
    //move to inspLocation
    await sendTcpMsgPack(cmd.G1({ "X": inspLocation.X+offset.X,"Y":inspLocation.Y+offset.Y,"A":0 }))

    //drop Z to inspLocation.Z
    await sendTcpMsgPack(cmd.G1({ "Z":inspLocation.Z,"A":0 }))
    
    await sendTcpMsgPack(camTrig(IO_Pins.O.CAM_Btm, IO_Pins.O.CAM_Btm_Light0, { reset_ms: 50 }))
    sendTcpMsgPack(cmd.G4(0.001))

    // safeZ
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }))


    return await rep_promise as NozzleCheckData;
    
  }

  

  const goToSlotLocation=async()=>{

    //move to safe_z
    let mult=0.1;
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0,F:speed*mult,Cor:cor*mult,ACC:acc*mult,DEA:dea*mult,JERK:jerk*mult }))
    //move to inspLocation
    await sendTcpMsgPack(cmd.G1({ "X": slotLocation.X,"Y":slotLocation.Y,"A":0 }))

    //drop Z to inspLocation.Z
    await sendTcpMsgPack(cmd.G1({ "Z":slotLocation.Z,"A":0 }))
    
    // safeZ
    // await sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 2 })
    // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z,"A":0 })
    
  }


  
  const testBurn=async()=>{
    if(_this.BurnRunning==true){

      return;
    }

    let mult=1;
    _this.BurnRunning=true;
    _this.BurnRunningStopTrigger=false;
    if(false)
    {

      let scale=1.0;

      let R=100*scale;
      await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0,F:speed*mult,Cor:R*10,ACC:acc*mult,DEA:dea*mult,JERK:jerk*mult }))
      
  
      for(let i=0;_this.BurnRunningStopTrigger==false;i++){
        let theta=0;
        let loc0={X: R*Math.cos(theta), Y: R*Math.sin(theta)};
        theta+=120*Math.PI/180;
        let loc1={X: R*Math.cos(theta), Y: R*Math.sin(theta)};
        theta+=120*Math.PI/180;
        let loc2={X: R*Math.cos(theta), Y: R*Math.sin(theta)};
  
  
        await sendTcpMsgPack(cmd.G1({ "X": loc0.X,"Y":loc0.Y}))
  
        await sendTcpMsgPack(cmd.G1({ "X": loc1.X,"Y":loc1.Y}))
        await sendTcpMsgPack(cmd.G1({ "X": loc2.X,"Y":loc2.Y}))
  
        
        //await delay(500);
  
      }
    }
    else
    {
      mult=0.7;
      await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0,F:speed*mult,Cor:15,ACC:acc*mult,DEA:dea*mult,JERK:jerk*mult }))
      for(let i=0;_this.BurnRunningStopTrigger==false;i++){
        //move to safe_z
        let scale=Math.random()*0.7+0.3;
        let loc1={X: 75.227*scale, Y: -72.697*scale};
  
        
        let loc2={X: 13.597*scale, Y: 121.228*scale};
        let loc3={X: -91.321*scale, Y: 0.470*scale};
  
        if(i%2==0)
        {
          scale=1;
          loc1={X: 8.535*scale, Y: 125.208*scale};
          loc2={X: 6.823*scale, Y: -17.430*scale};
          loc3={X: 63.371*scale, Y: -96.207*scale};
    
        }
  
        await sendTcpMsgPack(cmd.G1({ "X": loc1.X,"Y":loc1.Y}))
        await sendTcpMsgPack(cmd.G1({ "Z":slotLocation.Z}))
        await sendTcpMsgPack(cmd.G4(0.01))
        await sendTcpMsgPack(cmd.G1({ "Z":safe_z }))
  
        await sendTcpMsgPack(cmd.G1({ "X": loc2.X,"Y":loc2.Y}))
        await sendTcpMsgPack(cmd.G1({ "Z":slotLocation.Z}))
        await sendTcpMsgPack(cmd.G4(0.01))
        await sendTcpMsgPack(cmd.G1({ "Z":safe_z }))
  
        await sendTcpMsgPack(cmd.G1({ "X": loc3.X,"Y":loc3.Y}))
        await sendTcpMsgPack(cmd.G1({ "Z":slotLocation.Z}))
        await sendTcpMsgPack(cmd.G4(0.01))
        await sendTcpMsgPack(cmd.G1({ "Z":safe_z }))
  
  
        
        //await delay(500);
  
      }
      _this.BurnRunning=false;
    }

      
    // safeZ
    // await sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 2 })
    // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z,"A":0 })
    
  }

  const stopTestBurn=async()=>{
    _this.BurnRunningStopTrigger=true;
  }


  async function getDigitalInputFlipCount():Promise<{raw:number, fc:number[]}>{
    let rep=await sendTcpMsgPack(cmd.GetDigitalInputFlipCount());
    return {raw:(rep.raw as number)??0, fc:(rep.fc as number[])??new Array(16).fill(0)};
  }


  const runAllObjects=(async(runinng_checkpoint:(checkpoint_name:string,data:any)=>Promise<any>)=>{

    if(_this.isRunning==true){
      throw new Error("runAllObjects isRunning is true");
    }
    _this.isRunning=true;
    if(calibParams == null){
      _this.isRunning=false;
      return;
    }
    // COMCtrlObj.regTcpMsgCB(134500,undefined);
    
    // COMCtrlObj.regTcpMsgCB(134500, (data: any) => {
    //   console.log("data",data);
    // });

    // Tunables: lib/production/params.ts (tape, inspection, nozzle, feeder).
    const slotDist=GEOMETRY.SLOT_PITCH_MM;
    const {REEL_CELL_DISTANCE, REEL_STOP_TIMEOUT_MS, REEL_SETTLE_MS, TOP_CAM_CLEAR_MM}=TAPE;
    const REEL_ADV_MOVE=TAPE.REEL_MOVE;
    const {PRE_PLACE_FRACTION}=INSPECTION;
    let topShotEventId=TAPE.TOP_SHOT_EVENT_ID_BASE;

    // PLC sends for the cycle. sendTcpMsgPack returns `false` when there is
    // no socket, and `await false` used to carry on as if the command had
    // been accepted. `send` throws instead, so the run stops visibly.
    // `sendNoWait` is for commands the cycle does not wait on: a NAK
    // (e.g. flyevent_buffer_full, so a camera never fires) used to be
    // lost; it now raises current_error and the cycle holds at the next
    // checkpoint instead of running into a vision timeout.
    const send=(pkt:any, ...rest:any[]):Promise<any>=>{
      const r=(sendTcpMsgPack as any)(pkt, ...rest);
      if(r===false) return Promise.reject(new Error("PLC not connected ("+(pkt?.cmd??pkt?.type)+")"));
      return Promise.resolve(r);
    };
    const sendNoWait=(pkt:any):void=>{
      send(pkt).catch((e:any)=>{
        console.error("command failed",pkt?.cmd,e?.message??e);
        if(_this.current_error==undefined) _this.current_error={errorString:"PLC 指令失敗 ("+(pkt?.cmd??"?")+"): "+(e?.message??e)};
      });
    };


    _this.plan_mismatch=0;
    try{
      console.log("[PLAN] sync:", await syncPlanWithPlc(send));
    }catch(e:any){
      // An older PLC program without PLAN_* NAKs: run on the renderer's plan.
      console.warn("[PLAN] PLC plan sync failed, renderer plan only:", e?.message??e);
    }

    await runinng_checkpoint("start",{time:Date.now()});

    // trig: when the tape may move, as a WAIT_FOR_TRIGGER_MOTION_PROGRESS
    // relative to the motion queued when this is called.
    // The cell as the cycle modules see it (lib/production/machine.ts).
    const machine:Machine={
      send, sendNoWait, mark:evtMark,
      waitVision:(c)=>waitForCheckData(VISION_CHECK[c].name),
      sendVision:(pkt)=>VP_sendTcpMsgPack(pkt),
      feeder:FlexVibCtrl,
      delay,
    };

    // Toss bins (lib/production/judge.ts Bin) and where they are.
    const BIN_LOCATION:Record<Bin,{X:number,Y:number,Z:number}>={feeder:tossLocation_0, tape_ng:tossLocation_1, part_ng:tossLocation_2};
    const binOf=(loc:{X:number,Y:number,Z:number}):Bin=>loc===tossLocation_0?'feeder':loc===tossLocation_1?'tape_ng':'part_ng';

    // Tape step + top check (lib/production/tape.ts). Keeps the PLC's cell
    // count for the end-of-run comparison and flags plan disagreements.
    async function advanceTape(cells:number, kind:'pack'|'empty',
        trigger?:{motion_id_offset:number,motion_progress:number}):Promise<TopView>{
      const r=await tapeStep(machine,{cells,kind,trigger});
      if(r.cellsDone!==undefined) _this.plc_cells_done=r.cellsDone;
      if(r.planErr){
        // The PLC's plan puts a different segment kind (or fewer cells) at
        // this point of the tape than the renderer just advanced.
        console.error("[PLAN] PLC disagrees with this advance",kind,cells,"cells_done",r.cellsDone);
        _this.plan_mismatch=(_this.plan_mismatch??0)+1;
      }
      return r.view as TopView;   // undefined on a vision timeout: callers check
    }
    let start_time=Date.now();

    let packCounter=0;




    let candidate_obj_arr:FlexFeeder_object_data_type[]=[];

    // let newLatestObjArr=[...latestObjArr];




            
    await send(cmd.G1({ "Z": safe_z,"A":0,Cor:cor,...getFeedSpeedConfig(1000) }))
    
    await send(cmd.G1({"X":44,"Y":89}))
    await send(cmd.G1({"X":-20,"Y":-22,...getFeedSpeedConfig(1000 ) }));

    await runinng_checkpoint("go ready",{time:Date.now()});
    let nxt_adv_count=0;
    // Parts put in the tape but not yet counted (the count drops when the
    // tape advances past them). Drives the end-of-plan lookahead below.
    let placedUncounted=0;
    let isZinSafeZone=false;




    
    type FlexFeeder_object_data_type = FeederPart;   // lib/production/feeder.ts


    await runinng_checkpoint("BtmCheckCalib",{time:Date.now()});
    let btmCheckCalibInfo=await BtmCheckCalib();
    if(btmCheckCalibInfo.error!==undefined){
      // Outside the cycle loop's try: handle here so isRunning is cleared.
      await runinng_checkpoint("ERROR",{errorString:"BtmCheckCalib failed: "+btmCheckCalibInfo.error}).catch(()=>{});
      _this.isRunning=false;
      return;
    }

    // return;

    await send(cmd.G1({ "Z": safe_z,"A":0,...getFeedSpeedConfig(2000) }))
    // The top-camera view for the next cycle, when a tape step already made it.
    let slotCheckPromise_BK:Promise<TopView> | undefined = undefined;




    let feederCheckPromise:Promise<any> | undefined = undefined;

    let latestInputObj:any = undefined;

    _this.current_error=undefined;
    // Set when runAllObjects returns, however it ends. The watchdog used to
    // stop only on run_cycle_stop: after a plan finished or an error it
    // kept pulsing ReelWheelFeed, and every RUN started another one.
    let cycleEnded=false;
    // Input watchdog: independent 400ms poll thread. Reads digital-input
    // flip-counters (catches sub-poll glitches), drives the press-roller
    // re-feed pulse, and stamps _this.current_error so the main pipeline
    // can abort at the next checkpoint. Runs until run_cycle_stop flips.
    async function inputWatchdog(){
      let prevFc:number[]=new Array(16).fill(0);
      let ReelLackingCounter=0;
      let isFirstCycle=true;
      let readFailures=0;
      while(_this.run_cycle_stop!=true && !cycleEnded){

        // A failed read used to throw out of this loop: the watchdog died
        // silently and the cycle ran on unguarded (review 2026-09-24).
        let raw:number, fc:number[];
        try{
          ({raw,fc}=await getDigitalInputFlipCount());
          readFailures=0;
        }catch(e:any){
          readFailures++;
          console.error("input watchdog: read failed",readFailures,e?.message??e);
          if(readFailures>=WATCHDOG.READ_FAILURE_LIMIT){
            _this.current_error={errorString:"輸入讀取失敗 (input read failed): "+(e?.message??e)};
          }
          await delay(WATCHDOG.POLL_MS);
          continue;
        }

        // flip delta: how many transitions happened on each bit since last poll
        // catches glitches that reset before the next poll (PLC counts every scan ~1ms)
        if(isFirstCycle){ isFirstCycle=false; prevFc=[...fc]; continue; }

        let flipDelta=fc.map((c,i)=>Math.max(0,c-prevFc[i]));
        prevFc=[...fc];

        let ReelLacking            =(raw>>IO_Pins.I.ReelLacking)            &1;
        let ReelTapeHTension       =(raw>>IO_Pins.I.ReelTapeHTension)       &1;
        let PackedReelNoProtrusion =flipDelta[IO_Pins.I.PackedReelNoProtrusion]==0 && ((raw>>IO_Pins.I.PackedReelNoProtrusion)&1)===1;
        let ReelPressRollerInPlace =(raw>>IO_Pins.I.ReelPressRollerInPlace) &1;

        latestInputObj={PackedReelNoProtrusion,ReelLacking,ReelTapeHTension,ReelPressRollerInPlace,raw_data:{raw,fc}};
        _this.latestInputs={raw,fc};
        // console.log("ReelPressRollerInPlace",ReelPressRollerInPlace);




        if(ReelLacking)
        {
          if((ReelLackingCounter&0b1)==0)
          {
            sendNoWait(cmd.M4({ "pin": 1<<IO_Pins.O.ReelWheelFeed, "state": 1<<IO_Pins.O.ReelWheelFeed, reset_ms:WATCHDOG.REEL_FEED_PULSE_MS }))
          }
          ReelLackingCounter++;
        }
        else
        {
          ReelLackingCounter=0;
        }

        let errorString="";

        if(PackedReelNoProtrusion==false)
          errorString+="凸料感應,";
        if(ReelPressRollerInPlace==0)
          errorString+="冷封氣缸沒壓到,";
        // use flipDelta so even a brief tension spike (cleared before next poll) is caught
        if(ReelTapeHTension || flipDelta[IO_Pins.I.ReelTapeHTension]>0)
          errorString+="上蓋帶張力過強,";
        if(ReelLackingCounter>WATCHDOG.REEL_LACKING_LIMIT)
          errorString+="載帶缺料,";

        if(errorString!="")
        {
          _this.current_error={errorString,raw,fc};
          console.log("current_error",_this.current_error);
        }

        await delay(WATCHDOG.POLL_MS);
      }
      console.log("input watchdog thread end",_this.isRunning,latestInputObj);
    }
    inputWatchdog();


    // for(let i=0;_this.isRunning==true;i++)
    // {
    //   await delay(1000);
    // }

    // if(_this.isRunning==false){
    //   return;
    // }

    try{


    let waitForReelVisualClearPromise:Promise<any> | undefined = undefined;
    for(let i=0;;i++){


      let cycle_start_data=await runinng_checkpoint("cycle_start",i);

      console.log("[DBG]cycle_start_data",JSON.stringify(cycle_start_data),nxt_adv_count);

      let _PP_=cycle_start_data.production_plan;
      if(_PP_===undefined || _PP_.length==0){
        break;
      }
      else if(_PP_[0]<0)
      {
        // Empty segment: advance it in one tape step (TAPE.MAX_EMPTY_ADVANCE
        // cells). It used to go 2 cells per pass, each a full tape step,
        // top-check wait and a fixed 100 ms delay: ~5 s for -20 with the
        // arm idle. The top check of the step is the view for the next
        // cycle, so it is kept instead of shooting again.
        nxt_adv_count=0;
        const action=nextCycleAction(_PP_, placedUncounted);
        const adv_count=action.kind==='skip_empty' ? action.cells : Math.min(2,-_PP_[0]);
        // Don't wait for it: the arm picks and inspects the next part while
        // the tape moves and the top camera shoots (the arm is away from
        // the tape); the next cycle awaits the view where it needs it.
        // The PLC runs one TAPE_CYCLE at a time: finish a pending one first.
        if(slotCheckPromise_BK) await slotCheckPromise_BK;
        const emptyStep=advanceTape(adv_count,'empty');
        emptyStep.catch(()=>{});   // rejections surface where it is awaited

        await runinng_checkpoint("[STEP][REEL ADV]",{
          adv_count:adv_count,

          type:"empty",
        });
        slotCheckPromise_BK=emptyStep;
        continue;
      }
      
      // Start the tape step (advance + top shots) before waiting for a feeder
      // refill: it only needs the arm off the tape, not the feeder. It used
      // to start after the pick move, so a refill in the same cycle delayed
      // it ~0.6 s (event log, 2026-09-24).
      let slotCheckPromise:Promise<TopView> | undefined = slotCheckPromise_BK;
      await runinng_checkpoint("TOP_CAM check slot",i);
      if(slotCheckPromise==undefined){

        if(nxt_adv_count>2)nxt_adv_count=2;
        packCounter+=nxt_adv_count;
        placedUncounted=Math.max(0,placedUncounted-nxt_adv_count);
        console.log("nxt_adv_count",nxt_adv_count,"packCounter",packCounter);
        // Tape step: may start as soon as the last queued move (the Z rise
        // after placing, or the end of a toss) starts.
        slotCheckPromise= advanceTape(nxt_adv_count,'pack',{motion_id_offset:0,motion_progress:0});

        await runinng_checkpoint("[STEP][REEL ADV]",{
          adv_count:nxt_adv_count,
          type:"pack",
          packCounter:packCounter,
        });
        waitForReelVisualClearPromise=undefined;
        nxt_adv_count=0;
      }
      await runinng_checkpoint("_PACK_INFO_",{packCounter:packCounter});
      slotCheckPromise_BK=slotCheckPromise;
      // Plan done? The pack count drops when the tape advances, which is the
      // tape step just started -- so check again here, before picking. It
      // used to pick one more part, inspect it and toss it back ("place
      // count hit"), then pick and toss another ("plan is empty"): two
      // wasted cycles at the end of every batch. Let the last advance and
      // top check finish, then stop.
      if((_this.production_plan ?? []).length==0){
        if(slotCheckPromise) await slotCheckPromise;
        break;
      }
      // Near the end the tape may already hold the last parts, placed but
      // not yet counted (a part goes into slot 2 before slot 1 advances).
      // When enough parts have been placed to finish the plan, look at this
      // cycle's top check before picking another one; if the OK parts in a
      // row from slot 1 finish it, advance them and stop. Only that last
      // cycle waits for the result; the rest keep it overlapped with the
      // pick (waiting on every one of the last 3 cost ~100 ms per part).
      // Generalised from "last segment only": at the end of *any* pack
      // segment the parts already in the tape may complete it. Picking
      // another part then meant inspecting it and tossing it back ("segment
      // count reached", ~1.1 s each, 4-5 per run in the 2026-09-25
      // baseline). Wait for this cycle's top check instead; if the OK run
      // completes the segment, advance it and start the next cycle without
      // a pick.
      {
        const planNow=_this.production_plan ?? [];
        if(nextCycleAction(planNow, placedUncounted).kind==='settle_segment' && slotCheckPromise){
          // The top shots fire only once the arm has left the tape, and no
          // move is queued while we wait here: clear the view first (it
          // deadlocked into a 10 s vision timeout otherwise). The feeder
          // standby point is on the way to the next pick anyway.
          await send(cmd.G1({ "Z": safe_z}));
          await send(cmd.G1({X:wait_flexfeeder_location.X,Y:wait_flexfeeder_location.Y }));
          const st=await slotCheckPromise;
          let okRun=0;
          while(st && okRun<st.is_OK.length && st.is_OK[okRun]==1 && st.is_clear[okRun]==0) okRun++;
          if(okRun>=planNow[0]){
            const adv=planNow[0];
            packCounter+=adv;
            await runinng_checkpoint("[STEP][REEL ADV]",{adv_count:adv,type:"pack",packCounter:packCounter});
            await runinng_checkpoint("_PACK_INFO_",{packCounter:packCounter});
            placedUncounted=Math.max(0,placedUncounted-adv);
            const settledView=await advanceTape(adv,'pack',{motion_id_offset:0,motion_progress:0});
            if((_this.production_plan ?? []).length==0){ slotCheckPromise_BK=undefined; break; }
            // More plan left (e.g. an empty segment next): the top check
            // after this advance is the next cycle's view.
            slotCheckPromise_BK=settledView ? Promise.resolve(settledView) : undefined;
            isZinSafeZone=true;
            continue;
          }
        }
      }

      if(feederCheckPromise!=undefined){
        await send(cmd.G1({ "Z": safe_z}))
        await send(cmd.G1({X:wait_flexfeeder_location.X,Y:wait_flexfeeder_location.Y }))
     
        candidate_obj_arr=await feederCheckPromise;
        feederCheckPromise=undefined;
      }



      if(candidate_obj_arr.length==0){//still no available object, shake and check flex feeder now

        let candidate_obj_arr_promise=refillFeeder(machine); 
        if(candidate_obj_arr_promise!=undefined){
            candidate_obj_arr=await candidate_obj_arr_promise;
        }

        continue;
      }

      // return;

      let item=candidate_obj_arr[0];
      

      await runinng_checkpoint("fetch one item on FF",i);

      candidate_obj_arr.shift();
      if(item.surround_clear == 0){
        continue;
      }
      // if(item.center_clear == 0){
      //   continue;
      // }

      if(isZinSafeZone==false){
        
        await send(cmd.G1({ "Z": safe_z,"A":0 }))
      }


      let predicted_location = predictRobotCoordinates(calibParams,item);
      console.log(predicted_location,item);

      isZinSafeZone=false;
      
      await runinng_checkpoint("go to predicted location",i);
      await pickFromFeeder(machine,{X:predicted_location.X,Y:predicted_location.Y,Z:predicted_location.Z,A:-item.angle_deg});

      
      if(candidate_obj_arr.length==0){//no available object, shake and check flex feeder plate

        feederCheckPromise=refillFeeder(machine);

      }




      await runinng_checkpoint("[STEP] object picked",i);
      // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 5 })//DBG
      // await delay(5000);

      let inspBasAngle=90;

      await send(cmd.G1({ "X":inspLocation_withObject.X,"Y":inspLocation_withObject.Y,"Z": inspLocation_withObject.Z+1,"A":inspBasAngle  }))

      await runinng_checkpoint("SideCam check",i);
      let sideCam_repReg=waitForSideCheckData();
      await send(cmd.G1({ "Z":inspLocation_withObject.Z}))
      sendNoWait(camTrig(IO_Pins.O.CAM_Side, IO_Pins.O.CAM_Side_Light0, { reset_ms: 5, motion_id_offset: 0, motion_progress: 1 }))



      if(true){
        let angOffset=0;

        await runinng_checkpoint("[STEP]go to BTM insp",i);
        sendNoWait(cmd.G4(0.01))
        let btm_check_rep_promise= waitForBTMCheckData();
        await send(camTrig(IO_Pins.O.CAM_Btm, IO_Pins.O.CAM_Btm_Light0, { reset_ms: 4 }))
        // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.03 })
        // await send({ "type": "M", "cmd": "M4","group":1, "pin": (1<<3) | (1<<2), "state":(1<<3) | (1<<2),reset_ms:4 })
        sendNoWait(cmd.G4(0.001))


        
        // await send({ "type": "M", "cmd": "G1", "A":inspBasAngle+5 })//twist a bit to align the hole
        await send(cmd.G1({ "A":inspBasAngle }))

        // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.05 })
        
        await send(cmd.G1({ "Z": inspLocation_withObject.Z+1}))
        


        async function waitTime(promise:Promise<any>,name:string){
          let cur_time=Date.now();
          let data = await promise;
          let end_time=Date.now();
          console.log("waitTime",name,end_time-cur_time);
          return data;
        }


        console.log("wait for SideCam report");
        let sideCam_rep_data = await waitTime(sideCam_repReg,"SideCam report")  ;//WAIT: SideCam report
        const sideTimedOut = sideCam_rep_data===undefined;
        if(sideTimedOut) sideCam_rep_data={status:0,facing:0,measure:{status:0,OK_vec:[0,0,0]}};



        console.log("wait for BTM report");


        let btm_check_rep_data = await waitTime(btm_check_rep_promise,"BTM report") as NozzleCheckData;//WAIT: BTM report
        const btmTimedOut = btm_check_rep_data===undefined;
        if(btmTimedOut) btm_check_rep_data={status:0,
          obj_pose:{x:btmCheckCalibInfo.center.X,y:btmCheckCalibInfo.center.Y,ang:0,status:0},
          nozzle_pose:{x:btmCheckCalibInfo.center.X,y:btmCheckCalibInfo.center.Y,ang:0,status:0},
          mmpp:btmCheckCalibInfo.mmpp};
        console.log("btm_check_rep_data",btm_check_rep_data,"sideCam_rep_data",sideCam_rep_data);

        let tossReasons:string[]=[];   // reasons collected before the top result; judgePlacement adds the rest
        if(sideTimedOut) tossReasons.push("vision timeout: side");
        if(btmTimedOut) tossReasons.push("vision timeout: bottom");


        let TOP_NG_Location=tossLocation_0;
        let ETC_NG_Location=tossLocation_2;


        if(btm_check_rep_data.status!=1 || sideCam_rep_data.status!=1)//check failed
        {

          await send(cmd.G1({"Z": safe_z}))//GO TO THE SECOND SLOT LOCATION IN ADVANCE TO SPEED UP

          tossReasons.push("BTM or SideCam check failed");
          ETC_NG_Location=tossLocation_0;

        }





        if(sideCam_rep_data.facing!=0)//reverse facing
        {
          angOffset=180;
          // await send({ "type": "M", "cmd": "G1", "A":inspBasAngle+angOffset })
        }
        angOffset+=btm_check_rep_data.obj_pose.ang;//compensate the angle of the object(from bottom check camera)
        angOffset-=8;

        // await send({ "type": "M", "cmd": "G1",X:slotLocation.X, Y:slotLocation.Y, "Z": safe_z})
        // 


        await send(cmd.G1({"A":inspBasAngle+angOffset}))

        await runinng_checkpoint("[STEP]",i);
        if(tossReasons.length==0){

          let sideCam_rectified_repReg=waitForSideCheckData();

          let cam_pin=1<<IO_Pins.O.CAM_Side;
          let light_pin=1<<IO_Pins.O.CAM_Side_Light0;

          let trigPin=light_pin|cam_pin;
          await send(cmd.M4({ "pin": trigPin, "state":trigPin,reset_ms:5, "motion_progress":1}))

          // await runinng_checkpoint("[TOSS] object angle",{angOffset:angOffset});
          // Head toward the tape while the result is pending, but only to a
          // midpoint (PRE_PLACE_FRACTION), not above the slot itself.
          const preTarget={X:slotLocation.X+slotDist, Y:slotLocation.Y};
          await send(cmd.G1({
            X:inspLocation_withObject.X+(preTarget.X-inspLocation_withObject.X)*PRE_PLACE_FRACTION,
            Y:inspLocation_withObject.Y+(preTarget.Y-inspLocation_withObject.Y)*PRE_PLACE_FRACTION,
            "Z": safe_z}))

          let sideCam_rectified_repData = await waitTime(sideCam_rectified_repReg,"SideCam rectified report")  ;//WAIT: SideCam rectified report

          console.log("SideCam rectified report",sideCam_rectified_repData);

          if(sideCam_rectified_repData?.measure?.status!=1)
          {
            tossReasons.push("SideCam measure failed"+sideCam_rectified_repData?.measure?.OK_vec);
          }

        }



        // await send({ "type": "M", "cmd": "WAIT_FOR_DIGITAL_INPUT", "group":1,"pin":1<<1,state:1<<1 })
        
        await runinng_checkpoint("[STEP]",i);

        
        // postInspPromise=VP_sendTcpMsgPack("SideCheck");//second trigger
        // sendTcpMsgPack({ "type": "M", "cmd": "M4","group":1, "pin": 3, "state":3,reset_ms:5,"motion_id_offset": 0, "motion_progress":0.9, })

        const slotStatus=await waitTime(slotCheckPromise as Promise<TopView>,"TOP CAM report");//WAIT: TOP CAM report
        slotCheckPromise_BK=undefined;

        const armOffset=BtmCheckObjLoc2ArmOffset(
          btmCheckCalibInfo,{
          X:btm_check_rep_data.obj_pose.x,
          Y:btm_check_rep_data.obj_pose.y},angOffset);

        // Place or toss (lib/production/judge.ts): tape view, corrections
        // and the plan, in the order the checks always ran.
        const production_plan=(await runinng_checkpoint("GetProductionPlan",i)).production_plan;
        const J=judgePlacement({
          reasons:tossReasons,
          partBin:binOf(ETC_NG_Location),
          sideStatus:sideCam_rep_data.status,
          btmPoseStatus:btm_check_rep_data.obj_pose.status,
          armOffset,
          top:slotStatus,
          plan:production_plan,
        });
        console.log("[JUDGE]",JSON.stringify({place:J.place,reasons:J.reasons,bin:J.partBin,slot:J.placeSlot,ng:J.ngSlot,next:J.nextAdvance}));
        tossReasons=J.reasons;
        nxt_adv_count=J.nextAdvance;
        ETC_NG_Location=BIN_LOCATION[J.partBin];
        TOP_NG_Location=BIN_LOCATION[J.tapeNgBin];
        const targetPlaceSlotIdx=J.placeSlot;
        const targetPickSlotIdx=J.ngSlot;
        const compensationIsNG=J.compensationNg;
        const slotHoleOffset=J.holeOffset;

        // Image bookkeeping for vision; nothing here depends on its reply
        // (vision_contract.md: fire-and-forget). It was awaited, with no
        // timeout, right before every place.
        (async()=>VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"save_target",
          t0:J.saveNames[0],
          t1:J.saveNames[1],
          t2:J.saveNames[2]}))().catch((e:any)=>console.warn("save_target failed",e?.message??e));

        if(J.holeTooFar){
          try{
            await runinng_checkpoint("ERROR",{errorString:"slotHoleOffset is too far",slotHoleOffset,distance:Math.hypot(slotHoleOffset.X,slotHoleOffset.Y)});
          }
          catch(error){
            break;
          }
        }

        if(J.place){
          
          
          evtMark(EVT.PLACE);
          placedUncounted++;
          await runinng_checkpoint("place object",i);

          let x_place_offset=targetPlaceSlotIdx*slotDist;

          await placePart(machine,
            {X:slotLocation.X+x_place_offset-armOffset.X+slotHoleOffset.X,Y:slotLocation.Y-armOffset.Y+slotHoleOffset.Y,Z:slotLocation.Z,A:inspBasAngle+angOffset},
            ()=>runinng_checkpoint("[STEP] place object",i));


          // await send({ "type": "M", "cmd": "M4","group":0, "pin": 1<<7, "state": 1<<7,reset_ms:50,"motion_id_offset":-1 })//reel adv

          
          //sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1<<6, "state": 1<<6,reset_ms:500,"motion_progress":0.7 })//reel adv

  
        }      
        else
        {//toss
          evtMark(EVT.TOSS);
          await runinng_checkpoint("[TOSS] object",{tossReasons:tossReasons});
          
          //console.log("toss object",sideCam_rep_data.status,targetPlaceSlotIdx,compensationIsNG);
          console.log("toss object",tossReasons);

          await tossTo(machine,ETC_NG_Location);
          await runinng_checkpoint("NG_COUNT",{class:ETC_NG_Location==tossLocation_0?0:ETC_NG_Location==tossLocation_1?1:2,count:1});

          // await send({ "type": "M", "cmd": "G1", "Z": safe_z })
          isZinSafeZone=true;
          
        }
        
        let ng_x_pick_offset=targetPickSlotIdx*slotDist;
        console.log("ng_x_pick_offset",ng_x_pick_offset,"compensationIsNG",compensationIsNG);

        if(!Number.isNaN(ng_x_pick_offset) && compensationIsNG==false)
        {

          
          await runinng_checkpoint("[NG PICK] object",{ng_x_pick_offset:ng_x_pick_offset,compensationIsNG:compensationIsNG});
          // await runinng_checkpoint("go to NG location and pick",i);
          await pickFromTape(machine,slotLocation.X+ng_x_pick_offset+slotHoleOffset.X,slotLocation.Y+slotHoleOffset.Y);
          await tossTo(machine,TOP_NG_Location);
          await runinng_checkpoint("NG_COUNT",{class:TOP_NG_Location==tossLocation_0?0:TOP_NG_Location==tossLocation_1?1:2,count:1});
          isZinSafeZone=true;
        }
        isZinSafeZone=true;
        // if(postInspPromise!=null){
        //   let ret_reg_data = await postInspPromise;
        //   console.log("postInspPromise",ret_reg_data);
        // }


      }


      //break;
      
      // await send({ "type": "M", "cmd": "G1", "X":57,"Y":-67})
      // await send({ "type": "M", "cmd": "G1", "Z": -11.9 })
      // await send({ "type": "M", "cmd": "G1", "Z": -11.9 })
      // await send({ "type": "M", "cmd": "G1", "Z": safe_z })






    }

    await send(cmd.WaitForTriggerMotionProgress({}));
    let end_time=Date.now();
    console.log("time",end_time-start_time,packCounter);
    console.log("time per pack",(end_time-start_time)/packCounter);
    
    {
      const original=(_this.production_plan_original ?? []) as number[];
      const mine=cellsDone(original,(_this.production_plan ?? []) as number[]);
      console.log("[PLAN] end: renderer cells_done",mine,"PLC cells_done",_this.plc_cells_done,
        mine===_this.plc_cells_done ? "MATCH" : "MISMATCH","kind disagreements",_this.plan_mismatch??0);
    }
    await runinng_checkpoint("cycle_end",{time:Date.now()});
    
      
    }
    catch(error){
      // A stop rejects the checkpoint with no reason: end quietly. A real
      // failure (e.g. a vision reply timeout) must be visible.
      if(error instanceof Error){
        console.error("run cycle aborted:",error);
        setRunningState(JSON.stringify({errorString:error.message}));
      }
    }
    //setLatestObjArr(newLatestObjArr);

    // Leave the cell quiet, whatever ended the loop (plan done, STOP, an
    // error): feeder vibration and feeder light off; if the PLC still takes
    // motion, let the queued moves finish and lift to safe Z. The nozzle is
    // left as it is -- releasing a held part would drop it. Best effort:
    // nothing here may throw.
    try { FlexVibCtrl.voff(0x1D); FlexVibCtrl.voff(10); FlexVibCtrl.top_light_off(); } catch {}
    try {
      await send(cmd.WaitForMotionStop({timeout_ms:5000}));
      await send(cmd.G1({"Z": safe_z}));
    } catch {}
    if(_this.run_cycle_stop==true && _this.current_error==undefined) setRunningState("stopped");

    cycleEnded=true;
    _this.isRunning=false;
  });


  //index on reel
  //-10 -9 -8 -7 -6 -5 -4 -3 -2 -1 | 0 1 2 3
  const checkInspObject=async(pickObjIndex:number,placeObjIndex:number,speed_alpha:number=0.3)=>{
    //set speed
    let btmCheckCalibInfo=await BtmCheckCalib();
    if(btmCheckCalibInfo.error!==undefined){
      throw new Error("BtmCheckCalib failed: "+btmCheckCalibInfo.error);
    }

    let alpha=speed_alpha;
    let _speed=speed*alpha;
    let _jerk=jerk*alpha;
    let _acc=acc*alpha;
    let _dea=dea*alpha;
    let _cor=cor*alpha;
    await sendTcpMsgPack(cmd.G1({ "F":_speed,Cor:_cor,ACC:_acc,DEA:_dea,JERK:_jerk }));
    //lift Z to safe_z
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }));



    
    let safeLocation:PointXYZ={X: -40.103, Y: 14.961,Z:safe_z};

    ////////////////////STAGE 1:pick object to insp

    //step 1: pick object from reel at index pickObjIndex

    //interpolation
    let objnN_location:PointXYZ={
      X:(objn10_location.X-objn00_location.X)*-pickObjIndex/10 + objn00_location.X,
      Y:(objn10_location.Y-objn00_location.Y)*-pickObjIndex/10 + objn00_location.Y,
      Z:(objn10_location.Z-objn00_location.Z)*-pickObjIndex/10 + objn00_location.Z};

    //go to objnN_location
    await sendTcpMsgPack(cmd.G1({ "X":objnN_location.X,"Y":objnN_location.Y,"A":0 }));
    //drop Z to objnN_location.Z
    await sendTcpMsgPack(cmd.G1({ "Z":objnN_location.Z,"A":0 }));

    //SUCK
    
    await sendTcpMsgPack(cmd.G4(0.01))
    await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state":1<<IO_Pins.O.Nozzle_suck }))//suck off

    //lift Z to safe_z
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }));








    //goto inspLocation
    await sendTcpMsgPack(cmd.G1({ "X":inspLocation_withObject.X,"Y":inspLocation_withObject.Y,"A":0 }));
    //drop Z to inspLocation.Z
    await sendTcpMsgPack(cmd.G1({ "Z":inspLocation_withObject.Z,"A":0 }));

    ////////////////////STAGE 2:do inspection

    let sideShotPin=1<<IO_Pins.O.CAM_Side | 1<<IO_Pins.O.CAM_Side_Light0;
    let BTMShotPin=1<<IO_Pins.O.CAM_Btm | 1<<IO_Pins.O.CAM_Btm_Light0;


    //SIDE Check
    // await sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.01 })
    
    let sideCam_repReg=waitForSideCheckData();
    await sendTcpMsgPack(cmd.M4({"pin": sideShotPin, "state":sideShotPin,reset_ms:5, "motion_progress": 1}))

    //BTM Check
    await sendTcpMsgPack(cmd.G4(0.02))


    let btmCam_repReg=waitForBTMCheckData();
    await sendTcpMsgPack(cmd.M4({"pin": BTMShotPin, "state":BTMShotPin,reset_ms:5 }))

    let sideCam_rep_data = await sideCam_repReg ;//WAIT: SideCam report
    console.log("sideCam_repData",sideCam_rep_data);
    let btm_check_rep_data = await btmCam_repReg ;//WAIT: BTM report
    console.log("btmCam_repData",btm_check_rep_data);

    let inspBasAngle=0;
    let angOffset=0;

    if(sideCam_rep_data.facing!=0)//reverse facing
    {
      angOffset=180;
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "A":inspBasAngle+angOffset })
    }
    angOffset+=btm_check_rep_data.obj_pose.ang;//compensate the angle of the object(from bottom check camera)
    angOffset-=8;


    let armOffset=BtmCheckObjLoc2ArmOffset(
      btmCheckCalibInfo,{
      X:btm_check_rep_data.obj_pose.x,
      Y:btm_check_rep_data.obj_pose.y},angOffset);

    console.log("armOffset",armOffset);
    //SIDE Check with angle compensated
    let sideCam_rectified_rep_promise=waitForSideCheckData();

    await sendTcpMsgPack(cmd.G1({ 
      "X":inspLocation_withObject.X-armOffset.X,
      "Y":inspLocation_withObject.Y-armOffset.Y,
      "A":inspBasAngle+angOffset}))
    //lift Z to safe_z
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }));
    await sendTcpMsgPack(cmd.M4({"pin": sideShotPin, "state":sideShotPin,reset_ms:5, "motion_progress": 0 }))
 





    
    let objPlace_location:PointXYZ={
      X:(objn10_location.X-objn00_location.X)*-placeObjIndex/10 + objn00_location.X,
      Y:(objn10_location.Y-objn00_location.Y)*-placeObjIndex/10 + objn00_location.Y,
      Z:(objn10_location.Z-objn00_location.Z)*-placeObjIndex/10 + objn00_location.Z};
    //goto placeLocation
    await sendTcpMsgPack(cmd.G1({ "X":objPlace_location.X,"Y":objPlace_location.Y,"A":0 }));
    //drop Z to objPlace_location.Z
    await sendTcpMsgPack(cmd.G1({ "Z":objPlace_location.Z,"A":0 }));

    //PLACE


    await sendTcpMsgPack(cmd.G4(0.01))
    await sendTcpMsgPack(cmd.M4({"pin": 1<<IO_Pins.O.Nozzle_suck, "state":0}))//suck off


    // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
    await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_blow, "state": 1<< IO_Pins.O.Nozzle_blow,reset_ms:5 }))




    //lift Z to safe_z
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }));


    //TODO move to wait location

    await sendTcpMsgPack(cmd.G1({ "X":safeLocation.X,"Y":safeLocation.Y,"A":0 }));

    
    //check top

    async function checkSlot_and_reelAdv():Promise<{is_clear:number[],is_OK:number[],post_check_advCount:number,locHole:{status:number,x:number,y:number,mmpp:number}}> { 
      

      let reelAdvPinOpSeq:number[]=[];

      let reelAdvWaitTime=0;
      let topCheckDataPromise=waitForTOPCheckData();
      console.log("TRIGGER top check data");


      let initDelayTime=0;
      initDelayTime=100;
      initDelayTime+=reelAdvWaitTime;

      let pin_side_light=1<<IO_Pins.O.CAM_Top_SideLight;
      let pin_down_light=1<<IO_Pins.O.CAM_Top_Light0;
      let pin_cam_trigger=1<<IO_Pins.O.CAM_Top;


      let lastPinOpSeq=[//top check camera trigger IO
        ...reelAdvPinOpSeq,
        initDelayTime, pin_side_light|pin_cam_trigger, pin_side_light|pin_cam_trigger,
        1,pin_side_light|pin_cam_trigger, 0,
       40, pin_down_light|pin_cam_trigger, pin_down_light|pin_cam_trigger,
       1, pin_down_light|pin_cam_trigger, 0,]

      sendTcpMsgPack(cmd.M4({
        pin_op_seq:lastPinOpSeq
         ,"motion_id_offset":-1,"motion_progress":0
      }));

      console.log("lastPinOpSeq",lastPinOpSeq);

      console.log("wait for top check data");
      let topCheckData=(await topCheckDataPromise) as ReturnType<typeof waitForTOPCheckData>;
      console.log("topCheckData",topCheckData);

      let retData={...topCheckData,post_check_advCount:0};
      return retData;
    }


    await sendTcpMsgPack(cmd.G4(0.01))

    let topcam_check_report_promise= checkSlot_and_reelAdv();


    

    //Check done


    ////////////////////STAGE 3:put it back
    //goto placeLocation to pick 
    await sendTcpMsgPack(cmd.G1({ "X":objPlace_location.X,"Y":objPlace_location.Y,"A":0 }));

  
    await sendTcpMsgPack(cmd.G1({ "Z":objPlace_location.Z,"A":0 }));

    //SUCK pick

    
    await sendTcpMsgPack(cmd.G4(0.01))
    await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state":1<<IO_Pins.O.Nozzle_suck }))//suck off


    //lift Z to safe_z
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }));


    //go to objnN_location
    await sendTcpMsgPack(cmd.G1({ "X":objnN_location.X,"Y":objnN_location.Y,"A":0 }));
    //drop Z to objnN_location.Z
    await sendTcpMsgPack(cmd.G1({ "Z":objnN_location.Z,"A":0 }));

    //place object

    

    await sendTcpMsgPack(cmd.G4(0.01))
    await sendTcpMsgPack(cmd.M4({"pin": 1<<IO_Pins.O.Nozzle_suck, "state":0}))//suck off


    // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
    await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_blow, "state": 1<< IO_Pins.O.Nozzle_blow,reset_ms:5 }))


    

    
    await sendTcpMsgPack(cmd.G1({ "Z":safe_z,"A":0 }));


    //go back safeLocation
    await sendTcpMsgPack(cmd.G1({ "X":safeLocation.X,"Y":safeLocation.Y,"A":0 }));
  


    let btmcam_check_report=btm_check_rep_data;
    let sidecam_check_report=await sideCam_rectified_rep_promise;
    let topcam_check_report=await topcam_check_report_promise;
  


    console.log("btmcam_check_report",btmcam_check_report);
    console.log("sidecam_check_report",sidecam_check_report);
    console.log("topcam_check_report",topcam_check_report);
  }


  
  async function FVib(idx:number,delay_ms:number=1000){
    evtMark(EVT.VIB_ON);
    FlexVibCtrl.von(idx);
    await delay(delay_ms);
    evtMark(EVT.VIB_OFF);
    FlexVibCtrl.voff(idx);
  }

  _this.stepMode=stepMode;
  _this.tossPauseMode=tossPauseMode;

  useHarnessAction('get_running_state', () => ({
    runningState,
    stepMode,
    tossPauseMode,
    isRunning: _this.isRunning === true,
    calibLoaded: calibParams != null,
    packInfoString,
    packSpeedInfo,
    currentError: _this.current_error,
  }), [runningState, stepMode, tossPauseMode, packInfoString, packSpeedInfo]);

  useHarnessAction('set_step_mode', (payload: any) => {
    const on = payload?.on;
    const target = typeof on === 'boolean' ? on : !stepMode;
    if (target === false && stepMode === true) {
      _this.stepMode_resolve?.();
      _this.stepMode_resolve = undefined;
    }
    setStepMode(target);
    return { stepMode: target };
  }, [stepMode]);

  useHarnessAction('set_toss_pause', (payload: any) => {
    const on = payload?.on;
    const target = typeof on === 'boolean' ? on : !tossPauseMode;
    if (target === false && tossPauseMode === true) {
      _this.stepMode_resolve?.();
      _this.stepMode_resolve = undefined;
    }
    setTossPauseMode(target);
    return { tossPauseMode: target };
  }, [tossPauseMode]);

  useHarnessAction('resume_cycle', async () => {
    let curTime = Date.now();
    while (_this.current_error != undefined) {
      _this.current_error = undefined;
      await delay(500);
      if (Date.now() - curTime > 10000) {
        return { resumed: false, reason: 'current_error_not_clearing' };
      }
    }
    setRunningState('no error');
    _this.stepMode_resolve?.();
    _this.stepMode_resolve = undefined;
    return { resumed: true };
  }, []);

  useHarnessAction('run_cycle', async () => {
    const btn = _this.runButtonEl as HTMLButtonElement | undefined;
    if (!btn) throw new Error('run_cycle: RUN button not mounted');
    if (_this.isRunning === true) return { started: false, reason: 'already_running' };
    btn.click();
    return { started: true };
  }, []);

  // Stop after the part in hand (the loop checks the flag at the start of
  // each cycle), then wait for the loop to really end. isRunning is only
  // cleared by the loop itself: it used to be forced false after 3 s, so a
  // RUN during a long vision wait started a second loop next to the first.
  useHarnessAction('stop_cycle', async (payload: any) => {
    const t0 = Date.now();
    _this.run_cycle_stop = true;
    _this.stepMode_resolve?.();
    _this.stepMode_resolve = undefined;
    const limit = typeof payload?.wait_ms === 'number' ? payload.wait_ms : 14000;
    while (_this.isRunning === true && Date.now() - t0 < limit) {
      await new Promise((r) => setTimeout(r, 50));
    }
    return { stopped: _this.isRunning !== true, ms: Date.now() - t0 };
  }, []);

  // payload.plan: same encoding as the plan editor -- positive = pack that
  // many parts, negative = leave that many cells empty. E.g. [-2, 10].
  useHarnessAction('set_plan', async (payload: any) => {
    const plan = payload?.plan;
    if (!Array.isArray(plan) || plan.length === 0 || !plan.every((n: any) => Number.isInteger(n) && n !== 0)) {
      throw new Error('set_plan: plan must be a non-empty array of non-zero integers');
    }
    setProductionPlan(plan);
    return { plan: _this.production_plan };
  }, []);

  useHarnessAction('get_plan', async () => ({
    plan: _this.production_plan ?? [],
    original: _this.production_plan_original ?? [],
    stage: _this.production_plan_stageIndex ?? 0,
  }), []);

  // The plan as the PLC keeps it, and what it resolves to.
  useHarnessAction('get_plc_plan', async () => {
    const st = await sendTcpMsgPack(cmd.PlanGet()) as PlanState;
    return { ...st, remaining: remainingPlan(st?.seg ?? [], st?.cells_done ?? 0) };
  }, []);

  // Forget the renderer's plan (what a crash/restart does) and take the
  // PLC's: used to test resume without restarting the UI.
  useHarnessAction('forget_plan', async () => {
    _this.production_plan = undefined;
    _this.production_plan_original = undefined;
    _this.production_plan_id = undefined;
    _this.production_plan_stageIndex = 0;
    setProductionPlanTick((x) => x + 1);
    return { ok: true };
  }, []);

  type PlanSegmentType = 'pack' | 'empty';
  type PlanSegment = { type: PlanSegmentType; count: number; key: string };

  function planToSegments(plan: number[]): PlanSegment[] {
    return plan.map((n, idx) => ({
      type: n > 0 ? 'pack' : 'empty',
      count: Math.abs(n),
      key: `${Date.now()}_${idx}_${Math.random().toString(16).slice(2)}`,
    }));
  }

  function segmentsToPlan(segments: PlanSegment[]): { ok: true; plan: number[] } | { ok: false; error: string } {
    const plan: number[] = [];
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      const count = Math.trunc(Number(seg.count));
      if (!Number.isFinite(count) || count <= 0) return { ok: false, error: `Segment ${i + 1} count must be > 0` };
      plan.push(seg.type === 'pack' ? count : -count);
    }
    if (plan.length === 0) return { ok: false, error: 'Empty plan' };
    return { ok: true, plan };
  }

  function setProductionPlan(plan: number[], planId: number = Date.now() % 2147483647) {
    _this.production_plan = [...plan];
    _this.production_plan_original = [...plan];
    _this.production_plan_stageIndex = 0;
    // Labels the plan on the PLC (PLAN_SET plan_id), so a RUN can tell
    // "the PLC's progress on this plan" from "a plan the PLC never saw".
    _this.production_plan_id = planId;
    setProductionPlanTick((x) => x + 1);
  }

  // The PLC keeps the whole plan and the tape cells advanced since it was
  // set (retained, GVL.PlanSeg / PlanCellsDone), so the job survives a
  // renderer crash or a PC restart. Called at the start of every run:
  //  - same plan on both sides: the PLC's cell count is the truth (the
  //    tape cannot be wrong), derive the remaining plan from it;
  //  - the renderer has no plan (e.g. it restarted): adopt the PLC's;
  //  - otherwise (a new plan): send it, with the progress already made.
  async function syncPlanWithPlc(send: (pkt: any) => Promise<any>): Promise<string> {
    const st = await send(cmd.PlanGet()) as PlanState;
    const seg = (st?.seg ?? []) as number[];
    const plcRemaining = remainingPlan(seg, st?.cells_done ?? 0);
    const original = _this.production_plan_original as number[] | undefined;
    const samePlan = !!original && st?.plan_id === _this.production_plan_id
      && seg.length === original.length && seg.every((n, i) => n === original[i]);
    if (samePlan) {
      const mine = (_this.production_plan ?? []) as number[];
      if (JSON.stringify(mine) !== JSON.stringify(plcRemaining)) {
        console.warn("[PLAN] resynced from the PLC", JSON.stringify(mine), "->", JSON.stringify(plcRemaining));
      }
      _this.production_plan = plcRemaining;
      _this.production_plan_stageIndex = seg.length - plcRemaining.length;
      setProductionPlanTick((x) => x + 1);
      return "plc";
    }
    if (!original || original.length === 0) {
      if (plcRemaining.length > 0) {
        _this.production_plan_original = [...seg];
        _this.production_plan_id = st.plan_id;
        _this.production_plan = plcRemaining;
        _this.production_plan_stageIndex = seg.length - plcRemaining.length;
        setProductionPlanTick((x) => x + 1);
        console.warn("[PLAN] resumed from the PLC", JSON.stringify(seg), "at cell", st.cells_done);
        return "resumed";
      }
      return "none";
    }
    const done = cellsDone(original, (_this.production_plan ?? []) as number[]);
    await send(cmd.PlanSet(original, _this.production_plan_id ?? 0, done));
    console.log("[PLAN] sent to the PLC", JSON.stringify(original), "cells_done", done);
    return "set";
  }

  function formatPlanProgressDisplay(
    original: number[] | undefined,
    current: number[] | undefined,
    stageIndex: number | undefined
  ): string {
    const o = Array.isArray(original) ? original : undefined;
    const c = Array.isArray(current) ? current : undefined;
    const idx = typeof stageIndex === 'number' && Number.isFinite(stageIndex) ? Math.max(0, Math.trunc(stageIndex)) : 0;

    if (!o || o.length === 0) {
      if (c && c.length > 0) return c.join(',');
      return '';
    }

    const headRemaining = c && c.length > 0 ? c[0] : undefined;

    return o
      .map((val, i) => {
        if (i !== idx) return String(val);
        if (headRemaining === undefined) return String(val);

        const orig = val;
        const rem = headRemaining;
        if (orig > 0) return `${rem}/(${orig})`;
        // For "empty" segments, show remaining as a negative value but normalize magnitude.
        return `-${Math.abs(rem)}/(${Math.abs(orig)})`;
      })
      .join(',');
  }

  const RECENT_SETUPS_KEY = 'productionPlan_recent';
  const [planSegments, setPlanSegments] = useState<PlanSegment[]>(() => planToSegments([1, -30, 445, -20, 1]));
  const [isPlanExpanded, setIsPlanExpanded] = useState<boolean>(false);
  const [selectedPlanIdx, setSelectedPlanIdx] = useState<number>(0);
  const [deleteConfirmPending, setDeleteConfirmPending] = useState<boolean>(false);
  const [planEditWarningVisible, setPlanEditWarningVisible] = useState<boolean>(false);
  const [recentSetups, setRecentSetups] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem(RECENT_SETUPS_KEY);
      return stored ? JSON.parse(stored) : [];
    } catch { return []; }
  });

  function saveRecentSetup(planStr: string) {
    setRecentSetups((prev) => {
      const next = [planStr, ...prev.filter((s) => s !== planStr)].slice(0, 15);
      try { localStorage.setItem(RECENT_SETUPS_KEY, JSON.stringify(next)); } catch {}
      return next;
    });
  }

  const planPreview = useMemo(() => {
    const res = segmentsToPlan(planSegments);
    if (!res.ok) return '';
    return res.plan.join(',');
  }, [planSegments]);

  const { progressDisplay, isProductionFinished } = useMemo(() => {
    // Depend on productionPlanTick so this updates while running.
    void productionPlanTick;
    const display = formatPlanProgressDisplay(_this.production_plan_original, _this.production_plan, _this.production_plan_stageIndex);
    const finished =
      Array.isArray(_this.production_plan_original) &&
      _this.production_plan_original.length > 0 &&
      Array.isArray(_this.production_plan) &&
      _this.production_plan.length === 0;
    return { progressDisplay: display, isProductionFinished: finished };
  }, [productionPlanTick, _this]);

  useEffect(() => {
    setSelectedPlanIdx((prev) => {
      if (planSegments.length === 0) return 0;
      return Math.min(prev, planSegments.length - 1);
    });
  }, [planSegments.length]);

  const cardStyle: React.CSSProperties = {
    border: '1px solid #e5e7eb',
    borderRadius: 12,
    background: '#ffffff',
    padding: 12,
    marginBottom: 12,
  };
  const rowWrapStyle: React.CSSProperties = {
    display: 'flex',
    gap: 8,
    flexWrap: 'wrap',
    alignItems: 'center',
  };
  const statusPillStyle: React.CSSProperties = {
    fontSize: 12,
    fontWeight: 700,
    borderRadius: 999,
    padding: '5px 10px',
    background: '#e2e8f0',
    color: '#334155',
  };

  return (
    <div style={{ color: '#111827' }}>
      {planEditWarningVisible && (
        <div
          onClick={() => setPlanEditWarningVisible(false)}
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.45)', zIndex: 2000,
            display: 'flex', justifyContent: 'center', alignItems: 'center',
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: '#fff', borderRadius: 12, padding: '28px 36px',
              maxWidth: 340, textAlign: 'center',
              boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
            }}
          >
            <div style={{ fontSize: 36, marginBottom: 10 }}>⚠️</div>
            <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 20, color: '#92400e' }}>
              {t(uiLang, 'planEditWhileRunning')}
            </div>
            <Button type="primary" onClick={() => setPlanEditWarningVisible(false)}>OK</Button>
          </div>
        </div>
      )}
      <div style={{ ...cardStyle, borderColor: '#bfdbfe', background: '#eff6ff', borderRadius: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 10, alignItems: 'center' }}>
          <div>
            <strong style={{ fontSize: 16 }}>{t(uiLang, 'productionConsole')}</strong>
            <div style={{ marginTop: 4, fontSize: 12, color: '#475569' }}>
              {t(uiLang, 'productionDesc')}
            </div>
          </div>
          <span style={{ ...statusPillStyle, background: '#dbeafe', color: '#1e40af' }}>
            {stepMode ? t(uiLang, 'stepMode') : t(uiLang, 'autoMode')}
          </span>
        </div>
        {flipCountSnapshot && (
          <div
            style={{
              marginTop: 10,
              display: 'flex',
              flexWrap: 'wrap',
              gap: 6,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              fontSize: 11,
            }}
          >
            {([
              ['ReelLacking', IO_Pins.I.ReelLacking],
              ['ReelTapeHTension', IO_Pins.I.ReelTapeHTension],
              ['ReelPressRollerInPlace', IO_Pins.I.ReelPressRollerInPlace],
              ['PackedReelNoProtrusion', IO_Pins.I.PackedReelNoProtrusion],
            ] as const).map(([label, pin]) => {
              const high = ((flipCountSnapshot.raw >> pin) & 1) === 1;
              const flips = flipCountSnapshot.fc[pin] ?? 0;
              return (
                <div
                  key={pin}
                  title={`pin ${pin}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '3px 8px',
                    borderRadius: 6,
                    border: `1px solid ${high ? '#86efac' : '#cbd5e1'}`,
                    background: high ? '#f0fdf4' : '#f8fafc',
                    color: high ? '#166534' : '#475569',
                  }}
                >
                  <span style={{ fontWeight: 700 }}>{label}</span>
                  <span>{high ? '1' : '0'}</span>
                  <span style={{ color: '#94a3b8' }}>·</span>
                  <span>flips {flips}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div style={{ ...cardStyle, borderRadius: 14 }}>
      <Divider style={{ marginTop: 0 }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Plan preview row */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
          <Button
            onClick={() => {
              if (_this.isRunning) {
                setPlanEditWarningVisible(true);
                return;
              }
              setIsPlanExpanded((v) => !v);
            }}
            style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              letterSpacing: '0.02em',
              ...(isProductionFinished
                ? { borderColor: '#fde047', background: '#fef9c3', color: '#713f12' }
                : progressDisplay
                ? { borderColor: '#86efac', background: '#f0fdf4', color: '#166534' }
                : {}),
            }}
          >
            {progressDisplay || planPreview || '(empty)'}
            {isProductionFinished ? <span style={{ marginLeft: 6, fontWeight: 700, opacity: 0.8 }}>✓ {t(uiLang, 'planFinished')}</span> : null}
            &nbsp;&nbsp;<span style={{ fontSize: 10, opacity: 0.5 }}>{isPlanExpanded ? '▲' : '▼'}</span>
          </Button>

          {packSpeedInfo && (
            <div style={{
              display: 'flex', gap: 6, alignItems: 'stretch',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              fontSize: 12,
            }}>
              {/* Count */}
              <div style={{
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                padding: '4px 10px', borderRadius: 8, background: '#f1f5f9', border: '1px solid #cbd5e1',
                minWidth: 52,
              }}>
                <span style={{ fontSize: 10, color: '#64748b', fontFamily: 'inherit' }}>{t(uiLang, 'speedCount')}</span>
                <span style={{ fontWeight: 700, fontSize: 15, color: '#1e293b' }}>{packSpeedInfo.count}</span>
              </div>
              {/* Overall speed */}
              <Popconfirm
                title={t(uiLang, 'resetOverallTitle')}
                description={t(uiLang, 'resetOverallDesc')}
                onConfirm={() => {
                  _this.speedStartTime = Date.now();
                  _this.packCountOffset = _this.lastPackCount ?? 0;
                  setPackSpeedInfo(prev => prev ? { ...prev, overallHr: 0 } : prev);
                }}
                okText={t(uiLang, 'yes')}
                cancelText={t(uiLang, 'no')}
              >
                <div style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                  padding: '4px 10px', borderRadius: 8, background: '#eff6ff', border: '1px solid #bfdbfe',
                  minWidth: 72, cursor: 'pointer',
                }}>
                  <span style={{ fontSize: 10, color: '#3b82f6', fontFamily: 'inherit' }}>{t(uiLang, 'speedOverall')}</span>
                  <span style={{ fontWeight: 700, fontSize: 15, color: '#1e40af' }}>{packSpeedInfo.overallHr.toFixed(0)}</span>
                  <span style={{ fontSize: 9, color: '#93c5fd', fontFamily: 'inherit' }}>{t(uiLang, 'speedUnit')}</span>
                </div>
              </Popconfirm>
              {/* Recent speed */}
              <Popconfirm
                title={t(uiLang, 'resetRecentTitle')}
                description={t(uiLang, 'resetRecentDesc')}
                onConfirm={() => {
                  _this.packTimestamps = [];
                  setPackSpeedInfo(prev => prev ? { ...prev, recentHr: 0 } : prev);
                }}
                okText={t(uiLang, 'yes')}
                cancelText={t(uiLang, 'no')}
              >
                <div style={{
                  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                  padding: '4px 10px', borderRadius: 8, background: '#f0fdf4', border: '1px solid #86efac',
                  minWidth: 72, cursor: 'pointer',
                }}>
                  <span style={{ fontSize: 10, color: '#16a34a', fontFamily: 'inherit' }}>{t(uiLang, 'speedRecent')}</span>
                  <span style={{ fontWeight: 700, fontSize: 15, color: '#166534' }}>{packSpeedInfo.recentHr.toFixed(0)}</span>
                  <span style={{ fontSize: 9, color: '#86efac', fontFamily: 'inherit' }}>{t(uiLang, 'speedUnit')}</span>
                </div>
              </Popconfirm>
              {/* NG Class Counts */}
              {packSpeedInfo.ngCount && Object.entries(packSpeedInfo.ngCount).some(([, v]) => v > 0) && (
                <>
                  {Object.entries(packSpeedInfo.ngCount).map(([cls, count]) => (
                    count > 0 && (
                      <div key={cls} style={{
                        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                        padding: '4px 10px', borderRadius: 8, background: '#fef2f2', border: '1px solid #fca5a5',
                        minWidth: 72,
                      }}>
                        <span style={{ fontSize: 10, color: '#dc2626', fontFamily: 'inherit' }}>{t(uiLang, 'ngLabel')} {cls}</span>
                        <span style={{ fontWeight: 700, fontSize: 15, color: '#991b1b' }}>{count}</span>
                      </div>
                    )
                  ))}
                </>
              )}
              {/* Reset button */}
              <Popconfirm
                title={t(uiLang, 'resetConfirmTitle')}
                description={t(uiLang, 'resetConfirmDesc')}
                onConfirm={() => {
                  setPackSpeedInfo(null);
                  _this.packTimestamps = [];
                }}
                okText={t(uiLang, 'yes')}
                cancelText={t(uiLang, 'no')}
              >
                <Button size="small" danger style={{ borderRadius: 8, fontSize: 11, height: 'auto', padding: '4px 8px' }}>
                  {t(uiLang, 'reset')}
                </Button>
              </Popconfirm>
            </div>
          )}
        </div>

        {isPlanExpanded ? (
          <div style={{ border: '1px solid #d1d5db', borderRadius: 10, padding: 10, background: '#f8fafc' }}>

            {/* Segment list */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              {planSegments.map((seg, idx) => {
                const rawVal = seg.type === 'pack' ? seg.count : -seg.count;
                const isSelected = idx === selectedPlanIdx;
                const isPack = seg.type === 'pack';
                return (
                  <Button
                    key={seg.key}
                    type={isSelected ? 'primary' : 'default'}
                    onClick={() => setSelectedPlanIdx(idx)}
                    style={{
                      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                      ...(isSelected ? {} : isPack
                        ? { borderColor: '#3b82f6', color: '#1d4ed8' }
                        : { borderColor: '#f97316', color: '#c2410c' }),
                    }}
                  >
                    {rawVal}
                  </Button>
                );
              })}
              <Button
                size="small"
                onClick={() => {
                  setPlanSegments((prev) => [...prev, { type: 'pack', count: 1, key: `${Date.now()}_${Math.random().toString(16).slice(2)}` }]);
                  setSelectedPlanIdx(planSegments.length);
                }}
                style={{ fontWeight: 700, fontSize: 16, lineHeight: 1, padding: '0 10px' }}
              >
                +
              </Button>
              <Popover
                trigger="hover"
                placement="bottomLeft"
                content={
                  <div style={{ minWidth: 200, maxWidth: 320 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#64748b', marginBottom: 6 }}>
                      {t(uiLang, 'planRecentSetupsTitle', { count: recentSetups.length })}
                    </div>
                    {recentSetups.length === 0 ? (
                      <div style={{ fontSize: 12, color: '#94a3b8', padding: '4px 0' }}>{t(uiLang, 'planNoHistory')}</div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        {recentSetups.map((entry, i) => (
                          <div
                            key={i}
                            onClick={() => {
                              const nums = entry.split(',').map(Number);
                              if (nums.some(isNaN)) return;
                              setPlanSegments(planToSegments(nums));
                              setSelectedPlanIdx(0);
                              setProductionPlan(nums);
                            }}
                            style={{
                              cursor: 'pointer',
                              padding: '4px 8px',
                              borderRadius: 5,
                              fontSize: 12,
                              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                              background: '#f8fafc',
                              border: '1px solid #e2e8f0',
                              color: '#1e293b',
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                            }}
                            onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#dbeafe'; }}
                            onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = '#f8fafc'; }}
                          >
                            {entry}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                }
              >
                <Button
                  size="small"
                  style={{ fontSize: 12, color: '#64748b', borderColor: '#cbd5e1' }}
                >
                  {t(uiLang, 'planRecentSetup')}
                </Button>
              </Popover>
            </div>

            {/* Selected segment editor */}
            {planSegments.length > 0 ? (
              <div style={{ marginTop: 10, border: '1px solid #cbd5e1', borderRadius: 8, padding: '10px 10px 10px 10px', position: 'relative', background: '#fff' }}>

                {/* Header: segment index */}
                <div style={{ marginBottom: 8 }}>
                  <Typography.Text style={{ fontSize: 12, color: '#64748b' }}>
                    {t(uiLang, 'planSegmentOf', { current: selectedPlanIdx + 1, total: planSegments.length })}
                  </Typography.Text>
                </div>

                {/* Count digit spinners with type button to the left */}
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10 }}>
                  {/* Type toggle button */}
                  <Button
                    size="small"
                    onClick={() => {
                      setPlanSegments((prev) =>
                        prev.map((seg, idx) => (idx === selectedPlanIdx ? { ...seg, type: seg.type === 'pack' ? 'empty' : 'pack' } : seg))
                      );
                    }}
                    style={{
                      fontWeight: 700, fontSize: 11, letterSpacing: '0.04em',
                      height: 28, padding: '0 8px', alignSelf: 'center',
                      background: planSegments[selectedPlanIdx]?.type === 'pack' ? '#dbeafe' : '#ffedd5',
                      color: planSegments[selectedPlanIdx]?.type === 'pack' ? '#1e40af' : '#9a3412',
                      borderColor: planSegments[selectedPlanIdx]?.type === 'pack' ? '#93c5fd' : '#fdba74',
                    }}
                  >
                    {planSegments[selectedPlanIdx]?.type === 'pack' ? t(uiLang, 'planTypePack') : t(uiLang, 'planTypeEmpty')}
                  </Button>

                  <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                  {[0, 1, 2, 3, 4, 5].map((digitIdx) => {
                    const c = Math.max(1, planSegments[selectedPlanIdx]?.count ?? 1);
                    const digits = String(Math.min(999999, c)).padStart(6, '0').split('').map(Number);
                    const d = digits[digitIdx];
                    return (
                      <div key={`${selectedPlanIdx}_${digitIdx}`} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                        <Button
                          size="small"
                          style={{ padding: '0 6px', fontSize: 12 }}
                          onClick={() => {
                            setPlanSegments((prev) =>
                              prev.map((seg, idx) => {
                                if (idx !== selectedPlanIdx) return seg;
                                const ds = String(Math.min(999999, Math.max(1, seg.count))).padStart(6, '0').split('').map(Number);
                                ds[digitIdx] = (ds[digitIdx] + 1) % 10;
                                const newCount = Math.max(1, ds.reduce((a, x) => a * 10 + x, 0));
                                return { ...seg, count: newCount };
                              })
                            );
                          }}
                        >
                          ▲
                        </Button>
                        <div style={{
                          minWidth: 28, height: 28, display: 'flex', alignItems: 'center', justifyContent: 'center',
                          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                          fontSize: 15, fontWeight: 600,
                          background: '#f1f5f9', border: '1px solid #cbd5e1', borderRadius: 4, color: '#1e293b',
                          userSelect: 'none',
                        }}>
                          {d}
                        </div>
                        <Button
                          size="small"
                          style={{ padding: '0 6px', fontSize: 12 }}
                          onClick={() => {
                            setPlanSegments((prev) =>
                              prev.map((seg, idx) => {
                                if (idx !== selectedPlanIdx) return seg;
                                const ds = String(Math.min(999999, Math.max(1, seg.count))).padStart(6, '0').split('').map(Number);
                                ds[digitIdx] = (ds[digitIdx] + 9) % 10;
                                const newCount = Math.max(1, ds.reduce((a, x) => a * 10 + x, 0));
                                return { ...seg, count: newCount };
                              })
                            );
                          }}
                        >
                          ▼
                        </Button>
                      </div>
                    );
                  })}

                  </div>
                </div>

                {/* Bottom row: delete + apply */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Button
                    danger
                    size="small"
                    style={{ borderColor: '#dc2626', color: '#dc2626' }}
                    onClick={() => setDeleteConfirmPending(true)}
                  >
                    {t(uiLang, 'planDeleteSegment')}
                  </Button>
                  <Button
                    type="primary"
                    onClick={() => {
                      const res = segmentsToPlan(planSegments);
                      if (!res.ok) return;
                      setProductionPlan(res.plan);
                      saveRecentSetup(res.plan.join(','));
                    }}
                  >
                    {t(uiLang, 'planApplySetup')}
                  </Button>
                </div>

                {deleteConfirmPending ? (
                  <div
                    style={{
                      position: 'absolute',
                      bottom: 8,
                      right: 8,
                      display: 'flex',
                      gap: 8,
                      alignItems: 'center',
                      padding: 8,
                      background: '#fff',
                      border: '1px solid #e5e7eb',
                      borderRadius: 8,
                      boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
                    }}
                  >
                    <Typography.Text style={{ fontSize: 12 }}>{t(uiLang, 'planDeleteConfirm')}</Typography.Text>
                    <Button size="small" onClick={() => setDeleteConfirmPending(false)}>
                      {t(uiLang, 'cancel')}
                    </Button>
                    <Button
                      size="small"
                      danger
                      onClick={() => {
                        setPlanSegments((prev) => prev.filter((_, idx) => idx !== selectedPlanIdx));
                        setSelectedPlanIdx((prev) => Math.max(0, Math.min(prev, planSegments.length - 2)));
                        setDeleteConfirmPending(false);
                      }}
                    >
                      {t(uiLang, 'planConfirmDelete')}
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        <Divider style={{ margin: '4px 0' }} />

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <button ref={(el) => { _this.runButtonEl = el; }} onClick={async() =>{
        _this.run_cycle_stop=false;
        _this.visionTimeouts=0;

        _this.current_error=undefined;

        let loop_count=0;
        _this.speedStartTime = Date.now();
        _this.packCountOffset = 0;
        _this.packTimestamps = [];
        _this.lastPackCount = 0;
        //setPackSpeedInfo(null);
        await runAllObjects((checkpoint_name:string,data:any)=>{
          if(checkpoint_name.startsWith("[INPUT]")==false){
            console.log("checkpoint",checkpoint_name,data);
          }
          // setRunningState(checkpoint_name);

          // console.log("run_cycle_stop",_this.run_cycle_stop);
          return new Promise((resolve,reject)=>{
          // The checkpoint's handling, re-runnable: an error hold parks the
          // checkpoint and resume runs this again. Resume used to call the
          // bare resolve with no value, so a hold at cycle_start or
          // GetProductionPlan crashed the loop on `undefined.production_plan`
          // (at GetProductionPlan with a part on the nozzle), and a hold at
          // [STEP][REEL ADV] skipped the plan bookkeeping.
          const handle=():void=>{


            function _resolve(){
              if(_this.stepMode==true){
                _this.stepMode_resolve=resolve;
                return;
              }
              
              else
              {
                resolve(undefined);
              }
            }


            
            // STOP takes effect only at the start of a cycle, when the part in
            // hand has been placed or tossed and the plan / pack bookkeeping
            // is complete. It used to reject at *any* checkpoint ("||true"),
            // including "[STEP][REEL ADV]": the reel had already moved but
            // the plan was not decremented, so after RUN the empty (or pack)
            // segment was done again -- 2 extra empty cells in a chaos test,
            // the owner's "+2 after stop" (2026-09-24). It could also stop
            // with a part on the nozzle.
            if(_this.run_cycle_stop==true && checkpoint_name=="cycle_start")
            {
              reject();
              return;
            }


            if(_this.current_error!=undefined){
              console.log("ERROR HOLDING",_this.current_error);
              setRunningState(JSON.stringify(_this.current_error));

              _this.stepMode_resolve=()=>handle();
              return;
            }



            if(checkpoint_name=="NG_COUNT"){

              resolve(undefined);
              setPackSpeedInfo(prev => {
                const ng = { ...(prev?.ngCount ?? {}) };
                const cls = data.class.toString();
                ng[cls] = (ng[cls] ?? 0) + data.count;
                return prev ? { ...prev, ngCount: ng } : { count: 0, overallHr: 0, recentHr: 0, ngCount: ng };
              });
              return;
            }


            
            if(checkpoint_name.startsWith("[STEP][REEL ADV]")){
              let adv_count=data.adv_count;

              console.log("pre production_plan",JSON.stringify(data),JSON.stringify(_this.production_plan));
              const plan = _this.production_plan ?? (_this.production_plan = []);
              const segmentsBefore = plan.length;
              try{
                applyAdvance(plan, adv_count, data.type);   // lib/production/plan.ts
              }catch(e:any){
                console.error("plan bookkeeping:",e?.message??e);
                reject(e instanceof Error ? e : new Error(String(e)));
                return;
              }
              if(plan.length < segmentsBefore){
                _this.production_plan_stageIndex = (_this.production_plan_stageIndex ?? 0) + 1;
              }
              _resolve();
              setProductionPlanTick((x) => x + 1);
              console.log("production_plan",JSON.stringify(_this.production_plan));
              return;
            }
            
            if(checkpoint_name.startsWith("[STEP]")){
              _resolve();
              return;
            }
            else if(checkpoint_name.startsWith("[NG PICK]")||checkpoint_name.startsWith("[TOSS]")){
              setTossInfo(data);
              
              if(_this.tossPauseMode==true){
                _this.stepMode_resolve=resolve;
                return;
              }
              resolve(undefined);
            }
            
            else if(checkpoint_name=="start"){

              setRunningState("start");
              resolve(undefined);
            }
            else if(checkpoint_name=="cycle_start"){

              
              if((_this as any).run_cycle_stop==true){
                reject();
                return;
              }
              loop_count++;
              resolve({production_plan:_this.production_plan,batch_count:0,current_count:0});
            }
            else if(checkpoint_name=="ERROR"){
              setRunningState(JSON.stringify(data));
              _this.run_cycle_stop=true;
              reject();
            }
            else if(checkpoint_name=="_PACK_INFO_"){

              const now = Date.now();
              const delta = data.packCounter - (_this.lastPackCount ?? 0);
              _this.lastPackCount = data.packCounter;
              for (let i = 0; i < delta; i++) (_this.packTimestamps as number[]).push(now);

              const elapsed = now - (_this.speedStartTime ?? now);
              const adjustedCount = data.packCounter - (_this.packCountOffset ?? 0);
              const overallHr = elapsed > 0 ? adjustedCount / elapsed * 3600000 : 0;

              const recentWindowMs = 60000;
              const timestamps = _this.packTimestamps as number[];
              const recentPacks = timestamps.filter((ts: number) => now - ts < recentWindowMs).length;
              const firstTs = timestamps.length > 0 ? timestamps[0] : now;
              const recentElapsed = Math.min(recentWindowMs, now - firstTs);
              const recentHr = recentElapsed > 0 ? recentPacks / recentElapsed * 3600000 : 0;
              

              setPackSpeedInfo(prev =>{


                let prevCount=(prev?.count??0);
                if(isNaN(prevCount))prevCount=0;

                return{ count:prevCount+delta, overallHr, recentHr, ngCount: prev?.ngCount ?? {} }
              });
              setPackInfoString(
                uiLang === 'zh'
                  ? `數量:${data.packCounter}pcs 速度:${(overallHr/60).toFixed(2)}pcs/min`
                  : `Count:${data.packCounter}pcs Speed:${(overallHr/60).toFixed(2)}pcs/min`
              );
              resolve(undefined);
            }
            else if(checkpoint_name=="GetProductionPlan"){

              resolve({production_plan:_this.production_plan});
              return;
            }
            else{
              resolve(undefined);
            }
          };
          handle();
          });
        });
      }} style={{ backgroundColor: '#16a34a', color: 'white', fontWeight: 700, border: 'none', borderRadius: 8, padding: '10px 14px' }}>RUN</button>
      <button onClick={async() =>{
        if(stepMode==true){
          _this.stepMode_resolve?.();
          _this.stepMode_resolve=undefined;
        }
        setStepMode(!stepMode);

      }} style={{ borderRadius: 8, padding: '9px 12px' }}>Step:{stepMode?"ON":"OFF"}</button>


      
      <button onClick={async() =>{
        if(tossPauseMode==true){
          _this.stepMode_resolve?.();
          _this.stepMode_resolve=undefined;
        }
        setTossPauseMode(!tossPauseMode);

      }} style={{ borderRadius: 8, padding: '9px 12px' }}>{t(uiLang, 'tossPause')}:{tossPauseMode?"ON":"OFF"}</button>

      
      <button //disabled={stepMode==false && tossPauseMode==false} 
        onClick={async() =>{
        
        let curTime=Date.now();
        while(_this.current_error!=undefined){
          _this.current_error=undefined;

          await delay(500);
          if(Date.now()-curTime>10000){
            return;//after 10 seconds,current_error is still not resolved, stop the cycle
          }
        }
        setRunningState("no error");



        console.log("stepMode_resolve",_this.stepMode_resolve);
        _this.stepMode_resolve?.();
        _this.stepMode_resolve=undefined;
      }} style={{ borderRadius: 8, padding: '9px 12px', fontWeight: 700 }}>{">"}</button>


      <button style={{backgroundColor:"#b91c1c",color:"white", border: 'none', borderRadius: 8, padding: '10px 14px', fontWeight: 700}} onClick={async() =>{
        // While running: ask the loop to stop after the part in hand; it
        // clears isRunning itself once it has (see runAllObjects' end).
        // While idle: the branch below unloads the nozzle to the feeder.
        const wasRunning=_this.isRunning==true;
        _this.run_cycle_stop=true;
        if(wasRunning) setRunningState("stopping");

        _this.stepMode_resolve?.();
        _this.stepMode_resolve=undefined;

        if(_this.isRunning==false){
          await sendTcpMsgPack(cmd.G1({"Z": safe_z,"A":0 }))
  
          await sendTcpMsgPack(cmd.G1({ "X":tossLocation_0.X,"Y":tossLocation_0.Y }))
  
          await sendTcpMsgPack(cmd.G4(0.1))
  
          await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state":0 }))
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
          await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_blow, "state": 1<<IO_Pins.O.Nozzle_blow, reset_ms:5 }))
        }
      }}>STOP</button>
        </div>
      </div>
      <div style={{ marginTop: 10 }}>
        <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: '#374151' }}>{t(uiLang, 'quickCheckPlate')}</div>
        <div style={rowWrapStyle}>
      {[-2,-1,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14].map((item)=>{
        return <button key={"Check Plate_"+item} onClick={async() =>{


          if(item<1){
            await checkInspObject(-item,-item,0.5);
            return;
          }
          await checkInspObject(-item,0,0.5);


        }} style={{ borderRadius: 8, padding: '6px 10px' }}>{item}</button>
      })}
        </div>
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ ...statusPillStyle, background: '#ecfeff', color: '#0f766e' }}>
          {packInfoString || t(uiLang, 'waitingCycleData')}
        </span>
        <span style={{ ...statusPillStyle, background: '#f1f5f9', color: '#0f172a' }}>
          {t(uiLang, 'status')}: {runningState}
        </span>
        {tossInfo && <span style={{ ...statusPillStyle, background: '#fff7ed', color: '#9a3412' }}>{t(uiLang, 'toss')}: {JSON.stringify(tossInfo)}</span>}
      </div>
      </div>

      <details style={{ ...cardStyle, background: '#f8fafc', borderStyle: 'dashed' }}>
        <summary style={{ cursor: 'pointer', fontWeight: 700 }}>{t(uiLang, 'engineeringConsole')}</summary>
        <div style={{ marginTop: 10 }}>
          <div style={{ ...rowWrapStyle, marginBottom: 10 }}>
      <button onClick={async() =>{
        // await testupdatecalibParam();
        // 
        let calibResult=await BtmCheckCalib();
        console.log("BtmCheckCalib",calibResult);
        console.log("BtmCheckOffset2ArmOffset",BtmCheckOffset2ArmOffset(calibResult,{X:20,Y:0},0));
        console.log("BtmCheckOffset2ArmOffset",BtmCheckOffset2ArmOffset(calibResult,{X:20,Y:0},180));
      }}>BtmCheckCalib</button>



            
      <button onClick={async() =>{
        await sendTcpMsgPack(cmd.G1({ "A": -700 }));
        await sendTcpMsgPack(cmd.G1({ "A": 100 }));
      }}>Zrot</button>
      
      <button onClick={async() =>{

        
        await sendTcpMsgPack(cmd.G1({ "Z": safe_z }));
        await sendTcpMsgPack(cmd.G1({ "X":inspLocation_withObject.X,"Y":inspLocation_withObject.Y}));
        //drop Z to inspLocation.Z
        await sendTcpMsgPack(cmd.G1({ "Z":inspLocation_withObject.Z }));


        async function checkSlot_and_reelAdv():Promise<{is_clear:number[],is_OK:number[],post_check_advCount:number,locHole:{status:number,x:number,y:number,mmpp:number}}> { 
              

          let reelAdvPinOpSeq:number[]=[];

          let reelAdvWaitTime=0;
          let topCheckDataPromise=waitForTOPCheckData();
          console.log("TRIGGER top check data");


          let initDelayTime=0;
          initDelayTime=100;
          initDelayTime+=reelAdvWaitTime;

          let pin_side_light=1<<IO_Pins.O.CAM_Top_SideLight;
          let pin_down_light=1<<IO_Pins.O.CAM_Top_Light0;
          let pin_cam_trigger=1<<IO_Pins.O.CAM_Top;


          let lastPinOpSeq=[//top check camera trigger IO
            ...reelAdvPinOpSeq,
            initDelayTime, pin_side_light|pin_cam_trigger, pin_side_light|pin_cam_trigger,
            1,pin_side_light|pin_cam_trigger, 0,
          40, pin_down_light|pin_cam_trigger, pin_down_light|pin_cam_trigger,
          4, pin_down_light|pin_cam_trigger, 0,]

          sendTcpMsgPack(cmd.M4({
            pin_op_seq:lastPinOpSeq
            ,"motion_id_offset":-1,"motion_progress":0
          }));

          console.log("lastPinOpSeq",lastPinOpSeq);

          console.log("wait for top check data");
          let topCheckData=(await topCheckDataPromise) as ReturnType<typeof waitForTOPCheckData>;
          console.log("topCheckData",topCheckData);

          let retData={...topCheckData,post_check_advCount:0};
          return retData;
        }

        
        let sideShotPin=1<<IO_Pins.O.CAM_Side | 1<<IO_Pins.O.CAM_Side_Light0;
        let BTMShotPin=1<<IO_Pins.O.CAM_Btm | 1<<IO_Pins.O.CAM_Btm_Light0;
        for(let i=0;i<3;i++){
          
          //let topcam_check_report_promise= checkSlot_and_reelAdv();
          let sideCam_repReg=waitForSideCheckData();
          await sendTcpMsgPack(cmd.M4({"pin": sideShotPin, "state":sideShotPin,reset_ms:5, "motion_progress": 1}))
          

          
          let btmCam_repReg=waitForBTMCheckData();
          await sendTcpMsgPack(cmd.M4({"pin": BTMShotPin, "state":BTMShotPin,reset_ms:5 }))
          await sideCam_repReg;
          await btmCam_repReg;

          
          // let sideCam_repReg2=waitForSideCheckData();
          // await sendTcpMsgPack({ "type": "M", "cmd": "M4","pin": sideShotPin, "state":sideShotPin,reset_ms:5, "motion_progress": 1, })
          // let sideCam_rep_data2 = await sideCam_repReg2 ;
          //let topcam_check_report = await topcam_check_report_promise;

          
          //await delay(200);

          console.log("i",i);

        }
      }}>TestInspStressTest</button>
      






      <button onClick={async() =>{
        goToSlotLocation();
      }}>GoSlotLoc</button>

      <button onClick={async() =>{
        testBurn();
      }}>BurnTest</button>

      
      <button onClick={async() =>{
        stopTestBurn();
      }}>StopBurnTest</button>
      

      <button onClick={async() =>{
        await sendTcpMsgPack(cmd.ReelGo({"Distance":4*2, "F":5000,ACC:100000,DEA:10000,JERK:100000 }));
        await delay(100);
        await sendTcpMsgPack(cmd.ReelGo({"Distance":4*2, "F":5000,ACC:100000,DEA:10000,JERK:100000 }));
      }}>ReelGo</button>
      {/* <button onClick={async() =>{
        // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 1 });

        sendTcpMsgPack(cmd.M4({
          pin_op_seq:[
    
            0, 1<<3|1<<(6+8), 1<<3|1<<(6+8),
            1, 1<<3|1<<(6+8), 0,
           30, 1<<15|1<<(6+8), 1<<15|1<<(6+8),
           1, 1<<15|1<<(6+8), 0,]
        }));



        // FlexVibCtrl.top_light_on();
        // sendTcpMsgPack({ "type": "M", "cmd": "M4","group":1, "pin": 1<<6, "state": 1<<6,reset_ms:20 });
        // await delay(15);
        // console.log("repReg",repReg);
        FlexVibCtrl.top_light_off();
      }}>TopCamTrig</button> */}

      
      {/* <button onClick={async() =>{
        sendTcpMsgPack(cmd.M4({"group":1, "pin": 1<<6, "state": 1<<6,reset_ms:500 }));
        sendTcpMsgPack(cmd.M4({"group":1, "pin": 1<<0 | 1<<2 | 1<<4, "state": 1<<0 | 1<<2 | 1<<4,reset_ms:500 }));
      }}>SynCam</button> */}



      <button onClick={async() =>{
        await loadCalibData();
      }}>LOAD</button>

      {/* <button 
      onKeyDown={async() =>{
        sendTcpMsgPack(cmd.M4({"group":0, "pin": 1<<6, "state":0xFF,reset_ms:500 }))
        // await sendTcpMsgPack({ "type": "G1", "Z": -18.9 })
      }}
      
      onMouseDown={async() =>{
        sendTcpMsgPack(cmd.M4({"group":0, "pin": 1<<6, "state":0xFF }))
        // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
      }}
      
      onMouseUp={async() =>{
        sendTcpMsgPack(cmd.M4({"group":0, "pin": 1<<6, "state":0 }))
      }}
      
      >MirrorOn</button> */}

      <button onClick={async() =>{
        await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.ReelAdv, "state": 1<<IO_Pins.O.ReelAdv, reset_ms:40 }))
        console.log("ReelAdv 1");
        await delay(60);
        await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.ReelAdv, "state": 1<<IO_Pins.O.ReelAdv, reset_ms:40 }))
        console.log("ReelAdv 2");
      }}>ReelAdv</button>

      
      <button onClick={async() =>{
        await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.ReelWheelFeed, "state": 1<<IO_Pins.O.ReelWheelFeed, reset_ms:10 }))


        console.log("reel wheel feed",await sendTcpMsgPack(cmd.GetDigitalInput(0)));
      }}>reel wheel feed</button>

      <button onClick={async() =>{
        let speed = 100;
        await sendTcpMsgPack(cmd.G1({ "F":speed,ACC:speed*3,DEA:speed*3,JERK:speed*300 }))
        setIsJoggingModalOpen(true);
      }}>Jogging</button>


      <button onClick={async() =>{
        (async()=>{
          FlexVibCtrl.top_light_on();
          await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.CAM_FlexFeeder, "state": 1<<IO_Pins.O.CAM_FlexFeeder, reset_ms:50 }));
          await delay(100);

          FlexVibCtrl.top_light_off();
        })();

        
        await sendTcpMsgPack(camTrig(IO_Pins.O.CAM_Side, IO_Pins.O.CAM_Side_Light0, { reset_ms: 50 }));
        
      }}>SideCam Trig</button>



      <button onClick={async() =>{
        await FVib(0x1D,600);
      }}>FVib_v0x1D</button>
      <button onClick={async() =>{
        await FVib(5,600);
      }}>FVib_v5</button>
      <button onClick={async() =>{

        
        await FVib(10,100);
        await delay(200);
        await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.FlexVib_brake, "state": 1<<IO_Pins.O.FlexVib_brake, "motion_id_offset": 0, "motion_progress": 0, "reset_ms": 900 }))


      }}>FVib_v10</button>
      <button onClick={async() =>{
        await FVib(11,100);
      }}>FVib_v11</button>
      <button onClick={async() =>{
        
        // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z,"F":speed })
        // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": -5,Y:-50 })

        await sendTcpMsgPack(cmd.WaitForMotionStop());

        //sendTcpMsgPack({ "type": "M", "cmd": "M4", "pin": 1, "state": 1, "motion_id_offset": 0, "motion_progress": 1, "reset_ms": 100 })
        
        let repReg=waitForFFeederCheckData();
        (async()=>{
          await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.CAM_FlexFeeder, "state": 1<<IO_Pins.O.CAM_FlexFeeder, reset_ms:50 }));
          FlexVibCtrl.top_light_on();
          
          await delay(50);

          FlexVibCtrl.top_light_off();
        })();
      
        let ret_str_arr_data = await repReg;

        type datatype = {
          x:number;
          y:number;
          angle_deg:number;
          surround_clear:number;
          center_clear:number;
        }
        let data=ret_str_arr_data.map((item:{x:number,y:number,ang:number,inner:number,outer:number}):datatype=>{
          //2310.54;1520.57;3.86173;1;1;id;4 format
          return {
            x:item.x,
            y:item.y,
            angle_deg:item.ang,
            surround_clear:item.outer,
            center_clear:item.inner,
          };
        }).filter((item:datatype)=>item.surround_clear == 1 && item.center_clear ==1);
        console.log(data);
        setLatestObjArr(data);
        
      }}>CAM shot </button>


      <button onClick={async() =>{
        await fsPromises.writeFile(env_path+"/calib.json", JSON.stringify(calibRecPair));
        await loadCalibData();
      }}>Process calib pair</button>


<br/>

      <button onClick={async() =>{

      let light_pin=1<<IO_Pins.O.CAM_Side_Light0;
      let cam_pin=1<<IO_Pins.O.CAM_Side;


      sendTcpMsgPack(cmd.M4({
        pin_op_seq:[

          0, light_pin|cam_pin,  light_pin|cam_pin,
          1, light_pin|cam_pin, 0,
        ]
      }));

      }}>SCamTake</button>

      <button onClick={async() =>{

        let light_pin=1<<IO_Pins.O.CAM_Btm_Light0;
        let cam_pin=1<<IO_Pins.O.CAM_Btm;


        sendTcpMsgPack(cmd.M4({
          pin_op_seq:[

            0, light_pin|cam_pin,  light_pin|cam_pin,
            1, light_pin|cam_pin, 0,
          ]
        }));

      }}>BCamTake</button>

recheck:
      {[0,1,2,3,4,5,6,7,8,9].map((item)=>{
        return <button key={"BTM_recheck_"+item} onClick={async() =>{
          let ret_data = await VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"revisit",index:item});
          console.log("ret_data",ret_data);
        }}> {item}</button>
      })}


<br/>


      <button onClick={async() =>{
        let slight_pin=0;//1<<IO_Pins.O.CAM_Side_Light0;

        sendTcpMsgPack(cmd.M4({
          pin_op_seq:[

            0, 1<<IO_Pins.O.CAM_Top_SideLight|1<<IO_Pins.O.CAM_Top|slight_pin, 1<<IO_Pins.O.CAM_Top_SideLight|1<<IO_Pins.O.CAM_Top|slight_pin,
            1, 1<<IO_Pins.O.CAM_Top_SideLight|1<<IO_Pins.O.CAM_Top, 0,
           40, 1<<IO_Pins.O.CAM_Top_Light0|1<<IO_Pins.O.CAM_Top|slight_pin, 1<<IO_Pins.O.CAM_Top_Light0|1<<IO_Pins.O.CAM_Top|slight_pin,
           1, 1<<IO_Pins.O.CAM_Top_Light0|1<<IO_Pins.O.CAM_Top|slight_pin, 0,
          ]
        }));

      }}>CamTake</button>

      recheck:
      {[0,1,2,3,4,5,6,7,8,9].map((item)=>{
        return <button key={"TOP_recheck_"+item} onClick={async() =>{
          
          _this.revisit_obj_idx=item;
          let ret_data = await VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"revisit",index:_this.revisit_obj_idx,revisit_idx:_this.revisit_idx,SL_sens_alpha:_this.SL_sens_alpha});
          console.log("ret_data",ret_data);
        }}> {item}</button>
      })}

      
save:
      {[0,1,2].map((item)=>{
        return <button key={"TOP_save_"+item} onClick={async() =>{
          let ret_data = await VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"save_target",
            t0:item==1?"NG_0":undefined,
            t1:item==1?"NG_1":undefined,
            t2:item==1?"NG_2":undefined});
          console.log("ret_data",ret_data);
        }}> {item}</button>
      })}

<br/>
      revisit_idx:
      {[-1,0,1,2].map((item)=>{
        return <button key={"TOP_recheck_revidx_"+item} onClick={async() =>{
          _this.revisit_idx=item;
          await VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"revisit",index:_this.revisit_obj_idx,revisit_idx:_this.revisit_idx,SL_sens_alpha:_this.SL_sens_alpha});
        }}> {item}</button>
      })}


      slider:
      <input
        type="range"
        defaultValue={256}
        min={0}
        max={256*2}
        step={1}
        onChange={e => {
          _this.SL_sens_alpha = Number(e.target.value);
        }}
        onMouseUp={() => {
          VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"revisit",index:_this.revisit_obj_idx,revisit_idx:_this.revisit_idx,SL_sens_alpha:_this.SL_sens_alpha});
        }}
        onKeyUp={e => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"revisit",index:_this.revisit_obj_idx,revisit_idx:_this.revisit_idx,SL_sens_alpha:_this.SL_sens_alpha});
          }
        }}

      />

<br/>

      <button onClick={async() =>{

// VP_sendTcpMsgPack({"type":"TopInsp","index":Math.floor(Math.random()*50)*2});
      let ret_data = await VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"BufferSize"});
      console.log("ret_data",ret_data);
      }}>getBufSize</button>
          </div>
        </div>
      </details>


      <details style={cardStyle}>
        <summary style={{ cursor: 'pointer', fontWeight: 700 }}>{t(uiLang, 'calibRecords')}</summary>
        {/* <button onClick={async() =>{
            await CalibFeederAcc();
          }}>Calib Feeder Acc</button> */}
        <Divider />
        {calibRecPair.map((item,index)=>{
          return <div key={index} style={{ marginBottom: 8 }}>
            {item.ObjOnCamCoord.x.toFixed(3)},{item.ObjOnCamCoord.y.toFixed(3)}:::{item.ObjOnRobotCoord.X.toFixed(3)},{item.ObjOnRobotCoord.Y.toFixed(3)},{item.ObjOnRobotCoord.Z.toFixed(3)}
            
            <button onClick={async() =>{
              await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"F":speed }))
              await sendTcpMsgPack(cmd.G1({ "X": item.ObjOnRobotCoord.X,"Y": item.ObjOnRobotCoord.Y }))
              await sendTcpMsgPack(cmd.G1({ "Z": item.ObjOnRobotCoord.Z }))
            }} style={{ marginLeft: 8 }}>go point</button>

            
            <button disabled={calibParams == null} onClick={async() =>{
              if(calibParams == null){
                return;
              }
              let predicted_location = predictRobotCoordinates(calibParams,item.ObjOnCamCoord);
              console.log(predicted_location,item.ObjOnRobotCoord);

              
              await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"F":speed }))
              await sendTcpMsgPack(cmd.G1({ "X": predicted_location.X,"Y":predicted_location.Y }))
              await sendTcpMsgPack(cmd.G1({ "Z": predicted_location.Z }))



            }} style={{ marginLeft: 6 }}>go Predict point</button>
            
            <button onClick={async() =>{
              setCalibRecPair(calibRecPair.filter((_item,fidx)=>fidx!=index));
            }} style={{ marginLeft: 6 }}>X</button>


            
            </div>
        })}

        <Divider />
        {latestObjArr.map((item,index)=>{
          return <div key={index} style={{ marginBottom: 8 }}>{item.x.toFixed(3)},{item.y.toFixed(3)},{item.angle_deg.toFixed(3)},{item.surround_clear.toFixed(3)},{item.center_clear.toFixed(3)} 


          <button disabled={calibParams == null} onClick={async() =>{
              if(calibParams == null){
                return;
              }
              let predicted_location = predictRobotCoordinates(calibParams,item);
              console.log(predicted_location,item);

              
              await sendTcpMsgPack(cmd.G1({ "Z": safe_z,F:600,Cor:15 }))
              await sendTcpMsgPack(cmd.G1({ "X": predicted_location.X,"Y":predicted_location.Y }))
              await sendTcpMsgPack(cmd.G1({ "Z": predicted_location.Z }))
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z })

              

              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X":27,"Y":0 })
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z })


              
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X":57,"Y":-67})
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -11.9 })
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -11.9 })
              // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z })
          }} style={{ marginLeft: 8 }}>Go</button>
          <button onClick={async() =>{
            
            await sendTcpMsgPack(cmd.WaitForMotionStop())
            let current_location = await sendTcpMsgPack(cmd.ReadLatestCmdLocation())
            console.log(item,current_location);
            setCalibRecPair([...calibRecPair, {ObjOnCamCoord:item,ObjOnRobotCoord:current_location}]);
          }} style={{ marginLeft: 6 }}>+</button>

          </div>
        })}
      </details>






      <Modal isOpen={isJoggingModalOpen} onClose={() => setIsJoggingModalOpen(false)} closeClickCount={2} closeClickTimeout={400}
        style={{width:"40%",height:"40%"}}
        >
        <JoggingPad speedFactor_XY={0.2} speedFactor_Z={0.1} sendTcpMsgPack={sendTcpMsgPack}  />
      </Modal>
    </div>
  )
}





