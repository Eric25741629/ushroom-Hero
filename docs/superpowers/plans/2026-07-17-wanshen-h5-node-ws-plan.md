# 萬神試煉 H5 節點化 + 狀態改用 Cocos/WS 判斷 規劃

日期：2026-07-17　目標裝置：web-001(寶兒)、web-002(暴哥)　測試裝置：emulator-5556(其實是 web_h5 後端)

> 所有節點路徑 / WS cmd / 勝敗訊號皆為 2026-07-17 在 5556 經 CDP live 實測所得，非推測。

## 1. 問題（已實測確認）

| 裝置 | Bug | 失效點 |
|------|-----|--------|
| 寶兒 web-001 | 開局獎勵遮罩 `RogueGoodsGetView` 的子節點 `Block`(1500×3000 全螢幕輸入攔截) 吞掉「開始挑戰」的**首次座標點擊** → 每局首關空燒 `_STAGE_RESULT_TIMEOUT=150s` | `weekly_trials.py:93` 座標 tap 打在 Block 上 |
| 暴哥 web-002 | `_settle_run` 用座標點右下紅箭頭 `_EXIT_ARROW_XY=(510,920)` 沒開出「結束本局」對話框 → 結算中止 → 0/10 局 | `weekly_trials.py:164/167/234` |

證據：
- CDP 實測：Block 蓋住 `interactable=true` 的「開始挑戰」；改用 `emit('click')` 穿過 Block 一發即觸發戰鬥（WS TX/RX `0x4C04`=19460，畫面進戰鬥）。
- 純程式碼/log subagent：web-001 三局「第1關→第2關」間隔實測 158/156/160s，全撞 `weekly_trials.py:122` 的 150s timeout warning。
- web-002 log：05:13:11「紅箭頭未開出『結束本局』對話框 → 中止」，完成 0/10。

共同根因：H5 用「OCR 找字 + 座標 tap」既判斷又操作，遇全螢幕遮罩 / 座標飄移就雙雙失效。

## 2. 目標與設計原則

- **H5**：判斷改用 cocos 場景狀態(node `active`/label) + WS；操作改用 `node.emit('click')`（繞過 z-order/遮罩）；主動偵測並清除遮罩。全程不靠 OCR。
- **ADB**：維持現況(OCR + 座標 u2)。原生 app 無 cocos 場景樹，OCR 是唯一可行來源。
- 不破壞 ADB 既有行為。H5/ADB 共用同一 `fight_test` 入口，內部依 `backend_kind` 分派。
- H5 driver 任一步例外 → **fallback 回現有 OCR 流程**，保證不比現況差（KISS 保底）。

## 3. 已實測的 H5 訊號清單（5556，無猜測）

進場序列（每顆都 `emit('click')`）：

| 步驟 | 節點路徑 | 觸發型態 |
|------|----------|----------|
| 主面板「開始」 | `/UIRoot/NormalView/RogueView/view/btnStart` | `on('click')`，emit 有效 |
| 選起點「開始」 | `/UIRoot/NormalView/RogueEnterView/bg/btn` | emit |
| 確認窗「是否確認開啟新一局試煉」→確定 | `/UIRoot/TopView/MessageView/boxTips/dialog/content/buttons/btnEnsure` | emit |
| 開局獎勵「進入遊戲」 | `/UIRoot/NormalView/RogueRemakeRewardView/view/btnEnter` | emit |
| 確認窗「是否確認進入本次萬神試煉」→確定 | 同上 MessageView `btnEnsure` | emit |
| 開始挑戰 | `/UIRoot/NormalView/RogueMainView/view/btnStart` | emit（穿過 RogueGoodsGetView 的 Block） |

狀態判斷（讀 cocos，取代 OCR）：

