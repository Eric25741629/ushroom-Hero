# WS 開神燈：百分比 / 最低保留 + Dashboard 進度顯示 設計

日期：2026-06-13
狀態：設計已過使用者口頭核可
對象：所有啟用 WS 開神燈的裝置（`ws_token.open_lamp`）

## 1. 需求

WS 開神燈目前只會「一直開到沒燈」。使用者要三件事：

1. **Dashboard 進度**：開神燈時看到 `(已開 / 本輪目標)`，例如 `(0/10000)` 跑到 `(10000/10000)`。
2. **百分比設定**：依當前神燈總數的百分比決定本輪要開幾顆。
   例：有 100 萬神燈、設 1% → 開 1 萬。**目標數須能被 20 整除，不是則取最接近的 20 倍數。**
3. **最低保留**：剩餘神燈的硬地板。例：設 50 萬最低保留 → 最多只開到剩 50 萬，不會更低。

兩個使用者已確認的關鍵語義：
- **百分比每輪重算**：每次喚醒讀當前總數，`target = round20(總數 × %)`，總數隨開燈縮小、下輪重算。
- **顯示分母 = 本輪目標數**（不是擁有總數）。

## 2. 現況（已存在的零件，勿重造）

| 零件 | 位置 | 現況 |
|------|------|------|
| WS 開神燈核心 | `ws_token/lamp.py` `open_lamp()` | 逐批開 20，開到 server 回沒燈為止；**不讀神燈數、無 %/保留概念** |
| runner 串接 | `ws_token/runner.py` `_run_lamp()` | `open_lamp(dry_run=False, batch_num=20, max_batches=500)`（10000 上限） |
| 純 WS 迴圈 | `runtime_services/ws_runner_service.py` | 讀 flat key `ws_token_open_lamp`；另讀巢狀 `ws_token` dict（carpark_plan） |
| WS-first 階段 | `game_actions/ws_phase.py` | 讀巢狀 `ws_token` dict（`open_lamp` 等） |
| 進度回報 | 兩路徑的 `_progress(name, status, detail)` | 只回任務層 start/ok/error 字串 → `bot_state.update_state(step=...)` |
| 設定 UI | `templates/dashboard.html` `chkWsOpenLamp` 區 | 勾選框；存進巢狀 `ws_token`（淺併保留其餘欄位） |
| config 預設 | `config_manager.py` `DEFAULT_DEVICE_CONFIG["ws_token"]` | 有 `open_lamp: True` |

### 神燈數量協議（已驗，EQUIPMENT_SCHEMA.md §9-10）
- 神燈 = `item_id 1001`。
- 消耗時 server 推 `0x0402` evt=`1001006`，`f2 sub { item_id=1001, qty=當前剩餘 }`，每自動開 20 → qty −20。
- 登入時也有 `0x0402` 庫存快照（evt 9700002 完整素材 / 9800004 單一道具）——**是否含 1001 待 live 驗證**。

## 3. 設計

### 3.1 Config（單一真相 = 巢狀 `ws_token` dict）

`DEFAULT_DEVICE_CONFIG["ws_token"]` 新增：
- `lamp_percent`: float，預設 `0`（= 不依百分比）。例 `1.0` = 1%。
- `lamp_min_keep`: int，預設 `0`（= 無下限）。

兩條執行路徑都改成從巢狀 `ws_token` dict 取這兩值（`ws_runner_service` 已有 `_ws_nested` 取法，`ws_phase` 直接讀 `cfg`），不新增 flat key，避免雙來源。

### 3.2 神燈數量讀取（`lamp.py` 新增）

新增 push handler 監聽 `0x0402`，從含 `item_id=1001` 的 frame 取 `qty`（當前剩餘），即時維護 `remaining`。

開燈前算總數，主 + 備援：
- **主**：登入 settle 期間的 `0x0402` 快照若已帶 1001 → 直接拿總數（可在開 0 顆前就決定，完全尊重最低保留）。
- **備援**：若 settle 後仍未知 → 開第一批 20，由 `1001006` 推送反推 `初始總數 = 剩餘 + 20`。

> Live 驗證項：確認登入快照是否含 1001。若含，純百分比/最低保留可在不開任何一批前判斷；若不含，最低保留在極端邊界最多誤差 20 顆（先開一批才知道數量），可接受。

### 3.3 開燈邏輯（改寫 `open_lamp`）

新增參數 `lamp_percent: float = 0.0`、`lamp_min_keep: int = 0`、`on_progress: Callable[[int,int],None] | None = None`。

