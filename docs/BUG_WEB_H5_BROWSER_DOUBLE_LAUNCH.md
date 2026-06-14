# Bug Report: web_h5 瀏覽器「啟動→閃退→再啟動」

狀態：已修復，待 Claude 驗證
日期：2026-06-12
影響範圍：所有 web_h5 裝置（7fe98fc6、emulator-5554/5556/5558/5560 等）

## 症狀

裝置 thread 初始化（bot 重啟、device thread 重生）時，Playwright 瀏覽器會：

1. 啟動並載入遊戲頁（視窗出現）
2. 約 2~3 秒後整個瀏覽器被關閉（看起來像閃退）
3. 立刻又重新啟動一次，且重啟後連續 goto 遊戲頁兩次

## 根因

**不是 Chrome 崩潰**，是喚醒流程自己把剛開好的瀏覽器關掉：

- `utils/wake_up_handler.py:408`：`handle_device_wakeup` 內無條件執行
  `d.app_stop("com.mxdzz.tw.and")`。本意是 ADB 裝置喚醒後殺 App 求乾淨重啟。
- 但 web_h5 的 `app_stop`（`device_wrapper.py:1156`）在預設
  `web_stop_mode="close"` 下走 `self.close()`，**關掉整個瀏覽器**。
- 而 `PlaywrightGameDevice.__init__` → `_start()`（`device_wrapper.py:575`）
  在裝置建立當下就急性啟動瀏覽器 + goto 遊戲頁
  （由 `web_session_service.initialize_runtime_device` →
  `create_web_device_if_enabled` 觸發）。

時序（log 證據：`logs/7fe98fc6/main.20260612_041206.log` 21:46:38~21:46:42）：

```
21:46:38  device_wrapper:881  建構子 _start() 啟動瀏覽器 + goto ok      ← 第 1 次啟動
21:46:39  web_session_service:300  backend=web_h5（初始化完成）
          （主迴圈 → handle_device_wakeup → sleep(2) → app_stop → 瀏覽器被關）← 「閃退」
21:46:41  new_main_v2:297  「web_h5 瀏覽器已關閉，跳過喚醒截圖，直接啟動」
21:46:41  device_wrapper:814  session unavailable (app_start), restarting...  ← 第 2 次啟動
21:46:42  goto ok ×2（_start 開一次、app_start 又 _open_game_url 一次）   ← 重複 goto
```

歷史 log 統計：`session unavailable (app_start), restarting browser session`
共 547 次（emulator-5554 占 228）。真正的 Chrome launch 失敗
（profile 鎖 / TargetClosedError）只在 web-001 出現 2~3 次，非主因。

註：每小時喚醒時「瀏覽器已關閉 → 冷啟一次」是正常的
（睡眠時 close_browser 自己關的），不在本 bug 範圍。

## 建議修法

1. **主修**：`utils/wake_up_handler.py` 喚醒流程中，backend 為 web_h5 時跳過
   `d.app_stop("com.mxdzz.tw.and")`（檔內 line 425 已有
   `is_web_backend = getattr(d, "backend_kind", None) == "web_h5"` 可複用，
   但注意該判斷目前位於 app_stop 之後，需前移）。
   理由：web 的 `app_start` 本來就會重新 goto 遊戲頁，等效重啟 App，
   不需要殺瀏覽器。
2. **順手修**：`device_wrapper.py` `app_start`（line 1129）在
   `_ensure_browser_session` 觸發過 `_restart_browser_session`（其 `_start`
   已開過 URL）後，不要再 `_open_game_url` 重複 goto。
   做法建議：`_restart_browser_session` / `_start` 回傳是否已完成導航，
   或在 `app_start` 檢查 `self._page.url` 已是 `web_url` 即略過。
3. （可選、影響面大，先不做）建構子改 lazy `_start()`，
   徹底消掉初始化那次「開了就被殺」的啟動；牽動
   `create_web_device_if_enabled` registry 與 `is_alive` 語義。

## 修復後驗收標準（Claude 驗證用）

