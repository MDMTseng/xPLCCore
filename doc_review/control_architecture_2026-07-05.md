# UI / PLC / 週邊 整體控制架構 — 知識彙整（2026-07-05）

> 這份是跨多個 session 累積的系統理解彙整，供工程組對照與新人導讀。
> 來源：doc/ 官方文件、codesys_code/ 實碼、live PLC 實測、
> [`refactor_analysis_2026-07-02.md`](./refactor_analysis_2026-07-02.md) 四視角分析、
> R1–R11 / B1–B4 test-first 修復過程的實證。
> 事實性描述以 code 為準；有疑義處標了檔案位置可核對。

---

## 1. 全域拓撲

```
┌─────────────────────────── Dev PC ────────────────────────────┐
│  Electron UI (React18/TS renderer)                            │
│   ├─ 業務編排 orchestration（CalibPage.runAllObjects、         │
│   │   production plan、runinng_checkpoint、resume.ts）         │
│   ├─ PluginHello: 1Hz SYS/PING 心跳                            │
│   └─ remote_harness (127.0.0.1:8127) ← 測試/腳本驅動 UI 用      │
│  CODESYS IDE + RPC daemon (127.0.0.1:7420)                    │
│   └─ rpc.py exec → import_all / stop_then_install / 讀 GVL    │
│  Vision（缺陷檢測，4 面）── JSON-over-TCP :7950 ── 只連 host    │
└───────────┬───────────────────────────────────────────────────┘
            │ msgpack-over-TCP :8125（無長度 framing、單一 client）
┌───────────▼──────────── PLC box (192.168.1.70) ───────────────┐
│  CODESYS 3.5 SP21 runtime                                     │
│   ├─ EC_Task 1ms prio0（watchdog OFF）：motion + 封包 dispatch │
│   ├─ Planning 1ms（空）                                        │
│   └─ Comm 5ms prio20：TCP socket I/O + framing + ring copy    │
│  EtherCAT：EAxis0/1/2（delta SpiderR 三臂）+ reelpullmotor     │
│  Conveyor encoder → GVL.ConveyorPulseRaw                      │
│  HECAT_1616 數位 IO（目前 DI 未接線 — dead path）              │
│  COM3/COM4（Modbus/485，PLC box 本地）                        │
└───────────────────────────────────────────────────────────────┘
```

機器本體：delta 機器人 tape-and-reel packer——從 FlexBowl/輸送帶取料、
視覺檢查、放入載帶（carrier tape）格子、捲帶（reel）進給。

## 2. 職責切分（最重要的設計原則）

**PLC stays generic**：PLC 是通用 motion/IO/safety server，不含任何業務語義。
料件、格號、生產計畫、視覺結果全在 renderer。曾被否決的方向：
PLC 加 WaitingForMaterial 狀態（W2）、PLC↔vision 直連——都因違反此原則。

**業務編排放 renderer 的原因**：現場 hot-swap——改 TS 立即生效不用重啟／
重編 PLC。代價是 renderer 是 volatile 的，因此需要下面 §6 的 crash-and-resume
架構把「生產順序狀態」錨在 PLC。

**Host-authority 安全契約（W1）**：PLC 是安全的最終權威。心跳階梯：
UI 1s PING → 3.5s host 判 stale → 5s（`UI_HEARTBEAT_TIMEOUT_MS`）PLC 端
A3 supervisor 在「有 motion in-flight」時跳 Error（idle 時刻意不跳——
「操作員去吃午餐」豁免）。任何 inbound 封包都會刷新 `LastUiPingMs`（不只 PING）。

**No-silent-drop 硬不變式**：每個被拒/被丟的封包必須 NAK 或 bump counter。
R1–R11 那輪修掉了最後幾個違規點（M4 gate、WFT gate、slot 飽和 wedge、
unknown cmd bare NAK）。

## 3. PLC 內部分層（封包的一生）

