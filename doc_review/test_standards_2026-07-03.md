# 修復前測試標準 — refactor_analysis 各發現的驗收判準（2026-07-03）

> 對應 [`refactor_analysis_2026-07-02.md`](./refactor_analysis_2026-07-02.md)。
> 原則（test-first）：**每個要修的發現先有紅燈測試**——測試在修復前失敗
> （紅 = bug 自證），修復後不改測試直接轉綠。修復 commit 必須引用對應測試 ID。
>
> 自動化套件：[`codesys_scripts/tests/test_plc_refactor_findings.py`](../codesys_scripts/tests/test_plc_refactor_findings.py)
> （live-PLC wire 層，跑法同 W1 套件：`pytest codesys_scripts/tests/test_plc_refactor_findings.py -v`）。
> 此套件**刻意不進 CI tier-1 gate**（需要真 PLC + daemon + UI 仲裁），
> 與 W1/fuzz 同屬 merge 前手動 gate。

---

## 1. 自動化紅燈測試（wire 層可驗）

| Test ID | 對應發現 | 驗收判準（修復後必須成立） | 修復前預期 |
|---|---|---|---|
| R1 | §4.1 GA_EV sparse enum | `ev∈{1,3,5,10,11}` 一律 `ack=false`；host-postable 只有 {2,4,6,7,8,9} | 🔴 全部 ack=true |
| R2 | §1.7 bare NAK | unknown M cmd 的 NAK 必帶非空 `err` 字串 | 🔴 NAK 無 err |
| R3 | §1.2-1 M4 gate | `coord1_bind` + 非 pulse trig → 必回 NAK+err | 🔴 靜默消失（timeout） |
| R4 | §1.2-1 M4 gate | pin_op 淨零 stage → 必回 NAK+err | 🔴 靜默消失 |
| R5 | §1.2-2 WFT gate | `WAIT_FOR_TRIGGER_MOTION_PROGRESS` 被拒 → 必回 NAK+err（修復含刪除 `MotionOutputPin` 殘留條件） | 🔴 靜默消失 |
| R6 | §1.3 id sentinel | 兩個無 `id` 的 G1 **都要執行**（無 id 永不進 dedupe）；以 `arm_z` 到達第二目標驗證 | 🔴 第二個被 dedupe 跳過 |
| R7 | §1.7 scratchpad wrap | `intent_movement_id > 2³¹-1` → NAK 且原值不被覆蓋 | 🔴 ack=true + 值 wrap |
| R8 | §2.2 reset drift | SYS `RESET_DBG_INFO` 後，GET_DIAG 發布的**所有** counter 鍵歸零（canonical list = GET_DIAG 全集，測試內建清單 `DIAG_COUNTER_KEYS`） | 🔴 `dupe_cmd`/`io_cmd_count` 等殘留 |
| R9 | §1.7 duplicate key | 任一 NAK reply 的 raw msgpack frame 中 `ack` 鍵恰出現 1 次 | 🔴 出現 2 次 |
| R10 | §1.1 abort MOVE_DONE | EV_ERROR abort 佇列中的 move：不得對其發 MOVE_DONE、`last_completed_movement_id` 不得推進到被 abort 的 id | 🔴 誤發 + 推進 |
| R11 | §1.2-3 saturation wedge | slot 飽和時 M4 → 立即 NAK `err='flyevent_buffer_full'`，且其後方佇列（GET_MACHINE_STATE probe）持續流動 | 🔴 佇列 wedge、probe timeout |

**設計備註**：
- R10 依賴 virtual 軸能觀察到 in-flight buffer（排 4 段慢速 G1）；若環境把整鏈瞬間跑完會 `skip` 而非誤判——skip 時改用真軸或更慢 F 補跑。
- R11 的 filler 事件帶 `ttl_ms=12s` + 永不命中的 distance trigger，紅燈時 wedge 會靠 TTL 自癒，測試自清理，不留髒 slot。
- R8 在 reset 前故意先弄髒 `dupe_cmd`（同 id G1 兩發），紅燈具決定性、不靠殘留狀態。
- 修復 R3/R4/R5/R11 時新增的 NAK counter（如 `FlyEventRejectNakCount`）應同步加入 GET_DIAG 與 `DIAG_COUNTER_KEYS`——R8 的 missing-key 斷言會逼著清單保持誠實。

