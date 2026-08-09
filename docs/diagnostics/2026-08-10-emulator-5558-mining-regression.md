# emulator-5558 挖礦失敗與競技場「對戰記錄」殘留診斷

- 診斷日期：2026-08-10
- 主要設備：`emulator-5558`（修哥帳號，`web_h5`）
- 狀態：已確認根因，尚未修改功能程式碼
- 目的：提供後續修復模型可直接使用的證據、失敗鏈與驗收邊界

## 結論摘要

5558 的挖礦問題不是 Planner 選錯格子，而是兩個近期回歸疊加：

1. 競技場 Cocos 收尾程式主動點擊「記錄」，打開「對戰記錄」彈窗，導致下一個挖礦任務進場前不在主頁。
2. 即使下一輪重啟瀏覽器、成功通過主頁守衛並進入礦場，`mining_service` 仍在設定前置檢查拋出 `TypeError: 'DeviceConfig' object is not iterable`，第 0 輪就停止。
3. `mining_service.run()` 吞掉例外並回傳 `None`；外層 `oracle()` 無法區分失敗與成功，仍記錄「挖礦任務已完成」。因此主日誌存在假成功。

所以使用者看到的是「5558 沒有正常挖礦」，但主日誌可能同時顯示完成；必須看 `miner.log` 才會看到真實失敗。

## 為什麼程式會打開「對戰記錄」

### 1. 舊 OCR 收尾並不是按文字本身

提交 `662db41fc234c13cd8478098deff7966a917f08b` 之前，競技場收尾使用：

```python
img_tools.click_str_by_server(d, "刷新", y_range=(711, 782), shift_y=60)
time.sleep(1)
img_tools.click_str_by_server(
    d,
    "記錄",
    y_range=(831, 865),
    x_range=(437, 521),
    shift_y=60,
    wait_timeout=5,
)
```

這段看起來像要按「刷新」和「記錄」，但兩次都有 `shift_y=60`。文字只是 OCR 定位錨點，真正點擊位置在文字下方約 60 像素，用來命中相鄰的底部操作／離開區域，而不是直接按文字按鈕。

### 2. Cocos 遷移誤解了舊行為

2026-08-04 的提交 `662db41f` 將收尾改成 `CocosArena.finish()`：

```python
def finish(self) -> None:
    if self.ui.has_text("刷新"):
        self.ui.click_text("刷新")
    if self.ui.has_text("記錄"):
        self.ui.click_text("記錄")
```

`CocosUI.click_text()` 會命中文字節點本身；它沒有舊 OCR 的 `shift_y=60` 語意。因此第二個動作確實會點擊競技場介面的「記錄」按鈕，正常結果就是打開「對戰記錄」彈窗。

這不是隨機座標誤觸，而是遷移時把「以文字為錨點的位移點擊」錯誤翻譯成「點擊文字本身」。

相關程式：

- `game_actions/cocos_arena.py:34-38`
- `utils/cocos_ui.py:103-126`
- `game_actions/arena_battle.py:234-245`

### 3. OCR fallback 狀態在收尾階段又被覆蓋

5558 的主日誌經常先出現：

```text
競技場 Cocos 進場未驗證，退回 OCR
```

進場失敗時，`run_arena_challenges()` 會將區域變數 `cocos` 設成 `None`，戰鬥流程改走 OCR。這個決策本來應該一路保留到收尾。

但目前收尾程式重新呼叫 `_cocos_arena(d)`：

```python
cocos = _cocos_arena(d)
if cocos is not None:
    cocos.finish()
else:
    # OCR 收尾
```

只要設備仍是 `web_h5` 且 Playwright page 存在，`_cocos_arena(d)` 就會建立新的 `CocosArena`，不會反映先前「Cocos 進場未驗證、已退回 OCR」的事實。於是 5558 雖然戰鬥走 OCR，收尾仍強制走有問題的 Cocos `finish()`，最後打開記錄彈窗。

### 4. `finish()` 沒有驗證收尾結果

`CocosArena.finish()` 的回傳型別是 `None`，沒有檢查：

- 「對戰記錄」是否被打開；
- 競技場是否已退出；
- 是否已回到主頁；
- 收尾點擊是否實際成功。

所以競技場任務照樣返回，`daily_tasks.click_arena_challenges()` 隨即寫入每日完成記錄並輸出「競技場每日挑戰完成」。下一個挖礦任務才是第一個發現頁面不對的任務。

## 2026-08-10 的實際失敗鏈

來源：

- `logs/emulator-5558/main.log`
- `logs/emulator-5558/action_trace/events_20260810.jsonl`
- `logs/emulator-5558/error_screenshots/20260810_000940_223720_挖礦_Oracle_前不在主頁面_未知.jpg`

