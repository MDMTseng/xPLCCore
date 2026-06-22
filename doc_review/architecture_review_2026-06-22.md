# 架構 Review — xPLCCore（2026-06-22）

> 給工程組的架構合理性檢視。聚焦一個核心問題：**故障之後，生產流程能不能接著做，而不是報廢重來。**
>
> 本文自包含，不需要其他背景即可閱讀。引用的程式碼位置都標了檔案與行號，可直接核對。

---

## TL;DR

架構方向**合理且專業**，分層正確（PLC 管 motion/IO/safety、renderer 管業務協調），安全契約紮實。

但它目前**只蓋了一半**：
- ✅ **安全那半很完整** —— renderer 死掉，PLC 會把機器凍結在已知安全狀態。
- ❌ **可恢復那半基本沒做** —— renderer 死掉後，PLC 沒有足夠資料讓重啟的 renderer 知道「生產流程做到哪一步」。在製的那顆料、那一格載帶，現況只能報廢/重做。

這不是設計錯誤，是當初為了「一人維護、moving parts 要少、現場可 hot-swap」而做的取捨，現在「生產可恢復」這個需求把帳單翻出來了。

**建議方向：讓 PLC 當恢復錨點，renderer 維持可拋棄。** 用三個小的、不違反 generic-PLC 原則的改動就能補上（詳見 §4）。

---

## 1. 架構總評（站得住腳的部分）

| 項目 | 評價 |
|---|---|
| **Host-authority 安全契約** | PLC 不信任 host 會活著。心跳階梯 1s ping → 3.5s host 自判 stale → 5s PLC 跳 Error 有層次，網路抖動可恢復而不誤停。已核對程式碼一致。 |
| **No silent drops 硬契約** | 每個 request 都回 `ack` 或帶 `err` 的 NAK。工業通訊最容易省、省掉最難 debug 的東西，他們列為不可協商。正確。 |
| **Generic-PLC 原則** | 業務邏輯留 renderer，PLC 只做 motion/IO/safety，而且這條線被反覆守住（W2 物料流、vision↔PLC 直連都明確 reject 並記錄理由）。對一人維護尤其關鍵。 |
| **並發模型** | EC task 1ms 管 motion、Comm task 5ms 管 TCP，RTT 邊界可推導。硬即時與通訊解耦，標準做法。 |
| **Coupling invariants 專章** | 把跨層必須一起改的耦合顯式登記。這規模專案很少見的紀律。 |

文檔品質本身（4 層 reading-order、每個 workstream 都有 acceptance test 與 rejected-options 紀錄）就反映決策是經過推敲的。

---

## 2. 評估標準的轉換：safety ≠ resumability

這是本次 review 最關鍵的一點。

現有文檔的安全契約解的是「**機器別撞壞**」。但生產上真正在乎的是「**故障後能不能接著做**」——這是**不同的問題**。

證據：reconnect 的設計明白寫著「offer resume **or** route to a safe restart」，但實際做出來的只有後者——`ControlPage` 把 tab 強制導回 Welcome。對在製的料來說，那就是報廢/重做。

> **核心張力（工程組需理解）：** generic-PLC 原則（對可維護性是對的）和「可恢復」直接衝突，因為它*保證了* reel 第幾格、夾爪佔用這些**生產關鍵的順序狀態永遠不會待在耐久的那一側（PLC）**。
>
> 你不可能同時要「PLC 保持 generic」+「host 易失/無狀態」+「生產可恢復」。三選二。需求逼著**某一側必須長出耐久層**。

---

## 3. 核心發現：故障恢復的三個洞（已在程式碼證實）

問題：**renderer 死了之後，PLC 有沒有足夠資料讓重啟的 renderer 知道之前的狀況？**

答案：**今天沒有。** `GET_MACHINE_STATE`（見 [`lib/protocol.ts` MachineState](../lib/protocol.ts)）回的是*機器*狀態（FSM、fault、最後 `movement_id`、`coord_set`），不是*生產*狀態。具體三個洞：

### 洞 1 — Reel 邏輯格號推不出來
- snapshot 裡的 `axes_state` 是 DS402 *狀態機*狀態，不是*位置*。
- 取位置要用 `READ_LATEST_CMD_LOCATION`，但它只回手臂 X/Y/Z/A/B/C；**reel 是獨立第 4 軸（`reelpullmotor`），位置目前任何指令都拿不到。**
- 後果：「載帶推到第幾格」這個**報廢主因**，現況無從恢復。

### 洞 2 — 夾爪有沒有料：量不到（硬體缺口）
- 輸入腳位（[`components/CalibPage.tsx` IO_Pins.I](../components/CalibPage.tsx)）只有 `ReelLacking` / `PackedReelNoProtrusion` / `ReelTapeHTension` / `ReelPressRollerInPlace`——**全是載帶感測器，沒有真空 / part-present。**
- 吸嘴用 `Nozzle_suck` / `Nozzle_blow` *輸出*控制，但**沒有回授輸入**確認「料真的還吸著」。
- 後果：崩潰後連「吸嘴上還有沒有料」都驗不了。

### 洞 3 — renderer 的生產游標：沒地方放
- snapshot 沒有 host scratchpad 欄位。
- `movement_id`（PLC 給的單調序號）算半個麵包屑，但「movement_id ↔ 業務步驟」的映射活在 renderer 記憶體，跟著一起死。

---

