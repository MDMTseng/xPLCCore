# PLC Code 重構分析 — 四視角綜合（2026-07-02）

> 方法：四個獨立 subagent 各自完整讀過 `codesys_code/`，視角分別為
> **架構/模組邊界**、**正確性/錯誤路徑**、**效能/scan 預算**、**可讀性/新人視角**。
> 每個 agent 都帶了專案約束（PLC stays generic、不過度拆 method、no-silent-drop、
> EC_Task 1ms、已完成的重構清單）再分析，以下是交叉綜合。
>
> 標記：`[C]`=正確性 `[A]`=架構 `[P]`=效能 `[R]`=可讀性 視角來源。
> 所有 file:line 皆出自 agent 實讀原始碼。

---

## TL;DR — 嚴重度總表

| # | 問題 | 視角 | 嚴重度 |
|---|---|---|---|
| 1 | Group error/abort 時誤發 MOVE_DONE 並推進 `last_completed_movement_id` | [C] | 🔴 会毒化 resume |
| 2 | 三處 silent-drop：M4 gate、WAIT_FOR_TRIGGER gate、FlyEvent 滿載 wedge | [C][R] 交叉 | 🔴 違反硬 invariant |
| 3 | `id` sentinel -1 vs 0 → 無 id 的 G1 被 dedupe 誤判、直接跳過運動 | [C] | 🔴 |
| 4 | InputEvent level-latch：第二次 fly-bind fault 被吞掉、FSM 不進 Error | [C]（與 [A]#4 同根） | 🟠 |
| 5 | Coord1 子系統散在 6 檔、兩份 bind 實作已 drift | [A]（[C]#8 相關） | 🟠 |
| 6 | coord1/transform 路徑 SMC FB `.Error` 全部沒檢查 | [C] | 🟠 |
| 7 | G4 在 `CommandAccepted=FALSE` 時提早 ACK＋重複 ACK | [C] | 🟠 |
| 8 | RESET_DBG_INFO 兩份 handler、counter 清單已 drift | [A][C][R] **三視角命中** | 🟡 |
| 9 | 被擋 tail 封包每 1ms 全量重 parse；`FindValueByPath` O(欄位×鍵) | [P] | 🟡 |
| 10 | Comm task 每 tick 只搬 1 封包 → 200 pkt/s 硬天花板 | [P] | 🟡（10x 負載才會撞） |
| 11 | `MotionStateString` 每 scan 重建、零消費者 | [P][R] 交叉 | 🟢 一行修 |
| 12 | GA_EV 範圍檢查沒排除 sparse enum 空洞與 internal 事件 | [R] | 🟡 |
| 13 | 可讀性批次：stale 註解/假常數/dead code/magic number | [R] | 🟢 |

**交叉印證的價值**：#2、#8、#11 由不同視角獨立命中，置信度最高；
#4 與 [A]#4（session 清理分裂）、#5 與 [C]#8（Coord1 錯誤路徑）互為同一結構問題的兩面——
**Coord1 子系統與「事件邊緣偵測的所有權」是這輪分析指出的兩大結構熱點。**

---

## 1. 🔴 正確性（上線前必看）

### 1.1 Abort 誤發 MOVE_DONE，毒化 `last_completed_movement_id`

`UpdateMotionProgress.st:52-61` 的 MOVE_DONE 邊緣條件是
`PrevMotionBufferSizeForEvent > 0 AND MotionBufferSize = 0 AND LastAcceptedMovementId <> 0`，
**整條路徑沒有 FSM 狀態或 error gate**。而 `:7-10` 在 error freeze / abort 後會把
`MotionBufferSize` 強制歸 0：

> G1 執行中 → 軸/bus fault → supervisor 發 EV_ERROR → GroupStop abort →
> buffer >0→0 → **對一個被 abort 的 move 發 MOVE_DONE**，且 `:76` 推進
> `PrevCompletedMovementId`——正是 §4(2) resume reconcile 信任的
> `last_completed_movement_id`。host 會判定 pick/blow-off 已完成 → **重複進 reel**。

這直接攻擊剛修好的 crash-and-resume 機制（`43c7090`/`41b9013` 防住了 retry-drop
的謊報，但沒防 abort 路徑）。

**修法**：edge-latch/emit 加 gate
`_eState = Ready AND NOT GroupReadStatusFb.Error AND NOT GroupErrorStop`；
error 時清 `PendingMoveDoneId` 不 emit。

### 1.2 三處 silent-drop（違反 no-silent-drop 硬 invariant）