```
socket → FB_TcpMsgPakServer（NBS wrap、framing、stall/idle 重置）
       → minfo_buf ring（6 slot × 256B，host→PLC）        [Comm 5ms]
------------------------------------------------------------------
       → AxisGroupSM.DrainHostPackets（EC_Task 1ms！）
           ├─ protocol_version 檢查（先於一切 dispatch）
           ├─ type='SYS' → GA_EV/PING/GET_MACHINE_STATE/GET_DIAG/
           │   SCRATCHPAD_WRITE/RESET_DBG_INFO/VERSION/
           │   COORD1_BIND/UNBIND/GET_COORD1_DEBUG
           ├─ type≠'M' → NAK missing_type
           ├─ type='M' 且非 Ready → NAK group_not_ready
           └─ type='M' 且 Ready → EXIT 留 tail 給↓
       → AxisGroupSM.ProcessMotionPacket
           G1/G4/M4/WAIT_FOR_TRIGGER/SetCoord0/1/ReelGo/
           BLOCK_FOR_*/READ_LATEST_CMD_LOCATION/...
       → SoftMotion FBs（MoveLinearAbsolute/GroupWait/TrackConveyorBelt）
------------------------------------------------------------------
reply/event → reMP_info ring（128 slot × 1024B，PLC→host）
       → Comm task 每 tick 送 1 個 → socket
```

關鍵事實：**msgpack parse + dispatch 全部跑在 EC_Task 1ms**，Comm task 只做
搬運。效能分析（analysis §3）指出的重 parse/線性 lookup 成本都落在 motion task。
Comm 每 tick 收/發各 1 封包 → **200 pkt/s 硬天花板**（4hr fuzz 基線 25pkt/s 遠低於此）。

回覆槽生命週期：`TryAcquireReplySlotOrScratch` → pack → `CommitReplySlotOrScratch`
（統一補 id+ack——NAK 分支自己不 pack ack，R9）。ring 滿走 scratch slot +
drop counter（drop-newest 策略）。

## 4. FSM 與事件

`AxisGroupManager`（Robot_FBs 層）：
```
UnInited(10) ─EV_POWER_ON(2)→ Powering(20) → Powered(30)
  ─EV_GROUP_ENABLE(4)→ GroupEnabling(40) → GroupEnabled(50)
  ─EV_HOME_GO(6)/EV_HOME_GO_FORCE_SKIP(7)→ Homing(60) → Ready(70)
  任意態 ─EV_ERROR(9)→ Error(990) ─EV_RESET(8)→ UnInited
```
- host-postable 事件**恰為 {2,4,6,7,8,9}**；1/3/5 是 tombstone 空洞、
  EV_OK=10/EV_ER=11 是內部 handshake 事件，GA_EV 一律 NAK（R1 修復）。
- 進 Error 的路徑：GA_EV EV_ERROR、A3 心跳 supervisor、GroupErrorStop
  supervisor、homing 失敗（虛擬軸上真 EV_HOME_GO 必失敗：`Homing:homing_fb`，
  要用 FORCE_SKIP）、B2 之後新增的 coord/TCB error consumer、
  fly-bind fault path。
- 錯誤現場：`GVL.LastErrorSource`/`LastErrorID`（EV_RESET→UnInited 循環清空）。
- 事件傳遞有兩條路：TCP GA_EV **直呼** `Transition()`；`InputEvent` 變數
  +邊緣偵測**只屬於 VisuEventControl**（IDE 視覺化）。歷史教訓：任何非 Visu
  路徑寫 InputEvent 都會出 double-fire 或吞事件 bug（B4 修掉最後一處）。
- Ready 進出時的 session 清理分居兩處（FSM entry action 清 coord gate；
  comm 側 ST_CHG 邊緣清 BLOCK latch + G1 dedupe ring）——`xForceReentry`
  同態重入不觸發後者，是已知結構債（analysis §2.3 session epoch，次階段）。

