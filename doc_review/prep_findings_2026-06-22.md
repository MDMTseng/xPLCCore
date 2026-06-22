# 會前預查結果（2026-06-22）

> 對應 [`consultant_agenda_2026-06-22.md`](./consultant_agenda_2026-06-22.md) 的「開會前功課」。
> 這裡放**已用程式碼證實**的答案，讓會議只需確認、不需重查。引用都標了檔案行號。
>
> 狀態圖例：✅ 已查證 ｜ ⏸ 需環境/文件（會上指派）

---

## ✅ C.8 — Production plan 存哪、會不會落地？

**結論：plan 本身和游標都只在 renderer 記憶體，而且 plan 是「邊跑邊就地消耗的活陣列」，不是「靜態 config + 獨立游標」。**

證據：

| 事實 | 位置 |
|---|---|
| `production_plan` / `production_plan_original` / `production_plan_stageIndex` 全住在 `_this`（記憶體 closure） | [`CalibPage.tsx:127`](../components/CalibPage.tsx) |
| `setProductionPlan()` 只把陣列 copy 進 `_this`、stageIndex 歸零；來源是操作員 UI（`planSegments`），預設種子寫死 `[1,-30,445,-20,1]` | [`CalibPage.tsx:1933`](../components/CalibPage.tsx) / `:1971` |
| 唯一落地的是 `localStorage['productionPlan_recent']` —— 是「最近用過的設定」選單，**不是 live plan/游標** | [`CalibPage.tsx:1970`](../components/CalibPage.tsx) |
| plan 就地 shift/decrement：`plan[0]+=adv_count`；`plan[0]==0` 時 `stageIndex++` 並 `plan.shift()` | [`CalibPage.tsx:2558-2576`](../components/CalibPage.tsx) |

**對會議的影響：**

1. **agenda C.8 的擔憂成立** —— crash 後連 plan 本身都重建不回來（除非操作員重輸入）。所以 scratchpad「只存 index」不夠：
   - 要嘛 scratchpad 存**整個 remaining plan**，
   - 要嘛照 agenda 預設方案 —— **plan 先落地到磁碟 + scratchpad 存 `plan_id` + index**。
   - 👉 建議會上朝後者收斂。

2. **順帶釘死 D.3 / review §5.3（plan 語義）** —— plan 是**結果驅動**的（NG 被 toss、不消耗格號 → reel 不前進），不是純位置索引。
   - 代表 **游標 ≠ reel 格號**，兩者會分岔。
   - 👉 **scratchpad 是必要的**，不能靠 reel 軸位反推省掉。

---

## ✅ C.10 — 多 client / hot-swap socket handoff

**結論：換手是手動的（停舊 client → 新的連上），沒有自動 graceful transfer 協定。agenda 描述正確、預設方案站得住。**

證據：[`PluginHello.tsx`](../PluginHello.tsx) 有 `connect_tcp` / `disconnect_tcp` harness action、`setTcpClientEnabled(false)` 按鈕、socket `close` handler、reconnect 取 snapshot —— 但沒有「new client 自動踢掉 old client」的流程。與 memory `ui_plc_single_client_arbitration` 及 agenda C.10「這條沒寫進 W1」一致。

👉 預設方案（W1 補 graceful client transfer，舊 client 收 transfer 指令後自行 disconnect）可直接採用。

---

## ⏸ A.1 — Reel 原點是否跨電源穩定？

**需對 live PLC 探 retained var，需要 daemon/rig 在線 —— 程式碼層無法回答。**

可行做法：寫一支 probe 腳本，在現場跑，量「PLC cold/warm reset 前後同一 reference 物的 reel raw count」。會上指派 owner + 時間即可。

---

## ⏸ A.3 — FlexBowl serial timeout 行為

**需廠商手冊，repo 內沒有。** 會上指派 owner 把手冊「serial timeout / watchdog」一節拉出來。

---

## 一句話總結帶進會議

> plan 與游標**完全**在 renderer 記憶體、且 plan 邊跑邊被消耗 → 故障後重建不回來。
> 這讓 **§4 ② scratchpad 從「nice-to-have」升級成「方案能不能成立的前提」**，而且因為游標是結果驅動、與 reel 格號分岔，**scratchpad 無法被 reel 軸位反推取代**。
> A.1 / A.3 兩項待環境與文件，會上指派即可。
