# TODO：挖礦 H5 JavaScript 判斷

## 現況

- `miner/core/ws_inventory.py` 已讓 H5 道具數量優先使用 WS，ADB 才需要 OCR。
- `miner/mining_service.py` 仍有礦洞 overlay OCR、每輪 overlay 清理及週期性鏟子 OCR 驗證。
- 盤面分類屬影像模型，不應在這個任務中誤當成文字 OCR 全部移除。

## 待辦

- [ ] 盤點 `miner/mining_service.py` 每一個 OCR 呼叫，標記為 overlay、道具數量或盤面分類。
- [ ] 用 JavaScript 查找 `MysteryMineView`、礦洞標題 overlay、獎勵/確認 popup 的 active 狀態。
- [ ] overlay 存在時優先透過 node event、view close 或可靠的 Cocos 座標關閉。
- [ ] H5 鏟子、鑽頭、炸彈以 WS inventory 為真值；WS snapshot 缺失時才讀 Cocos label，再失敗才 OCR。
- [ ] 停止 H5 每 N 輪固定 OCR 驗證；改成 WS/runtime 不一致或資料過期時才觸發 OCR。
- [ ] 保留盤面 CNN/圖片分類，除非另有任務證明盤面資料可從 runtime 取得。
- [ ] ADB 行為完全不變。

## 驗收

- [ ] 一次完整 H5 挖礦正常流程，道具數量與 overlay 判斷為零 OCR。
- [ ] 模擬 WS inventory 缺失和 Cocos 節點改名，能退回 OCR 且不中斷。
- [ ] 道具 `4001/4002/4003` 數量與 `0x0402` push 一致。
