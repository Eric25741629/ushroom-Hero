# TODO：開神燈 H5 JavaScript 判斷

## 現況

- `opengold_v2/ui_controller.py` 已能以 Cocos label 讀取神燈數量與部分 UI 狀態。
- `utils/lamp_drop_watch.py`、封包資料與 `ws_token/lamp.py` 已覆蓋部分數量、掉落及詞條判斷。
- `opengold_v2/lamp_service.py` 仍會在比較頁、原裝/新裝詞條或多件候選時退回 OCR。

## 待辦

- [ ] 逐一標記 `lamp_service.py` 的 OCR 用途：數量、詞條、機率、品質、按鈕、popup、連閃。
- [ ] H5 數量、auto/start、出售/裝備、確認窗全部改由 `lamp_ui_state()` 或 Cocos component 判斷。
- [ ] 優先使用封包 item/affix 資料建立新裝備詞條與機率，不從畫面文字反推。
- [ ] 對比較頁探查原裝與新裝綁定的資料 model/component，解決多件候選對應問題。
- [ ] 若 runtime 無法穩定取得原裝詞條，保留該小段 OCR fallback，不要讓整個 H5 流程退回 OCR。
- [ ] 將 `OCRParser` 留給 ADB 與明確 fallback，避免 H5 正常流程建立 OCR 請求。
- [ ] ADB 與手機 OCR 專用模式維持不變。

## 驗收

- [ ] H5 單件掉落、比較、出售/裝備與持續開燈正常流程零 OCR。
- [ ] 多件候選能正確對應畫面當前裝備；無法對應時只 fallback 詞條辨識。
- [ ] 封包/Cocos 缺失、節點變動與 OCR server 不可用時都不會誤換裝。