| 原 OCR 判斷 | H5 訊號 |
|-------------|---------|
| 在主面板(可開新局) | `NormalView/RogueView` active 且無 overlay view active |
| 已到關卡視圖(開始挑戰) | `NormalView/RogueMainView` active + `view/btnStart` 存在 |
| 戰鬥結果窗 | `NormalView/RogueBattleResultView` active |
| **勝利** | `RogueBattleResultView/nodeWin` active 且 `nodeDefeat` inactive（`title/txtTime`="战斗胜利"） |
| **失敗** | `RogueBattleResultView/nodeDefeat`(內含 `nodeLose`) active、`nodeWin` inactive |

遮罩層（各帶全螢幕 mask，§5 處理）：

| Overlay view | mask 子節點 | 關閉方式 |
|--------------|-----------|----------|
| `RogueGoodsGetView` | `Block`(1500×3000) | 「開始挑戰」emit 可直接繞過；或 emit 關閉 |
| `RogueBattleResultView` | `imgMask` + Label「點擊任意位置關閉」 | emit 關閉後回關卡/主面板 |
| `RogueRemakeRewardView` | — | emit `view/btnEnter`(進入遊戲) |
| `TopView/MessageView` | 對話框 | emit `btnEnsure`/`btnCancel` |

WS：`0x4C04`(=19460) TX 空請求 → RX 戰鬥結果(3390 bytes，server-authoritative)。可作勝敗權威來源，或直接發 WS 免點擊（見 §6.2 純 WS 探索）。

### 3.1 已 drain 到的 rogue WS cmd（5556 實測，待 schema 解碼確認語意）

rogue module 76(`0x4C..`) 與 module 13(`0x0D..`)。cmd = module*256+N（見 memory `reference_ws_proto_schemas`，schema 在 `docs/protocol/*.json`）。各動作觀察到的 frame：

| 動作 | 觀察到的 TX / RX cmd(len) |
|------|---------------------------|
| 主面板「開始」 | TX/RX `0x0D04`(3332) heartbeat、TX/RX `0x0104`(260)、RX `0x4708`(18184) |
| 選起點「開始」 | TX `0x0D03`(3331)、RX `0x0709`(1801,617)、TX/RX `0x0D02`(3330) |
| 確認開啟新一局 | TX `0x4C24`(19492)/`0x4C02`(19458)/`0x4C07`(19463)、RX 對應 |
| 確認進入本次試煉 | TX `0x4C26`(19494)、RX `0x4C09`(19465,1139)/`0x4C02`(19458,1993)/`0x4C01`(19457,966) |
| **開始挑戰(打一關)** | **TX `0x4C04`(19460,空) → RX `0x4C04`(19460,3390)** + RX `0x4C09`/`0x4C07`/`0x4C05`/`0x4C01` |

> 未涵蓋：結束本局/結算的 WS cmd（Phase 0 一併補抓）。

## 4. 架構

- **分派點**：`battle/weekly_trials.py::fight_test` 開頭依 `getattr(d, "backend_kind", None) == "web_h5"` 分派：
  - H5 → `battle/rogue_h5.py::fight(d, rounds)`
  - 其他 → 現有 OCR 流程（**原封不動**）
- **新檔 `battle/rogue_h5.py`**（聚焦，<400 行）：
  - `_page(d)`：取 playwright page（複用 device_wrapper 既有存取；實作時確認公開 accessor，勿直接摸 `_page`）
  - `RogueState` enum + `current_state(page)`：讀 cocos 場景（複用 `utils/page_detector` / `utils/cocos_navigator` 既有 eval plumbing，不重造）
  - `emit_click(page, path)` / `find_active(page, name)`
  - `sweep_overlays(page)`：§5
  - `advance_to_stage(page)` / `battle_loop(page)` / `settle_run(page)`：node 版，狀態用 §3 訊號
- **WS**：複用 `utils/web_game_api` / `utils/ws_listener` 取 rogue frame。勝敗主判用 cocos `nodeWin/nodeDefeat`（即 WS 結果的 render），WS 作可選交叉驗證。

## 5. 遮罩偵測與移除

`sweep_overlays(page)`：
1. 列出 `NormalView` + `TopView` 下 active 的子 view。
2. 對 §3 已知遮罩，emit 其關閉/確認按鈕。
3. 未知 active overlay → log `UNKNOWN` 並回報，**不盲關**（避免改版誤觸）。