## 5. 運動與座標系

- **G1**：直線移動。modal 參數（X/Y/Z/A/F/ACC/DEA/JERK/FAC/Cor 缺省沿用
  上次值）。`frame:0`=WCS、`frame:1`=PCS_1（需 Coord1Bound）。A 軸有 /10
  結構性 workaround（`A_AXIS_KIN_WRAP_SCALE`，SpiderR Kin_CAxis 包裝所致，
  勿「修正」）。dedupe ring 16 格防 TCP retry 重複執行（id≤0 不進 dedupe）。
- **G4**：dwell（SMC_GroupWait）。只在 accepted 分支回覆（B1）。
- **coord gate**：UnInited 進入時清 `CoordSystemConfigured`；SetCoord0/1
  之前 G1 一律 NAK `coord_not_configured`。SetCoord 在 motion in-flight 時
  NAK `motion_in_flight`（防 WCS 原點在移動中被抽走）。
- **movement_id 語義（易踩）**：`movement_id`=目前位置對應的 move（開始就跳）；
  `last_completed_movement_id`=MOVE_DONE emit 點 latch 的已完成 id——
  **resume 比較只能用後者**。abort/error flush 不發 MOVE_DONE、不推進
  last_completed（R10）。
- **工作空間**：XY ±25mm、Z ∈ [-130, -70]（Z<0 only）。**界外 G1 會 ack
  但不動（silent fail）**——測試/腳本必用界內目標。
  ⚠️ 未解：batch-2 合跑後曾實測 `arm_z=+33.8`（正 Z、idle、未 bind）——
  尚未歸因，見 §9。

### 輸送帶追蹤（conveyor pick，Phase 4/5）
- 設計：tracking coord（PCS_1）綁定物件原點——不是 e-cam、不是 BVP，
  是業界標準 pattern。
- `COORD1_BIND`（SYS，直接綁）或 M4 `action='coord1_bind'` + `trig:130`
  （PulseTrigger，在精確 encoder pulse 那個 scan 原子性綁定）。
- 綁定 = `Coord1CommitBind`（B3 之後 SYS/fly 兩路共用同一 method）：
  驗證順序 busy→rebind→scaleX，belt 僅 +X 軸向，
  `Coord1PulseToMm=1/scaleX`，`MC_TrackConveyorBelt` 每 scan 讓 PCS_1
  原點跟著 encoder。
- 窗口保護：`exit_pulse_offset` → `Coord1ExitPulse`；window-exit 偵測
  → `Coord1LatchWindowError` + COORD1_ERROR 事件 + Error + GroupStop +
  auto-unbind。
- fly-bind 被拒（busy/rebind/scale）走 fault path（Error），不是 silent skip。

### FlyEvents（10 slot）
- M4 註冊；trigger 家族：`trig:20` MovementProgress（綁 motion_id + 進度%）、
  `120` Distance（TCP 進/出球形邊界；比較平方距離避免 SQRT）、
  `130` Pulse（encoder 門檻）。
- action：PIN_OPERATION（`pin_op_seq` = [delay,pin,state]×N 多段 IO 序列，
  舊 {pin,state,reset_ms} 形已下線、host 端展開）、COORD1_BIND、
  ACK_SRC_ID（WAIT_FOR_TRIGGER 的延遲回覆）。
- TTL：`ttl_ms`（-1=永久）。slot 飽和（avail≤3）→ NAK
  `flyevent_buffer_full`（R11 之前會 wedge 整條 inbound 佇列）。
- DistanceTrigger 的位置來源 `GroupActualPositionFb` 加了
  `ArmPositionValid` gate（B2c）——無效讀值時跳過判斷、TTL 照 decay。

## 6. 故障可恢復架構（§4 recovery，2026-06 主線）

問題設定：錯誤非致命（單機），但**中斷後生產流程必須可續**，否則報廢/重工。
renderer volatile（crash 或 hot-swap 是同一件事），PLC 是 recovery anchor。