## 2. 既有套件回歸保護（修復不得打破）

- **W1 套件**（`test_plc_w1_recovery.py`）全綠——安全契約不動。
- **dedupe 套件**（`test_plc_dedupe.py`）全綠——R6 修法（sentinel 統一）不得破壞有 id 的正常 dedupe。
- **fly events / virtual motion soak** 全綠——R11 修法不得改變合法 M4 的註冊行為。
- **4hr fuzz 基線**（memory: `plc_4hr_fuzz_baseline`）：修復落地後重跑至少 30min smoke，drop/NAK counter 與基線同形（R3/R4/R5 會新增 NAK counter，屬預期差異，記入基線更新）。
- **vitest 26/26**（tier-1 CI gate）不受影響。

## 3. 不自動化（或次階段）的標準

| 發現 | 標準形式 | 判準 |
|---|---|---|
| §1.4 InputEvent 二次 fault 吞沒 | 次階段 live 測試（需 conveyor pulse 模擬造出兩次 window-exit） | fault #2 後 FSM 必須離開 Ready；短期以 code review 判準代替：fly-bind fault 路徑改直呼 `Transition(EV_ERROR)`，`InputEvent` 不再被非 Visu 路徑寫入 |
| §1.5 G4 提早/重複 ACK | 難以 wire 強制 `CommandAccepted=FALSE`；判準=code review + 不變式 | G4 的 `ShouldReturn/CommandAck` 只在 accepted/NAK 分支設；任何 fuzz run 中同一 id 的 reply 數 ≤1（fuzz 報表已可驗） |
| §1.6 SMC FB `.Error` 未檢查 | code review 判準 | `trackConveyorBeltFb`/`SetCoordTransformFb`/`GroupActualPositionFb` 的 Error 都有消費者：NAK-by-event 或 trip EV_ERROR + 關 gate；COORD1_BIND 的 ack 延後到 TCB 實際 InSync/非 Error |
| §2.1 Coord1 雙 bind drift | parity 判準 | 重構後兩路徑呼叫同一 `Coord1CommitBind`；SYS 與 fly 路徑對同一組非法輸入（scale=0、buffer>0、re-bind）行為一致（NAK vs reject counter 各自形式，但**判定邏輯同源**） |
| §2.3 session epoch / 重入 scrub | 次階段 | stacked-fault（同態 EV_ERROR×2）後 dedupe ring 已 scrub：reset 回 Ready 後重放舊 id 的 G1 必須執行非 dedupe |
| §1.7 DI group bounds | 接 IO 前必做；判準 | `group` 超界 → NAK `err='invalid_pin_group'`（接上 HECAT 後補 wire 測試） |
| MoveLeft 重疊 MemCpy | code review 判準 | 改 `MemMove`（或證明 MEMUtils 前向複製）+ 既有 fuzz 全綠 |

## 4. 效能項的量測標準（先量再修，非 pass/fail 測試）