- [ ] 重啟 bot 後，web_h5 裝置 log 中「裝置初始化 → 進入任務迴圈」全程
      只出現**一次** `web_h5 opening game url`，
      且**不出現** `session unavailable (app_start)`。
- [ ] 喚醒流程後，ADB 裝置行為不變（仍會 app_stop 殺 App）。
- [ ] 每小時喚醒（睡眠時瀏覽器已關）仍能正常冷啟進入主頁面。
- [ ] 中控「開啟瀏覽器」（manual web launch）流程不受影響。
- [x] 相關單元測試綠（喚醒流程 / device_wrapper app_start 的測試，
      建議新增：web_h5 喚醒不觸發 close、app_start 不重複 goto）。

## Claude 驗證記錄（2026-06-12，code review + 測試層）

- [x] Code review 通過：`wake_up_handler.py` 的 `is_web_backend` 已前移到第一次
      `app_stop` 之前；web_h5 跳過 app_stop / Home / launcher / 通知 / Messenger
      整段 Android-only 清理；ADB 路徑順序不變。
- [x] `device_wrapper.py`：`_ensure_browser_session` 回傳重建與否、
      `_current_page_matches_game_url` 前綴比對含 `/?#` 邊界處理正確；
      只有「剛重建且已在遊戲 URL」才略過第二次 goto，未重建時維持原 reload 語義。
- [x] `python -m pytest tests/test_wake_home_order.py
      tests/test_device_wrapper_session_helpers.py -q` → 9 passed。
- [x] 回歸：`tests/test_wake_loop_escape.py tests/test_wake_phone_reconnect.py`
      → 12 passed；`tests/test_close_notification.py` 單獨跑 4 passed
      （與其他檔合跑會踩既知的跨檔 import 汙染，非本次回歸）。
- [ ] **Live 驗證待做（需重啟 bot 後觀察）**：重啟 `new_main_v2.py`，確認
      web_h5 裝置初始化只出現一次 `web_h5 opening game url`、無
      `session unavailable (app_start)`；下一個整點喚醒能正常冷啟進主頁面。

備註（行為小變更，已評估可接受）：manual web launch 時若瀏覽器已是 headful
且頁面已在遊戲 URL，`app_start` 不再強制 reload 頁面（舊行為會重新 goto）。

## Codex 修復記錄

修復日期：2026-06-12

重點改動：

1. `utils/wake_up_handler.py`
   - 將 `is_web_backend = getattr(d, "backend_kind", None) == "web_h5"` 前移到第一次 `app_stop` 之前。
   - web_h5 喚醒時不再呼叫 `d.app_stop("com.mxdzz.tw.and")`，避免 `PlaywrightGameDevice.app_stop()` 關閉整個瀏覽器。
   - web_h5 也跳過後段 Android-only 清理流程：連按 Home、launcher 檢查、`close_notification(d)`、`d.app_stop("com.facebook.orca")`。原因是 web_h5 沒有 Android launcher/通知欄，而 `app_stop()` 不分 package 都會 close Playwright session。
   - ADB 裝置原本順序保留：停止遊戲 App → 回桌面確認 → 清通知 → 回桌面確認 → 停 Messenger。

2. `device_wrapper.py`
   - `_ensure_browser_session()` 改為回傳是否重建 Playwright session。
   - 新增 `_current_page_matches_game_url()`，用目前 page URL 判斷 restart 後是否已在 `web_url`。
   - `app_start()` 若剛重建 session（包含 manual headful restart）且 `_start()` 已載入遊戲 URL，就略過第二次 `_open_game_url()`，避免 log 中連續兩次 `web_h5 opening game url`。
   - 一般 session 未重建時仍維持原行為，`app_start()` 會重新 goto 遊戲 URL。

3. 回歸測試
   - `tests/test_wake_home_order.py::test_web_h5_wake_does_not_run_android_stop_cleanup`
     驗證 web_h5 wake 不觸發任何 `app_stop` 或 notification cleanup。
   - `tests/test_device_wrapper_session_helpers.py::test_app_start_skips_second_goto_when_restart_already_loaded_game_url`
     驗證 restart 已載入遊戲 URL 時，`app_start()` 不會再呼叫第二次 `_open_game_url()`。
