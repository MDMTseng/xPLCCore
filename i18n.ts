export type UILang = 'en' | 'zh';

type Dict = Record<string, { en: string; zh: string }>;

const dict: Dict = {
  appTitle: { en: 'Packaging Machine Console', zh: '包裝機控制台' },
  instanceProject: { en: 'Instance: {instance} · Project: {project}', zh: '實例: {instance} · 專案: {project}' },
  systemReady: { en: 'System Ready', zh: '系統就緒' },
  waitingLinks: { en: 'Waiting Links', zh: '連線待完成' },
  hide: { en: 'Hide', zh: '收合' },
  setup: { en: 'Setup', zh: '設定' },
  tcpUnavailable: { en: 'TCP unavailable. Run in Electron with Node integration.', zh: 'TCP 不可用，請於啟用 Node 整合的 Electron 中執行。' },
  linkHelp: { en: 'Click connect/disconnect per link.', zh: '可在此連線/斷線各通訊鏈路。' },
  tcpPlc: { en: 'TCP (PLC)', zh: 'TCP (PLC 控制器)' },
  tcp2Vision: { en: 'TCP2 (Vision)', zh: 'TCP2 (視覺系統)' },
  modbusFeeder: { en: 'Modbus (Feeder)', zh: 'Modbus (送料器)' },
  host: { en: 'Host', zh: '主機' },
  port: { en: 'Port', zh: '連接埠' },
  comPort: { en: 'COM Port', zh: 'COM 埠' },
  baudRate: { en: 'Baud Rate', zh: '鮑率' },
  connect: { en: 'Connect', zh: '連線' },
  disconnect: { en: 'Disconnect', zh: '斷線' },
  controlsLocked: { en: 'Machine controls remain locked until TCP2 and Modbus are connected.', zh: '機台控制仍鎖定，請先連上 TCP2 與 Modbus。' },

  tabWelcome: { en: 'Step 2 · Welcome', zh: '步驟 2 · 歡迎' },
  tabWelcomeSub: { en: 'Initialize PLC motion', zh: '初始化 PLC 運動' },
  tabCalib: { en: 'Step 3 · Calib', zh: '步驟 3 · 生產' },
  tabCalibSub: { en: 'Run production cycle', zh: '執行生產循環' },
  tabOperation: { en: 'Operation', zh: '操作' },
  tabOperationSub: { en: 'Manual operation utilities', zh: '手動操作工具' },
  workflowLocked: { en: 'Workflow locked: complete Welcome initialization first.', zh: '流程已鎖定：請先在 Welcome 完成初始化。' },
  plcNotReady: { en: 'PLC NOT READY', zh: 'PLC 未就緒' },

  welcomeTitle: { en: 'Welcome - PLC Initialization', zh: '歡迎頁 - PLC 初始化' },
  welcomeDesc: { en: 'Initialize motion here before entering Calib/Operation.', zh: '進入生產/操作前，請先在此完成運動初始化。' },
  checklist: { en: 'Checklist: power on -> group enable -> home -> ready posture -> coordinate set.', zh: '檢查流程：上電 -> 群組使能 -> 回原點 -> 就緒姿態 -> 座標設定。' },
  initPlcMotion: { en: 'Init PLC Motion', zh: '初始化 PLC 運動' },
  enterError: { en: 'Enter Error', zh: '進入錯誤狀態' },
  advancedTestControls: { en: 'Advanced / Test Controls', zh: '進階 / 測試控制' },

  operationTitle: { en: 'Operation Workspace', zh: '操作工作區' },
  operationDesc: { en: 'Use this page for manual PLC state actions. Production remains in Calib.', zh: '此頁用於手動 PLC 狀態操作；生產流程請在 Calib。' },
  operationHint: { en: 'Recommended flow: run `Init PLC Motion` to get state to Ready, then switch to Calib for cycle operation.', zh: '建議流程：先執行「初始化 PLC 運動」至 Ready，再切換到 Calib 執行生產循環。' },

  productionConsole: { en: 'Step 3 - Production Console', zh: '步驟 3 - 生產控制台' },
  productionDesc: { en: 'Operator mode shows only core cycle controls. Engineering tools are below in collapsed panels.', zh: '操作模式僅顯示核心循環控制；工程工具收納於下方可折疊區塊。' },
  stepMode: { en: 'STEP MODE', zh: '步進模式' },
  autoMode: { en: 'AUTO MODE', zh: '自動模式' },
  tossPause: { en: 'Toss Pause', zh: '丟棄暫停' },
  quickCheckPlate: { en: 'Quick Check Plate', zh: '快速檢查盤位' },
  waitingCycleData: { en: 'Waiting for cycle data', zh: '等待循環資料' },
  status: { en: 'Status', zh: '狀態' },
  toss: { en: 'Toss', zh: '丟棄' },
  engineeringConsole: { en: 'Engineering Console · Advanced setup and test tools', zh: '工程控制台 · 進階設定與測試工具' },
  calibRecords: { en: 'Calibration records and object debug data', zh: '校正紀錄與物件除錯資料' },

  planProgress: { en: 'progress', zh: '進度' },
  planFinished: { en: 'Production finished', zh: '生產結束' },
  planSegmentOf: { en: 'segment {current} of {total}', zh: '段落 {current} / {total}' },
  planTypePack: { en: 'PACK', zh: '裝入' },
  planTypeEmpty: { en: 'EMPTY', zh: '空位' },
  planDeleteSegment: { en: 'delete segment', zh: '刪除段落' },
  planApplySetup: { en: 'apply setup', zh: '套用設定' },
  planRecentSetup: { en: 'recent setup', zh: '最近設定' },
  planRecentSetupsTitle: { en: 'Recent setups ({count})', zh: '最近設定 ({count})' },
  planNoHistory: { en: 'No history yet', zh: '尚無歷史紀錄' },
  planDeleteConfirm: { en: 'Delete this segment?', zh: '確認刪除此段落？' },
  cancel: { en: 'Cancel', zh: '取消' },
  planConfirmDelete: { en: 'Confirm delete', zh: '確認刪除' },
  planEditWhileRunning: { en: 'Cannot edit plan while production is running', zh: '生產中無法編輯計劃' },
  speedOverall: { en: 'Overall', zh: '整體' },
  speedRecent: { en: 'Recent', zh: '近期' },
  speedUnit: { en: 'pcs/hr', zh: 'pcs/hr' },
  speedCount: { en: 'Count', zh: '數量' },
  ngLabel: { en: 'NG', zh: 'NG' },
  reset: { en: 'Reset', zh: '重置' },
  resetConfirmTitle: { en: 'Reset', zh: '重置' },
  resetConfirmDesc: { en: 'Reset speed info and NG counts?', zh: '重置速度資訊和NG計數？' },
  yes: { en: 'Yes', zh: '是' },
  no: { en: 'No', zh: '否' },
  resetOverallTitle: { en: 'Reset Overall Speed', zh: '重置整體速度' },
  resetOverallDesc: { en: 'Reset overall speed statistics?', zh: '重置整體速度統計？' },
  resetRecentTitle: { en: 'Reset Recent Speed', zh: '重置近期速度' },
  resetRecentDesc: { en: 'Reset recent speed statistics?', zh: '重置近期速度統計？' },
};

export const t = (lang: UILang, key: keyof typeof dict, vars?: Record<string, string | number>) => {
  const template = dict[key][lang] ?? dict[key].en;
  if (!vars) return template;
  return Object.entries(vars).reduce(
    (acc, [k, v]) => acc.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v)),
    template,
  );
};