- **Scratchpad_v1**（RETAIN，~33B）：host-owned opaque cursor。PLC 只解讀
  `SchemaVersion` 與 `BootEpoch`（「durable register, not a brain」）。
  欄位：plan_id/plan_index/intent_kind/intent_movement_id/last_vision_pulse。
  `SCRATCHPAD_WRITE` 五欄全寫；缺欄 NAK `partial_scratchpad`、超寬
  NAK `scratchpad_range`（值域 2³¹-1 / intent_kind 255）。
- **Boot epoch**：`BootEpochCount`（RETAIN）每次 app boot +1（`AppBootHandled`
  one-shot gate）；cursor 的 `boot_epoch` ≠ `boot_epoch_now` → PLC 重啟過
  → cursor 作廢 → cold start。cold reset 清 retain → SchemaVersion=0 →
  同樣 cold start。
- **寫入時序契約（load-bearing！）**：intent 在動作 **ack 之後**寫，stamp
  **ack 回覆裡的 movement_id**（不是 last_completed——那是上一動）。
  寫錯邊 → resume 恆判「已完成」→ 跳過 blow-off → 重複進給 → 報廢。
  契約鎖在 `orchestrator/resume.ts` writeIntent docstring +
  coupling_invariants.md + unit test。
- **Resume 四態**（decisions B.6）：idle→continue；intent 未完成→**blow_off**
  （吹掉手上料，接受每次 crash 報廢 1 件，D.11）；完成+vision OK→continue；
  完成+vision 未知→**bowl_back**（退回 feeder）。
  判斷式：`last_completed_movement_id >= intent_movement_id`。
- **Plan persistence**：localStorage `plan_v1`（plan_id 每次編輯 bump；
  與 scratchpad.plan_id 不符 → cold start）。
- **現況**：PLC 側 live + 17 條 vitest + RecoveryDemoPage（獨立 tab）端到端
  驗證過；**CalibPage.runAllObjects 尚未接線**（W4#9，PROJECT.md 標 🚧）。
- 保守性原則：任何「不確定完成」都判未完成（retry-drop 不推進 last_completed、
  abort 不發 MOVE_DONE）——寧可多走一次 blow-off。

## 7. 週邊細節

| 週邊 | 介面 | 狀態/備註 |
|---|---|---|
| Delta 三臂（EAxis0/1/2） | EtherCAT, SM_Drive_GenericDSP402 | SpiderR kin；虛擬軸模式 `bVirtualMotorsMode`（daemon force；gate TON 有 TTL 重置 quirk） |
| Reel pull motor | EtherCAT 第 4 軸 | `ReelGo`（雙 MC_MoveRelative FB 輪替；JERK 必非零）；`reel_pos` 進 GET_MACHINE_STATE（A.1 實測 STOP+START retain）；格號換算在 host（origin/pitch 校正未做） |
| Conveyor encoder | → `GVL.ConveyorPulseRaw` | PulseTrigger/COORD1 tracking 的時基 |
| 數位 IO HECAT_1616 | EtherCAT IO | **DI 未接線**（`DigitalInputPointer` 賦值被註解）→ BLOCK_FOR_DIGITAL_INPUT 只能 timeout、GET_DIGITAL_INPUT 恆 0。接線前要先修 group 索引 bounds（analysis §1.7）。DO：nozzle suck/blow 經 pin_op |
| Vision | JSON TCP :7950 | **只連 host**；PLC vision-blind（direct link 已否決）。時序關聯用 `last_vision_pulse` |
| Modbus/485 | COM3/COM4（PLC box 本地） | 不能遠端重啟 PLC box；需要時使用者現場處理 |
| FlexBowl feeder | host 側控制 | bowl_back 退料目的地 |

## 8. 診斷與工具鏈

