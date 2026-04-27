import React, { useCallback, useState } from 'react';
import { JoggingPad } from '../JoggingPad';
import { Modal } from '../Modal';
import { DiagPanel } from './DiagPanel';
import { PlcEventLog } from './PlcEventLog';
import type { COMCtrlObj } from '../types';
import { delay } from '../utils/async';
import { t, type UILang } from '../i18n';
import { cmd, Event, type EventOrdinal } from '../lib/protocol';

export const MiscControlsPage: React.FC<{
  COMCtrlObj:COMCtrlObj,
  env_path: string,
  lib_path: string,
  UI_path: string,
  onPlcReadyChange?: (ready: boolean) => void,
  uiLang: UILang,
}> = ({

  COMCtrlObj,
  env_path,
  lib_path,
  UI_path,
  onPlcReadyChange,
  uiLang,

}) => {
  const [plcMotionStatus, setPlcMotionStatus] = useState("None");
  const [plcLastError, setPlcLastError] = useState<{src: string, id: number} | null>(null);
  const [isJoggingModalOpen, setIsJoggingModalOpen] = useState(false);
  const init_plc_motion = useCallback(async (loop_count: number = 20, delay_ms: number = 500,latest_state_cb:(status_str:string, err_src:string, err_id:number)=>void) => {
    let event: EventOrdinal = Event.NONE;
    let Counter = 0;
    while (true) {//feed event to enter ready state
      Counter += 1;
      if (Counter > loop_count) {
        console.log("SMach tried >" + loop_count + " times")
        return;
      }

      let retInfo = await COMCtrlObj.sendTcpMsgPack(cmd.GA_EV(event)) as any;
      event = Event.NONE;
      console.log(retInfo)
      let status_str = retInfo['st_str'];
      // err_src/err_id: published by the PLC on every SYS/GA_EV reply; cleared
      // on UnInited entry, so capture it the scan we see it.
      const err_src: string = retInfo['err_src'] ?? '';
      const err_id: number = retInfo['err_id'] ?? 0;
      latest_state_cb(status_str, err_src, err_id);
      if (status_str == "Powered") {
        event = Event.GROUP_ENABLE
      }

      if (status_str == "GroupEnabled") {
        event = Event.HOME_GO
      }

      if (status_str == "Ready") {
        // SetCoord1 must fire before any G1: PLC's CoordSystemConfigured gate
        // (cleared on UnInited entry) NAKs G1 with err='coord_not_configured'.
        await sendTcpMsgPack(cmd.SetCoord1())
        await delay(100);
        await sendTcpMsgPack(cmd.G1({ X: 0, Y: 0, Z: 10, A: 90 }))
        await sendTcpMsgPack(cmd.G1({ Z: 11, A: 0 }))



        return true;
        break;
      }



      if (status_str == "Powering") {
      }
      // event=Event.ERROR

      if (status_str == "Error") {
        event = Event.RESET
      }


      if (status_str == "UnInited") {
        event = Event.POWER_ON
      }

      await delay(delay_ms);
    }
    return false;

  }, [COMCtrlObj.sendTcpMsgPack]);


  const sendTcpMsgPack = COMCtrlObj.sendTcpMsgPack;
  return (
    <div>
      <div
        style={{
          border: '1px solid #cbd5e1',
          borderRadius: 14,
          background: '#ffffff',
          padding: 14,
          marginBottom: 10,
        }}
      >
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 12, alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 800, color: '#111827' }}>{t(uiLang, 'welcomeTitle')}</div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>
              {t(uiLang, 'welcomeDesc')}
            </div>
          </div>
          <span
            style={{
              fontSize: 12,
              fontWeight: 700,
              borderRadius: 999,
              padding: '4px 10px',
              background: plcMotionStatus === 'Ready' ? '#dcfce7' : '#f3f4f6',
              color: plcMotionStatus === 'Ready' ? '#166534' : '#374151',
            }}
          >
            PLC: {plcMotionStatus}
          </span>
        </div>

        {plcLastError && (
          <div
            style={{
              marginTop: 10,
              border: '1px solid #fca5a5',
              borderRadius: 10,
              padding: '8px 10px',
              background: '#fef2f2',
              color: '#991b1b',
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            Last error: {plcLastError.src} (id={plcLastError.id})
          </div>
        )}

        <div
          style={{
            marginTop: 12,
            border: '1px solid #e5e7eb',
            borderRadius: 10,
            background: '#f8fafc',
            padding: '9px 10px',
            fontSize: 12,
            color: '#334155',
          }}
        >
          {t(uiLang, 'checklist')}
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
          <button
            type="button"
            style={{
              background: '#16a34a',
              color: '#fff',
              border: 'none',
              borderRadius: 8,
              padding: '8px 12px',
              fontWeight: 700,
              cursor: 'pointer',
            }}
            onClick={async () => {
              setPlcLastError(null);
              let ret = await init_plc_motion(30, 500, (status_str: string, err_src: string, err_id: number) => {
                console.log(status_str, err_src, err_id);
                setPlcMotionStatus(status_str);
                // Latch the last non-empty error so it stays visible after the
                // auto EV_RESET clears GVL.LastErrorSource on UnInited entry.
                if (err_src) setPlcLastError({ src: err_src, id: err_id });
              });
              if (ret === true) {
                onPlcReadyChange?.(true);
              }
              console.log(ret);
            }}
          >
            {t(uiLang, 'initPlcMotion')}
          </button>

          <button
            type="button"
            style={{
              background: '#b91c1c',
              color: '#fff',
              border: 'none',
              borderRadius: 8,
              padding: '8px 12px',
              fontWeight: 700,
              cursor: 'pointer',
            }}
            onClick={async () => {
              onPlcReadyChange?.(false);
              await sendTcpMsgPack(cmd.GA_EV(Event.ERROR))
            }}
          >
            {t(uiLang, 'enterError')}
          </button>
        </div>

        <details style={{ marginTop: 12 }}>
          <summary style={{ cursor: 'pointer', fontWeight: 700, color: '#374151' }}>{t(uiLang, 'advancedTestControls')}</summary>
          <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={async () => {
              let event_id = Math.floor(Math.random() * 1000000);
              COMCtrlObj.sendTcpMsgPack(cmd.G4(0.2), false);
              for (let i = 0; i < 60; i++) {
                COMCtrlObj.sendTcpMsgPack(cmd.G4(0.02), false);
                let wait_for_tracking = i % 10 == 0;
                let retInfoPromise = sendTcpMsgPack(cmd.M4({ pin: 1, state: 1, event_id: event_id + 1, reset_ms: 1 }), wait_for_tracking)
                if (retInfoPromise instanceof Promise) {
                  console.log(await retInfoPromise);
                }
              }
            }}>PinToggle</button>

            <button onClick={() => setIsJoggingModalOpen(true)}>Jogging</button>
          </div>
        </details>

                  {/* <button onClick={async () => {
                    let distance =20;
                    let Z_safe = 10;


                    let cor = 30

                    function getFeedSpeedConfig(speed:number=25){


                      let jerk = speed * 2000
                  
                      let acc = speed * 50
                      let dea = acc
                      return {
                        F:speed,
                        JERK:jerk,
                        ACC:acc,
                        DEA:dea,
                      }
                    }
                  
                    await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe})
                    await sendTcpMsgPack({ "type": "M", "cmd": "G1", X:-5})
                    

                    let zDistance = -15;

                    for (let i = 0; i < 10; i++) {
                      await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Y": -distance,A:180,...getFeedSpeedConfig(2000),Cor:cor })
                      await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe + zDistance })
                      await sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.005 })
                      await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe+5})
  
  
                      await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Y": distance, A:0,})
                      await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe + zDistance })
                      await sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.005 })
                      await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe+5})
                    }



                    // for (let i = 0; i < 3; i++) {

                    //   let retInfo = await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": 0, "Y": -distance })

                    //   sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe + Z_move })
                    //   sendTcpMsgPack({ "type": "M", "cmd": "M4", "pin": 1, "state": 1, "motion_id_offset": 0, "motion_progress": 1 })
                    //   sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.001 });
                    //   sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe })

                    //   retInfo = await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": 0, "Y": distance + 90 })

                    //   sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe + Z_move })
                    //   sendTcpMsgPack({ "type": "M", "cmd": "G4", "P": 0.001 });
                    //   sendTcpMsgPack({ "type": "M", "cmd": "M4", "pin": 1, "state": 0, "motion_id_offset": 0, "motion_progress": 1 })
                    //   sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe })



                    // }

                    // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": Z_safe, "Cor": 0 })
                    // await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": 0, "Y": 0 })





                    // console.log(retInfo);
                  }}>Motion a bit</button> */}

                  <br/>
                  {/* <div>
                
                  <button onClick={async () => {
                    FlexVibCtrl.top_light_on();
                     // sendTcpMsgPack({ "type": "M", "cmd": "M4", "pin": 1, "state": 1, "motion_id_offset": 0, "motion_progress": 1 })
                  }}>Top light ON</button>
                  <button onClick={async () => {
                    FlexVibCtrl.top_light_off();
                      //sendTcpMsgPack({ "type": "M", "cmd": "M4", "pin": 1, "state": 0, "motion_id_offset": 0, "motion_progress": 1 })
                  }}>Off</button>
                  

                  <button onClick={async () => {
                      sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 1, "state": 1, "motion_id_offset": 0, "motion_progress": 1 })
                  }}>suck1</button>
                  <button onClick={async () => {
                      sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 2, "state": 2, "motion_id_offset": 0, "motion_progress": 1 })
                  }}>suck2</button>
                  <button onClick={async () => {
                      sendTcpMsgPack({ "type": "M", "cmd": "M4","group":0, "pin": 3, "state": 0, "motion_id_offset": 0, "motion_progress": 1 })
                  }}>off</button>
                  <br />
                  <button onClick={() => 
                    setIsJoggingModalOpen(true)
                    
                    }>Jogging</button>
                  </div>

                  <button onClick={async () => {
                    
                    await sendTcpMsgPack({ "type": "M", "cmd": "WAIT_FOR_MOTION_STOP" })
                    _this.jog_base_location = await sendTcpMsgPack({ "type": "M", "cmd": "READ_LATEST_CMD_LOCATION" })
                    console.log("press down",_this.jog_base_location);

                    setLocPathList([...locPathList, _this.jog_base_location]);
                  }}>Push in loc</button>


                  <button onClick={async () => {
                    for(let i=0;i<10;i++){
                      for(let i=0;i<locPathList.length;i++){
                        await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": locPathList[i].X, "Y": locPathList[i].Y, "Z": locPathList[i].Z,"Cor": 15 })
                      }
                    }
                  }}>Go path</button>


                  <button onClick={async () => {
                    
                    setLocPathList([]);
                  }}>clear path</button> */}



                  <br />
{/* 
                  <button onClick={async () => {
                    await sendTcpMsgPack({ "type": "M", "cmd": "SetCoord0" })
                  }}>SetCoord0</button> */}

                  {/* <button onClick={async () => {
                    await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": 11 , "F":1000,ACC:1000,DEA:1000,JERK:10000 })
                    await sendTcpMsgPack({ "type": "M", "cmd": "WAIT_FOR_MOTION_STOP" })
                    await sendTcpMsgPack({ "type": "M", "cmd": "SetCoord1" })

                    await delay(100);
                    await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": 10, "A": 90})
                    await sendTcpMsgPack({ "type": "M", "cmd": "G1",  "Z": 11,"A": 0})




                  }}>SetCoord1</button> */}
                  {/* <input
                      type="text"
                      placeholder="JSON to MsgPack and send..."
                      value={tcpJsonMessage}
                      onChange={(e) => setTcpJsonMessage(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && sendTcpMsgPackInput()}
                      style={{ marginRight: '5px', padding: '4px', width: '200px' }}
                    />
                    <button onClick={sendTcpMsgPackInput}>Send MsgPack</button> */}
                  {/* <label style={{ marginLeft: '8px', fontSize: '12px', color: '#555' }}>
                      <input
                        type="checkbox"
                        checked={tcpClearAfterSend}
                        onChange={(e) => setTcpClearAfterSend(e.target.checked)}
                        style={{ marginRight: '4px' }}
                      />
                      Clear after send
                    </label> */}
        <Modal isOpen={isJoggingModalOpen} onClose={() => setIsJoggingModalOpen(false)} closeClickCount={2} closeClickTimeout={400}
          style={{ width: "40%", height: "40%" }}
        >
          <JoggingPad speedFactor_XY={0.2} speedFactor_Z={0.1} sendTcpMsgPack={sendTcpMsgPack} />
        </Modal>
      </div>

      <DiagPanel sendTcpMsgPack={sendTcpMsgPack} />
      <PlcEventLog />
    </div>
  )
}


