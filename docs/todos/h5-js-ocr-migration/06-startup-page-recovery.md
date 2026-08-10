# TODO：啟動與頁面恢復 H5 JavaScript 判斷

## 現況

- `utils/page_detector.py` 已採 Cocos-first、OCR-fallback，但只完整辨識部分 view。
- `try_detect_main_page` 目前對非主頁狀態常回 `None`，呼叫端仍進全畫面 OCR。
- loading、reconnect、異地登錄與未知 popup 是必須保留 fallback 的高風險狀態。

## 待辦

- [ ] 從 action trace 統計最常觸發 OCR 的 stage detector 呼叫端與未知畫面。
- [ ] 擴充 `PageState` 與 Cocos fingerprints，覆蓋本批任務涉及的 view 和常見 popup。
- [ ] 將 fast path 從「只確認 MAIN」改成回傳可用的明確頁面狀態。
- [ ] 用 Cocos/runtime 判斷 loading、guide、reconnect、login conflict 與 modal stack。
- [ ] 呼叫端只有在 `UNKNOWN`、Cocos 未載入或注入例外時才跑全畫面 OCR。
- [ ] 加入節點改名診斷：記錄 active overlays/view names，方便更新 fingerprint。
- [ ] 不讓 web_h5 頁面恢復呼叫 Android-only 的 `app_stop()` 清理。

## 驗收

- [ ] 已知 H5 頁面切換及回首頁正常路徑零 OCR。
- [ ] loading/reconnect/異地登錄仍能正確處理，且未知狀態可轉 OCR。
- [ ] ADB stage detector 行為完全不變。
