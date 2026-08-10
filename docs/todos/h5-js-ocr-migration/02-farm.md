# TODO：農場 H5 JavaScript 判斷

## 現況

- `farm_v2/web_farm.py` 已能讀 `PlantMainView`、buff、種子/肥料 label 與相關 view。
- `farm_v2/operations/harvest_card.py` 已有 H5 Cocos 購買、種植與施肥分流。
- 殘餘問題主要是導航 fallback、工作狀態、部分 popup 與流程驗證仍可能進 OCR。

## 待辦

- [ ] 列出 `farm_v2` 所有 OCR 呼叫，確認 H5 是否已有對應 `web_farm` helper。
- [ ] 讓 `web_h5` 預設啟用 Cocos 農場導航，不再只依賴 experimental flag；失敗才走舊導航。
- [ ] 用 `PlantMainView` 節點/元件欄位判斷空地、成熟、工作中、可收成與可施肥狀態。
- [ ] 豐收卡使用 `SpecialBuff.activeInHierarchy`，不要使用可能過期的數字 label。
- [ ] 種子與肥料選擇以 label/元件資料定位；節點事件無效時允許使用節點 worldPosition 點擊。
- [ ] 所有成功操作都增加結果驗證，例如 view 開啟、庫存變化或作物狀態變化。
- [ ] 保留 ADB 的 OCR/座標實作。

## 驗收

- [ ] H5 完成導航、買卡、種植、施肥、收成及工作狀態判斷，正常路徑零 OCR。
- [ ] JavaScript 節點缺失時可完整轉回既有 OCR 流程。
- [ ] 不因 `emit('click')` 無效而假成功，操作後狀態必須改變。