## 4. 建議方向：PLC 當恢復錨點，renderer 維持可拋棄

### 為什麼是這個方向（而不是把 orchestrator 搬出 renderer）

關鍵洞察：**hot-swap 和 crash 對「狀態」來說是同一件事**——in-memory closure 兩種情況都會死。

所以為「PLC 持有恢復資料」設計，會讓 hot-swap *更好用*（swap 完直接「讀 PLC → 接著做」，不必重跑整批）。**現場可 hot-swap 開發**這個原始需求，和**生產可恢復**，其實是同一個答案，互相加分。

> 附帶結論：把 orchestrator 搬到 Electron main process **不適合**——main process 改了通常要重啟，正好破壞 hot-swap。orchestrator 留在 renderer 是對的。

### 三個改動（皆不違反 generic-PLC）

**① 把 reel 軸絕對位置加進 `GET_MACHINE_STATE`。**
純物理量，PLC 照樣 generic。renderer 自己換算 `格號 = round((pos - origin)/pitch)`，PLC 不需要懂「格」。

**② 加一個 host scratchpad 到 snapshot。**
PLC 存一小段 renderer 寫進去的不透明整數（一個 SYS 指令寫、在 `GET_MACHINE_STATE` 回讀），**PLC 從不解讀它**。renderer 在每個不可逆動作*之前*寫游標（plan index、cycle 數、「即將推進 reel」的意圖旗標）。這就是最小耐久日誌，存在 PLC 上（撐過崩潰*也*撐過 hot-swap），renderer 維持無狀態。

> **澄清（重要，避免誤會違反原則）：** 這**不違反** generic-PLC。原則禁的是 PLC *去推理*業務，不是禁 PLC *存放*不透明位元組。**存放 ≠ 擁有**——PLC 變成耐久暫存器，不是長出大腦。
>
> 配上 write-ahead 順序（先寫 scratchpad 意圖、動作帶 `movement_id`），恢復時比對「scratchpad 說我要推 reel」vs「PLC 最後完成的 `movement_id`」，連「指令送出到確認之間死掉」的原子邊界都能補上。

**③ part-present 感測器 —— 這是唯一的真分岔（需工程組決策）。**
- **加硬體**：真空 / part-present DI，generic 回報，resume 能*自動驗證*在製那顆。
- **不加硬體**：resume 時一律假設吸嘴可能有料 → 先 blow-off 到固定 toss bin → 從乾淨狀態起步。代價最多一顆料，**不是一整捲載帶**。

### 恢復流程長這樣

重啟 / hot-swap 後的 renderer：
```
讀 GET_MACHINE_STATE
  → { FSM 狀態, reel 位置→格號, scratchpad 游標, 最後 movement_id }
  → 對賬（比對意圖 vs 已完成）
  → 接著做
```
**不需要 host 端寫任何磁碟檔**，renderer 完全可拋棄——正好就是要保留的開發體驗。

---

## 5. 待工程組決策 / 確認的問題

1. **part-present 感測器要不要加？**（§4 ③）決定 resume 能全自動還是半自動。這是唯一的硬體分岔。
2. **reel 軸位能不能從現有 SoftMotion 軸讀出？**（§4 ①）決定第 ① 點的成本。
3. **production plan 語義**（負/正陣列編碼）目前未文檔化。若 plan 游標是「位置索引」就能從 reel 格號反推；若是「結果驅動」（NG 被 toss、不消耗格號），則游標與格號會分岔，scratchpad（§4 ②）就是必要的。請工程組確認語義。
4. **W1 恢復路徑沒有自動化測試。** 安全契約靠手動 live probe 驗證，`ControlPage` 的 reconcile-on-reconnect 只 code-reviewed、沒跑過完整 crash-and-relaunch。對一個把「可崩潰恢復」當核心賣點的架構，這條路徑該補自動化迴歸測試。

---

## 附錄 A — 順手修掉的一個文檔錯誤

[`doc/1-concepts/coupling_invariants.md`](../doc/1-concepts/coupling_invariants.md) 原寫 `UI_HEARTBEAT_TIMEOUT_MS := 10000`，但實際 [`GVL.st`](../codesys_code/Application/GVL.st) 是 `5000`（architecture.md / solidification.md 也都是 5000）。這是個 load-bearing 的安全數字，已改成 5000。

---

## 附錄 B — 本文未深入、但值得注意的次要風險

- **renderer 是單點承重牆**：三條 I/O（PLC / Vision / FlexBowl）+ 整個 job sequence 壓在單執行緒 JS。安全面有 PLC 兜底，但「UI 必須保持前景，否則 Electron 節流 → 心跳逾時 → PLC 誤跳 Error」是個脆弱耦合。
- **`runAllObjects` 約 2400 行單一函式**，state-machine 重寫（W4 #9）還 Open，被 cycle-time baseline 卡住。最大的 renderer 結構債。
- **「No silent drops」有幾處自我違反**：`M4 coord1_bind` 註冊閘門條件不符時「ack 但 silently dropped」；Delta workspace Z>0 的 G1、SMC 虛擬軸首個 G1 後 self-halt，都是「回 ack 卻沒動作」。建議至少 bind 閘門改成帶 `err` 的 NAK。
- **無框架的 msgpack-over-TCP** 依賴串流自界定，對半開連線 / 部分寫入較敏感（已有 idle watchdog、drop counter 緩解，靠 4hr fuzz baseline 守護）。
