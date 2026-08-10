# TODO：競技場 H5 JavaScript 判斷

## 現況

- `game_actions/arena_battle.py` 的 pure WS 模式已可避開 UI。
- local simulation 已使用頁面內 BattleMainServer，但「挑戰」入口、動畫結果與收尾仍使用 OCR。
- animation fallback 每秒輪詢「勝利/對決/跳過」，是主要 OCR 熱點。

## 待辦

- [ ] pure WS 成功時維持直接完成，不進 H5 或 OCR。
- [ ] 為 H5 建立 Arena view probe，取得挑戰按鈕、剩餘次數、刷新、記錄與結果 view 狀態。
- [ ] local_sim/remote_calc 的挑戰按鈕改用 Cocos node/component 或 worldPosition。
- [ ] animation 模式改讀戰鬥 controller、結果 view、勝負欄位與跳過按鈕 active 狀態。
- [ ] 點擊後驗證戰鬥 request、combat hook 或 view transition，不能只相信 click 回傳。
- [ ] local_sim 失敗時先轉 H5 animation runtime；H5 runtime 也失敗才進 OCR animation。
- [ ] ADB 保持原 animation OCR。

## 驗收

- [ ] H5 三場動畫競技場正常情況零 OCR。
- [ ] pure WS、local_sim、runtime animation、OCR fallback 的優先順序有測試。
- [ ] timeout、失敗、勝利與剩餘次數不足都能正常結束。