```
total = current_lamp_count()                    # 3.2 取得（主/備援）
floor_cap   = max(0, total - lamp_min_keep)     # 最低保留地板
percent_amt = total * lamp_percent / 100.0      # 百分比量（lamp_percent>0 才算）

if lamp_percent > 0 and lamp_min_keep > 0:
    raw = min(percent_amt, floor_cap)           # 兩者都設 → 取較小（你的 51 萬例 → 開到剩 50 萬）
elif lamp_percent > 0:
    raw = percent_amt
elif lamp_min_keep > 0:
    raw = floor_cap
else:
    raw = total                                 # 都沒設 → 維持現行（開到沒燈）

target = round_to_nearest_20(raw)               # 「能被 20 整除、取最接近」
target = min(target, _LAMP_MAX_BATCHES * 20)    # 每輪安全上限（預設 10000，沿用）
```

`round_to_nearest_20(n) = int(round(n / 20.0)) * 20`。

逐批開 20，**停止條件**（任一成立即停）：
- `opened >= target`
- server 回沒燈（現行的 timeout / CMD_ERROR / 空 drops）
- `remaining <= lamp_min_keep`（每批讀 1001006 後檢查）

每批結束呼叫 `on_progress(opened, target)`。回傳 dict 加 `target`、`initial_count`、`remaining` 欄位。

### 3.4 串接

- `runner.run_device(...)` 新增 `lamp_percent`、`lamp_min_keep` 參數 → `_run_lamp(client, percent, min_keep, on_progress)` → `open_lamp(...)`。
- `_run_lamp` 把 `on_progress` 接成更新 `bot_state` 的 callback（由上層注入，runner 保持不依賴 bot_state；見 3.5）。
- `ws_runner_service.run_ws_device_cycle` 與 `ws_phase.run_ws_phase` 從巢狀 `ws_token` 取 `lamp_percent`/`lamp_min_keep` 傳入，並提供 on_progress。

### 3.5 Dashboard 進度顯示

`open_lamp` 的 `on_progress(opened, target)` 由兩路徑接成：
```
bot_state.update_state(ip, task="WS 任務", step=f"WS 開神燈 ({opened}/{target})")
```
**節流**：每批都更新即可（批間已有 0.2s delay，不會洗版），或每 N 批更新一次。沿用現有 dashboard 顯示 `step` 的機制，前端不必改。

### 3.6 設定 UI（`dashboard.html`）

`chkWsOpenLamp` 旁新增兩個數字輸入：
- `inpWsLampPercent`「開神燈百分比 %」（0 = 不依百分比）
- `inpWsLampMinKeep`「最低保留神燈數」（0 = 無下限）

load：從 `config.ws_token.lamp_percent` / `lamp_min_keep` 帶入。
save：寫進 `payload.ws_token`（與現有 `open_lamp` 同一淺併物件，保留其餘欄位）。

## 4. 測試（TDD）

單元（純函式、可離線）：
- `round_to_nearest_20`：邊界（10001→10000、10011→10020、19→20、9→0）。
- target 計算四組合（只%/只保留/兩者/皆無）+ 安全上限 clamp + 51 萬例。
- `open_lamp`：fake client，min_keep 提前停、達 target 停、沒燈停；備援反推初始總數；`on_progress` 每批觸發、回傳 target/initial。
- config 串接：`run_device` → `open_lamp` 參數正確傳遞（monkeypatch `_load_lamp`）。
- `ws_runner_service` / `ws_phase` 從巢狀 dict 取值並傳入（既有測試風格）。
- dashboard 模板：`test_dashboard_template.py` 加上新輸入欄位存在 + load/save 對應。

Live 驗證（使用者真帳號；dashboard manual-hold 取得獨佔控制）：
- 確認登入快照是否含 1001 / `1001006` qty 語義。
- 設一個小 % 與一個最低保留，觀察 dashboard `(opened/target)` 與實際剩餘。

## 5. 影響檔案

`ws_token/lamp.py`、`ws_token/runner.py`、`runtime_services/ws_runner_service.py`、
`game_actions/ws_phase.py`、`config_manager.py`、`templates/dashboard.html`、
對應 `tests/test_*`。

## 6. 風險 / 非目標

- 風險：百分比可能算出極大目標（如 50% × 千萬）。由 `_LAMP_MAX_BATCHES` 安全上限 + 每輪重算自然分攤多次喚醒處理；不在單輪無限開。
- 非目標：不改裝備比對/賣出邏輯（`decide_v2` 維持原樣）；不動 Playwright(OCR) 開神燈路徑。