- **GET_DIAG**：全 counter dump（drop/NAK/reset/supervisor/dedupe/IO/ping 統計）。
  canonical 契約：**GET_DIAG 發布的每個 counter 都被 `ResetDiagCounters()` 清**
  （SYS 與 M 兩個 RESET_DBG_INFO 都 delegate；R8 測試交叉鎖住）。
  新 counter 三處同步：GET_DIAG pack + ResetDiagCounters + 測試 `DIAG_COUNTER_KEYS`。
- **GET_COORD1_DEBUG**：`arm_x/y/z`（ReadPosition.c，虛擬軸 halt 時仍可用）+
  bind 全狀態。**到位判定用它輪詢**——`motion_buffer_size==0` 是佇列清空
  不是位置到位。
- **push events**（無 id）：ST_CHG、MOVE_DONE、COORD_SET、HEARTBEAT、
  COORD1_ERROR。皆有 pending-latch + retry pump + 最終 drop counter。
- **工具鏈**：`.st` 檔改完 → `import_all`（新 method 要先跑 create-method job
  建專案物件）→ build errors=0 → `stop_then_install.py`（canonical；layout
  變更下 online change 會彈 dialog 掛 daemon）。force download 會清 runtime
  狀態（虛擬軸歸零、TCP=(0,0,0)）但 RETAIN 留存。
- **測試**：CI tier-1（tsc+vitest+py_compile；husky pre-push）不含 live-PLC；
  live 套件（R1–R11、B1–B4、W1、dedupe、fly、fuzz、soak）是 merge 前手動 gate。
  單一 TCP client——測試自己用 remote_ctrl 仲裁 UI 連線 + 1Hz PING keepalive。

## 9. 已知未解 / 進行中

1. **batch-2 合跑回歸未收斂**（最優先）：B+R 同一 pytest 合跑 4/23 fail，
   簽名=FSM 非同步掉出 Ready（group_not_ready）；嫌疑=B2a/B2b 把 transient
   FB error 升級成 EV_ERROR + 測試間狀態污染；**`arm_z=+33.8`（正 Z）未解釋**
   ——若 bind/unbind 循環真能把手臂拖出工作空間是機器危害等級的問題。
   已發包給工程組 agent，因 session limit 中斷，**待續跑**（收斂條件：
   watcher 抓 trip 當下 err_src、合跑連續兩次全綠）。
2. 次階段結構項（standards §3）：session epoch/重入 scrub、DI bounds（接 IO 前）、
   MoveLeft 重疊 MemCpy、COORD1_BIND ack 延後到 TCB InSync（另案評估）。
3. 效能項（standards §4，先量再動）：EC cycle time 量測 → blocked-tail
   header cache / FindValueByPath 線性掃描；comm 200pkt/s 天花板（egress
   drain loop）；`MotionStateString` 刪除（零消費者，一行）。
4. CalibPage 生產迴圈接 resume（W4#9）。
5. PLC `VERSION` 的 git_sha 過期（上次 install 沒跑 stamp_build_info）。

## 10. 文件地圖

| 文件 | 內容 |
|---|---|
| `doc/2-contracts/protocol.md` | wire 契約全表（M/SYS 命令、NAK err 字串、push events） |
| `doc/1-concepts/coupling_invariants.md` | 跨層耦合不變式（含 intent 時序三站耦合） |
| `doc/3-subsystems/conveyor_pick.md` | 輸送帶追蹤子系統 |
| `doc_review/architecture_review_2026-06-22.md` | 為什麼要做 recovery（safety≠resumability） |
| `doc_review/decisions_2026-06-22.md` | 12 項決議（scratchpad schema、blow-off 等） |
| `doc_review/refactor_analysis_2026-07-02.md` | 四視角重構分析（本輪修復的母文件） |
| `doc_review/test_standards_2026-07-03.md` | R/B 驗收判準 + 執行記錄 |
| `doc_review/implementation_review_2026-06-22.md` | §4 實作 review + 追蹤 |
