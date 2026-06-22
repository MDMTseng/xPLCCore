# 實作 Review — §4 故障可恢復（2026-06-22）

> 對 decisions_2026-06-22.md 之後落地的 13 個 commit 做的程式碼檢視。
> 配套：[`architecture_review`](./architecture_review_2026-06-22.md)（為什麼）／
> [`decisions`](./decisions_2026-06-22.md)（決議）／[`probe_results`](./probe_results_2026-06-22.md)（A.1 實測）。
>
> 範圍：commit `28aab8e`..`d9fc48a`（reel 軸位、scratchpad+boot-epoch、plan persistence、
> resume orchestrator、C.10 takeover、A.1/A.3/B.4 probe、W1 自動化測試）。
>
> 引用都標檔案行號可核對。

---

## TL;DR

**整體品質高、方向完全照決議走。** PLC 側契約落實乾淨、boot-epoch 邏輯自洽、A.1 probe 報告誠實、W1 自動化測試紮實。

但有**一個必須在上線前處理的正確性地雷**，加兩個狀態/測試缺口：

| 嚴重度 | 問題 | 後果 |
|---|---|---|
| 🔴 必修 | `movement_id` 語義 + intent 寫入時機未定義 | state-2 blow-off 偵測可能恆失效 → 重複進給/跳格 → **報廢**（與目標相反） |
| 🟡 | §4② renderer 側尚未接線（library only） | 端到端 resume 其實還不能跑；commit 訊息略誇大 |
| 🟡 | resume 純函式零自動化測試 | 防報廢核心邏輯沒被鎖住，地雷不會被 CI 抓到 |
| 🔵 註記 | movement_id 型寬 LINT vs DINT；STOP+START→cold_start 範圍；reel→格號需校正 | 長尾 / 需團隊知悉 |

---

## 1. 做得好的部分

- **PLC scratchpad 契約落實乾淨。** `SCRATCHPAD_WRITE` handler 盲存 host 欄位，只蓋 `SchemaVersion=1` + `BootEpoch`（[`AxisGroupSM.st`](../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st) 新段）。完全是「durable register, not a brain」。`Scratchpad_v1` struct 註解也明確寫「PLC NEVER interprets any field other than SchemaVersion and BootEpoch」（[`Scratchpad_v1.st`](../codesys_code/Application/APP_COMM_FBs/Scratchpad_v1.st)）。
- **boot-epoch 一次性 gate 正確。** `AppBootHandled`（非 retain，每次 boot 預設 FALSE）保證 `BootEpochCount` 每次 app boot 只 +1 一次（[`InitRuntime.st`](../codesys_code/Application/APPs/AxisGroupSM/InitRuntime.st)）。cold reset 清 retain → `SchemaVersion=0` → renderer cold_start。
- **A.1 probe 誠實**（[`probe_results`](./probe_results_2026-06-22.md)）：STOP+START 確證 retained，WARM/COLD 標 inconclusive 並說明是 daemon 限制 + 0→0 無法區分 retain/wipe，列了下版改法。無 overclaim。
- **W1 自動化測試（D.12）紮實**（[`test_plc_w1_recovery.py`](../codesys_scripts/tests/test_plc_w1_recovery.py)）：T1–T7 覆蓋 reconnect 保留 FSM/coord、心跳跳 Error、post-trip snapshot、EV_RESET、斷線不 silent-drop、protocol version。
- **文檔同步**：[`protocol.md`](../doc/2-contracts/protocol.md) 加了 `MachineState` 新欄位 + `SCRATCHPAD_WRITE`；[`lib/protocol.ts`](../lib/protocol.ts) `REQUIRED_KEYS` schema guard 同步加 `reel_pos` / `scratchpad` / `boot_epoch_now`。

---

## 2. 🔴 必修：`movement_id` 語義地雷

resume 判斷「動作完成」的核心比較（[`orchestrator/resume.ts:171`](../orchestrator/resume.ts)）：

```ts
intent_completed =
    sp.intent_kind !== IntentKind.Idle &&
    sp.intent_movement_id !== 0 &&
    machineState.movement_id >= sp.intent_movement_id;
```

兩端語義對不上，會讓 **state-2（blow-off）偵測失效**：

### 2.1 PLC 端 movement_id 不是 LastCompleted

`GET_MACHINE_STATE` 的 `movement_id` 餵的是 `GroupReadPositionFb.MovementId`
（[`AxisGroupSM.st:882`](../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st)）——是「**目前實際位置對應的 move id**」，
不是 decisions §B 表格指定的獨立 `LastCompletedMovementId`。靜止時勉強當 proxy，
但它不是設計指定的量，且執行中會在動作「**開始**」就到達目標值，而非「**完成**」。

### 2.2 intent 寫入時機未定義 → off-by-one

