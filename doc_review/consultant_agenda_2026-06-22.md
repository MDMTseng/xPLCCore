# 顧問會議 agenda — 故障可恢復方向確認（2026-06-22）

> 配套文件：[`architecture_review_2026-06-22.md`](./architecture_review_2026-06-22.md)
>
> 該 review 的 §5 列了 4 條工程組決策題；本文補完隱含假設、把 12 道題排了優先序。每題格式：**問題 / 為什麼問 / 預設方案 / 等對方回答的事**。
>
> 開會前可以先把「必問」四題印出來；其餘按時間放。

---

## 必問（直接決定 §4 三條提案能不能動工）

### A.1 Reel 原點是否跨電源穩定？

- **問題**：SoftMotion 軸位置在 PLC 掉電後是 0、retained、還是必須 home 一次才重建？
- **為什麼問**：[`architecture_review_2026-06-22.md`](./architecture_review_2026-06-22.md) §4 ① 提案「reel 軸位 → 格號」假設 origin 在 PLC 重啟後仍然準。若每次重啟都得重 home → 無人值守 resume 就不成立。
- **預設方案**：若 retained → 直接用；若不 retained → 加一個「reel zero capture」步驟在 home 流程裡，把當下 raw count 存到 retained var 當 origin。
- **等對方回答**：drive 的位置 retain 行為、home 是否同時 capture origin。

### A.2 Real EC encoder (Phase 0) 上線時程？

- **問題**：真實 EtherCAT encoder 何時接好？同步問：synthetic monotonic pulse 跟真 encoder 切換時，origin 是否會跳？
- **為什麼問**：Phase 6 conveyor pick 跟 §4 ① 的 reel 位置都 ride on encoder。Phase 4 step 3 是在 synthetic pulse 上完成的 ([`doc/3-subsystems/conveyor_pick.md`](../doc/3-subsystems/conveyor_pick.md))。切換的時候不光是換訊號源，origin 對齊也得處理。
- **預設方案**：切換時新增一支 calibration probe，比對切換前後對同一 reference 物的 pulse 值差，把差值寫進 origin offset。
- **等對方回答**：時程；硬體是否已採購；是否需要我們先寫切換校驗 probe。

### D.11 §4 ③「resume 時最多丟一顆料」的取捨可接受嗎？

- **問題**：不加 part-present 感測器、改用「resume 一律先 blow-off 到 toss bin」這個方案，業務可接受嗎？
- **為什麼問**：這是整份 review 唯一的硬體 BOM 分岔。決定整顆機台採購、安裝、線材、機構面的工。
- **預設方案**：先不加硬體（簡單、便宜、機構不動）；若 toss-per-resume 不可接受，再回頭加 vacuum/part-present DI。
- **等對方回答**：每天可接受的 toss/resume 次數上限；資安/合規方面是否有「不可丟料」要求。

### C.8 Production plan 本身存哪？　✅ 已預查（見 [`prep_findings_2026-06-22.md`](./prep_findings_2026-06-22.md)）

> **預查結論：plan 本身與游標全在 renderer 記憶體，且 plan 邊跑邊就地消耗 → crash 後重建不回來。會上只需確認方案：plan 落地磁碟 + scratchpad 存 `plan_id`+index。同時釘死 D.3：plan 是結果驅動、游標與 reel 格號分岔，scratchpad 無法被軸位反推取代。**

- **問題**：scratchpad 存的是 **游標**（plan 進度）；production plan **本身**（這批要填什麼、NG 怎麼路由）目前住哪？renderer 記憶體？磁碟檔？host shell？
- **為什麼問**：scratchpad 設計只解了「我做到第幾步」，沒解「我在做哪份 plan」。若 plan 也在 renderer 記憶體 → crash 後連 plan 都重建不回來，scratchpad 也沒意義。
- **預設方案**：plan 本身落地到 host 磁碟（renderer 啟動時讀檔），scratchpad 只存 plan 內的 index；plan 內容變更要 bump plan_id 寫進 scratchpad，resume 時對得起來。
- **等對方回答**：plan 來源（人工輸入 / MES / 排程檔）；plan 變更頻率；plan 是不是已經有外部持久層。

---

## 該問（會議釘完可以直接動工）

### B.5 PLC boot epoch counter

- **問題**：PLC 重啟後 scratchpad retained 還是清零？若 retained，renderer 怎麼分「我寫的還在」vs 「PLC 重啟過、狀態其實壞了」？
- **為什麼問**：scratchpad-on-resume 的整個信任模型。沒 boot epoch → renderer 可能 resume 到一個其實已經因 PLC 重啟而 reset 的位置。
- **預設方案**：PLC 加 `BootEpochCount : UDINT` retained，啟動 +1，每包 `GET_MACHINE_STATE` 都回。renderer 寫 scratchpad 時把當下 epoch 一起寫進；resume 時 epoch 不符 → scratchpad 視為過期、走 cold-start 流程。
- **等對方回答**：retained var 的可靠性（CODESYS 文件 vs 工程組現場經驗）。

### B.6 Intent vs done — 寫前還是寫後？

