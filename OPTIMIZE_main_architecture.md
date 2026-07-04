# 菇勇者全自動掛機 — 主程式架構優化建議

> 分析日期：2026-05-27
> 分析範圍：main.py, new_main_v2.py, game_api.py, game_initialization.py, config_manager.py, bot_state.py, event_manager.py, device.py, device_wrapper.py, adb_operations.py, adb_devices.py

---

## 一、整體架構現況

### 模組職責概覽

| 模組 | 行數 | 職責 |
|------|------|------|
| `new_main_v2.py` | 487 | **實際主程式**（main.py 為空），設備生命週期管理 + 主循環 |
| `bot_state.py` | 636 | 全域設備狀態、暫停/恢復控制、互檢信箱、Web 啟動請求 |
| `config_manager.py` | 583 | JSON 設定檔讀寫、Typed DeviceConfig、OCR 設定 |
| `device_wrapper.py` | 1346 | MonitoredDevice (ADB 封裝) + PlaywrightGameDevice (Web H5) |
| `game_initialization.py` | 520 | 啟動頁面處理、彈窗清理、線上互檢 (protocol + OCR) |
| `adb_operations.py` | 624 | ADB 指令執行、u2 連線、模擬器重啟、螢幕操作 |
| `adb_devices.py` | 104 | ADB 設備列舉、分身啟動 |
| `device.py` | 128 | 螢幕截圖驗證、通知清理 |
| `event_manager.py` | 230 | 優先級事件隊列 + Flask API |
| `game_api.py` | 322 | Flask 網頁控制面板 API |

### 啟動流程

```
__main__
  ├─ ensure_push_server_started()
  ├─ control_panel_app.run_server() (master) / ensure_worker_webhook_started() (worker)
  ├─ load CNN models
  └─ while True:
       scan_and_start_devices()
         └─ 對每個新設備 spawn Thread → main(ip, ...)
              ├─ bot_state.init_device(ip)
              ├─ setup_logger_for_device(ip)
              ├─ _handle_startup_sleep()
              ├─ initialize_runtime_device() → connect_u2 + MonitoredDevice
              └─ while True: (主循環)
                   ├─ check_force_sleep / handle_pending_web_launch
                   ├─ handle_device_wakeup()
                   ├─ screenshot → check_in_game → get_stage_with_check
                   ├─ 啟動遊戲 (若不在遊戲中)
                   ├─ daily_pipeline.run(DailyContext(...))
                   └─ run_sleep_cycle()
```

---

## 二、問題分析與優化建議

### 🔴 P0 — 嚴重問題

#### 1. `main()` 函數過於龐大 (487 行)，職責不清

**現狀：** `main()` 承擔了設備初始化、喚醒檢查、遊戲啟動、日常任務執行、休眠管理、錯誤恢復等全部職責。巢狀 try/except 深達 4-5 層，`while(1)` 主循環內含 200+ 行邏輯。

**影響：**
- 新功能難以插入正確位置
- 錯誤處理路徑難以追蹤（ForceSleepRequested / StartupBypassError / LoginConflictError 各自不同的 sleep_policy）
- 難以撰寫單元測試

**建議：**

```python
# 重構為狀態機模式
class DeviceLifecycle:
    """單一設備的生命週期管理器"""
    
    def __init__(self, ip, cnn_model, ...):
        self.ip = ip
        self.state = "INIT"
        self.device = None
        self.logger = setup_logger_for_device(ip)
    
    def run(self):
        """主入口：狀態機驅動"""
        while True:
            match self.state:
                case "INIT":       self._handle_init()
                case "WAKEUP":     self._handle_wakeup()
                case "GAME_START": self._handle_game_start()
                case "RUN_TASKS":  self._handle_run_tasks()
                case "SLEEP":      self._handle_sleep()
                case "SHUTDOWN":   break
    
    def _handle_init(self):
        """設備初始化（含 force-sleep 中斷）"""
        ...
    
    def _handle_wakeup(self):
        """喚醒與解鎖"""
        ...
    
    def _handle_game_start(self):
        """遊戲啟動與頁面穩定"""
        ...
    
    def _handle_run_tasks(self):
        """日常任務管線"""
        ...
    
    def _handle_sleep(self):
        """休眠週期"""
        ...
```