注意：對「開始挑戰」這種被 `Block` 蓋住的按鈕，emit 目標按鈕本身即可繞過遮罩，不必先關；但結果窗/獎勵窗要顯式關掉才能正確回主面板。

## 6. 結算退出流程 — 已於 5556 實測補齊（Phase 0 完成）

### 6.1 結束本局/結算 節點序列（全部 emit，勝敗皆同）
1. `/UIRoot/NormalView/RogueMainView/view/btnClose`（右下紅箭頭，world 648,52）→ 開 `RogueEndTipsView`
2. `/UIRoot/NormalView/RogueEndTipsView/btn1`（結束本局）[btn2=暫時離開, btn3=取消] → 開確認窗
3. `/UIRoot/TopView/MessageView/boxTips/dialog/content/buttons/btnEnsure`（確定，txtContent=「是否確認結算本局」）→ 結算，WS **TX `0x4c03`**
4. 結算後兩窗依序關：`/UIRoot/NormalView/GoodsGetView/Block`（獎勵，全螢幕點擊關閉）→ `/UIRoot/NormalView/RogueRecordInfoView/view/btnClose`（本局報告 ✕）→ 回 `RogueView` 主面板
- 本局報告欄位：`RogueRecordInfoView/.../Label2`=「最終抵達關卡：」、`Label4`=「剩餘試煉之心：」（可作 WS 外的 cocos 讀數）
- 結算 WS：TX `0x4c03`(結算請求) → RX `0x4c09`(954) + `0x4c1e`/`0x32c`(獎勵掉落串流) → RX `0x4c01`(964, 更新後 rogue 狀態)

> web-002 舊 bug 正是第 1 步用座標 (510,920) 點 btnClose 打偏；改 emit `btnClose` 即根治。

### 6.1b 完整一圈已跑通（2026-07-17, 5556, 全程 node emit 零 OCR）
主面板 → 開始 → 選起點 → 確認 → 進入遊戲 → 確認 → 開始挑戰(穿過 Block) → 戰鬥(勝, nodeWin) → 關結果 → 回 RogueMainView → 結束本局 → 結算確定 → 關獎勵+報告 → **回主面板**。積分正確入帳(6761→7534)。

### 6.2 純 WS 路徑探索（H5 stretch goal）
使用者要求評估「H5 只用 WS：從主頁面出發 → 完成 1 局 → 回主頁面」，全程不點擊。
- 已知 `0x4C04` 是 server-authoritative 的「打一關並回結果」（TX 空、RX 3390）。
- 需解碼 §3.1 各 cmd 語意：開新局 / 選起點 / 確認 / 逐關 fight / 結束本局，找出可直接送的最小序列。
- 判定完全靠 RX payload（勝敗 / 剩餘試煉之心 / 是否本局結束），零截圖、零場景樹。
- 風險：rogue 是 client 戰鬥，若某步 server 要求 client 先送特定 handshake，純 WS 可能被拒；故此路徑先做**唯讀錄製比對**（emit-click 觸發 → 錄 WS 序列 → 嘗試純 WS 重放於測試裝置驗證），能重放成功才採用。node-emit 路徑為主、純 WS 為進階選項。

## 7. 實作階段

