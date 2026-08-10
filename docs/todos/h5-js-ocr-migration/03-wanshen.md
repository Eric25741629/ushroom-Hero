# TODO：萬神試煉周邊 H5 JavaScript 判斷

## 現況

- `battle/rogue_h5.py` 已完成 H5 戰鬥流程的 Cocos 狀態判斷與 node click，戰鬥本身零 OCR。
- ADB 仍由 `battle/weekly_trials.py` 使用 OCR，應保留。
- 剩餘 OCR 集中在秘寶閣、入口/退出、回首頁與未識別 popup。

## 待辦

- [ ] 不重寫 `battle/rogue_h5.py` 已完成的戰鬥狀態機。
- [ ] 盤點秘寶閣相關 view、商品/免費狀態、確認窗與關閉節點。
- [ ] 用 `uiMgr.openView` 或穩定入口節點進入秘寶閣，操作後驗證領取/購買結果。
- [ ] 將結算報告、退出確認與回首頁改成 view active + node event。
- [ ] 未知 overlay 交由共用 page detector/popup handler；無法辨識才 OCR。
- [ ] 保留 `weekly_trials.py` 的 ADB/OCR 路徑。

## 驗收

- [ ] H5 從進場、戰鬥、秘寶閣到回首頁的正常路徑零 OCR。
- [ ] 已完成、無免費商品、確認窗等分支都有明確 runtime 狀態。
- [ ] Cocos driver 失敗時能整段切回舊 OCR 路徑，避免兩套狀態機交錯。
