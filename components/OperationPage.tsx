import React, { useCallback, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { delay } from '../utils/async';
import { t, type UILang } from '../i18n';
import { useHarnessAction } from '../harness/registry';

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
  const [plcLastError, setPlcLastError] = useState<{src: string, id: number} | null>(null);
  const init_plc_motion = useCallback(async (loop_count: number = 20, delay_ms: number = 500,latest_state_cb:(status_str:string, err_src:string, err_id:number)=>void) => {
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
      // err_src/err_id are published by the PLC on every SYS/GA_EV reply.
      // Non-empty src means the supervisor or a state FB latched a cause;
      // UnInited entry clears it, so capture it the scan we see it.
      const err_src: string = retInfo['err_src'] ?? '';
      const err_id: number = retInfo['err_id'] ?? 0;
      latest_state_cb(status_str, err_src, err_id);
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

  useHarnessAction('get_motion_status', () => ({
    plcMotionStatus,
    plcLastError,
  }), [plcMotionStatus, plcLastError]);

  useHarnessAction('init_plc_motion', async (payload: any) => {
    const loopCount = Number(payload?.loop_count ?? 20);
    const delayMs = Number(payload?.delay_ms ?? 500);
    setPlcLastError(null);
    const ret = await init_plc_motion(loopCount, delayMs, (status_str: string, err_src: string, err_id: number) => {
      setPlcMotionStatus(status_str);
      if (err_src) setPlcLastError({ src: err_src, id: err_id });
    });
    return { reached_ready: ret === true };
  }, [init_plc_motion]);

  useHarnessAction('enter_error', async () => {
    await sendTcpMsgPack({ type: 'SYS', cmd: 'GA_EV', ev: PlcMotionEvent.EV_ERROR });
    return { sent: true };
  }, [sendTcpMsgPack]);

  useHarnessAction('ga_ev', async (payload: any) => {
    const ev = Number(payload?.ev);
    if (!Number.isFinite(ev)) throw new Error('ga_ev: missing numeric ev');
    const ret = await sendTcpMsgPack({ type: 'SYS', cmd: 'GA_EV', ev });
    return { reply: ret };
  }, [sendTcpMsgPack]);
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
              setPlcLastError(null);
              let ret = await init_plc_motion(20, 500, (status_str: string, err_src: string, err_id: number) => {
                console.log(status_str, err_src, err_id);
                setPlcMotionStatus(status_str);
                // Latch the last non-empty error so it stays visible after the
                // auto EV_RESET clears GVL.LastErrorSource on UnInited entry.
                if (err_src) setPlcLastError({ src: err_src, id: err_id });
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