- [x] **Phase 0**：5556 live recon 補齊 §6 結算退出節點路徑 + 全流程一圈跑通（2026-07-17 完成）
- [x] **Phase 1**：`battle/rogue_h5.py`(worktree `feat/wanshen-h5-node-ws`, commit cad1b7a0) — state-driven machine + emit + 12 單元測全過。
- [x] **Phase 2**：`fight_test` 依 `backend_kind=='web_h5'` 分派到 `rogue_h5.run_rounds`(commit 9b7907b7)；抽出 `_fight_rounds_ocr` 保 ADB 行為不變 + H5 例外中止本輪。已 merge 回 main。
- [x] **Phase 3（關鍵，使用者指定）**：用寫好的 `rogue_h5.py` 在 5556 實跑一整套跑通（2026-07-17, 30s）：
  主面板→開始→進場(HOME→ENTER→CONFIRM→REMAKE→CONFIRM→STAGE)→打3關(全 RESULT_WIN)→結束本局結算→**回到主面板(HOME)**，全程零 OCR、判斷零截圖。
  live 驗證抓到並修掉 3 個 state-machine bug：
  1. confirm 偵測要用 `boxTips.active`（txtContent 字串關閉後 stale 不可信）。
  2. `REMAKE` 必須優先於 `STAGE`（開局獎勵 RogueRemakeRewardView 與 RogueMainView 並存，先判 STAGE 會對遮罩亂點）。
  3. 結果窗關閉 catcher 是 `RogueBattleResultView/imgMask`（emit root 無效）。
- [x] **Phase 4**：截圖優化達成——H5 判斷/操作全走 `page.evaluate`(cocos 場景)，判斷零截圖(舊 OCR 首關即 75+ 張)。
- [x] **Phase 5**：ADB 迴歸——`_fight_rounds_ocr` 為原邏輯精確抽出，27 個 weekly_trials+rogue_h5 測試全過，OCR 路徑行為不變。
- [~] **Phase 6**：web-001/web-002 實跑——待各裝置下次萬神視窗(週二~六 03-07)由 live bot 自動走新路徑觀察；5556 已於 Phase 3 跑通整套(RESULT_LOSE→結算分支由 settle_run live 驗證 + battle_loop 一行 return 覆蓋)。
- [ ] **Phase 7（stretch，未做）**：純 WS 路徑（§6.2）——node-emit 路徑已足夠可靠，純 WS 列為未來選項。已 drain 到的 cmd 見 §3.1/§6.1。

## 8. 測試

- 單元：`rogue_h5` state machine 餵 scene-tree JSON fixture（勝/敗/各遮罩/主面板）驗 `current_state` + `sweep_overlays` 決策。純資料，不接真 device。
- Live：Phase 3 用寫好的碼實跑完整 1 局回主面板，逐步觀察。
- 迴歸：一台 adb backend 裝置確認 `weekly_trials` OCR 流程未動。

### 8.1 截圖次數優化（使用者指定）

現況(OCR 路徑)：`_battle_loop` result-wait 每 2s 一張截圖 + `click_str_by_server` 每次點擊最多 3 張，單局首關空燒 150s ≈ 75+ 張截圖，全程數百張；web-001 log 另見 `slow screenshot 561ms` → 截圖是主要時間成本之一。

H5 節點/WS 路徑目標：
- **判斷零截圖**：state 用 `page.evaluate` 讀 cocos 場景 active/label（非影像）；勝敗用 `nodeWin/nodeDefeat`。
- **操作零截圖**：`emit('click')` 用節點路徑，不需先截圖定位。
- 截圖**只保留人工檢查點**（Phase 3 逐步存證、或出錯時抓一張）。
- 純 WS 路徑(§6.2)則連 `page.evaluate` 都省，只讀 WS RX payload。
- 驗收：量測「每局截圖數」由數百 → 個位數（Phase 4）。

## 9. 風險與回退

| 風險 | 緩解 |
|------|------|
| `backend_kind` 分派誤判 → ADB 走到 H5 path | 明確 gate；H5 driver 任一例外即 fallback OCR |
| 遊戲改版節點名變動 → `current_state` 回 UNKNOWN | fallback 到 OCR（H5 仍留 OCR 當保底）+ log |
| 某些 Button 是 clickEvents 型，emit 無效 | 備 clickEvents fallback（實測 `btnStart` 是 `on('click')` 型，emit 有效） |
| 動到正在跑的 bot | 只加分支、不改 ADB；H5 例外 fallback，不讓萬神整段掛掉 |

**回退**：`fight_test` H5 分支整包 try/except → 例外時退回現有 OCR flow，保證 ≥ 現況。
