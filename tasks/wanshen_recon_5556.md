# 萬神試煉Beta live recon（5556 帳號，2026-07-11，CDP 9223）

> 目的：釐清 `battle/weekly_trials.py` 兩個失敗點 ——（1）每關只等 18s 找「點擊」逾時誤判本局結束；
> （2）逾時後 `_settle_run` 對還在戰鬥的畫面點紅箭頭失敗 → 整任務 0/8 中止。
> 全程只點 rogue 自身按鈕，未碰商店/出售/合成。attach 時帳號已在中途殘局（第37關 宗師-02，❤14），
> 直接續打；共贏 37→41 關（banked 第41關 王者-01），最後主動「結束本局」乾淨退出。**未觸發任何原生 alert/confirm。**

## 0. 座標系統（重要）
- canvas DOM = **540x960**，Cocos 設計解析度 = **720x1280**（`cc.view.getVisibleSize()`），縮放 0.75。
- 下列所有 css 座標 = 540x960 視窗座標（= ADB 540x960 邏輯座標，舊碼 `d.click()` 同一套）。
- world(design,原點左下)→ css：`x*0.75, (1280-y)*0.75`。
- 引擎版本行為：本場景樹 `convertToWorldSpaceAR` 不存在，改用 `worldPosition`／`UITransform`（Cocos 3.x）。

## 1. 進入流程（節點路徑）
attach 時已在殘局，實測到的節點：
- 副本清單 `NormalView/MainView/container/DungeonMainView/.../scrollDungeon/view/content/8/node4`＝「萬神試煉Beta」卡，
  入場鈕 `.../btnGoto/Label`＝「入場」css=**(431,415)**（列表卡**無挑戰次數顯示**；隔壁神樹試煉 content/9 才有「今日可挑戰次數：30/30」）。
- 入場後 `NormalView/RogueView`（主面板）與 `NormalView/RogueMainView`（關卡視圖）**同時 active**，RogueMainView 疊在上層。
- **RogueView 主面板隨 run 狀態換按鈕**（舊碼未區分）：
  - 中途殘局：`RogueView/view/info/btnContinue`＝「繼續」css=(271,745) + `info/btnEnd`＝**「立即結算」css=(270,845)** + `info/cur`＝「當前關卡：第N關 …」css=(270,698)。
  - 全新/結束後：`RogueView/view/btnStart`＝「開始」css=(271,845)，無 info 子面板、無「立即結算」。
  - 主面板辨識字 `神樹祝福`(161,941)、`結算倒計時`(nodeTime) 皆在 → 舊碼 `_ROGUE_HOME_MARKERS` 有效。
- 關卡視圖 `RogueMainView/view/btnStart`＝**「開始挑戰」css=(271,711)**（舊碼 OCR 命中一致）。

> ⚠ 新發現：`RogueView/view/info/btnEnd`＝「立即結算」可**不進 RogueMainView 直接結算本局**，
> 是舊碼「紅箭頭→結束本局」以外的另一條退出路徑（目前未用）。

## 2. 戰鬥畫面與「跳過」鈕（Q2 核心）

### 2a 跳過鈕
- 節點：**`BattleView/BattleHubView/bottom/pvpInfo/btnExit`**（Label 子節點）。
- css=**(270,770)**（大紅鈕）。上方 `pvpInfo/title`＝「與對手激烈搏鬥！」(270,571)，中間 spinner「正在挑戰」。
- **同一顆鈕開場前 ~5s 顯示「逃跑」，約 t≈5.8s label 才變「跳過」**（開場動畫階段只能逃跑不能跳）。
- ⚠ **節點池陷阱**：btnExit 是回收節點，開新局 t<1s 時 label 仍是上一局殘留的「跳過」（此時 `nlabels≈5`，戰鬥層還沒建好）。
  可靠判斷：等戰鬥層建好（`nlabels` 跳到 ~41，約 t≈4s）**且 t>5.5s** 再信「跳過」。
- **點「跳過」→ 結果窗約 0.7–1.0s 後出現**（實測 skip@6.5s → result@7.2s）。無二次確認窗。
- 戰鬥計時器由 **02:00 倒數**（畫面右上），即單場最長 ~2 分鐘 → **這就是舊碼等 18s 逾時的根因**。

### 2b 結果窗 marker
- 視圖：**`NormalView/RogueBattleResultView`**。
- 穩定 OCR 字：**`RogueBattleResultView/Label`＝「點擊任意位置關閉」css=(270,821)** → 舊碼「點擊」子字串**仍有效**。
- 勝負橫幅＝**Sprite 圖片，非 cc.Label**（場景樹 label 掃描讀不到「勝利/失敗」，但**影像 OCR 讀得到**）。
  勝利＝金色「勝利」橫幅；內容顯示「對決」+ 我方(菜菜雞) vs 敵方(宗師-0X)。
- ⇒ 結論：判勝負**別靠 cocos label**，用影像 OCR（舊碼 OK）或 WS `result(0x4c05){2:is_win}`（最可靠）。

### 2c 不點跳過時長
- 可達關卡（37–40）client 自動約 **8s** 自然出結果窗（本帳號強）。難關/boss 可跑到 2 分鐘上限。
- 點跳過可壓到 ~1s → **修復應主動點「跳過」快轉**，而非單純加長等待。

