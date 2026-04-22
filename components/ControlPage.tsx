import React, { useState } from 'react';
import type { COMCtrlObj } from '../types';
import { CalibPage } from './CalibPage';
import { MiscControlsPage } from './MiscControlsPage';
import { OperationPage } from './OperationPage';
import { t, type UILang } from '../i18n';

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

  const [tab, setTab] = useState("Welcome");
  const [plcReady, setPlcReady] = useState(false);
  const tabs: Array<{ id: "Welcome" | "Calib" | "Operation"; label: string; subtitle: string; requiresReady: boolean }> = [
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
        </section>
      </div>
    </>
  )
}