| 位置 | 情境 | 現況 |
|---|---|---|
| `ProcessMotionPacket.st:419-448` | M4 registration gate 失敗（coord1_bind 缺 trig=130、pin_op_seq 淨零 stage、progress trigger 帶 motion_id=0） | `ConsumeTail` 但無 NAK、無 counter |
| `ProcessMotionPacket.st:462,495` | WAIT_FOR_TRIGGER_MOTION_PROGRESS 的 gate 用了**不相干的 scratch 變數** `MotionOutputPin`（上次 fired 的 pin mask；沒 fire 過任何 pin-op 前恆 0 → 全部拒絕）| 消費但永不回覆（兩個 agent 獨立命中）|
| `ProcessMotionPacket.st:251,455` | `FlyEventAvailableCount > 3` 無 ELSE：slot 飽和時 M4 卡在 tail | **整條 inbound queue wedge**（`DrainHostPackets.st:603-624` 在 type-M tail EXIT），且 ping 仍會刷新所以 A3 supervisor 不會跳，無任何 counter |

**修法**：三處都補 NAK（`err='flyevent_reject_*'` / `'flyevent_buffer_full'`）+ counter；
`MotionOutputPin <> 0` 這個條件是 legacy M4 copy-paste 殘留，直接刪。
另建議 Ready-exit 時清 FlyEventBuffer（比照 BLOCK latch 的清法，
`UpdateRuntimeAndInputEvent.st:44-48`）。

### 1.3 `id` sentinel 不一致（-1 vs 0）→ dedupe 誤殺

`DrainHostPackets.st:44` 缺 `id` 時預設 **-1**，但 dedupe ring 的 no-id sentinel
是 **0**（`ProcessMotionPacket.st:508,626`）。兩個連續無 id 的 G1（raw 測試腳本常見）：
第一個執行並 cache `(-1, mid)`，第二個命中 dedupe → 回 `ack:TRUE, dedup:TRUE`
**但不執行運動**。修法：parse 預設改 0，或三處比較改 `<= 0`。

### 1.4 InputEvent level-latch 吞掉第二次 fault

`ProcessFlyEventsAndIo.st:154,163` 用 `InputEvent := EV_ERROR` 報 fly-bind fault，
但唯一消費者是邊緣偵測 `UpdateRuntimeAndInputEvent.st:17-21`，且**沒人把
InputEvent 重設回 EV_NONE**（GA_EV 路徑刻意 bypass InputEvent）。
fault #1 → 邊緣觸發 → 進 Error → 操作員 GA_EV RESET 恢復 → fault #2
再寫 EV_ERROR → `PreviousInputEvent` 還是 EV_ERROR → **無邊緣、FSM 留在
Ready、手臂繼續跑**，只有 COORD1_ERROR payload 送出。

**修法**：改直接呼叫 `Transition(EV_ERROR)`（supervisor 與 GA_EV 已是此
pattern），或邊緣觸發後重設 `InputEvent := EV_NONE`。

同根問題（[A]#4）：session 清理 side-effect 分居兩處——FSM entry action 清
`CoordSystemConfigured`/`Coord1Bound`，comm 側 ST_CHG 邊緣偵測清 BLOCK latch
與 G1 dedupe ring（`UpdateRuntimeAndInputEvent.st:44-65`）。`xForceReentry`
同態重入（stacked fault）不產生狀態 diff → **dedupe ring 不會被清**。
建議引入 `_sessionEpoch`（Error/UnInited entry bump，重入也 bump），comm 側
比對 epoch 變化執行 scrub，取代狀態 diff 條件。

### 1.5 G4 提早 ACK + 重複 ACK

`ProcessMotionPacket.st:107-134`：`ShouldReturn/CommandAck := TRUE` 在 FB 呼叫
**之前**就設了。`SMC_GroupWait.CommandAccepted=FALSE` 時封包留在 queue，但
tail-commit 照樣發 `ack:TRUE`（無 movement_id），100ms cooldown 後重跑再發第二次
同 id 回覆（拒絕持續期間每 ~100ms 一發）。修法：比照 G1（`:616-636`），
只在 accepted/NAK 分支設 ShouldReturn。

### 1.6 coord1/transform 路徑的 SMC FB `.Error` 沒人看

- `trackConveyorBeltFb`（`AxisGroupSM.st:454-460`）：全部輸出無人讀。FB 拒絕時
  COORD1_BIND 已 ACK、`Coord1Bound` 留 TRUE → frame=1 G1 打到**非追蹤的 PCS_1**
  ——正是 window-exit detector 要抓的錯誤類別，但這條進不了 detector。
