# 截圖速度優化 (A 路徑 — bot 自身截圖)

來源分析：`OPTIMIZE_streaming_screenshot.md` A 節。預設 PNG → JPEG，實測 130ms→62ms (2.1x)。
**安全原則**：JPEG 會動到 OCR/CNN 輸入，預設維持 PNG (`web_screenshot_jpeg_quality=None`)，
benchmark 確認安全品質下限前不改預設。

## TDD 步驟 (已完成 2026-06-05)

- [x] (RED→GREEN) `tests/test_screenshot_jpeg_format.py` (6 測全綠)：
  - [x] 設定 `web_screenshot_jpeg_quality=85` → locator.screenshot 收到 `type='jpeg', quality=85`
  - [x] 未設定 → 不傳 type/quality（維持 PNG 預設）
  - [x] locator 失敗 fallback 到 `page.screenshot` 也帶 jpeg kwargs
  - [x] opencv vs pillow 格式像素等價（保護下面 executor 改動的不變式）
- [x] (GREEN) `device_wrapper.py`：
  - [x] module helper `_coerce_jpeg_quality` (None/0/不合法→None, 否則 clamp 1..100)
  - [x] `__init__` 解析 → `self.screenshot_jpeg_quality`
  - [x] `_playwright_screenshot_kwargs()` + `_capture_via_playwright()`（順手把兩段重複的擷取邏輯 DRY 掉）
  - [x] `screenshot()` 兩條路徑都走 `_capture_via_playwright()`
- [x] (GREEN) `config_manager.py` (test_device_config.py 10 測全綠)：
  - [x] `DEFAULT_DEVICE_CONFIG` 加 `web_screenshot_jpeg_quality: None`
  - [x] `DeviceConfig` dataclass 加 typed field + import `Optional`
  - 註：runtime 端 `_coerce_jpeg_quality` 已 clamp，update_device_config 端 clamp 留待有 UI 再加
- [x] (順手, 免風險) `miner/planning/executor.py:264,506` 補 `format='opencv'`
- [x] 全測綠 + py_compile：29 passed（screenshot 6 + config 10 + timing/session helpers）
      + miner executor 26 passed（1 個 `test_mining_item_logic` 失敗為**既有跨測污染**，
      stash 我的改動後 baseline 同樣失敗，與本次無關）

## 預設行為不變
`web_screenshot_jpeg_quality` 預設 None → 維持 PNG，OCR/CNN 輸入完全不變。
要啟用：在 `bot_config.json` 某裝置設 `"web_screenshot_jpeg_quality": 85`（先過下方 benchmark）。

## 待 live 驗證 (需真實 H5 裝置 / OCR server，無法在此環境跑)
- [ ] 擴充 `benchmark_screenshot.py`：q70/80/85/90/100 跑 OCR 字串 diff + `classify_board` label diff，挑零 drift 最低 q
- [ ] 補真實挖礦盤面 PNG fixtures（`tests/images/` 目前只有神燈 UI）
- [ ] benchmark 過了再決定要不要把某些裝置預設改 JPEG