事件順序：

1. 競技場完成第 3 場戰鬥。
2. `wait_for_any_text()` 偵測戰鬥結果並在 `00:09:39.317` 點擊 `(272, 218)` 關閉／推進戰鬥結果。
3. `run_arena_challenges()` 進入收尾階段，重新建立 `CocosArena`。
4. `CocosArena.finish()` 透過 Playwright/Cocos 直接點擊「刷新」與「記錄」。這類 page evaluate 點擊不會表現成 `MonitoredDevice.tap`，所以 action trace 不會出現對應的裝置座標點擊。
5. 「對戰記錄」彈窗被打開並留在畫面上。
6. `00:09:40`，挖礦的主頁守衛執行 OCR，stage 判定為「未知」。
7. Smart screenshot 明確拍到「對戰記錄」彈窗。
8. 後續菇菇武道會、菇菇雕像、航海任務也連續判定不在主頁。
9. pipeline 達到連續 4 個任務不在主頁的門檻，強制關閉 web_h5 瀏覽器，等待下一次喚醒。

主日誌關鍵行：

```text
2026-08-10 00:09:40 ... 挖礦/Oracle 前不在主頁面，stage=未知
2026-08-10 00:09:42 ... 連續 4 個任務不在主頁面，中止本輪 pipeline，強制關閉 app
```

相同「對戰記錄」畫面也出現在 8 月 5、7、8、9 日的 5558 錯誤截圖。5554、5556、5560 等 web_h5 裝置也已出現相同症狀，因此這是共用競技場收尾回歸，不是 5558 專屬版面問題。

## 第二個獨立回歸：挖礦設定型別錯誤

下一輪瀏覽器重啟後，「對戰記錄」彈窗會消失，5558 可以重新進入主頁與礦場。但 `miner/mining_service.py:960-962` 目前執行：

```python
loaded_cfg = config_manager.get_device_config(ip)
device_cfg = dict(loaded_cfg or {})
```

`config_manager.get_device_config()` 自 2026-05-16 起回傳 `DeviceConfig` dataclass。它只提供相容用的 `.get()`，沒有實作 mapping iteration，因此 `dict(DeviceConfig)` 會拋出：

```text
TypeError: 'DeviceConfig' object is not iterable
```

正確的現有 API 邊界是：

- 需要 typed config：使用 `get_device_config()`，直接呼叫屬性或 `.get()`；
- 需要完整 dict：使用 `get_device_config_dict()`。

這個錯誤由 2026-08-05 的提交 `6fc25576bfa1c040b78fb654f187b29758e48e19` 引入。該提交為了強化 telemetry lifecycle，新增設定前置檢查時誤用了 `dict(loaded_cfg)`。

5558 從 8 月 6 日第一次執行新版後，到現有日誌最後一筆共找到 22 個設定前置檢查失敗 session；每個 session 都是：

- `rounds=0`
- `screenshot_calls=0`
- `classify_calls=0`
- `stopped_reason="exception"`

因此目前沒有證據顯示 A*、`final_v1`、分類器或 Executor 在這些 session 中曾經真正執行。

## 為什麼主日誌仍然顯示挖礦成功

`mining_service.run()` 在設定前置檢查失敗時記錄 exception 後直接 `return`，沒有回傳失敗狀態，也沒有重新拋出例外。

`game_actions/miner_action.py:46-50` 則無條件執行：

```python
run_mining(...)
time_recording(ip, name="挖礦")
logger.info(f"[{ip}] 挖礦任務已完成並記錄。")
```

因此 `run_mining()` 正常返回 `None` 可能代表：

- 真正完成挖礦；
- 設定前置檢查失敗；
- 啟動 map recorder 失敗；
- 其他被 service 內部吞掉的 fatal error。

外層無法區分，導致失敗仍寫入冷卻記錄。8 月 9 日 02:07、04:07、12:07、16:07 都能看到 `miner.log` 報 exception、`main.log` 同一秒報完成的矛盾證據。

## 建議修復邊界

### P0：修復 mining config preflight

建議擇一：

1. `mining_service` 改用 `config_manager.get_device_config_dict(ip)`；或
2. 保留 `DeviceConfig`，後續全部使用 `.get()`／typed attributes，不轉 dict。

不要在 `DeviceConfig` 上新增寬鬆的 `__iter__` 只為掩蓋此呼叫點，否則會模糊 typed config 與 raw dict 的既有 API 邊界。

### P0：停止挖礦假成功

應讓 service 明確回傳 outcome，例如 `success/completed/stopped_reason`，或在 fatal preflight error 時重新拋出可辨識例外。外層只有在確認成功時才能：