- `SetCoordTransformFb.Error`（`ProcessFlyEventsAndIo.st:269-273`）：只進 DBG 欄位。
  SetCoord0/1 在 FB 跑之前就 ACK 並設 `CoordSystemConfigured := TRUE`
  （`ProcessMotionPacket.st:154-158,177-190`）→ transform 失敗但 G1 gate 已開。
- `GroupActualPositionFb.Error`（`UpdateAxisGroupState.st:17-22`）沒檢查：
  stale position 餵 DistanceTrigger 判斷。

**修法**：三處至少 NAK-by-event 或 trip EV_ERROR + 關 gate。

### 1.7 其餘（中低）

- `BLOCK_FOR_DIGITAL_INPUT` 的 `group` 是 wire 控 0..65535、無 bounds check 就
  index `DigitalInputPointer[]`（`ProcessMotionPacket.st:59,68`）。現在 pointer
  沒接（dead path）所以打不到，**接上 HECAT IO 的那天就是 runtime exception**。
  先 clamp 0..15。
- `SCRATCHPAD_WRITE` 的 `TO_UDINT` 對 ≥2³² 的值靜默 wrap 且 ACK
  （`DrainHostPackets.st:290-294`）——建議 range-NAK，跟 partial-write NAK 同精神。
- NAK 分支自己 pack `ack:FALSE` 後 Commit 又補一次 → msgpack map 重複 key
  （decoder 取後值，暫時無害但脆弱）。
- Unknown motion cmd 的 NAK 沒帶 `err` 字串、沒 counter（`ProcessMotionPacket.st:653-655`）。

---

## 2. 🟠 架構熱點

### 2.1 Coord1 子系統：6 檔案、兩份 bind、兩份 error latch

散布：`GVL.st:338-439`（~45 globals）、`AxisGroupSM.st:421-541,624-706`、
`DrainHostPackets.st:387-533`、`ProcessFlyEventsAndIo.st:129-195`、
`ProcessMotionPacket.st:541-553`、`AxisGroupManager/Update.st:36-38`。

**已經 drift 的差異**：SYS bind 檢查 `MotionBufferSize > 0`，FlyEvent bind 沒有；
ExitPulse 一邊隱式 -1 一邊計算；counter 各 bump 各的；驗證集合不同。
下一個變更（rotary table / 非 X 軸 belt，註解已標 future）要改 2-3 處，漏一處
= belt origin 算錯 = 手臂打錯位置。

**修法**（不違反「不拆小 method」——這是大單元多呼叫點）：
`Coord1CommitBind(...)` (~35 行、2 呼叫點) + `Coord1LatchWindowError(...)`
(~15 行、2 呼叫點)，GVL symbol 全保留（scripting 可觀測性不變）。
Method 新增是 online-change 安全的。

### 2.2 RESET_DBG_INFO 雙 handler drift（三視角命中）

`DrainHostPackets.st:300-327`（SYS）vs `ProcessMotionPacket.st:194-221`（M legacy）。
M 版少 4 個 counter、SYS 版不清 Io* locals，**兩版都不清新增的十來個 counter**
（Coord1*Nak*、DupeCommandCount、ScratchpadPartialNakCount、ReMpDropCount_* 等）。
fuzz/soak 基線靠 counter 零點，走錯 path rebaseline 會把殘值讀成 regression。
修法：單一 `ResetDiagCounters()`，canonical 清單 = GET_DIAG 發布的全部。

### 2.3 其他（S 級、可趁 planned stop 一起做）

- `ProcessMotionPacket.st:12-22,658-669` 手刻 reply-slot acquire/commit，drop
  counter 在 **acquire 時** bump（SYS 路徑是 commit 時 bump）→ ring 滿時每個
  BLOCK_FOR_* polling scan 都灌水 `ReMpDropCount`。改用現成
  `TryAcquireReplySlotOrScratch`/`CommitReplySlotOrScratch` 即修。
- `FB_TcpMsgPakServer`（library 層）直接寫 7 個 GVL counter——COMM_FBs 裡唯一
  的上向依賴，第二個 socket instance 會共用 counter。改 VAR_OUTPUT struct、
  上層 mirror 進 GVL（**FB 介面變更 → 冷 reset**，跟下次 install 一起）。
- `Robot_FBs/AxisGroupManager/Update.st:52-54` 讀 `AxisGroupSM.RuntimeMs`
  ——FSM 層唯一上向引用。把 RuntimeMs 升進 GVL 即解。
- DrainHostPackets → ProcessMotionPacket 靠 PROGRAM-scope scratch
  （`PacketType`/unpacker cursor）交接，中間隔著兩個會 re-Init unpacker 的
  method，正確性靠 main body 排序。讓 ProcessMotionPacket 自己重讀 4 個 header
   欄位（每封包一次，非每 scan）即可解耦。