每個 `_handle_*` 方法不超過 50 行，異常處理集中在 `run()` 的頂層 try/except。

---

#### 2. `bot_state.py` 過度集中 — 全域狀態 + 控制信號 + 互檢信箱全在同一模組

**現狀：** `bot_state.py` 同時管理：
- 設備狀態 (`_states`, `_locks`)
- 暫停/恢復控制 (`_pause_events`)
- 一次性旗標 (`_skip_sleep_flags`, `_force_sleep_flags`, `_manual_release_flags`)
- Web 啟動請求信箱 (`_web_launch_requests`)
- 互檢請求/回應信箱 (`_online_check_requests`, `_online_check_queue_by_checker`)
- 截圖耗時追蹤 (`_screenshot_windows`)
- 刷新旗標 (`_refresh_needed`)

**影響：** 636 行全在同一模組，任何變更都可能影響其他功能。全域字典多達 10+ 個，鎖策略分散。

**建議：** 拆分為獨立的管理器：

```
bot_state/
  __init__.py           # 向後相容 re-export
  device_registry.py    # _states, _locks, init/set_offline/update/get_all
  pause_controller.py   # _pause_events, check/set_pause
  flag_manager.py       # skip_sleep, force_sleep, manual_release, refresh
  web_launch_mailbox.py # web_launch_requests
  online_check_mailbox.py # online_check_requests/queue
  screenshot_tracker.py # screenshot_windows
```

每個子模組維護自己的鎖，避免 `_global_lock` 成為瓶頸。

---

#### 3. 線程安全隱患 — `_global_lock` 粒度不當

**現狀：** `bot_state.py` 使用 `_global_lock` 保護所有全域字典的操作。但多個方法在釋放 `_global_lock` 後再獲取 per-device lock（如 `clear_offline_devices()`），存在 TOCTOU 競態視窗。

**具體風險：**
```python
def clear_offline_devices():
    with _global_lock:  # 第一把鎖
        to_remove = [ip for ip, st in _states.items() if ...]
    
    for ip in to_remove:
        with get_device_lock(ip):  # 第二把鎖，中間有視窗
            st = _states.get(ip)
            if st is None or str(st.get("status")) != "OFFLINE":
                continue  # 已重新上線，跳過
```

雖然程式碼有二次驗證，但 `_locks.pop(ip, None)` 在 `_global_lock` 內執行，可能與 `get_device_lock()` 競態。

**建議：**
- 使用讀寫鎖 (`threading.RLock`) 替代粗粒度全域鎖
- 或將 device 註冊表改為 `concurrent.futures.ThreadPoolExecutor` + per-device 鎖的模式
- 對 `_pause_events` 的操作（`Event.set()/clear()`）本身是線程安全的，不需要在 `_global_lock` 內執行

---

### 🟠 P1 — 重要問題

#### 4. 重複的 `run_adb()` 函數定義

**現狀：** `adb_operations.py` 和 `adb_devices.py` **各自定義了 `run_adb()`**，邏輯幾乎相同但細節不同：
- `adb_operations.py`: 不指定 encoding，`errors` 未設定
- `adb_devices.py`: 指定 `encoding='utf-8'`, `errors='replace'`

**風險：** 其他模組 import 不同版本可能導致編碼行為不一致。

**建議：** 統一為一個 `run_adb()`，放在 `adb_operations.py`，`adb_devices.py` 改為 `from adb_operations import run_adb`。統一使用 `encoding='utf-8', errors='replace'`。

---

#### 5. `device.py` 與 `device_wrapper.py` 職責混淆

**現狀：**
- `device.py` (128 行): 包含 `device` 類別（截圖驗證）+ `get_adb_devices()` + `close_nofication()` / `open_nofication()`（拼寫錯誤）
- `device_wrapper.py` (1346 行): `MonitoredDevice` + `PlaywrightGameDevice` + 全域 Web 設備註冊表

