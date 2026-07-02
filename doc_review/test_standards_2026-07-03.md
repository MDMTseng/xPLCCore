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
| §1.4 InputEvent 二次 fault 吞沒 | ✅ **B4 done**（`bdc6368`，wire 測試 `test_B4_fly_fault_twice_leaves_ready_both_times`，紅燈基線實證 fault #1 就被前一測試留下的 latch 吞掉） | fault #2 後 FSM 必須離開 Ready ✅（兩輪 fault + EV_RESET 之間，皆到 990）；fly-bind fault 路徑改直呼 `Transition(EV_ERROR)` ✅，`InputEvent` 不再被非 Visu 路徑寫入 ✅ |
| §1.5 G4 提早/重複 ACK | ✅ **B1 done**（`934b75a`，wire 測試 `test_B1_g4_exactly_one_reply` 守單一回覆不變式；refusal 分支維持 code-review 判準） | G4 的 `ShouldReturn/CommandAck` 只在 accepted/NAK 分支設 ✅；同一 id 的 reply 數 =1 ✅；P<=0 NAK 補 `err='bad_dwell'` |
| §1.6 SMC FB `.Error` 未檢查 | ✅ **B2 done**（`3f04998`，B2c 正向 wire 測試 `test_B2c_distance_trigger_fires_when_position_valid` 防 over-suppression；B2a/B2b 為 code-review 判準——error 條件無法由 wire 強制） | `trackConveyorBeltFb`→latch+清 Coord1Bound+EV_ERROR ✅；`SetCoordTransformFb`→清 CoordSystemConfigured+latch+EV_ERROR ✅；`GroupActualPositionFb`→`ArmPositionValid` gate DistanceTrigger（不 trip EV_ERROR，TTL 照 decay）✅。**偏離原判準**：COORD1_BIND 的 ack 沒有延後到 TCB InSync——依 batch-2 spec 改為 error consumer + 關 gate（延後 ack 是 wire 語義變更，另案評估） |
| §2.1 Coord1 雙 bind drift | ✅ **B3 done**（`bdc6368`，parity wire 測試：SYS scale=0/re-bind/busy NAK pins + fly busy/scale=0 → fault path） | 兩路徑呼叫同一 `Coord1CommitBind` ✅；同組非法輸入（scale=0、buffer>0、re-bind）行為同源 ✅；fly bind 補上 buffer>0 refusal，refusal 走 fault path（FSM 離開 Ready、bound=FALSE），非 silent skip ✅；GVL.Coord1* symbol 全保留、per-path counter 留在呼叫點 ✅ |
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
4. ~~第二批（結構）~~ ✅ 2026-07-03 完成（BATCH 2，B1–B4）：新套件
   `codesys_scripts/tests/test_plc_refactor_findings_b2.py` 8/8 綠
   （紅燈基線：B3-fly=silent bind 實證、B4=latch 吞 fault 實證；
   B1/B2c/SYS-pins 依預期即綠）。commits `934b75a`（B1 G4）+
   `3f04998`（B2 error consumers）+ `bdc6368`（B3+B4 Coord1 統一 +
   Transition）。部署走 create_coord1_bind_methods → import_all
   （build 0 error）→ stop_then_install。回歸：R-suite 15/15、
   W1 5+3skip（skip 為既有 virtual-axis 環境限制）、dedupe/
   reply-format/fly-events 8/8、fuzz 9/9（fuzz FSM-walk 測試自身的
   stale enum 表已修：`69bc011`——Error=990 非 99；紅因是既有
   homing-on-virtual-axes 行為，err_src='Homing:homing_fb'，與
   batch-2 無關）。§3/§4 表其餘次階段項另排。

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