- 軸 roster 硬編碼 4-5 處（`AxisGroupSM.st:416-419`、`DrainHostPackets.st:161-207`）
  ——最低優先，等真的動軸配置時順手做。

**架構面乾淨的部分**（agent 明確確認）：msgpack stack 分層（GVL-free）、
AxisGroupManager FSM 本體、reply-slot helper 家族、GVL counter 刻意全域
（scripting 可觀測性）都不需要動。

---

## 3. 🟡 效能（EC_Task 1ms 預算）

前提確認：**msgpack parse + dispatch 全部跑在 EC_Task 1ms**，Comm task 5ms
只做 socket I/O + framing + ring copy。

1. **`MotionStateString` 每 scan 重建、零消費者**（`UpdateMotionProgress.st:33-40`）
   ——運動期間每 1ms 做 2×TO_STRING + 2×CONCAT。grep 全 codebase 無讀者。
   刪掉前確認 visu 沒引用。一行修。
2. **被擋 tail 封包每 scan 全量重 parse**（`DrainHostPackets.st:41-47` + dispatch 鏈）
   ——BLOCK_FOR_DIGITAL_INPUT 等 2 秒 ≈ **~10⁵ 次重複 key 比對**，全在 EC_Task、
   且正好是運動進行時。修法：以 tail-generation counter 為 key cache 住已 parse
   的 header（`BlockCommandId` latch 已是此 pattern 的先例）。
3. **`FindValueByPath` 每次 lookup 線性重掃整個 map**（`FindValueByPath.st:59-73`，
   每個不中的 key 都 MemCpy 進 `sLastString`）——G1 ≈ 100 次、M4 ≈ **300 次**
   key unpack/compare/skip in 一個 scan。修法二選一：(a) 單趟掃描填 struct
   （`pin_op_seq` reader 已證明團隊接受此風格）；(b) 低touch：resume-cursor
   （host 發 key 順序穩定 → 順序讀變 O(1) 攤提）。**先量 EC cycle time 再決定**
   （若 M4 全 parse < 100µs 就降級為順手做）。
4. **Comm task 每 tick 收/發各 1 封包 = 200 pkt/s 硬天花板**
   （`FB_TcpMsgPakServer.st:219-247`、`TCP_MSGPAK_Server.st:83-155`）。
   基線 25 pkt/s 沒事；10x 負載時 egress 先飽和（每 request ≥1 reply + events）。
   burst K 個命令的 RTT 尾巴 ≈ K×10ms。修法：egress 先做 bounded drain loop
   （~10 行）；ingress 要動 FB 介面，先跑 100/250 pkt/s fuzz 看
   `ReMpDropCount`/RTT 再決定。**若 ingress loop 落地，`minfo_buf` 6 slots
   會變成第一個溢位點，要同批加大。**
5. 診斷用的 `MC_ReadCoordinateTransform` ×2 每 scan Enable
   （`ProcessFlyEventsAndIo.st:281-306`）——先線上 force FALSE 量 cycle time
   差異，<10µs 就不做。

**效能面乾淨的部分**：copy path 已近最優（reply 直接 pack 進 ring slot、
TCP 直接從 slot 送、零中間複製）；FlyEvent loop、supervisor、事件 emit 的
gate 都已到位。reMP 128 slot 的 oversize 有文件化理由，**不建議縮**。
一個順手修：`FB_DataBuffer/MoveLeft.st:18` 對 overlapping 區域用 `MemCpy`
（被註解掉的 `MemMove` 才是對的）——正確性 nit 非效能。

---

## 4. 🟢 可讀性（誤導優先）

1. **GA_EV 驗證註解宣稱的覆蓋是假的**（`DrainHostPackets.st:96-99`）：
   `1..11` range check 沒排除 sparse enum 空洞（1/3/5 是 tombstone）與
   internal 事件——host 打 `ev=10 (EV_OK)` 可以**跳過狀態的硬體 handshake**
   （如 Powering→Powered），打 1/3/5 得到 `ack:TRUE` 但 FSM no-op。
   改成明確 membership check（host-postable 只有 2,4,6,7,8,9——與 memory
   `plc_event_numeric_values` 一致）。
2. **Stale「mandatory IF TRUE/IF FALSE」警告**（`AxisGroupSM.st:402`）：
   指涉的 `IF FALSE` 已於 06-23 刪除（`:765-770` tombstone 自己說的），
   剩下的 `IF TRUE` wrapper 沒人解釋為何 mandatory。新人看到的第一條註解
   就是個既過期又嚇人的禁令。