**問題：**
- `device.py` 的 `device` 類別名稱與模組同名，造成 import 混淆
- `get_adb_devices()` 放在 `device.py` 但語義上屬於 ADB 操作
- `open_nofication` / `close_nofication` 拼寫錯誤（應為 notification）

**建議：**
- `device.py` → 重命名為 `screen_utils.py`，只保留截圖驗證相關
- `get_adb_devices()` → 移至 `adb_devices.py`（已有同名模組）
- `open_nofication` / `close_nofication` → 修正拼寫並移至 `adb_operations.py`

---

#### 6. `device_wrapper.py` 過大 (1346 行)，混合兩種後端

**現狀：** `MonitoredDevice`（ADB u2 封裝）和 `PlaywrightGameDevice`（Web H5 Playwright 封裝）共存於同一檔案。兩者的底層 API 完全不同，僅透過 `__getattr__` 委派和 `backend_kind` 屬性區分。

**建議：**
```
device/
  __init__.py              # re-export
  base.py                  # 抽象介面 (Protocol/ABC)
  adb_device.py            # MonitoredDevice
  web_device.py            # PlaywrightGameDevice
  registry.py              # _WEB_DEVICE_REGISTRY, create/get/close
```

定義統一的 `DeviceProtocol`（或 ABC），明確列出所有共用方法：

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class GameDevice(Protocol):
    backend_kind: str
    def tap(self, x: int, y: int) -> bool: ...
    def click(self, x: int, y: int) -> bool: ...
    def swipe(self, x0, y0, x1, y1, duration: float = 0.2) -> bool: ...
    def screenshot(self, format=None): ...
    def app_start(self, pkg_name: str) -> bool: ...
    def app_stop(self, pkg_name: str) -> bool: ...
    def press(self, key: str) -> bool: ...
    def close(self) -> None: ...
    def is_alive(self) -> bool: ...
```

---

#### 7. 硬編碼的設備特定邏輯散佈各處

**現狀：** 多處出現設備特定的條件判斷：

```python
# new_main_v2.py
protect = False if ('emulator-5558' in ip or 'emulator-5562' in ip 
                     or '7fe98fc6' in ip or 'fc65396d' in ip) else True

if ip == 'emulator-5554':
    has_req = bot_state.has_pending_online_check_request('emulator-5554')
    ...

if 'fc65396d' in ip or '192.168' in ip:
    reset_screen_settings(ip, logger=logger)
    ...
```

```python
# game_initialization.py
target_cfg = config_manager.get_device_config('emulator-5558')  # 硬編碼
```

**影響：** 新增設備時必須搜尋所有檔案修改條件判斷。

**建議：**
- 在 `bot_config.json` 的設備設定中增加 `is_real_phone`, `enable_online_check`, `enable_screen_reset` 等布林旗標
- 將「是否保護」、「是否需要螢幕重置」等邏輯改為讀取 config flag
- 線上檢查的目標設備改為 config-driven：`config_manager.get_device_config(ip).get("online_check_target")`（線上檢查已由純 WS `runtime_services/online_check_service.py` + `ws_online_checker.check_via_ws` 承接，舊 `check_on_line()` 於 2026-07-05 移除）

---

#### 8. 事件系統 (`event_manager.py`) 與主程式完全脫節

**現狀：** `event_manager.py` 定義了完整的事件驅動架構（EventType, EventPriority, EventQueue, EventManager, EventDaemon），但 **`new_main_v2.py` 的主循環完全沒有消費事件隊列**。事件系統僅被 `game_api.py` 的 Flask API 使用。

`bot_state.py` 自己實作了一套平行的控制機制（pause/skip_sleep/force_sleep/web_launch），與事件系統功能重疊。

**建議：**
- **方案 A（推薦）：** 刪除 `event_manager.py`，將 `game_api.py` 改為直接調用 `bot_state` 的控制 API（已有 Flask 路由在 `control_panel_app.py` 中）
- **方案 B：** 整合事件系統到主循環，在主循環開頭加入事件消費：

```python
# 主循環開頭
event = event_manager.get_next_event(timeout=0)
if event:
    _handle_event(event, ip)
