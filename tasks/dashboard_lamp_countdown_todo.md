# Fix: dashboard 開神燈 倒數計時卡在 ~2400s 不遞減 (2026-06-05)

## 根因
- `bot_config.json:291` 某台 adb 裝置 `lamp_duration_sec: 2400`。
- `lamp_scheduler.py:107` 開燈時設 `step="執行中 (2400s)"`（一次）。
- 前端 `dashboard.html:2323-2337` 用 regex 抓步驟字串裡的 2400，relabel 成「等待中」，
  再算 `2400 - (now - last_update)`。
- 兩個疊加問題：
  1. `last_update` 是「狀態最後被碰時間」，會被背景心跳(`update_watchdog_probe` 等)
     刷新成 now → `elapsed≈0` → 倒數卡在 ~2400 不動。
  2. `Math.floor(Date.now()/1000)`(整數) 減 `info.last_update`(Python float)
     → 帶小數 → 顯示 `2399.8699922561646s`。

## 方案（正解：絕對截止時間戳）
後端給固定 epoch `step_deadline`，前端從它倒數（不受 last_update 影響、無小數）。

## 待辦
- [x] (test) `tests/test_bot_state_step_deadline.py`：update_state 存/清 step_deadline (3 測)
- [x] (test) `tests/test_lamp_scheduler.py`：開燈推送帶 step_deadline ≈ now+lamp_dur，step 不再內嵌 (Ns) (+2 測)
- [x] (test) `tests/test_dashboard_template.py`：倒數改用 info.step_deadline + Math.round；舊邏輯移除 (2 測)
- [x] (impl) `bot_state.update_state`：新增 `step_deadline` 參數，依 step 綁定生命週期（換 step 無 deadline 即清除）
- [x] (impl) `lamp_scheduler.py` 3 處：改 `step="執行中"` + `step_deadline=time.time()+lamp_dur`
- [x] (impl) `dashboard.html`：以 step_deadline 倒數，整數秒，格式 `(剩 M分S秒)`
- [x] 跑 focused pytest + py_compile

## Review
- TDD：先寫 7 個失敗測試 (RED) → 實作 → 全綠 (GREEN)。
- 驗證：新測 7 + lamp_scheduler 全套 + bot_state/stage_guard/daily_pipeline/sleep_service = 74 passed；
  `py_compile bot_state.py game_actions/lamp_scheduler.py` OK。
- 改動檔：`bot_state.py`、`game_actions/lamp_scheduler.py`、`templates/dashboard.html`
  + 3 個測試檔。`/api/status` 直接 jsonify 整包 state，step_deadline 自動帶到前端，無欄位白名單。
- 部署注意：
  - `bot_state.py` / `lamp_scheduler.py` 屬 Python 模組，**需重啟 `new_main_v2.py`** 才生效 (sys.modules cache)。
  - `dashboard.html` 需瀏覽器**強制重新整理** (Ctrl+F5) 清快取。
  - 目前卡住的狀態 (step="執行中 (2400s)" 無 step_deadline) 不會回填；重啟後下一輪開神燈才會出現正確倒數。
    在那之前新前端會直接顯示 "執行中 (2400s)"（不再有假「等待中」與小數），已不誤導。
- 未處理 (out of scope)：remote/worker 裝置若也跑開神燈，其 step_deadline 是否經 worker_sync 轉發到 master
  顯示未驗證；本次只修使用者回報的本機 adb 路徑。