`writeIntent` 註解說 stamp「currently-**accepted** movement id」（[`resume.ts:97`](../orchestrator/resume.ts)），
而模組 header 又叫「write-**ahead** scratchpad」。兩者矛盾，且**正確性完全取決於呼叫端順序**：

| 呼叫端順序 | `intent_movement_id` 值 | 結果 |
|---|---|---|
| 動作*之前*寫 intent（字面 write-ahead） | 上一個已完成 move 的 id | `movement_id >= intent_mid` **恆為真** → 崩在動作中途被誤判「已完成」→ **重複進給/跳格 → 報廢** |
| 動作被 accept *之後*寫 intent（stamp 動作自己的 id） | 動作本身的 id | 正確：未執行→`movement_id < intent_mid`→blow-off |

這正是 agenda B.6「寫前還是寫後」沒釘死的點。**實作沒有 enforce，也沒文件講明**，
而這是整個防報廢邏輯的成敗關鍵。

### 2.3 建議修法

1. **釘死契約**：intent 必須在動作被 accept *之後*、執行*之前*寫，stamp 動作自己的 movement_id。
   把這條寫進 `writeIntent` 註解 + coupling_invariants.md。
2. **PLC 加真正的 `last_completed_movement_id` 欄位**到 `GET_MACHINE_STATE`
   （或明確以 read-FB id 為準並文件化其「靜止時 = 最後執行」語義）。
3. resume.ts 比較改用該 completed 欄位。

---

## 3. 🟡 §4② renderer 側尚未接線

`writeIntent` / `reconcileOnResume` / `planResumeAction` / `savePlan` / `loadPlan`
**只定義在 [`orchestrator/resume.ts`](../orchestrator/resume.ts)，無任何地方 import 使用**。
[`CalibPage.tsx`](../components/CalibPage.tsx) 仍是舊的 in-memory `resume_cycle` harness action（:1879），未改。

→ 現況：**PLC 側 live，host 側是 library/scaffolding，端到端 resume 還不能跑。**
增量推進沒問題，但 commit `f1fc7b4`「renderer side ... resume reconcile」會讓人誤判已完成。
**狀態應如實標註**（建議在 PROJECT.md capability matrix 標 🚧「scaffolding only」）。

---

## 4. 🟡 resume 邏輯零自動化測試

W1 測試（D.12）只覆蓋舊安全契約，**完全沒測新的 resume 純函式**
（`reconcileOnResume` / `planResumeAction` / movement_id 比較）。

這幾個是純函式，用假的 `MachineState` 就能單測——而它們正是防報廢核心。
§2 的地雷只要兩三個 unit test 就會當場現形。**補測試應與 §2 修法同批進。**

最小測試集建議：
- `reconcileOnResume`：scratchpad 未初始化 → cold_start；boot_epoch 不符 → cold_start；
  plan_id 不符 → cold_start；正常 → resume。
- `planResumeAction`：4 個 state（idle/未完成/完成+vision OK/完成+vision 未知）各一例，
  特別是 state-2 未完成 → blow_off（這條會抓到 §2 地雷）。

---

## 5. 🔵 小註記

- **型寬不一致**：top-level `movement_id` 打包成 LINT(64-bit)，但
  `scratchpad.intent_movement_id` 存成 UDINT/DINT(32-bit)（[`AxisGroupSM.st`](../codesys_code/Application/APPs/AxisGroupSM/AxisGroupSM.st) write handler 用 `UDINT_TO_DINT`）。
  move id 超過 2³¹ 時會 wrap。長尾、低優先，但記一筆。
- **STOP+START → cold_start 是「正確」但要團隊知悉**：任何 PLC app 重啟都 bump epoch → cursor 作廢。
  resume 只覆蓋「renderer 崩、PLC 不重啟」這個最常見情境（也正是設計目標），
  不是「PLC 重啟也能接」。A.1 probe 雖證 STOP+START 保留*位置*，但 cursor 仍刻意作廢——兩者不衝突。
- **reel_pos → 格號需 origin/pitch 校正**（host 端，尚未做；屬 Phase 7 性質）。
  目前 PLC 只吐 raw `reelpullmotor.fActPosition`，正確；換算語義在 host。

---

## 6. 建議處理順序

1. **釘死 intent 寫前/寫後 + 用哪個 movement_id 的契約**（§2 地雷本體）。
2. **PLC 加 `last_completed_movement_id` 欄位**（或文件化 read-FB 語義），resume.ts 改用之。
3. **補 resume 純函式 unit test**（§4），把上面鎖住。
4. 接著才做 §4② 的 CalibPage 整合（§3），讓端到端 resume 真的 live。

§5 三項可併入各自相關 commit 或排次階段，不阻塞上線。