```

---

### 🟡 P2 — 改進建議

#### 9. ~~`game_initialization.py` 的 `check_on_line()` 職責過重~~ ✅ 已解決（2026-07-05）

> 此 finding 已作廢：舊 `check_on_line()`（含 protocol path 與 `_check_on_line_via_ocr_legacy` OCR path）
> 已於 2026-07-05 死碼清理整個移除。線上檢查改由純 WS 的 `runtime_services/online_check_service.py`
> + `ws_online_checker.check_via_ws` 承接，不再有 OCR 硬等待。以下策略模式提案僅存歷史參考。

**原現狀（已移除）：** `check_on_line()` 曾包含 protocol + OCR 兩條獨立檢查路徑。

**原建議（已作廢）：**
- 將 protocol path 和 OCR path 拆為獨立的 strategy 類別
- 使用策略模式選擇檢查方式：

```python
class OnlineCheckStrategy(ABC):
    @abstractmethod
    def check(self, target_ip: str, config: dict) -> Optional[bool]: ...

class ProtocolCheckStrategy(OnlineCheckStrategy): ...
class OCRCheckStrategy(OnlineCheckStrategy): ...
class CrossVerifyStrategy(OnlineCheckStrategy):
    """雙重驗證：兩者都判離線才放行"""
    def __init__(self, strategies: list[OnlineCheckStrategy]): ...
```

---

#### 10. `config_manager.py` 的 `load_config()` 每次呼叫都讀磁碟

**現狀：** `load_config()` 在 `_config_lock` 內每次都開啟 `bot_config.json` 讀取 + 自動補全 + 可能寫回。在高頻呼叫場景下（每個設備每輪循環多次呼叫 `get_device_config()`），會產生不必要的 I/O。

**建議：**
- 引入快取層：讀取後記住 mtime，僅在檔案變更時重新讀取
- 或使用 `watchdog` 監聽檔案變更事件
- 將 `load_config()` 改為：

```python
_config_cache = None
_config_mtime = 0.0

def load_config():
    global _config_cache, _config_mtime
    with _config_lock:
        current_mtime = os.path.getmtime(CONFIG_FILE)
        if _config_cache is not None and current_mtime == _config_mtime:
            return _config_cache
        # ... 讀取並補全 ...
        _config_cache = data
        _config_mtime = current_mtime
        return data