- **問題**：scratchpad 寫 **「即將推進 reel」** 還是 **「reel 已推進」**？兩者差在「指令送出到 ACK 之間死掉」的處理。
- **為什麼問**：write-ahead（[`architecture_review_2026-06-22.md`](./architecture_review_2026-06-22.md) §4 ②）的核心問題。沒講清楚就會 implement 不對。
- **預設方案**：寫 `(intent, movement_id_at_intent)` 配對 — 寫意圖時順手記錄當下 `LastAcceptedMovementId`。resume 比對「intent 寫了沒」vs PLC `LastCompletedMovementId`：
  - intent 有寫 + `LastCompleted >= intent_mid` → 動作完成
  - intent 有寫 + `LastCompleted <  intent_mid` → 不確定，走 §4 ③ blow-off 流程
  - intent 沒寫 → 還沒打算動，安全
- **等對方回答**：PLC `LastAcceptedMovementId` / `LastCompletedMovementId` 在 idle 狀態下會不會被 reset；intent slot 大小（一筆？環形 N 筆？）。

### A.3 FlexBowl 復原語義

- **問題**：renderer 死的時候 FlexBowl 振動中 → 它會繼續振、自己停、還是要 PLC 來砍？序列埠斷線後行為？
- **為什麼問**：review 整篇沒提這條 I/O。它不在 PLC 安全契約底下（PLC 看不到 FlexBowl），但它是生產流程的一部分 — renderer hang 的時候它在做什麼會影響 resume 決策。
- **預設方案**：renderer 啟動時不假設 FlexBowl 在已知狀態，先發一次 stop + status query 對齊。
- **等對方回答**：FlexBowl 的 watchdog 行為；序列埠 timeout 後它自己怎麼處理。

---

## 可問（時間夠再問；事後可以非同步處理）

### B.4 Scratchpad 寫入 atomicity

- **問題**：一個 SYS 寫包是否保證在一個 EC scan 內 commit？renderer hot-swap 是否可能讀到半寫狀態？
- **預設**：1 個 scan = atomic（PLC 是單執行緒 ST，看單一 SYS 指令 → 同 scan 內完成 unpack + write）。但要確認 SYS drain loop 上限 16/scan 跟我們的寫頻不會撞。

### B.7 Scratchpad schema / 容量規劃

- **問題**：要存哪些欄位（`plan_id`, `plan_index`, `cycle_count`, `intent`, `intent_mid`, `last_vision_pulse`, ...）？固定 N bytes 還是可擴充？
- **預設**：先列一份 v1 schema（128 bytes 內），用 versioned struct，未來欄位加在尾巴；版本不符就走 cold-start。

### C.9 Vision 再拍冪等性

- **問題**：resume 後重拍前一顆料，VP 是 stateless reissue 還是有 internal buffer 狀態？
- **預設**：假設冪等；若不是，要在 resume 流程加 vision reset 指令。
- **影響**：[`architecture_review_2026-06-22.md`](./architecture_review_2026-06-22.md) §4 ③「最多丟一顆料」的成本估算。

### C.10 多 client / hot-swap socket handoff　✅ 已預查（見 [`prep_findings_2026-06-22.md`](./prep_findings_2026-06-22.md)）

> **預查結論：換手是手動的，無自動 graceful transfer。agenda 描述正確，預設方案可直接採用，會上只需確認排程。**

- **問題**：PLC TCP 8125 單 client。hot-swap 時 new renderer 怎麼搶 socket？
- **現況**：已知要 `remote_ctrl` 介入（見 memory `ui_plc_single_client_arbitration`），但這條沒寫進 W1 設計。
- **預設**：在 W1 補一個「graceful client transfer」流程；舊 client 收到 transfer 指令後自己 disconnect。

### D.12 §5.4 W1 沒自動化迴歸 — 優先序

- **問題**：先補完 W1 自動化 crash-and-relaunch 測試，**再**做 §4 三個改動？還是並行？
- **預設**：先補測試。否則 §4 改動會在沒 baseline 的情況下進，迴歸風險不可控。
- **等對方回答**：工程組工時調度。

---

## 時間預算

| 區段 | 預計 |
|---|---|
| 必問 4 題 | 每題 5–8 min，合計 25–35 min |
| 該問 3 題 | 每題 4–6 min，合計 15–20 min |
| 可問 5 題 | 視時間，每題 2–3 min |
| Q&A / Buffer | 10 min |

合計 **45–80 min**。建議排 60 min 議程；超時的可問項改非同步 issue。

---

## 開會前可先做的功課（會議當天能更快收斂）

1. **A.1 retained var 預驗**：scriptable 探 PLC retained var 行為，把實測結果帶到會議。
2. **C.8 plan 來源摸底**：grep renderer 看 plan 怎麼初始化、會不會落地。
3. **A.3 FlexBowl 文件**：把 FlexBowl 廠商手冊的「serial timeout 行為」一節先拉出來。

開會結論落地到 `doc_review/decisions_<date>.md` 或直接 patch [`doc/1-concepts/solidification.md`](../doc/1-concepts/solidification.md) 對應 workstream。
