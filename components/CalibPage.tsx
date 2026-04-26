import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Divider, Popconfirm, Popover, Typography } from 'antd';
import { JoggingPad } from '../JoggingPad';
import { Modal } from '../Modal';
import type { COMCtrlObj } from '../types';
import { delay } from '../utils/async';
import { t, type UILang } from '../i18n';
import { useHarnessAction } from '../harness/registry';
import { cmd } from '../lib/protocol';


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

// Vision check IDs — match the IDs the vision plugin replies with.
const FFeederCheckID = 104500;
const SideCheckID    = 114500;
const BTMCheckID     = 124500;
const TOPCheckID     = 134500;

// Cartesian setpoints (mm). Calibrated for the current cell layout; if
// the machine is repositioned, recalibrate and update here.
const SAFE_Z = 12;
const OBJECT_HEIGHT = 2.4 + 1;
const PICK_Z_LIFT = 2.2;

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
const INSP_LOCATION: XYZ = { X: 15.618, Y: 10.330, Z: 0.7 + 0.4 };
const SLOT_LOCATION: XYZ = { X: 41.7,   Y: -79.752, Z: -11.400 };
const TOSS_LOCATION_0: XYZ = { X: -61.074, Y: 60.775, Z: 9 };
const TOSS_LOCATION_1: XYZ = { X: -31,     Y: 8.7,    Z: 5 };
const TOSS_LOCATION_2: XYZ = { X: -63.321, Y: 9.870,  Z: 5 };
const WAIT_FLEXFEEDER_LOCATION: XYZ = { X: -46.350, Y: 30.181, Z: SAFE_Z };



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
  const VP_sendTcpMsgPack = COMCtrlObj.VP_sendTcpMsgPack;
  const FlexVibCtrl = COMCtrlObj.FlexVibCtrl;
  
  const [runningState, setRunningState] = useState<string>("idle");
  const [tossInfo, setTossInfo] = useState<any>();

  const [packInfoString, setPackInfoString] = useState<string>("");
  const [packSpeedInfo, setPackSpeedInfo] = useState<{ count: number; overallHr: number; recentHr: number; ngCount: Record<string, number> } | null>(null);
  const [productionPlanTick, setProductionPlanTick] = useState<number>(0);

  const [stepMode, setStepMode] = useState<boolean>(false);
  const [tossPauseMode, setTossPauseMode] = useState<boolean>(false);
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


  useEffect(()=>{



    function registerPromiseTunnel(id:number,name:string){
      _this[name]=undefined;

      if(_this[name+"_Promise"]!=undefined){
        _this[name+"_Promise"].reject(new Error("it's a left over promise of "+name));
        _this[name+"_Promise"]=undefined;
      }
      COMCtrlObj.VP_regTcpMsgCB(id,undefined);//force unload previous leftover
      COMCtrlObj.VP_regTcpMsgCB(id, (data: any) => {
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



  
  let waitForFFeederCheckData=async():Promise<any> =>{
    // if(_this.TOPCheckData!==undefined){
    //   return _this.TOPCheckData;
    // }
    return new Promise((resolve, reject)=>{
      _this.FFeederCheckData_Promise={resolve, reject};
    });
  }

  let waitForTOPCheckData=async():Promise<any> =>{
    // if(_this.TOPCheckData!==undefined){
    //   return _this.TOPCheckData;
    // }
    return new Promise((resolve, reject)=>{
      _this.TOPCheckData_Promise={resolve, reject};
    });
  }

  let waitForSideCheckData=async():Promise<any> =>{
    // if(_this.SideCheckData!==undefined){
    //   return _this.SideCheckData;
    // }
    return new Promise((resolve, reject)=>{
      _this.SideCheckData_Promise={resolve, reject};
    });
  }

  let waitForBTMCheckData=async():Promise<any> =>{
    // if(_this.BTMCheckData!==undefined){
    //   return _this.BTMCheckData;
    // }
    return new Promise((resolve, reject)=>{
      _this.BTMCheckData_Promise={resolve, reject};
    });
  }

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

  function getFeedSpeedConfig(speed:number=25){
    let jerk = speed * 400
    let acc = speed * 100
    let dea = acc
    return {
      F:speed,
      JERK:jerk,
      ACC:acc,
      DEA:dea,
    }
  }

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

    if (Math.abs(det) > 1e-9) {
      const invDet = 1 / det;
      mat_offset_cam2arm = [
        [delta_cam_y.Y * invDet, -delta_cam_y.X * invDet, 0],
        [-delta_cam_x.Y * invDet, delta_cam_x.X * invDet, 0],
        [0, 0, 1],
      ];
    } else {
      console.warn("BtmCheckCalib: camera offsets are colinear, fallback to identity matrix.");
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

    const btmCheckInfo: TYPE_BtmCheckCalibInfo = {
      center: {...nozzleLoc0_0},
      mmpp,
      mat_offset_cam2arm,
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
      return;
    }
    // COMCtrlObj.regTcpMsgCB(134500,undefined);
    
    // COMCtrlObj.regTcpMsgCB(134500, (data: any) => {
    //   console.log("data",data);
    // });

    let slotDist=8;


    await runinng_checkpoint("start",{time:Date.now()});

    async function checkSlot_and_reelAdv(nxt_adv_count:number,waitForReelVisualClearPromise:Promise<any> | undefined):Promise<{is_clear:number[],is_OK:number[],post_check_advCount:number,locHole:{status:number,x:number,y:number,mmpp:number}}> { 
      
      
      if(waitForReelVisualClearPromise!=undefined){
        console.log("wait for reel visual clear");
        let cur_time=Date.now();
        await waitForReelVisualClearPromise;
        let end_time=Date.now();
        console.log("reel visual clear time",end_time-cur_time);
      }

      let reelAdvPinOpSeq=[];

      let reelAdvWaitTime=0;
      for(let i=0;i<nxt_adv_count;i++){
        {        
          // console.log("reel adv",i);
          // if(i>0){
          // }
          // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1<<IO_Pins.O.ReelAdv, "state": 1<<IO_Pins.O.ReelAdv,reset_ms:50,"motion_id_offset":-1,"motion_progress":0 })//reel adv
          // await delay(60);

          reelAdvPinOpSeq.push(reelAdvWaitTime, 1<<IO_Pins.O.ReelAdv, 1<<IO_Pins.O.ReelAdv);
          reelAdvPinOpSeq.push(50, 1<<IO_Pins.O.ReelAdv, 0);

          reelAdvWaitTime=60;
          // if(i>1){
          //   await delay(200);
          // }
        }
      }

      let topCheckDataPromise=waitForTOPCheckData();
      console.log("TRIGGER top check data");


      let initDelayTime = (nxt_adv_count === 2 ? 150 : 100) + reelAdvWaitTime;


      
      // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1<<3, "state": 1<<3,reset_ms:100,"motion_id_offset":0,"motion_progress":0 });
      // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":1, "pin": 1<<6, "state": 1<<6,reset_ms:100,"motion_id_offset":0,"motion_progress":0 });//check slot object


      // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.1 });



      // FlexVibCtrl.top_light_on();


      // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1<<3|1<<(6+8), "state": 1<<3|1<<(6+8),reset_ms:2,"motion_id_offset":-10,"motion_progress":0 });


      // // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.04 });

      // await delay(30);
      // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":1, "pin": 1<<7 | 1<<6, "state": 1<<7 | 1<<6,reset_ms:2,"motion_id_offset":-10,"motion_progress":0 });


      let lastPinOpSeq=[//top check camera trigger IO
        ...reelAdvPinOpSeq,
        initDelayTime, 1<<IO_Pins.O.CAM_Top_SideLight|1<<IO_Pins.O.CAM_Top, 1<<IO_Pins.O.CAM_Top_SideLight|1<<IO_Pins.O.CAM_Top,
        1, 1<<IO_Pins.O.CAM_Top_SideLight|1<<IO_Pins.O.CAM_Top, 0,
       80, 1<<IO_Pins.O.CAM_Top_Light0|1<<IO_Pins.O.CAM_Top, 1<<IO_Pins.O.CAM_Top_Light0|1<<IO_Pins.O.CAM_Top,
       1, 1<<IO_Pins.O.CAM_Top_Light0|1<<IO_Pins.O.CAM_Top, 0,]

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
    let start_time=Date.now();

    let packCounter=0;




    let candidate_obj_arr:FlexFeeder_object_data_type[]=[];

    // let newLatestObjArr=[...latestObjArr];




            
    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0,Cor:cor,...getFeedSpeedConfig(1000) }))
    
    await sendTcpMsgPack(cmd.G1({"X":44,"Y":89}))
    await sendTcpMsgPack(cmd.G1({"X":-20,"Y":-22,...getFeedSpeedConfig(1000 ) }));

    await runinng_checkpoint("go ready",{time:Date.now()});
    let nxt_adv_count=0;
    let isZinSafeZone=false;




    
    type FlexFeeder_object_data_type = {
      x:number;
      y:number;
      angle_deg:number;
      surround_clear:number;
      center_clear:number;
    }
    let FF_mode_counter=0;
    async function checkFlexFeederPlate(doShake:boolean=false,doStorageFeed:boolean=false){
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": -5,Y:-50 })
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z,"A":0})
      if(doShake){
        await sendTcpMsgPack(cmd.WaitForTriggerMotionProgress({"motion_progress": 0}));

        if(doStorageFeed)
        {
          FlexVibCtrl.von(0x1D);
        }

       
        await FVib(10,160);
  
        await delay(120);
        await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.FlexVib_brake, "state": 1<<IO_Pins.O.FlexVib_brake, "motion_id_offset": 0, "motion_progress": 0, "reset_ms": 700 }))

        console.log("FF_mode_counter",FF_mode_counter);

        FF_mode_counter++;


        
        await delay(700);
        FlexVibCtrl.voff(0x1D);

      }
      else
      {
        await sendTcpMsgPack(cmd.WaitForTriggerMotionProgress({"motion_progress": 1}));
        console.log("wait for motion progress 1");
      }
      //sendTcpMsgPack({ "type": "M", "cmd": "M4", "pin": 1, "state": 1, "motion_id_offset": 0, "motion_progress": 1, "reset_ms": 100 })
      
      let repReg=waitForFFeederCheckData();
      (async()=>{
        FlexVibCtrl.top_light_on();
        await delay(10);
        await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.CAM_FlexFeeder, "state": 1<<IO_Pins.O.CAM_FlexFeeder, reset_ms:50 }));

        await delay(50);

        FlexVibCtrl.top_light_off();
      })();

      let ret_str_arr_data = await repReg;


      let data=ret_str_arr_data.map((item:{x:number,y:number,ang:number,inner:number,outer:number}):FlexFeeder_object_data_type=>{
        //2310.54;1520.57;3.86173;1;1;id;4 format
        return {
          x:item.x,
          y:item.y,
          angle_deg:item.ang,
          surround_clear:item.outer,
          center_clear:item.inner,
        };
      })
      
      console.log(data);

      return data;
      // setLatestObjArr(data);
    }


     async function goCheckFlexFeederPlate():Promise<any>{
      await sendTcpMsgPack(cmd.WaitForTriggerMotionProgress({ "motion_id_offset": 0, "motion_progress": 0.02 }));
      let new_candidate_obj_arr=(await checkFlexFeederPlate(true,false)) as FlexFeeder_object_data_type[];

      if(new_candidate_obj_arr.length<25){
        FVib(0x1D,700);
        // FF_mode_counter=0;
      }

      return new_candidate_obj_arr.filter((item)=>item.surround_clear == 1 && item.center_clear ==1);

    }



    await runinng_checkpoint("BtmCheckCalib",{time:Date.now()});
    let btmCheckCalibInfo=await BtmCheckCalib();

    // return;

    await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0,...getFeedSpeedConfig(2000) }))
    let slotCheckPromise_BK:ReturnType<typeof checkSlot_and_reelAdv> | undefined = undefined;




    let feederCheckPromise:Promise<any> | undefined = undefined;

    let latestInputObj:any = undefined;

    _this.current_error=undefined;
    // Input watchdog: independent 400ms poll thread. Reads digital-input
    // flip-counters (catches sub-poll glitches), drives the press-roller
    // re-feed pulse, and stamps _this.current_error so the main pipeline
    // can abort at the next checkpoint. Runs until run_cycle_stop flips.
    async function inputWatchdog(){
      let prevFc:number[]=new Array(16).fill(0);
      let ReelLackingCounter=0;
      let isFirstCycle=true;
      while(_this.run_cycle_stop!=true){

        let {raw,fc}=await getDigitalInputFlipCount();

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
        // console.log("ReelPressRollerInPlace",ReelPressRollerInPlace);




        if(ReelLacking)
        {
          if((ReelLackingCounter&0b1)==0)
          {
            await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.ReelWheelFeed, "state": 1<<IO_Pins.O.ReelWheelFeed, reset_ms:70 }))
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
        if(ReelLackingCounter>10)
          errorString+="載帶缺料,";

        if(errorString!="")
        {
          _this.current_error={errorString,raw,fc};
          console.log("current_error",_this.current_error);
        }

        await delay(400);
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
        nxt_adv_count=0;
        let adv_count=Math.min(2,-_PP_[0]);
        let slotCheckPromise= checkSlot_and_reelAdv(adv_count,waitForReelVisualClearPromise);
        await slotCheckPromise;

        await delay(100);
        await runinng_checkpoint("[STEP][REEL ADV]",{
          adv_count:adv_count,

          type:"empty",
        });
        slotCheckPromise_BK=undefined;
        continue;
      }
      
      if(feederCheckPromise!=undefined){
        await sendTcpMsgPack(cmd.G1({ "Z": safe_z}))
        await sendTcpMsgPack(cmd.G1({X:wait_flexfeeder_location.X,Y:wait_flexfeeder_location.Y }))
     
        candidate_obj_arr=await feederCheckPromise;
        feederCheckPromise=undefined;
      }



      if(candidate_obj_arr.length==0){//still no available object, shake and check flex feeder now

        let candidate_obj_arr_promise=goCheckFlexFeederPlate(); 
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
        
        await sendTcpMsgPack(cmd.G1({ "Z": safe_z,"A":0 }))
      }


      let predicted_location = predictRobotCoordinates(calibParams,item);
      console.log(predicted_location,item);

      isZinSafeZone=false;
      
      await runinng_checkpoint("go to predicted location",i);
      await sendTcpMsgPack(cmd.G1({ "X": predicted_location.X,"Y":predicted_location.Y,"A":-item.angle_deg}))

      waitForReelVisualClearPromise=undefined;
      let _waitForReelVisualClearPromise= sendTcpMsgPack(cmd.WaitForTriggerMotionProgress({ "motion_id_offset": 0, "motion_progress": 0.01 }));

      if (_waitForReelVisualClearPromise && typeof (_waitForReelVisualClearPromise as Promise<any>).then === "function") {
      //  waitForReelVisualClearPromise = _waitForReelVisualClearPromise as Promise<any>;
      }

      let slotCheckPromise:Promise<{is_clear:number[],is_OK:number[],post_check_advCount:number,locHole:{status:number,x:number,y:number,mmpp:number}}> | undefined = slotCheckPromise_BK;
      await runinng_checkpoint("TOP_CAM check slot",i);
      if(slotCheckPromise==undefined){

        if(nxt_adv_count>2)nxt_adv_count=2;
        packCounter+=nxt_adv_count;
        console.log("nxt_adv_count",nxt_adv_count,"packCounter",packCounter);
        slotCheckPromise= checkSlot_and_reelAdv(nxt_adv_count,waitForReelVisualClearPromise);

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
      await sendTcpMsgPack(cmd.G1({ "Z": predicted_location.Z+pickZ_lift }))



          

      
      sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state": 1<<IO_Pins.O.Nozzle_suck }))//pick
      sendTcpMsgPack(cmd.G4(0.02))



      await sendTcpMsgPack(cmd.G1({ "Z": safe_z }))

      
      if(candidate_obj_arr.length==0){//no available object, shake and check flex feeder plate

        feederCheckPromise=goCheckFlexFeederPlate();

      }




      await runinng_checkpoint("[STEP] object picked",i);
      // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 5 })//DBG
      // await delay(5000);

      let inspBasAngle=90;

      await sendTcpMsgPack(cmd.G1({ "X":inspLocation_withObject.X,"Y":inspLocation_withObject.Y,"Z": inspLocation_withObject.Z+1,"A":inspBasAngle  }))

      await runinng_checkpoint("SideCam check",i);
      let sideCam_repReg=waitForSideCheckData();
      await sendTcpMsgPack(cmd.G1({ "Z":inspLocation_withObject.Z}))
      sendTcpMsgPack(camTrig(IO_Pins.O.CAM_Side, IO_Pins.O.CAM_Side_Light0, { reset_ms: 5, motion_id_offset: 0, motion_progress: 1 }))



      if(true){
        let angOffset=0;

        await runinng_checkpoint("[STEP]go to BTM insp",i);
        sendTcpMsgPack(cmd.G4(0.01))
        let btm_check_rep_promise= waitForBTMCheckData();
        await sendTcpMsgPack(camTrig(IO_Pins.O.CAM_Btm, IO_Pins.O.CAM_Btm_Light0, { reset_ms: 4 }))
        // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.03 })
        // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":1, "pin": (1<<3) | (1<<2), "state":(1<<3) | (1<<2),reset_ms:4 })
        sendTcpMsgPack(cmd.G4(0.001))


        
        // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "A":inspBasAngle+5 })//twist a bit to align the hole
        await sendTcpMsgPack(cmd.G1({ "A":inspBasAngle }))

        // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.05 })
        
        await sendTcpMsgPack(cmd.G1({ "Z": inspLocation_withObject.Z+1}))
        


        async function waitTime(promise:Promise<any>,name:string){
          let cur_time=Date.now();
          let data = await promise;
          let end_time=Date.now();
          console.log("waitTime",name,end_time-cur_time);
          return data;
        }


        console.log("wait for SideCam report");
        let sideCam_rep_data = await waitTime(sideCam_repReg,"SideCam report")  ;//WAIT: SideCam report



        console.log("wait for BTM report");


        let btm_check_rep_data = await waitTime(btm_check_rep_promise,"BTM report") as NozzleCheckData;//WAIT: BTM report
        console.log("btm_check_rep_data",btm_check_rep_data,"sideCam_rep_data",sideCam_rep_data);

        let tossReasons:string[]=[];


        let TOP_NG_Location=tossLocation_0;
        let ETC_NG_Location=tossLocation_2;


        if(btm_check_rep_data.status!=1 || sideCam_rep_data.status!=1)//check failed
        {

          await sendTcpMsgPack(cmd.G1({"Z": safe_z}))//GO TO THE SECOND SLOT LOCATION IN ADVANCE TO SPEED UP

          tossReasons.push("BTM or SideCam check failed");
          ETC_NG_Location=tossLocation_0;

        }





        if(sideCam_rep_data.facing!=0)//reverse facing
        {
          angOffset=180;
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "A":inspBasAngle+angOffset })
        }
        angOffset+=btm_check_rep_data.obj_pose.ang;//compensate the angle of the object(from bottom check camera)
        angOffset-=8;

        // await sendTcpMsgPack({ "type": "M", "cmd": "G1",X:slotLocation.X, Y:slotLocation.Y, "Z": safe_z})
        // 


        await sendTcpMsgPack(cmd.G1({"A":inspBasAngle+angOffset}))

        await runinng_checkpoint("[STEP]",i);
        if(tossReasons.length==0){

          let sideCam_rectified_repReg=waitForSideCheckData();

          let cam_pin=1<<IO_Pins.O.CAM_Side;
          let light_pin=1<<IO_Pins.O.CAM_Side_Light0;

          let trigPin=light_pin|cam_pin;
          await sendTcpMsgPack(cmd.M4({ "pin": trigPin, "state":trigPin,reset_ms:5, "motion_progress":1}))

          // await runinng_checkpoint("[TOSS] object angle",{angOffset:angOffset});
          await sendTcpMsgPack(cmd.G1({X:slotLocation.X+slotDist, Y:slotLocation.Y, "Z": safe_z}))//GO TO THE SECOND SLOT LOCATION IN ADVANCE TO SPEED UP

          let sideCam_rectified_repData = await waitTime(sideCam_rectified_repReg,"SideCam rectified report")  ;//WAIT: SideCam rectified report

          console.log("SideCam rectified report",sideCam_rectified_repData);

          if(sideCam_rectified_repData?.measure?.status!=1)
          {
            tossReasons.push("SideCam measure failed"+sideCam_rectified_repData?.measure?.OK_vec);
          }

        }



        // await sendTcpMsgPack({ "type": "M", "cmd": "WAIT_FOR_DIGITAL_INPUT", "group":1,"pin":1<<1,state:1<<1 })
        
        await runinng_checkpoint("[STEP]",i);

        
        // postInspPromise=VP_sendTcpMsgPack("SideCheck");//second trigger
        // sendTcpMsgPack({ "type": "M", "cmd": "M4","group":1, "pin": 3, "state":3,reset_ms:5,"motion_id_offset": 0, "motion_progress":0.9, })

        console.log("slotCheckPromise",slotCheckPromise);
        let slotStatus=await waitTime(slotCheckPromise,"TOP CAM report");//WAIT: TOP CAM report

        {

        }
        // await sendTcpMsgPack({ "type": "M", "cmd": "G1",X: slotLocation.X, Y: slotLocation.Y})



        
        slotCheckPromise_BK=undefined;
        let isSlotOKArr=slotStatus.is_OK.slice(slotStatus.post_check_advCount);  //remove slotStatus.advCount elements from index 0
        let isSlotClearArr=slotStatus.is_clear.slice(slotStatus.post_check_advCount);  //remove slotStatus.advCount elements from index 0



        nxt_adv_count=0;
        let saveImgName:(string|undefined)[]=[undefined,undefined,undefined];
        {
          for(let i=0;i<isSlotOKArr.length;i++){
            if(isSlotOKArr[i]==1&&isSlotClearArr[i]==0){//slot has object and object is right
              saveImgName[nxt_adv_count]="OK_"+Date.now();
              nxt_adv_count++;
            }
            else{
              break;
            }
          }
        }


        let targetPlaceSlotIdx=NaN;
        let targetPickSlotIdx=NaN;

        {
          targetPlaceSlotIdx=isSlotClearArr.findIndex((clear: number) => clear === 1); //first 0 value index
          
          targetPickSlotIdx=isSlotClearArr.findIndex((clear: number, idx: number) => clear === 0 && isSlotOKArr[idx] === 0); //first SlotClear==0 && SlotOK==0 value index
          if(targetPlaceSlotIdx==-1){
            targetPlaceSlotIdx=NaN;
          }
          if(targetPickSlotIdx==-1){
            targetPickSlotIdx=NaN;
          }
        

          console.log("isSlotClearArr",isSlotClearArr);
          console.log("isSlotOKArr",isSlotOKArr);
          console.log("targetPlaceSlotIdx",targetPlaceSlotIdx);
          console.log("targetPickSlotIdx",targetPickSlotIdx);

          if(!Number.isNaN(targetPickSlotIdx))
          {
            TOP_NG_Location=tossLocation_1;
          }
        }

        if(!Number.isNaN(targetPickSlotIdx))
        {
          saveImgName[targetPickSlotIdx]="NG_pick_"+Date.now();
        }

        await VP_sendTcpMsgPack({"type":"TopInsp","cmd_type":"save_target",
          t0:saveImgName[0],
          t1:saveImgName[1],
          t2:saveImgName[2]});

        async function placeObject(location:{X:number|undefined,Y:number|undefined,Z:number,A:number|undefined}){
            

          //if value is undefined, the target value will not be changed
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X":location.X,"Y":location.Y,"Z": safe_z })
          
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X":location.X,"Y":location.Y, "A":location.A,"abort":true})
          
          await sendTcpMsgPack(cmd.G1({ "X":location.X,"Y":location.Y, "Z":  location.Z+5,"A":location.A}))
          
          await runinng_checkpoint("[STEP] place object",i);
          // sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 2 })//DBG
          await sendTcpMsgPack(cmd.G1({ "Z":  location.Z}))
  
  
          // let repReg=VP_sendTcpMsgPack("SideCheck");
          // let ret_str_arr_data = await repReg;
          // console.log(ret_str_arr_data);
  
          await sendTcpMsgPack(cmd.G4(0.01))
          await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state":0 }))//suck off
          await sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_blow, "state": 1<<IO_Pins.O.Nozzle_blow, reset_ms:20 }))//vacuum break
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "A":(location.A??0)+5 })
          await sendTcpMsgPack(cmd.G4(0.02))
          
          await sendTcpMsgPack(cmd.G1({ "Z": safe_z }));
        }



        if(sideCam_rep_data.status!=1)
        {
          tossReasons.push("SideCheck failed");
          ETC_NG_Location=tossLocation_2;// object NG
        }




        let armOffset=BtmCheckObjLoc2ArmOffset(
          btmCheckCalibInfo,{
          X:btm_check_rep_data.obj_pose.x,
          Y:btm_check_rep_data.obj_pose.y},angOffset);
          // armOffset.X=0;
          // armOffset.Y=0;

        console.log("armOffset",armOffset);

        let compensationIsNG=false;

        if(btm_check_rep_data.obj_pose.status !== 1){
          compensationIsNG=true;
          tossReasons.push("btm check failed");
          ETC_NG_Location=tossLocation_2;// object NG
        }

        // if(ifPlaceComplete==true)
        // {
        //   tossReasons.push("place complete");

          
        //   //_this.production_plan=[1,-2,2,-2,1];
        // }

        let armOffsetDistance=Math.hypot(armOffset.X,armOffset.Y);
        if(armOffsetDistance>5){
          console.log("armOffset is too far, skip place");
          console.log("armOffset",armOffset);
          console.log("btm_check_rep_data",btm_check_rep_data);
          tossReasons.push("armOffset is too far");
          ETC_NG_Location=tossLocation_0;//not object NG, drop back to feeder
          compensationIsNG=true;
          
          console.log("armOffset is too far, skip place",armOffset,angOffset);
        }




        let slotHoleOffset={X:NaN,Y:NaN,A:0};
        if(slotStatus.locHole.status==1)
        {
          slotHoleOffset.X=slotStatus.locHole.x*slotStatus.locHole.mmpp;
          slotHoleOffset.Y=-slotStatus.locHole.y*btmCheckCalibInfo.mmpp;

          console.log("slotHoleOffset",slotHoleOffset);
        }
        if(Number.isNaN(slotHoleOffset.X))
        {

          tossReasons.push("slotHoleOffset is NaN");
          ETC_NG_Location=tossLocation_0;//not object NG, drop back to feeder
          compensationIsNG=true;
        }

        let slotHoleOffsetDistance=Math.hypot(slotHoleOffset.X,slotHoleOffset.Y);
        if(slotHoleOffsetDistance>1.5)
        {
          tossReasons.push("slotHoleOffset is too far");
          ETC_NG_Location=tossLocation_0;//not object NG, drop back to feeder
                    
          try{
            await runinng_checkpoint("ERROR",{errorString:"slotHoleOffset is too far",slotHoleOffset:slotHoleOffset,distance:slotHoleOffsetDistance});
          }
          catch(error){
            break;
          }

          compensationIsNG=true;
        }
        console.log("slotHoleOffsetDistance",slotHoleOffsetDistance);



        if(Number.isNaN(targetPlaceSlotIdx))
        {

          tossReasons.push("no slot to place");
          ETC_NG_Location=tossLocation_0;//not object NG, drop back to feeder
          // compensationIsNG=true;
        }

        
        {
          let production_plan=(await runinng_checkpoint("GetProductionPlan",i)).production_plan;

          if(production_plan.length>0 && ((production_plan[0]<=nxt_adv_count)))
          {
            console.log("[DBG]production plan place count hit",production_plan[0]," "+nxt_adv_count.toString());
            tossReasons.push("production plan place count hit"+production_plan[0]+" "+nxt_adv_count.toString());
            ETC_NG_Location=tossLocation_0;//not object NG, drop back to feeder
            if(production_plan[0]>0)
            {
              nxt_adv_count=production_plan[0];
            }
          }

          if(production_plan.length==0)
          {
            console.log("[DBG]production plan is empty");
            tossReasons.push("production plan is empty");
            ETC_NG_Location=tossLocation_0;//not object NG, drop back to feeder

          }
        }


        if(sideCam_rep_data.status==1 && !Number.isNaN(targetPlaceSlotIdx) && compensationIsNG==false && tossReasons.length==0){
          
          
          await runinng_checkpoint("place object",i);

          let x_place_offset=targetPlaceSlotIdx*slotDist;

          await placeObject({X:slotLocation.X+x_place_offset-armOffset.X+slotHoleOffset.X,Y:slotLocation.Y-armOffset.Y+slotHoleOffset.Y,Z:slotLocation.Z,A:inspBasAngle+angOffset});


          // await sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1<<7, "state": 1<<7,reset_ms:50,"motion_id_offset":-1 })//reel adv

          
          //sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1<<6, "state": 1<<6,reset_ms:500,"motion_progress":0.7 })//reel adv

  
        }      
        else
        {//toss
          await runinng_checkpoint("[TOSS] object",{tossReasons:tossReasons});
          
          //console.log("toss object",sideCam_rep_data.status,targetPlaceSlotIdx,compensationIsNG);
          console.log("toss object",tossReasons);

          await sendTcpMsgPack(cmd.G1({ "X":ETC_NG_Location.X,"Y":ETC_NG_Location.Y,"Z": safe_z }))
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": tossLocation.Z })
          if(ETC_NG_Location==tossLocation_0)//drop back to feeder
          {

            await runinng_checkpoint("NG_COUNT",{class:0,count:1});
          }
          else if(ETC_NG_Location==tossLocation_1)//drop back to feeder
          {
            await runinng_checkpoint("NG_COUNT",{class:1,count:1});
          }
          else
          {
            await runinng_checkpoint("NG_COUNT",{class:2,count:1});
          }
  
          // let repReg=VP_sendTcpMsgPack("SideCheck");
          // let ret_str_arr_data = await repReg;
          // console.log(ret_str_arr_data);
  
          sendTcpMsgPack(cmd.G4(0.01))
          sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state":0 }))//suck off
          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
          sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_blow, "state": 1<<IO_Pins.O.Nozzle_blow, reset_ms:5 }))//vacuum break
          sendTcpMsgPack(cmd.G4(0.01))

          // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z })
          isZinSafeZone=true;
          
        }
        
        let ng_x_pick_offset=targetPickSlotIdx*slotDist;
        console.log("ng_x_pick_offset",ng_x_pick_offset,"compensationIsNG",compensationIsNG);

        if(!Number.isNaN(ng_x_pick_offset) && compensationIsNG==false)
        {

          
          await runinng_checkpoint("[NG PICK] object",{ng_x_pick_offset:ng_x_pick_offset,compensationIsNG:compensationIsNG});
          // await runinng_checkpoint("go to NG location and pick",i);
          await sendTcpMsgPack(cmd.G1({ "X":slotLocation.X+ng_x_pick_offset+slotHoleOffset.X,"Y":slotLocation.Y+slotHoleOffset.Y }))//go NG location
        
          await sendTcpMsgPack(cmd.G1({ "Z":  slotLocation.Z-0.5}))
          sendTcpMsgPack(cmd.G4(0.01))
          sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state": 1<<IO_Pins.O.Nozzle_suck }))//pick NG
          sendTcpMsgPack(cmd.G4(0.1))
          await sendTcpMsgPack(cmd.G1({ "Z": safe_z }));

          {//go to toss location
            // await runinng_checkpoint("go to toss location",i);
            await sendTcpMsgPack(cmd.G1({ "X":TOP_NG_Location.X,"Y":TOP_NG_Location.Y,"Z": safe_z }))
            // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": tossLocation.Z })
    
            if(TOP_NG_Location==tossLocation_0)//drop back to feeder
            {
  
              await runinng_checkpoint("NG_COUNT",{class:0,count:1});
            }
            else if(TOP_NG_Location==tossLocation_1)//drop back to feeder
            {
              await runinng_checkpoint("NG_COUNT",{class:1,count:1});
            }
            else
            {
              await runinng_checkpoint("NG_COUNT",{class:2,count:1});
            }
            // let repReg=VP_sendTcpMsgPack("SideCheck");
            // let ret_str_arr_data = await repReg;
            // console.log(ret_str_arr_data);
    
            sendTcpMsgPack(cmd.G4(0.01))
            sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_suck, "state":0 }))//suck off
            // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -18.9 })
            sendTcpMsgPack(cmd.M4({ "pin": 1<<IO_Pins.O.Nozzle_blow, "state": 1<<IO_Pins.O.Nozzle_blow, reset_ms:5 }))//vacuum break
            sendTcpMsgPack(cmd.G4(0.01))
            isZinSafeZone=true;
          }
        }
        isZinSafeZone=true;
        // if(postInspPromise!=null){
        //   let ret_reg_data = await postInspPromise;
        //   console.log("postInspPromise",ret_reg_data);
        // }


      }


      //break;
      
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X":57,"Y":-67})
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -11.9 })
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": -11.9 })
      // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": safe_z })






    }

    await sendTcpMsgPack(cmd.WaitForTriggerMotionProgress({}));
    let end_time=Date.now();
    console.log("time",end_time-start_time,packCounter);
    console.log("time per pack",(end_time-start_time)/packCounter);
    
    await runinng_checkpoint("cycle_end",{time:Date.now()});
    
      
    }
    catch(error){
    }
    //setLatestObjArr(newLatestObjArr);

    _this.isRunning=false;
    // _this.run_cycle_stop=true;
  });


  //index on reel
  //-10 -9 -8 -7 -6 -5 -4 -3 -2 -1 | 0 1 2 3
  const checkInspObject=async(pickObjIndex:number,placeObjIndex:number,speed_alpha:number=0.3)=>{
    //set speed
    let btmCheckCalibInfo=await BtmCheckCalib();

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
    FlexVibCtrl.von(idx);
    await delay(delay_ms);
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

  useHarnessAction('stop_cycle', async () => {
    _this.run_cycle_stop = true;
    _this.stepMode_resolve?.();
    _this.stepMode_resolve = undefined;
    setTimeout(() => { _this.isRunning = false; }, 3000);
    return { stop_requested: true };
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

  function setProductionPlan(plan: number[]) {
    _this.production_plan = [...plan];
    _this.production_plan_original = [...plan];
    _this.production_plan_stageIndex = 0;
    setProductionPlanTick((x) => x + 1);
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


            
            if(_this.run_cycle_stop==true&&
              (checkpoint_name=="cycle_start"||checkpoint_name=="ERROR"||true)
            )
            {
              reject();
              return;
            }


            if(_this.current_error!=undefined){
              console.log("ERROR HOLDING",_this.current_error);
              setRunningState(JSON.stringify(_this.current_error));
              
              _this.stepMode_resolve=resolve;
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
              if(data.type=="empty"){
                plan[0]+=adv_count;
                _resolve();
              }
              else if(data.type=="pack"){
                plan[0]-=adv_count;
                _resolve();
              }
              else{
                reject();
              }
              if(plan.length>0 && plan[0]==0){
                _this.production_plan_stageIndex = (_this.production_plan_stageIndex ?? 0) + 1;
                plan.shift();
              }
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
        _this.run_cycle_stop=true;
        
        _this.stepMode_resolve?.();
        _this.stepMode_resolve=undefined;
        setTimeout(()=>{
          _this.isRunning=false;
        },3000);

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
        // await sendTcpMsgPack({ "type": "AUX", "thread_id": 1, "cmd": "M4","group":0, "pin": 1<<3|1<<(6+8), "state": 1<<3|1<<(6+8),reset_ms:25 });


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





