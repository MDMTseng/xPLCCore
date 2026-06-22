# 決策紀錄 — 故障可恢復方向（2026-06-22）

> 配套：[`architecture_review_2026-06-22.md`](./architecture_review_2026-06-22.md)（為什麼）／
> [`consultant_agenda_2026-06-22.md`](./consultant_agenda_2026-06-22.md)（問了什麼）／
> [`prep_findings_2026-06-22.md`](./prep_findings_2026-06-22.md)（會前預查）
>
> 本文是 12 題的內部決議結果。會議上需要顧問確認的剩 3 項，列在 §C。

---

## A. 決策一覽

| ID | 主題 | 決策 |
|---|---|---|
| A.1 | Reel 原點跨電源 | retained 假設成立；reel 軸位加進 `GET_MACHINE_STATE` |
| A.2 | Real EC encoder (Phase 0) | **暫不排程** — conveyor pick 視為概念測試，已收 |
| A.3 | FlexBowl 復原 | fail-safe：renderer 啟動無條件送 stop + status query（假設振動盤無限振） |
| B.4 | scratchpad 寫 atomicity | 寫一支 burst-write probe 驗一下 |
| B.5 | retained var 種類 | 全 retained（假設 PLC + 電源絕對穩定，不考慮換板 / 斷電） |
| B.6 | resume intent 對賬 | 採三狀態：未完成 → blow-off；完成但 vision 不確定 → 丟回 feeder；其他正常 |
| B.7 | scratchpad schema | strawman v1（~32 bytes，含 plan_id / index / intent / mid / vision_pulse / boot_epoch） |
| C.8 | Plan 存哪 | plan 落地磁碟、scratchpad 只存 cursor + plan_id |
| C.9 | Vision 強制重拍 API | VP 加一支 force-retrig 介面（同顆料） |
| C.10 | Hot-swap socket handoff | 加 graceful client transfer 流程，排在 §4 三條改動**之前** |
| D.11 | Resume 吸嘴策略 | resume 一律先 blow-off 到 toss bin，接受每次 crash 最多丟一顆 |
| D.12 | W1 自動化迴歸 | 先補測試、再做 §4 三條改動 |

---

## B. 關鍵決策的下游影響

### B.6 + 既有 production 邏輯統一

「丟回 feeder」原本就是 production 既有的「可能誤判」處置（例：側邊量測看到疊料 → 丟回 feeder 重來，盡量讓 toss 的都是真 NG）。

把這條既有邏輯直接套到 resume 狀態 4：

| Resume 狀態 | intent 寫了？ | `LastCompletedMovementId` | Vision 結果 | 處置 |
|---|---|---|---|---|
| 1 | 沒寫 | — | — | 安全，照常 resume |
| 2 | 寫了 | `< intent_mid` | — | 動作未完成 / 不確定 → **blow-off** (D.11) |
| 3 | 寫了 | `≥ intent_mid` | 有 buffered result | 動作完成、plan 已消耗 → **接著做** |
| 4 | 寫了 | `≥ intent_mid` | 無 result | 動作完成、量測結果未知 → **丟回 feeder**（套用 production 既有「可能誤判」分類） |

→ resume 邏輯不需要長出一套新處置 enum，跟既有 production 共用詞彙。

### B.7 schema 確定形態

```
TYPE Scratchpad_v1 :
STRUCT
    schema_version    : USINT;   // 1
    plan_id           : UDINT;   // 對應 host 磁碟 plan 檔
    plan_index        : UDINT;   // plan 內進度，host-defined
    intent_kind       : USINT;   // 0=idle 1=advance_reel 2=pick 3=bowl_back ...
    intent_movement_id: UDINT;   // 寫 intent 當下 LastAcceptedMovementId
    last_vision_pulse : UDINT;   // 上次 vision trig pulse，給 NG audit + resume 對賬
    boot_epoch        : UDINT;   // PLC boot counter，每次 cold start +1
    reserved          : ARRAY[0..7] OF BYTE;
END_STRUCT
END_TYPE
```

C.8 走 plan 落地磁碟，所以 scratchpad 不存 plan 本體；32 bytes 上限綽綽有餘。`intent_kind` 加 `bowl_back` 對齊 B.6。

### C.8 落地路徑