```

---

#### 11. 錯誤處理不一致 — 混用 `logger` 與 `print`

**現狀：**
- `config_manager.py`: `save_config()` 和 `update_device_config()` 使用 `print()` 而非 `logger`
- `device.py`: `close_nofication()` / `open_nofication()` 使用 `print()` 而非 `logger`
- `adb_operations.py`: `connect_u2_with_retries()` 使用 `safe_log()` 包裝，但 `start_game_by_icon()` 直接傳入 logger

**建議：** 統一使用 `logging` 模組。在每個模組頂部：

```python
logger = logging.getLogger(__name__)
```

消除所有 `print()` 呼叫，改為 `logger.info/warning/error`。

---

#### 12. `MonitoredDevice.__getattr__` 的隱性委派

**現狀：** `MonitoredDevice` 透過 `__getattr__` 將未定義的方法全部轉發給底層 `u2.Device`。這意味著：
- 底層 API 的任何變更都會靜默傳播
- 無法在 IDE 中自動補全可用方法
- 暫停檢查只覆蓋了明確定義的方法（tap/click/swipe/screenshot），其他操作（如 `d.info`, `d.xpath()`）繞過了 `_pause_guard`

**建議：**
- 顯式列出需要委派的方法，而非 `__getattr__` 全轉發
- 對需要暫停檢查的方法（如 `d.info` 的長時間查詢）加入 guard
- 或至少在 `__getattr__` 中記錄 warning，標記哪些方法走了隱性路徑

---

#### 13. `game_api.py` 與 `control_panel_app.py` 功能重疊

**現狀：** 兩個 Flask 應用同時存在：
- `game_api.py`: 提供事件發送、設備狀態管理、快捷命令
- `control_panel_app.py` (1886 行): 提供完整的控制面板

**建議：** 確認 `game_api.py` 是否仍被使用。若已被 `control_panel_app.py` 取代，應標記為 deprecated 或刪除。

---

#### 14. 拼寫錯誤與命名不一致

| 問題 | 位置 | 建議 |
|------|------|------|
| `open_nofication` / `close_nofication` | device.py | → `open_notification` / `close_notification` |
| `capture_screenshot` vs `screenshot` | device.py vs device_wrapper.py | 統一為 `screenshot()` |
| `d_orig` 變數命名 | new_main_v2.py | → `raw_device` |
| `oralce_cnn_model` / `oralce_classes` | new_main_v2.py | → `oracle_cnn_model` / `oracle_classes` |
| `Cnn_model` (大寫開頭) | 全域 | → `cnn_model` (PEP 8 變數小寫) |
| `fileState.txt` | 根目錄 | 考慮改為 `.file_state` 或使用 config |

---

## 三、架構重構路線圖

### Phase 1：低風險清理（1-2 天）

- [ ] 統一 `run_adb()` 為單一定義
- [ ] 修正 `open_nofication` / `close_nofication` 拼寫
- [ ] 統一 logger 使用，消除 `print()`
- [ ] 修正 `oralce` → `oracle` 拼寫

### Phase 2：模組拆分（3-5 天）

- [ ] `bot_state.py` → `bot_state/` 套件，拆分為 5-6 個子模組
- [ ] `device_wrapper.py` → `device/` 套件，分離 ADB / Web 後端
- [ ] `device.py` → `screen_utils.py` + 移動 `get_adb_devices` 到 `adb_devices.py`

### Phase 3：主程式重構（5-7 天）

- [ ] `main()` → `DeviceLifecycle` 狀態機
- [ ] 消除硬編碼設備判斷，改為 config-driven
- [ ] 整合或移除 `event_manager.py`
- [x] ~~`check_on_line()` → 策略模式~~ 2026-07-05 改採純 WS `online_check_service` + `check_via_ws`（舊函式已刪，提案作廢）

### Phase 4：效能優化（2-3 天）

- [ ] `config_manager.load_config()` 引入 mtime 快取
- [ ] `MonitoredDevice` 顯式委派替代 `__getattr__`
- [ ] 評估 `bot_state._global_lock` 拆分為細粒度鎖

---

## 四、正面發現

在批評之餘，也值得肯定的設計決策：

1. **`MonitoredDevice` 包裝模式** — 統一了 ADB/Web 兩種後端的操作介面，daily_pipeline 不需要感知底層差異
2. **`DeviceConfig` dataclass** — 從 dict 升級到 typed config，`_extra` 保持向後相容，是務實的遷移策略
3. **`resolve_stage_until_stable()` 彈窗鏈處理** — 用循環 + max_chain 處理疊加彈窗，比硬編碼序列更健壯
4. **`_pause_guard` 機制** — 在每個設備操作前檢查暫停/強制休眠，確保控制面板的指令能及時生效
5. **線上檢查的保守策略** — 舊 `check_on_line()`（protocol + OCR 雙重驗證，兩者都判離線才放行）避免了誤操作；2026-07-05 已由純 WS `online_check_service` + `check_via_ws` 取代
6. **`safe_log()` 容錯** — 防止 logging handler 故障導致 worker thread 崩潰

---

## 五、總結

本專案已從「快速原型」成長為「多設備生產系統」，但架構仍保留早期的單一巨型函數模式。最關鍵的三個改進：

1. **拆分 `main()` 為狀態機** — 可讀性、可測試性、可維護性的根本改善
2. **拆分 `bot_state.py` 為子模組** — 降低耦合，明確鎖策略
3. **消除硬編碼設備判斷** — 讓新增設備只需修改 config，不用改程式碼

這些重構可以漸進式執行，不需要一次性重寫。Phase 1 的低風險清理可以立即開始。
