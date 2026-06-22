# Probe results — A.1 reel retain (2026-06-22)

> 對應 [`decisions_2026-06-22.md`](./decisions_2026-06-22.md) C-i 項。
> Probe: [`codesys_scripts/jobs/templates/probe_reel_retain.py`](../codesys_scripts/jobs/templates/probe_reel_retain.py)

## 跑了什麼

對 live PLC 連跑三個情境，每個讀 `reelpullmotor.fActPosition` 前後做比對：

| 情境 | 結果 | 備註 |
|---|---|---|
| STOP + START | **RETAINED** (0.0 → 0.0) | 應用程式重啟，位置保持 |
| WARM RESET | INCONCLUSIVE | daemon socket timeout 180s — warm reset 把 scripting connection 也拖下 |
| COLD RESET | INCONCLUSIVE | 同上，前一步壞了連線 |

## 解讀

**Conclusive 的部分（STOP + START）**：
應用程式 stop 後再 start，`fActPosition` 不變。對應 **「renderer crash、PLC 繼續跑」這個最常見情境** — 位置直接可用，scratchpad-on-resume 設計成立。

**Inconclusive 的部分（WARM / COLD）**：
不是 retained 行為的問題，是 scripting daemon 配上 warm-reset 的既知行為。Memory [`daemon_plc_lifecycle.md`](../.claude/projects/c--Users-X1-Desktop-X2-5-TCP-UI-TCP-UI/memory/daemon_plc_lifecycle.md) 也提到 reset 後 comm 路徑需要重連。

**另一個解讀風險**：initial position = 0.0。retain 跟 wipe 同樣回 0.0，無法區別。要更強的證據需要：

1. 先 jog reel 到非零位置（手動或 scripted move）
2. 再跑 probe，看 STOP+START 後是否保持該非零值

對 STOP + START 已經夠用（既然 0→0 沒 delta），但 WARM / COLD 一定要非零起點才能下結論。

## 對 A.1 決策的影響

**不改變**：[`decisions_2026-06-22.md`](./decisions_2026-06-22.md) §A 的 A.1 決策仍然成立（假設 retained、加進 GET_MACHINE_STATE）。

**新增條件**：「retained」這個假設**只覆蓋 STOP+START 情境**（renderer crash + PLC 不動）。WARM/COLD reset 情境尚未實證；對應的生產情境是「PLC 應用層需 reset 才能恢復」，照經驗發生頻率遠低於 renderer crash，可以列為次階段驗證。

**建議下個步驟**：
- (a) 短期：把 STOP+START 驗證寫進 W6 Recovery workstream acceptance；
- (b) 中期：把 probe 第二版做出來 — 先 jog reel 到非零、再跑 WARM/COLD、加 daemon-reconnect 邏輯；
- (c) 長期：生產線上實際遇到 cold reset 場景再去確認（成本最低）。

## probe 本身的待修點

下版 probe 要做：

1. **初始位置非零**：在 read 之前 issue 一個 reel jog（MC_MoveRelative 1mm），確保 baseline != 0；
2. **Daemon reconnect**：WARM/COLD 後跑一個 wait + new `create_online_application` 並重試讀；
3. **超時短一點**：180s 等太久（試 30s），快速宣告失敗轉下一情境；
4. **不要連跑 3 情境**：每情境寫成獨立的子命令，避免前一情境壞 daemon 影響後一情境。

這些不在本次 commit 範圍。