- plan 檔案位置 / 命名 / 版本 — 待會議釘細節（host 端，工程組內部）
- plan 變更時 `plan_id` 必須 bump；renderer 寫 scratchpad 時必須帶當下 `plan_id`
- resume 時比對 scratchpad `plan_id` vs 磁碟 plan 檔 `plan_id`：不符 → cold-start，符 → 用 `plan_index` 接著走

### C.10 排程影響

graceful client transfer 在 §4 改動前，意味著 §4 的 acceptance test 可以拿「殺舊 renderer → 新 renderer 自動接手」當測試手段，不需手動操作。D.12 自動化測試直接 ride on 它。

### D.12 順序

排程順序：
1. **W1 自動化迴歸 (D.12)** — crash-and-relaunch 測試 baseline
2. **C.10 graceful client transfer** — 給 D.12 測試自動化用
3. **A.1** — reel 軸位加進 `GET_MACHINE_STATE` (§4 ①)
4. **C.8 + B.7** — plan 磁碟落地 + scratchpad 介面 (§4 ②)
5. **D.11** — resume blow-off 流程 + C.9 VP retrig 整合 (§4 ③)
6. **B.4** — burst-write probe (可插任意時點)

A.3 FlexBowl fail-safe 是 renderer 啟動 housekeeping，跟以上獨立，任何時候做。

---

## C. 留待會議確認 / 確認後動工

剩 3 項需要外部對齊：

### C-i. A.1 retained 實測

**狀態**：方向已決（假設成立、選 A），但需要對 live PLC 跑一支 probe 驗證軸位 retained 行為。

**Action**：寫一支 cold/warm reset 前後讀 axis position 的 probe，會議前先跑。**Owner**：我這邊可直接動，不用會議排。

### C-ii. C.9 VP force-retrig API

**狀態**：方向已決（要加），實作 owner 待確認。

**Action**：會議上對 VP 維護方確認誰加、何時加。**Blocker for §4 ③**：沒這支 API 之前 resume state 4「丟回 feeder + 後續重拍」流程做不完整。

### C-iii. D.11 toss 預算合規性

**狀態**：方向已決（接受每次 crash 丟一顆），但每天可接受上限 + 報廢 / 合規追溯影響需業務面確認。

**Action**：會議上跟業務 / 品保確認可接受 crash 頻率上限。若超出可接受值 → 回頭考慮加 part-present 感測器。

---

## D. 衍生 action items（給 solidification.md 用）

| Item | 工項 | Workstream | 依賴 |
|---|---|---|---|
| 1 | 寫 cold/warm reset retained var probe | W1 follow-on | — |
| 2 | 補 W1 自動化 crash-and-relaunch 測試 | W1 | C.10 |
| 3 | PLC 加 graceful client transfer SYS 指令 | W1 | — |
| 4 | `MachineState` 加 reel 軸位欄位 | §4 ① | item 1 |
| 5 | 設計 plan 磁碟格式 + plan_id 流程 | §4 ② host | — |
| 6 | PLC 加 `Scratchpad_v1` retained struct + SYS read/write 指令 | §4 ② PLC | — |
| 7 | renderer 整合 scratchpad write-ahead + resume 對賬 | §4 ② host | items 5, 6 |
| 8 | VP force-retrig API | §4 ③ | C-ii |
| 9 | renderer resume blow-off 流程 | §4 ③ | items 7, 8 |
| 10 | scratchpad burst-write probe | B.4 | item 6 |
| 11 | FlexBowl fail-safe stop on startup | A.3 | — |

實作順序見 §B / D.12。

---

## E. 影響其他既有文件

完成上述 action items 之後，要回頭更新：

- [`doc/1-concepts/solidification.md`](../doc/1-concepts/solidification.md) — 加新 workstream「W6 Recovery」或併進 W1
- [`doc/2-contracts/protocol.md`](../doc/2-contracts/protocol.md) — `MachineState` 加 reel 欄位、新增 `SCRATCHPAD_WRITE` / `SCRATCHPAD_READ` SYS 指令、新增 `CLIENT_TRANSFER` 流程
- [`doc/1-concepts/coupling_invariants.md`](../doc/1-concepts/coupling_invariants.md) — 加 scratchpad schema_version、plan_id 跨層耦合
- [`PROJECT.md`](../PROJECT.md) — capability matrix 加「故障可恢復」列，狀態 🚧
