# RESEARCH SUMMARY

## Stack
以既有 `Python + Flask + ADB/uiautomator2 + OCR` 為基礎最務實，優先補齊統一排程、狀態機、觀測與恢復機制，而非大重構。

## Table Stakes
v1 應先完成長時穩定掛機、自動恢復、即時控制分頁（啟停/切策略/看狀態）與可觀測狀態機。

## Watch Out For
最主要風險是：長時資源洩漏、排程飢餓、狀態漂移、OCR 誤判連鎖，以及跨主機裝置識別衝突。

## Scope Guidance
- 打車流程目前未開發且非主軸，應列為後續階段。
- 先把多電腦多實例與手機接入場景跑穩，再談進階收益優化。

## Recommended Build Order
1. 穩定性與狀態機基線
2. 統一排程器
3. 網頁即時控制分頁
4. 策略擴充框架
5. 打車流程納入排程（後續）
