# H5 PageState 擴展規劃（2026-08-04）

## 全局約束
- 不加新套件
- JSON 讀取 utf-8-sig
- pytest 必指定檔案（hook 擋裸 pytest）
- 只 stage 動到的檔（絕不 git add -A）
- 不 push、不加 attribution footer

## 任務 1：擴展 PageState/Cocos fingerprints

**目標**：增加 loading、reconnect、異地登錄、unknown popup 的 Cocos fingerprints，讓 UNKNOWN 呼叫大幅減少（目前偵測仍依賴 OCR）。

**規格**
- 擴展 `_OVERLAY_TO_STATE`、`PAGE_OCR_KEYWORDS` 與 `_SCAN_JS` 加入：
  - `异地登录` (异地登录弹窗)
  - `reconnect` 完整 view
  - `loading` 完整 loading 状态
  - `unknown popup` (未分類的 modal)
- 更新 `detect_via_cocos()` 優先級：先檢查 loading/reconnect/异地登录，再 fallback 舊 overlay。
- 保留 OCR fallback 作為安全網（當 Cocos 節點改名時）。
- 測試：新增 `tests/test_page_detector.py` 涵蓋以上狀態。

**驗收**
- H5 頁面切換 + 回首頁零 OCR
- loading/reconnect/异地登录仍能正確處理
- 未知狀態可轉 OCR

**工作樹**：`h5-ocr-06-startup`