- `time_recording(ip, name="挖礦")`
- 輸出「挖礦任務已完成並記錄」

`ForceSleepRequested` 仍必須維持原本向上傳遞語意。

### P1：修復競技場收尾

最低要求：

1. 不得直接以 `click_text("記錄")` 作為離開競技場的方式。
2. Cocos 進場失敗而退回 OCR 後，收尾必須保持同一路徑，不得重新建立 Cocos driver 覆蓋 fallback 決策。
3. Cocos 成功路徑也要實作真正的「關閉結果／退出競技場／回主頁」。
4. 收尾必須回傳成功與否，並驗證最終頁面；不能只發出點擊後忽略狀態。

若要透過 Cocos tree 操作，應先找出關閉按鈕或底部離開節點的穩定 node/name。不要直接照抄舊 OCR 的 `shift_y=60` 座標，除非已確認不同 viewport 與縮放下仍穩定。

### P1：主頁守衛的恢復能力

這不是第一根因，但可以降低連鎖影響：若偵測到競技場「對戰記錄」，stage guard 可嘗試點擊彈窗 X、退出競技場並回主頁，再決定是否跳過任務。恢復邏輯不能取代競技場本身的正確收尾。

## 建議回歸測試

### 競技場

1. `CocosArena.finish()` 不得呼叫 `click_text("記錄")`。
2. Cocos enter 失敗、OCR fallback 成功時，收尾不得重新切回 Cocos。
3. 以假 Cocos UI state machine 模擬：競技場列表 → 三場完成 → 收尾；最終狀態必須是主頁，不能是對戰記錄。
4. 收尾驗證失敗時，不得寫入競技場每日完成記錄。
5. web_h5 live smoke：完成三場後截圖不得包含「對戰記錄」，stage 必須是主頁。

目前 `tests/test_cocos_arena.py` 只驗證 enter 與 animation fight 不呼叫 OCR，沒有覆蓋 `finish()`，所以回歸能通過既有測試。

### 挖礦

1. 使用真實 `DeviceConfig.from_dict(...)` 執行 config preflight，確保不會嘗試 `dict(DeviceConfig)`。
2. 同時覆蓋 typed config 與 raw dict API，明確鎖住呼叫邊界。
3. config preflight 例外時，外層不得呼叫 `time_recording()`。
4. fatal service error 不得輸出「挖礦任務已完成」。
5. 正常 session 至少必須進入一輪或回傳可辨識的正常停止理由，不能以單純 `None` 代表所有 outcome。

目前 `tests/test_mining_service_final_v1.py` 的 helper 將 `get_device_config` mock 成 plain dict，因此無法捕捉 production 回傳 `DeviceConfig` 的型別落差。

## 修復後驗收條件

修復模型至少應提供以下證據：

1. 相關目標測試通過，且新增上述缺口的回歸測試。
2. 5558 執行競技場後能回主頁，不再產生「挖礦/Oracle 前不在主頁面」的對戰記錄截圖。
3. 5558 的 `miner.log` 不再出現 `DeviceConfig object is not iterable`。
4. 真正的挖礦 session 有 `rounds > 0`、`screenshot_calls > 0`、`classify_calls > 0`，或有明確且合理的非錯誤停止原因。
5. service 失敗時，主日誌不得出現「挖礦任務已完成並記錄」。
6. 檢查其他 web_h5 裝置，避免只對 `emulator-5558` 寫特例。

## 不建議的修法

- 不要只在挖礦前硬點一次返回鍵；這會掩蓋競技場錯誤收尾，且對不同彈窗狀態不可靠。
- 不要只把 stage「未知」當成主頁放行；目前錯誤截圖明確不是主頁。
- 不要只修 5558 設定；兩個根因都在共用程式碼，其他 web_h5 裝置也受影響。
- 不要只改成功日誌文字而保留錯誤的時間記錄；冷卻記錄也必須依真實 outcome 決定。
- 不要先調整 Planner 權重或分類器；現有失敗 session 根本沒有進入盤面分類與規劃階段。

## 相關檔案與提交

- `game_actions/arena_battle.py`
- `game_actions/cocos_arena.py`
- `utils/cocos_ui.py`
- `game_actions/daily_tasks.py`
- `game_actions/stage_guard.py`
- `game_actions/miner_action.py`
- `miner/mining_service.py`
- `config_manager.py`
- `tests/test_cocos_arena.py`
- `tests/test_mining_service_final_v1.py`
- `662db41fc234c13cd8478098deff7966a917f08b`：Cocos OCR migration，導入競技場記錄彈窗回歸
- `6fc25576bfa1c040b78fb654f187b29758e48e19`：mining telemetry lifecycle，導入 `dict(DeviceConfig)` 回歸
