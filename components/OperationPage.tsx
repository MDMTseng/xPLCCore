import React, { useCallback, useEffect, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { delay } from '../utils/async';
import { t, type UILang } from '../i18n';
import { useHarnessAction } from '../harness/registry';
import { cmd, Event, validateReply, type EventOrdinal } from '../lib/protocol';

import { Divider } from 'antd';

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
  // Per-axis DS402 ErrorID snapshot fetched via GET_MACHINE_STATE when err_src
  // latches. GA_EV replies don't carry axes_err_id, so we do a one-shot pull.
  // Index layout matches PLC's axes_err_mask bits 0..3.
  const [axesErrId, setAxesErrId] = useState<number[] | null>(null);
  const [axesErrMask, setAxesErrMask] = useState<number | null>(null);
  const [axesLabels, setAxesLabels] = useState<string[] | null>(null);
  // Coord-system gate. PLC clears it on UnInited entry (any reset/recovery)
  // and rejects G1 with err='coord_not_configured' until SetCoord0/1 is
  // re-called. Surface it so the operator can spot the gate state at a
  // glance instead of seeing a cryptic NAK on the first move.
  const [coordSet, setCoordSet] = useState<boolean | null>(null);
  // Static fallback used only until the first GET_MACHINE_STATE arrives
  // and gives us the PLC-provided axes_labels. Anything visible to the
  // operator should come from the live array, not this constant.
  const AXIS_LABELS_FALLBACK = ['EAxis0', 'EAxis1', 'EAxis2', 'reel'];

  const refreshMachineState = useCallback(async () => {
    try {
      const reply: any = await COMCtrlObj.sendTcpMsgPack(cmd.GetMachineState());
      validateReply('MachineState', reply);
      if (reply && Array.isArray(reply.axes_err_id)) {
        setAxesErrId(reply.axes_err_id.map((n: any) => Number(n) | 0));
      }
      if (reply && typeof reply.axes_err_mask === 'number') {
        setAxesErrMask(reply.axes_err_mask);
      }
      if (reply && typeof reply.coord_set === 'boolean') {
        setCoordSet(reply.coord_set);
      }
      if (reply && Array.isArray(reply.axes_labels)) {
        setAxesLabels(reply.axes_labels.map((s: any) => String(s)));
      }
    } catch {
      /* swallow — best-effort enrichment */
    }
  }, [COMCtrlObj.sendTcpMsgPack]);
  const fetchAxesErr = refreshMachineState;

  // PLC pushes a COORD_SET event on every edge of GVL.CoordSystemConfigured
  // (UnInited entry clears it; SetCoord0/1 raises it). Listening here keeps
  // the gate-state banner in sync without polling GET_MACHINE_STATE.
  useEffect(() => {
    const handler = (ev: globalThis.Event) => {
      const msg = (ev as CustomEvent).detail;
      if (msg && msg.name === 'COORD_SET' && typeof msg.value === 'boolean') {
        setCoordSet(msg.value);
      }
    };
    window.addEventListener('plc:event', handler as EventListener);
    return () => window.removeEventListener('plc:event', handler as EventListener);
  }, []);

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
      validateReply('GaEvReply', retInfo);
      event = Event.NONE;
      console.log(retInfo)
      let status_str = retInfo['st_str'];
      // err_src/err_id are published by the PLC on every SYS/GA_EV reply.
      // Non-empty src means the supervisor or a state FB latched a cause;
      // UnInited entry clears it, so capture it the scan we see it.
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
        await sendTcpMsgPack(cmd.G1({ A: 0 }))





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

  useHarnessAction('get_motion_status', () => ({
    plcMotionStatus,
    plcLastError,
  }), [plcMotionStatus, plcLastError]);

  useHarnessAction('init_plc_motion', async (payload: any) => {
    const loopCount = Number(payload?.loop_count ?? 20);
    const delayMs = Number(payload?.delay_ms ?? 500);
    setPlcLastError(null);
    setCoordSet(null);
    setAxesErrId(null);
    setAxesErrMask(null);
    const ret = await init_plc_motion(loopCount, delayMs, (status_str: string, err_src: string, err_id: number) => {
      setPlcMotionStatus(status_str);
      if (err_src) {
        setPlcLastError({ src: err_src, id: err_id });
        fetchAxesErr();
      }
    });
    if (ret === true) refreshMachineState();
    return { reached_ready: ret === true };
  }, [init_plc_motion, refreshMachineState]);

  useHarnessAction('enter_error', async () => {
    await sendTcpMsgPack(cmd.GA_EV(Event.ERROR));
    return { sent: true };
  }, [sendTcpMsgPack]);

  useHarnessAction('ga_ev', async (payload: any) => {
    const ev = Number(payload?.ev);
    if (!Number.isFinite(ev)) throw new Error('ga_ev: missing numeric ev');
    const ret = await sendTcpMsgPack(cmd.GA_EV(ev));
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

        {plcMotionStatus === 'Ready' && coordSet === false && (
          <div
            style={{
              marginTop: 10,
              border: '1px solid #fcd34d',
              borderRadius: 10,
              padding: '8px 10px',
              background: '#fffbeb',
              color: '#92400e',
              fontSize: 12,
              fontWeight: 600,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span>
              Coord system not configured — first G1 will NAK as
              {' '}<code>coord_not_configured</code>. Reset/Error clears the gate.
            </span>
            <button
              type="button"
              onClick={async () => {
                await sendTcpMsgPack(cmd.SetCoord1());
                refreshMachineState();
              }}
              style={{
                background: '#f59e0b',
                color: '#fff',
                border: 'none',
                borderRadius: 6,
                padding: '4px 10px',
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              Set Coord1
            </button>
          </div>
        )}

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
            {(axesErrId || axesErrMask !== null) && (
              <div style={{ marginTop: 6, fontWeight: 500, fontSize: 11 }}>
                {(axesLabels ?? AXIS_LABELS_FALLBACK).map((label: string, i: number) => {
                  const eid = axesErrId?.[i] ?? 0;
                  const faulted = axesErrMask !== null
                    ? ((axesErrMask >> i) & 1) === 1
                    : eid !== 0;
                  return (
                    <div
                      key={i}
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        fontFamily: 'monospace',
                        color: faulted ? '#991b1b' : '#6b7280',
                        fontWeight: faulted ? 700 : 400,
                      }}
                    >
                      <span>{label}</span>
                      <span>
                        {faulted ? 'FAULT' : 'ok'}
                        {' · ErrID 0x'}
                        {(eid >>> 0).toString(16).padStart(4, '0').toUpperCase()}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
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
              setAxesErrId(null);
              setAxesErrMask(null);
              let ret = await init_plc_motion(20, 500, (status_str: string, err_src: string, err_id: number) => {
                console.log(status_str, err_src, err_id);
                setPlcMotionStatus(status_str);
                // Latch the last non-empty error so it stays visible after the
                // auto EV_RESET clears GVL.LastErrorSource on UnInited entry.
                if (err_src) {
        setPlcLastError({ src: err_src, id: err_id });
        fetchAxesErr();
      }
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
              await sendTcpMsgPack(cmd.GA_EV(Event.ERROR))
            }}
          >
            {t(uiLang, 'enterError')}
          </button>
        </div>
      </div>
    </div>
  )
}


