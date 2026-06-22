import React, { useEffect, useState } from 'react';
import type { COMCtrlObj } from '../types';
import { CalibPage } from './CalibPage';
import { MiscControlsPage } from './MiscControlsPage';
import { OperationPage } from './OperationPage';
import { RecoveryDemoPage } from './RecoveryDemoPage';
import { t, type UILang } from '../i18n';
import { useHarnessAction } from '../harness/registry';

export const ControlPage: React.FC<{
  env_path: string,
  lib_path: string,
  UI_path: string,
  COMCtrlObj:COMCtrlObj,
  uiLang: UILang,
}> = ({
  env_path,
  lib_path,
  UI_path,
  COMCtrlObj,
  uiLang,
}) => {

  const [tab, setTab] = useState<"Welcome" | "Calib" | "Operation" | "Recovery">("Welcome");
  const [plcReady, setPlcReady] = useState(false);
  const [lastReconcile, setLastReconcile] = useState<{reason: string; at: number; snapshot: any} | null>(null);

  // A4 reconciliation: two event sources feed the same reconcile function.
  //   `plc:machine-state` - full snapshot on reconnect (includes coord_set).
  //   `plc:event`          - PLC push; ST_CHG events arrive mid-session, but
  //                          ST_CHG payloads don't carry coord_set, so we
  //                          treat a non-Ready transition as "move to Welcome"
  //                          regardless of coord. Ready transitions we leave
  //                          alone -- a later reconnect / explicit snapshot
  //                          handles the coord-set gate.
  useEffect(() => {
    const reconcile = (source: string, payload: any, hasCoord: boolean) => {
      if (!payload) return;
      const st = String(payload.st_str ?? '');
      let reason: string | null = null;
      if (st === 'Error') reason = 'plc_error';
      else if (st !== 'Ready') reason = 'plc_not_ready';
      else if (hasCoord && payload.coord_set !== true) reason = 'coord_not_configured';
      if (reason) {
        setTab('Welcome');
        setLastReconcile({ reason, at: Date.now(), snapshot: payload });
        console.log(`[A4/${source}] ControlPage forced to Welcome:`, reason, payload);
      } else {
        setLastReconcile({ reason: 'ok', at: Date.now(), snapshot: payload });
      }
    };

    const snapHandler = (ev: Event) => reconcile('snap', (ev as CustomEvent).detail, true);
    const evtHandler = (ev: Event) => {
      const msg = (ev as CustomEvent).detail;
      if (msg && msg.name === 'ST_CHG') reconcile('stchg', msg, false);
    };
    window.addEventListener('plc:machine-state', snapHandler as EventListener);
    window.addEventListener('plc:event', evtHandler as EventListener);
    return () => {
      window.removeEventListener('plc:machine-state', snapHandler as EventListener);
      window.removeEventListener('plc:event', evtHandler as EventListener);
    };
  }, []);

  useHarnessAction('get_tab', () => ({ tab, plcReady, allCoreLinksConnected: true, lastReconcile }), [tab, plcReady, lastReconcile]);
  useHarnessAction('set_tab', (payload: any) => {
    const target = String(payload?.tab ?? '');
    if (target !== 'Welcome' && target !== 'Calib' && target !== 'Operation' && target !== 'Recovery') {
      throw new Error(`set_tab: unknown tab '${target}'`);
    }
    setTab(target);
    return { tab: target };
  }, []);
  const tabs: Array<{ id: "Welcome" | "Calib" | "Operation" | "Recovery"; label: string; subtitle: string; requiresReady: boolean }> = [
    {
      id: 'Welcome',
      label: t(uiLang, 'tabWelcome'),
      subtitle: t(uiLang, 'tabWelcomeSub'),
      requiresReady: false,
    },
    {
      id: 'Calib',
      label: t(uiLang, 'tabCalib'),
      subtitle: t(uiLang, 'tabCalibSub'),
      requiresReady: true,
    },
    {
      id: 'Operation',
      label: t(uiLang, 'tabOperation'),
      subtitle: t(uiLang, 'tabOperationSub'),
      requiresReady: true,
    },
    {
      // Recovery demo (decisions_2026-06-22.md §4 (2)). Doesn't require
      // Ready because the page itself drives FSM up via its own buttons
      // and the resume reconcile is observable in any state.
      id: 'Recovery',
      label: 'Recovery demo',
      subtitle: 'scratchpad / write-ahead / resume',
      requiresReady: false,
    },
  ] as const;

  return (
    <>
      {!plcReady && (
        <div
          style={{
            margin: '8px 0 14px',
            padding: '12px 14px',
            borderRadius: 12,
            border: '1px solid #f59e0b',
            background: '#fff7ed',
            color: '#92400e',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            fontWeight: 700,
          }}
        >
          <span>{t(uiLang, 'workflowLocked')}</span>
          <span style={{ fontSize: 12 }}>{t(uiLang, 'plcNotReady')}</span>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '240px minmax(0,1fr)', gap: 12 }}>
        <aside
          style={{
            border: '1px solid #e5e7eb',
            borderRadius: 12,
            background: '#ffffff',
            padding: 8,
            height: 'fit-content',
            position: 'sticky',
            top: 8,
          }}
        >
          {tabs.map((tabItem) => {
            const isDisabled = tabItem.requiresReady && !plcReady;
            const isActive = tab === tabItem.id;
            return (
              <button
                key={tabItem.id}
                type="button"
                style={{
                  width: '100%',
                  textAlign: 'left',
                  border: isActive ? '1px solid #3b82f6' : '1px solid #e5e7eb',
                  borderRadius: 10,
                  background: isActive ? '#eff6ff' : '#ffffff',
                  padding: '10px 10px',
                  marginBottom: 8,
                  color: isDisabled ? '#9ca3af' : '#111827',
                  cursor: isDisabled ? 'not-allowed' : 'pointer',
                }}
                onClick={() => !isDisabled && setTab(tabItem.id)}
                disabled={isDisabled}
              >
                <div style={{ fontWeight: 700, fontSize: 13 }}>{tabItem.label}</div>
                <div style={{ marginTop: 3, fontSize: 11, color: '#6b7280' }}>{tabItem.subtitle}</div>
              </button>
            );
          })}
        </aside>

        <section
          style={{
            border: '1px solid #e5e7eb',
            borderRadius: 12,
            background: '#ffffff',
            padding: 12,
          }}
        >
          <div style={{ display: tab === "Welcome" ? "block" : "none" }}>
            <MiscControlsPage
              COMCtrlObj={COMCtrlObj}
              env_path={env_path}
              lib_path={lib_path}
              UI_path={UI_path}
              onPlcReadyChange={setPlcReady}
              uiLang={uiLang}
            />
          </div>
          <div style={{ display: tab === "Calib" ? "block" : "none" }}>
            <CalibPage COMCtrlObj={COMCtrlObj} env_path={env_path} lib_path={lib_path} UI_path={UI_path} uiLang={uiLang} />
          </div>
          <div style={{ display: tab === "Operation" ? "block" : "none" }}>
            <OperationPage COMCtrlObj={COMCtrlObj} env_path={env_path} lib_path={lib_path} UI_path={UI_path} uiLang={uiLang} />
          </div>
          <div style={{ display: tab === "Recovery" ? "block" : "none" }}>
            <RecoveryDemoPage COMCtrlObj={COMCtrlObj} />
          </div>
        </section>
      </div>
    </>
  )
}