## 3. 失敗窗（Q3）— 本次未能自然重現
- **本帳號(5556)太強**：連贏第37→41關（王者-01），全程 ❤ 維持 14 未掉，且週結算倒數只剩數分鐘，無法 grind 到自然敗北。
- 沿用前次 recon（5554，文件 §9.5）已確證：
  - 失敗＝**同一個 `RogueBattleResultView`，橫幅轉藍色「失敗」**（Sprite）+ 顯示「失去 ❤1」。
  - 最可靠信號＝WS `result(0x4c05){2:is_win}=0`（client 開打即算好送出）。
  - 失敗後**回同一 `RogueMainView` 關卡（不進關），`開始挑戰` 仍在，❤ −1**；run 不因單敗結束，❤=0 才真結束。
  - 舊碼 `_battle_loop` 偵測「失敗」就 break 是錯的（敗北是 run 內正常事件，非本局結束）—— 此為 §9.5 已知 bug，與本次「18s 逾時」是兩件事。

## 4. 退出箭頭 + RogueEndTipsView（Q4）
- 紅箭頭節點 `RogueMainView/view/btnClose`，anchor css=**(486,921)**，鈕尺寸 77x74 → 舊碼 **(510,920) 落在命中範圍內，可用**。
- 點下 → `NormalView/RogueEndTipsView`（**在 NormalView，非 TopView**）：
  - title「提示」(271,307)；desc「選擇暫時離開或結束本局」(270,435)。
  - `btn1/Label`＝**「結束本局」css=(168,520)** → 舊碼 `_END_RUN_BTN_XY=(168,522)` **相符**。
  - `btn2`＝「暫時離開」(372,520)；`btn3`＝「取消」(271,585)。
- 對話框在箭頭點擊後**幾乎即時**出現（等 3s 已在）；舊碼 `_DIALOG_WAIT=4.5s` 安全。

## 5. 完整「結束本局」結算流程（Q5）
逐步實測（每步 marker + 等待）：
1. `結束本局`(168,520) → **確認窗 `TopView/MessageView/boxTips/dialog`**：
   content「是否確認結算本局」(268,424)、`btnEnsure`＝**「確定」css=(371,554)**(右)、`btnCancel`＝「取消」css=(168,556)(左)。〔~即時〕
   → 與文件 §9.2 一致：確認窗掛 **TopView/MessageView**。舊碼用 OCR 點「確定」可命中此鈕。
2. `確定`(371,554) → 約 **3s** 後 `NormalView/RogueRecordInfoView`（本局報告）。
   穩定字：**「最終抵達關卡」(=第41關 王者-01)、「剩餘試煉之心」(=14)、「本局獲得古銀幣總數」(=203)、「試煉時間」、「分享」**。
   → 舊碼 `_at_rogue_home` 用的「試煉之心」「抵達關卡」marker **確認存在**。
   關閉鈕 `RogueRecordInfoView/view/btnClose` css=**(270,875)** → 舊碼 `_REPORT_CLOSE_XY=(270,875)` **完全相符**（此鈕無文字，座標點正確）。
3. 報告 `btnClose`(270,875) → **回 `RogueView` 主面板**，btnStart 變回「開始」(271,845)，RogueMainView 消失＝run 乾淨結束。
   本局**未見獨立 `GoodsGetView` 獎勵彈窗**（獎勵併入報告 / 自動入帳）。
- 主面板 marker「神樹祝福」「結算倒計時」皆在 → `_at_rogue_home` 判 home 有效。

## 6. 與 weekly_trials.py 常數對照（差異與修復重點）
| 常數 | 現值 | live 實測 | 結論 |
|------|------|-----------|------|
| `_BATTLE_SETTLE_TRIES=9`(~18s) | 等結果窗 18s | 戰鬥最長 **~2 分鐘** | **根因 bug**。改：主動點「跳過」(270,770) 快轉，或等待上限拉到 ~130s |
| 「點擊」結果 marker | `check_str_in_region("點擊")` | 「點擊任意位置關閉」在 | 有效 |
| 「失敗」勝負判定 | OCR `check_str_in_region("失敗")` | 橫幅是 **Sprite** | OCR 可讀；label 掃描不可。最佳用 WS `is_win` |
| `_EXIT_ARROW_XY=(510,920)` | 紅箭頭 | node (486,921)、77x74 | 命中範圍內，可用 |
| `_END_RUN_BTN_XY=(168,522)` | 結束本局 | (168,520) | 相符 |
| 結算確認「確定」 | OCR 點「確定」 | (371,554) TopView/MessageView | 有效 |
| `_REPORT_CLOSE_XY=(270,875)` | 報告 ✕ | (270,875) | **完全相符** |
| `_DIALOG_WAIT=4.5` | 轉場等待 | 箭頭→對話框即時、確定→報告 ~3s | 安全 |

**修復方向（不改行為結論，供實作參考）**：
1. 進戰鬥後改為「輪詢戰鬥層 → t>5.5s 且 label==跳過 → 點 (270,770) → ~1s 內等『點擊任意位置關閉』」，取代 18s 硬等。
2. 判勝負改用 WS `result(0x4c05){2:is_win}`（cocos label 讀不到橫幅 Sprite）；OCR「失敗」為 fallback。
3. 退出/結算座標全部沿用現值即可（僅需修 §6 第一列的等待邏輯）。
4. 注意節點池殘留「跳過」label（開局 t<1s 會誤讀上一局的值）。
