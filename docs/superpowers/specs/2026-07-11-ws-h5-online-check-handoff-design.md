# WS 到 H5 健康交接略過重複在線檢查設計

日期：2026-07-11
分支：`fix/ws-h5-online-check-handoff`
範圍：web_h5 裝置每輪先跑純 WS、再切換 H5 時，避免在線檢查把本輪自己的 WS 活動誤判為真人在線。

## 問題

首次建立 web_h5 runtime 時，`initialize_runtime_device()` 會先跑在線互檢，並透過
`skip_online_check_once` 避免同一輪再次檢查。但後續喚醒週期會依序執行：

1. `run_ws_phase()` 跑純 WS 任務並關閉連線。
2. `handle_device_wakeup()` 再次發出在線互檢。
3. 通過後才啟動或喚醒 H5。

在線監控的快照與寬限時間可能仍把剛結束的 WS 視為在線，因此第 2 步會把腳本自己的
WS 活動誤判為真人在線，導致不必要的等待與重試。

## 目標行為

web_h5 且本輪 WS 健康完成時，WS 到 H5 的交接直接略過在線互檢。只有下列情況保留
原本的 fail-closed 在線檢查：

- WS 登入失敗。
- WS 連線被異地登入踢掉或非預期斷線（`kicked=True`）。
- WS 被外部控制中止（`aborted=True`）。
- WS 階段發生未預期例外。

個別 WS 任務失敗但登入與連線健康時，不視為交接中斷；H5 仍直接啟動，讓既有
Playwright pipeline 處理未完成任務。

## 設計

### 獨立交接狀態

在 `bot_state.py` 新增每裝置的 `ws_h5_handoff_ok` 布林狀態及 thread-safe getter/setter。
不改變既有 `ws_login_ok` 的語意，避免影響 Phase D1 skip-browser 決策。

狀態生命週期：

1. 每次已啟用的 WS 階段開始時先設為 `False`，杜絕上一輪成功值殘留。
2. `run_ws_phase()` 取得 `RunReport` 後，只有 `login_ok=True`、`kicked=False`、
   `aborted=False` 時設為 `True`。
3. `report.errors` 不影響交接狀態。
4. 登入失敗、被踢、中止或例外的所有提前返回路徑維持 `False`。

### 主迴圈決策

`new_main_v2.py` 在呼叫 `handle_device_wakeup()` 前計算本輪是否略過在線檢查：

- `ws_token.enabled=True`：只採用本輪 `ws_h5_handoff_ok`。健康完成才略過；中斷時即使
  初始化前曾互檢，也要重新確認，因為中斷可能代表真人或其他 session 已接管。
- `ws_token.enabled=False`：保留既有 `skip_online_check_once`，避免首次初始化已互檢後
  無意義地再查一次。
- 非 `web_h5` 後端維持既有行為；交接旗標不放寬其在線保護。

既有 `handle_device_wakeup(..., skip_online_check_once=...)` 介面不變，只調整呼叫端傳入的
本輪值，因此在線檢查迴圈本身不需改寫。

## 錯誤處理

交接狀態採 fail-closed：讀取設定、執行 WS 或寫入狀態若無法證明健康完成，就不略過
在線互檢。這可避免 WS 其實已被打斷時直接開 H5，進一步踢掉真人 session。

手動開啟瀏覽器、暫停與強制休眠的既有優先序不變；`aborted=True` 不會取得健康交接
資格。

## 測試

以測試先行方式新增下列案例：

- WS 正常完成：交接狀態為 `True`。
- 某個 WS 任務失敗但未被踢、中止：交接狀態仍為 `True`。
- `login_ok=False`：交接狀態為 `False`。
- `kicked=True`：交接狀態為 `False`。
- `aborted=True`：交接狀態為 `False`。
- 前一輪成功、下一輪失敗：開始時重置，不能沿用舊值。
- WS 啟用且健康：主迴圈傳入略過在線檢查。
- WS 啟用但中斷：主迴圈保留在線檢查。
- WS 未啟用：首次初始化的 `skip_online_check_once` 行為維持不變。

驗證只執行相關 pytest 與修改檔案的 `py_compile`，不跑會載入真裝置、Playwright、OCR
或 OpenCV 的整包測試。

## 不在範圍

- 不改在線 presence 演算法、快照寬限時間或 checker 選擇。
- 不改 WS 任務是否成功映射到 Playwright skip-set 的規則。
- 不改 `ws_login_ok` 或 skip-browser 功能。
- 不合併或帶入主工作目錄目前未提交的其他修改。
