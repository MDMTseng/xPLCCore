import React, { useCallback, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { delay } from '../utils/async';
import { t, type UILang } from '../i18n';

import { Divider } from 'antd';
enum PlcMotionEvent {
  EV_NONE = 0,
  EV_BOOT = 1,
  EV_POWER_ON = 2,
  EV_POWER_OFF = 3,
  EV_GROUP_ENABLE = 4,
  EV_GROUP_DISABLE = 5,
  EV_HOME_GO = 6,
  EV_HOME_GO_FORCE_SKIP = 7,
  EV_RESET = 8,
  EV_ERROR = 9,
  EV_OK = 10,
  EV_ER = 11,
}

export const OperationPage: React.FC<{
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
  const [plcMotionStatus, setPlcMotionStatus] = useState("None");
  const init_plc_motion = useCallback(async (loop_count: number = 20, delay_ms: number = 500,latest_state_cb:(status_str:string)=>void) => {
    let event = PlcMotionEvent.EV_NONE;
    let Counter = 0;
    while (true) {//feed event to enter ready state
      Counter += 1;
      if (Counter > loop_count) {
        console.log("SMach tried >" + loop_count + " times")
        return;
      }

      let retInfo = await COMCtrlObj.sendTcpMsgPack({ "type": "SYS", "cmd": "GA_EV", "ev": event }) as any;
      event = PlcMotionEvent.EV_NONE;
      console.log(retInfo)
      let status_str = retInfo['st_str'];
      latest_state_cb(status_str);
      if (status_str == "Powered") {
        event = PlcMotionEvent.EV_GROUP_ENABLE
      }

      if (status_str == "GroupEnabled") {
        event = PlcMotionEvent.EV_HOME_GO
      }

      if (status_str == "Ready") {//set



        {
            await sendTcpMsgPack({ "type": "M", "cmd": "G1", "Z": 11 })
            await sendTcpMsgPack({ "type": "M", "cmd": "WAIT_FOR_MOTION_STOP" })
            await sendTcpMsgPack({ "type": "M", "cmd": "SetCoord1" })//set coord1 (realworld coordinate instead of machine coordinate)
    
            await delay(100);
            await sendTcpMsgPack({ "type": "M", "cmd": "G1", "X": 0, "Y": 0, "Z": 10,"A": 90 })
            await sendTcpMsgPack({ "type": "M", "cmd": "G1", "A": 0})
        }





        return true;
        break;
      }



      if (status_str == "Powering") {
      }
      // event=PlcMotionEvent.EV_ERROR

      if (status_str == "Error") {
        event = PlcMotionEvent.EV_RESET
      }


      if (status_str == "UnInited") {
        event = PlcMotionEvent.EV_POWER_ON
      }

      await delay(delay_ms);
    }
    return false;

  }, [COMCtrlObj.sendTcpMsgPack]);


  const sendTcpMsgPack = COMCtrlObj.sendTcpMsgPack;
  return (
    <div>
      <Divider />
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
            <div style={{ fontWeight: 800, color: '#111827' }}>{t(uiLang, 'operationTitle')}</div>
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 4 }}>
              {t(uiLang, 'operationDesc')}
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

        <div
          style={{
            marginTop: 12,
            border: '1px solid #e5e7eb',
            borderRadius: 10,
            padding: '9px 10px',
            background: '#f8fafc',
            color: '#334155',
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          {t(uiLang, 'operationHint')}
        </div>

        <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
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
              let ret = await init_plc_motion(20, 500, (status_str: string) => {
                console.log(status_str);
                setPlcMotionStatus(status_str);
              });
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
              await sendTcpMsgPack({ "type": "SYS", "cmd": "GA_EV", "ev": PlcMotionEvent.EV_ERROR })
            }}
          >
            {t(uiLang, 'enterError')}
          </button>
        </div>
      </div>
    </div>
  )
}