| 項 | 量測方法 | 動手門檻 | 修復後驗收 |
|---|---|---|---|
| §3.2 blocked-tail 重 parse / §3.3 線性 lookup | CODESYS task monitor 讀 EC_Task max/avg cycle time，負載=飽和 G1+M4 流（可用 soak 腳本） | M4 全 parse > ~100µs 或 max cycle 逼近 1ms 的 50% | 同負載下 max cycle 明顯下降；blocked BLOCK_FOR_* 等待期間 cycle time 不再隨佇列深度上升 |
| §3.4 comm 吞吐天花板 | fuzz harness 以 100 與 250 pkt/s 跑 10min，看 `ReMpDropCount`、reply RTT 尾巴、`PingMaxGapMs` | 250 pkt/s 出現 drop 或 RTT 尾巴 >200ms | egress loop 落地後同負載 0 drop；burst 10 命令 RTT 尾巴 <30ms |
| §3.1 MotionStateString | 無需量測（零消費者） | — | 刪除；visu 確認無引用 |
| §3.5 診斷 transform FB | 線上 force 兩個 Enable=FALSE，讀 cycle time 差 | 差 >10µs 才做 | gate 後 cycle time 持平、GET_COORD1_DEBUG 仍可用（可容 ≤8ms stale） |

## 5. 執行順序

1. ~~跑紅燈基線~~ ✅ 已完成，見 §6。
2. ~~第一批修復~~ ✅ 2026-07-03 完成：R1–R11 全部轉綠（15/15，測試零修改），
   commits `ad82d76`（R10 abort gate）+ `87423d4`（R1–R9/R11 NAK hygiene +
   ResetDiagCounters）。部署走 stop_then_install，build 0 error。
3. ~~回歸 + fuzz smoke~~ ✅ W1 recovery / dedupe / reply-format / fly-events 全綠
   （fly-events outside-boundary 測試本身有順序依賴，已修：`74a01c2`，
   probe 實證 trigger 是照規格發火——同時佐證 §3 表 GroupActualPositionFb
   判準的必要性）；fuzz smoke 9/9 全綠（pathological / burst / noise /
   ping-flood / counter invariants / FSM random walk）。
4. 第二批（結構）與 §3/§4 表的次階段項另排。

---

## 6. 紅燈基線實跑結果（2026-07-03，live PLC @192.168.1.70，virtual motors）

**15/15 全紅，且每條都紅在預期的 bug 簽名上**（非環境噪音）：

| 測試 | 實測 bug 簽名 |
|---|---|
| R1[1,3,5,10,11] | 全部 `ack:True`——sparse gap 與 internal 事件都被接受 |
| R2 | `{'id':82600,'ack':False,'seq':...}`——bare NAK 無 err |
| R3 | 2.5s 無回覆（silent drop） |
| R4 | 2.5s 無回覆（silent drop） |
| R5 | 2.5s 無回覆（silent drop） |
| R6 | G1#1 到 Z=-80、G1#2 被 dedupe 跳過停在 -80（預期 -100） |
| R7 | `2³²+5` 回 `ack:True`（靜默 wrap） |
| R8 | reset 後殘留 `self_reentry=6, dupe_cmd=1, io_cmd_count=315, io_trig_count=464` |
| R9 | raw frame `ack` 鍵 ×2：`\xa3ack\xc2 \xa3err unknown_event \xa2id ... \xa3ack\xc2 \xa3seq` |
| R10 | **對被 abort 的 move 誤發 MOVE_DONE**（movement_id=112），且 `last_completed_movement_id` 推進到被 abort 的 id（=117）——§1.1 兩個分支都實證 |
| R11 | 飽和 M4 無回覆 + **GET_MACHINE_STATE 卡死 timeout**（佇列 wedge 實證）；TTL 到期後自癒、slot 回 10 |

**基線過程中的兩個測試側修正**（已入測試，記錄備查）：
- G1 目標必須在 delta 工作空間內（XY ±25、Z -130..-70）——界外 G1 會 ack 但
  silent fail（memory: delta_workspace_z_negative），第一版 R6/R10 用了 Z=-10..-45 導致紅錯地方。
- **`motion_buffer_size==0` 不是「位置到位」訊號**——最後一段 move 還在執行時
  buffer 就已歸 0。到位判定要輪詢 `GET_COORD1_DEBUG.arm_z`（R6 的 `_wait_arm_z`）。
  這也修正了 memory `plc_movement_test_gotchas` 的適用範圍：buffer==0 適合
  「佇列清空」判定，不適合「終點到位」判定。