3. **`GVL.reMP_info_SIZE := 32` 零讀者且與實際容量 128 矛盾**（`GVL.st:9` vs `:21-28`）
   ——就在文檔化最完整的 ring 宣告旁邊，headroom 心算會錯 4 倍。刪除。
4. **DI 路徑 dead-wired 但讀起來像活的**：`DigitalInputPointer` 唯一賦值被註解
   （`AxisGroupSM.st:738`）→ BLOCK_FOR_DIGITAL_INPUT 永遠只能 timeout、
   GET_DIGITAL_INPUT 恆 0，三個消費點只有一處有註解。加一條宣告處的醒目註解
   + 刪 unused `DigitalOutputPointer` + rename `TmpDigitalOutputBits`
   （存的是 **input** diff，名字說 output——純謊言名）。
5. **假常數**：`PENDING_RETRY_MAX_SCANS`/`HEARTBEAT_INTERVAL_MS`/`DUPE_RING_SIZE`
   是 UPPER_SNAKE 但在普通 `VAR`（檔案明明有 `VAR CONSTANT` 區）。
   `DUPE_RING_SIZE:=16` 旁邊是硬編碼 `ARRAY[0..15]`——改一個不改另一個
   = 靜默越界。GVL 的可 force 偽常數群補一行「mutable by design」。
6. `Update(uidx)` 的 `-1`=enter/`+1`=exit 極性反直覺，三個具名常數解決。
7. Magic number 批次：`FlyEventAvailableCount > 3` 兩處（為何保留 3 slot 沒說）、
   `HomingDoneDebounceCD := 100` 兩處可 drift、`RETRY_COOLDOWN_G4_MS=100` vs
   `_G1_MS=101`（typo 還是刻意 de-phase？註解沒講）。
8. Dead code 批次（一個 cleanup commit 收掉）：`DBG_BLOCK`、`DBG_PIN`（magic
   mask `16#4000`）、`FlyEventActType.RETURN_ACT := 2342`、`FlyEventData.PinGrp`、
   `TaskInstructionBufferSize`、**`WebServer_SIMPLE.st`（教學貼上碼，還硬綁
   192.168.3.6:8123——正是 TCP_MSGPAK_Server 註解記錄修掉的那類錯誤）**、
   `MsgPakInfoWrapup.st:22` 的孤兒 TODO。

**可讀性面乾淨的部分**：整體註解品質「異常地好」（agent 原話）——drop-newest
理由、edge-trigger 修正、A-axis /10 workaround 都有帶日期的 why-comment 與
交叉引用；`FB_Homing` 的 phase 註解是範本級。

---

## 5. 建議行動順序

**第一批（正確性、多為 online-change 安全的小改）：**
1. §1.1 MOVE_DONE abort gate——保護 resume 資料的正確性，最高優先。
2. §1.2 三處 silent-drop 補 NAK + counter（含刪 `MotionOutputPin` 殘留條件）。
3. §1.3 id sentinel 統一。
4. §1.5 G4 ACK 時序比照 G1。
5. §1.4 fly-bind fault 改直呼 `Transition(EV_ERROR)`。
6. §4.1 GA_EV membership check。
7. §2.3 第一條：ProcessMotionPacket 改用現成 slot helpers（順帶修 counter 灌水）。

**第二批（結構，中改）：**
8. §2.1 Coord1CommitBind / Coord1LatchWindowError 統一。
9. §2.2 ResetDiagCounters() 單一化。
10. §1.6 SMC FB error 路徑補齊（與 8 同區域，可同批）。
11. session epoch 取代 ST_CHG 邊緣清理（§1.4 同根）——涉及 FB 加 VAR，
    走 `stop_then_install.py`。

**第三批（效能，先量再動）：**
12. §3.1 MotionStateString 刪除（一行，隨時可做）。
13. 量 EC cycle time（task monitor + 飽和 G1/M4 流）→ 決定 §3.2 header cache
    與 §3.3 parse 重構做不做。
14. §3.4 egress drain loop；ingress 等 100/250 pkt/s fuzz 數據。

**隨手批（任何 planned stop 順帶）：**
15. §4 可讀性批次（stale 註解、假常數、dead code、WebServer_SIMPLE 刪除）。
16. §1.7 DigitalInputPointer bounds clamp（接 IO 前必做）。

**刻意不做**（agent 確認符合團隊原則）：SYS dispatch ELSIF 鏈不拆
per-command micro-method；reMP ring 不縮；msgpack stack / FSM 本體 /
slot helper 家族不動；不把任何 business 語義搬進 PLC。
