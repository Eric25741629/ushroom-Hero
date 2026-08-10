# TODO：商店 H5 JavaScript 判斷

## 現況

- `ws_token/steward.py` 已處理管家代購，成功時 pipeline 跳過 `商店購買`。
- 舊 UI 流程仍可能以 OCR 辨識商品、限購狀態與購買確認。

## 待辦

- [ ] 先確認 `商店購買` 的商品集合是否全部由 steward WS 覆蓋。
- [ ] WS 成功時不再開商店 UI；部分成功時只處理剩餘商品。
- [ ] 探查商店 view 的商品 component：商品 ID、名稱、價格、限購數量、售罄與 buy button。
- [ ] 商品選擇以 ID/component data 為主，label 文字只作次要驗證，避免同名商品誤買。
- [ ] 購買以 node/controller 操作，並用庫存、貨幣或限購數變化確認成功。
- [ ] JS 資料不完整、價格超限或節點改名時停止該筆並轉原 OCR，不得盲點購買。
- [ ] ADB 保留既有 OCR 商品辨識。

## 驗收

- [ ] pure WS 成功時零 UI、零 OCR。
- [ ] WS 不可用時 H5 能正確選商品、判斷售罄並完成購買，正常路徑零 OCR。
- [ ] 價格與商品 ID 驗證失敗時不購買，且 fallback 可觀測。
