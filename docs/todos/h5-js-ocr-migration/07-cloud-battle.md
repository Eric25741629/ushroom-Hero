# TODO：雲端戰鬥 H5 JavaScript 判斷

## 現況

- `battle/cloud.py` 已能用 `uiMgr.openView("DoubleChapterMainView")` 進主面板。
- 戰友設置、申請、最高難度、入場、成功/失敗與獎勵仍大量依賴 OCR。
- `ws_token/cloud_ladder.py` 已提供純 WS 任務；`emulator-5558` 目前明確排除 WS。

## 待辦

- [ ] 非排除裝置先走 pure WS；成功後不再啟動 UI 任務。
- [ ] 建立 `DoubleChapterMainView` H5 driver，集中所有 view/node 路徑。
- [ ] 用 label/component data 判斷「已通過最高難度」、挑戰次數、戰友與申請狀態。
- [ ] 戰友招募、選擇、發送、同意、入場與關閉改用 node event 或 worldPosition。
- [ ] 挑戰進度與結果改讀 controller/result view，不再 OCR 輪詢文字。
- [ ] `emulator-5558` 若是 H5，可使用本 driver；若是 ADB，仍走舊 OCR。
- [ ] H5 driver 整段失敗時才切回 `battle/cloud.py` 原 OCR 流程。

## 驗收

- [ ] H5 戰友設定、助戰申請及五輪挑戰正常情況零 OCR。
- [ ] 已通關、無申請、挑戰成功、失敗與 timeout 分支均有測試。
- [ ] pure WS 成功、H5 fallback、OCR fallback 不會重複領取或重複開戰。
