# 每日 10:00 搶車位:喚醒打斷設計

日期:2026-06-15
範圍:讓 `手機fc` / `emulator-5554` / `7fe98fc6` 三台(均有 WS 後端)每天穩定在 09:59 醒來、10:00 整點搶跨界泊銀車位。`emulator-5556` / `emulator-5560`(無 WS 後端)與 `emulator-5558`(使用者明確排除)本輪不處理。

## 問題

使用者要「每天早上 10:00 去搶車位」,但觀察到喚醒不會為了搶車位打斷睡眠。

根因(已驗證,file:line):

1. **睡眠 poll 迴圈沒有車位中斷條件。** `runtime_services/device_runtime_service.py:124-169` 的 `sleep_until_wake_or_interrupt()` 每秒輪詢,可中斷條件只有:強制休眠、暫停恢復、跳過休眠、手動 wake override、中控網頁啟動請求、跨裝置上線檢查、到達 `wake_ts`。沒有任何一條跟車位有關。車位喚醒只能靠「進睡前把 `wake_ts` clamp 提前」這條路。

2. **提前喚醒的 clamp 只在 `run_sleep_cycle` 套用,`_maybe_resume_sleep` 沒套。** `runtime_services/sleep_service.py:204` 在常規進睡時呼叫 `_apply_carpark_repark_wake()`(只提前不延後);但「被中斷後返回休眠」的 `_maybe_resume_sleep`(`sleep_service.py:281`、`296`)兩條分支都沒套 clamp,會睡到未經 clamp 的原 `wake_ts`,偶發睡過當天 10:00 窗口。

3. **5554 / 7fe98fc6 的 `carpark_plan` 是關的。** `bot_config.json` 兩台都 `ws_token.carpark_plan.enabled: false`,所以 `_load_carpark_next_ts()`(`sleep_service.py:45-62`,有 `enabled` gate)永遠回 `None`,連那半套 clamp 都是 no-op。這是使用者直接觀察到「不會為車位醒」的原因。

## 既有機制(已驗證,正確,可直接沿用)

WS 車位 plan 路徑已經把「09:59 醒、10:00 搶」設計好了,只是兩台沒啟用:

- `ws_token/carpark_plan.py:210` `carpark_wake_ts()`:在沒有在停車、現在早於 10:00 時,回傳 `next_cross_open_dt`(今天 10:00) − `open_lead`(預設 60s) = **今天 09:59:00**,寫入 `ws_state/<ip>.json` 的 `carpark_repark.next_ts`(`ws_token/runner.py:375-398` `_store_next`)。
- `runtime_services/sleep_service.py:65` `_apply_carpark_repark_wake()`:常規進睡時把 `wake_ts` clamp 到 `next_ts`(只提前)。5554 偶數時 :00 醒、正常對齊喚醒會落在 10:00,clamp 後變 09:59。
- `ws_token/runner.py:363-373` + `ws_token/carpark_plan.py:178` `cross_open_wait()`:09:59 醒來時,WS 階段偵測到「開窗前 60s 內」→ `sleep_fn` block-wait 到 10:00 整 → 才搶(`auto_select_and_park_many`,跨界限定泊銀)。

手機fc(`adb-fc65396d`)的 plan 早已 `enabled: true` 且**無** reconcile(`carpark: null`),證明 WS plan 單獨就能完成競爭搶位,是本設計的參照基準。

## 設計

### Part 1 — Config(主修):`bot_config.json`

- `emulator-5554`、`7fe98fc6`:`ws_token.carpark_plan.enabled` → `true`。
  兩台的 `carpark_plan` 區塊已 scaffold 完成且與手機fc 一致:`silver_levels:[9,10]`、`day.window:["10:00","22:00"] cross:1`、`night cross:0`。不需改其他 plan 參數。
- 手機fc:已啟用,不動。

### Part 2 — Code 加固:`runtime_services/sleep_service.py`

在 `_maybe_resume_sleep` 的兩條「返回休眠」分支(checker 分支 `:281`、非 checker 分支 `:296`)進睡前,對 `resume_sleep_until_ts` 套用與 `run_sleep_cycle` 相同的 `_apply_carpark_repark_wake(ip, wake_ts, cur_ts, logger_obj)`,讓返回休眠路徑也會被 clamp 到 09:59。

- 只提前不延後的語意不變(`_apply_carpark_repark_wake` 內已保證 `next_ts <= cur_ts or next_ts >= wake_ts` 時 no-op),所以對非車位裝置與一般返回休眠零影響。
- poll 迴圈逐秒重讀 next_ts 的自我修正:**不做**(YAGNI)。兩條進睡路徑都套上 clamp 後,因為睡眠期間沒有任何程式會改寫 `next_ts`,clamp-at-entry 已足夠。

### Part 3 — reconcile 共存(待規劃期驗證後定案)

5554 / 7fe98fc6 仍開著 Playwright 的 `carpark.enabled: true`(reconcile,`daytime_cross:1 / daytime_total:6`)。手機fc 無此層。WS plan 啟用後兩套都想管那 1 個跨界位,需確認不打架。

- WS 階段在瀏覽器啟動前先跑(`new_main_v2.py:159`),所以同一次喚醒 WS plan 一定先於 reconcile;10:00 由 WS 搶到後,reconcile 走 current-parked 模型理應看到 cross 已達標而不動,WS 搶輸時 reconcile 可當 fallback 補搶。
- **決策規則(規劃期讀 `utils/carpark_auto.py` `reconcile()` 後選一)**:
  - 若 reconcile 純加法(只在低於 target 時補停、從不搬走/收掉超額跨界車)→ 保留 `daytime_cross:1` 當 fallback,不改 reconcile。
  - 若 reconcile 會搬動或移除跨界車(可能覆蓋 WS 的精準搶位)→ 將 5554 / 7fe98fc6 的 `carpark.daytime_cross` 與 `nighttime_cross` 設為 `0`,讓 WS plan 獨佔跨界(對齊手機fc 不靠 reconcile 管跨界的事實),reconcile 只管本服 total。

### Part 4 — 驗證

- Unit test(`tests/test_sleep_service.py`):新增「`_maybe_resume_sleep` 會把返回休眠的 `resume_sleep_until_ts` clamp 到 carpark `next_ts`」測試,並 pin「無 next_ts / next_ts 已過 / next_ts 晚於原 wake 時不動」。沿用該檔既有的 `next_ts_loader` monkeypatch 注入手法。
- 語法/聚焦測試:`python -m py_compile runtime_services/sleep_service.py` + `python -m pytest tests/test_sleep_service.py -q`。
- Live:用 dashboard manual-hold 取得一台(5554)獨佔控制(勿干擾正在跑的 bot 執行緒),觀察 log:09:59 提前喚醒(`跨界車位排程：喚醒提前 …`)+ 10:00 `pre-open wait … (grab)` + 實際搶位結果。

## 不在範圍

- 5556 / 5560(無 WS 後端)→ 使用者本輪明確不處理。
- 5558 → 使用者明確排除。
- poll 迴圈逐秒重讀 next_ts 的通用「每日強制喚醒」設定 → YAGNI,未來若要納入無 WS 裝置再評估。

## 風險

- reconcile 與 WS plan 對跨界位的互動(Part 3)是唯一需 live 確認的不確定點;決策規則已備好兩條路。
- 啟用 plan 後 5554/7fe98fc6 會在 09:59 提前醒(比原本 10:00 早 1 分鐘),其餘任務排程不受影響(clamp 只提前,且只在有 next_ts 時生效)。
