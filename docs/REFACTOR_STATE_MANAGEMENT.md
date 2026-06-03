# 狀態管理重構提案 (bot_state 及相關全域)

> 撰寫日期：2026-06-03。範圍：`bot_state.py` 為核心,連帶 `control_panel_app.py`、
> `runtime_services/*`、`device_wrapper.py`、`utils/wake_up_handler.py`、`utils/pause_guard.py`。
> **原則:零行為變更 (behavior-preserving)。** 這是 refactor,不是改功能。
>
> 與 `docs/REFACTORING_OPPORTUNITIES.md` 關係:該總表 line 320 把「bot_state 套件」結構級重構標記為
> 「多未做」但未展開;本文是其展開版,提供可執行的分階段設計。

---

## 0. TL;DR

`bot_state.py` 的並發 primitive 寫得用心(兩層鎖、deepcopy 快照、atomic check-and-consume、
TOCTOU/H7 修正),但**結構長歪了**:一個 god-module 塞了 12+ 個各自為政的全域狀態通道、
兩套重疊的鎖、重複的真相來源。維護成本高,而且已經因此漏了 bug(見 1.1)。

本提案分 5 階段,**依 ROI 排序**,每階段獨立可交付、可單獨回滾。前兩階段是 correctness 修正,
第三階段是這份提案的核心價值。

---

## 1. 證據:現況問題清單

### 1.1 真 bug — 清理路徑漏 channel(最高優先)

| 函式 | 有清的 channel | 漏掉的 channel |
|------|---------------|---------------|
| `clear_offline_devices()` (bot_state.py:632) | skip_sleep, force_sleep, manual_release, screenshot_windows, locks | **web_close** |
| `sweep_stale_states()` (bot_state.py:662) | skip_sleep, force_sleep, locks | **manual_release, web_close**, screenshot_windows |

裝置移除後殘留的 flag 會被下一個同 IP 裝置消費 → 幽靈 skip/close 指令。
根因:N 個平行 dict,每個都要在每個清理點手動 pop,人腦記不住,已漏。

### 1.2 12+ 個平行全域 + 各自一套臨時協議

`bot_state.py` module-level:
`_states` / `_locks` / `_pause_events` / `_refresh_needed` / `_screenshot_windows` /
`_local_device_ids` / `_skip_sleep_flags` / `_manual_release_flags` / `_force_sleep_flags` /
`_web_close_flags` / `_web_launch_requests` / `_online_check_queue_by_checker` /
`_online_check_requests`。

其中 4 個是**形狀完全相同**的 one-shot bool flag(skip_sleep / force_sleep / web_close /
manual_release),卻各佔一個 dict + 一對 set/check 函式。

### 1.3 重複的真相來源

- **`paused` 存兩份**:`_states[ip]["paused"]`(顯示用 bool) 與 `_pause_events[ip]`
  (真正 block 執行緒的 Event)。`set_pause` 同時寫兩邊,但任一處讀錯邊就分歧。
  真正的 source of truth 是 Event;bool 應該是衍生值。
- **`refresh_needed` 存兩份**:`bot_state._refresh_needed`(本機 in-process scan 觸發) 與
  `control_panel_app._global_commands["refresh_needed"]`(master→worker 線路訊號)。
  這兩者**其實是不同層**(本機 vs 跨機協議),不該硬合併;但目前關係是隱性的,
  且 `control_panel:1115+1119` 為了一個動作同時手寫兩邊。

### 1.4 紀律只在 bot_state 內,出門就破功(裸全域)

| 全域 | 位置 | 問題 | 實際嚴重度 |
|------|------|------|-----------|
| `CONNECT_FAILURE_COUNTS` | device_runtime_service.py:23,70-71,89 | 裸 read-modify-write 無鎖 | 低(per-ip 單 writer,GIL 下實際安全);但**疑似與 `_states["adb_consecutive_failures"]` 重複**(update_watchdog_probe 已存一份),應合併 |
| `_mumu_controller` | device_runtime_service.py:29,45-54 | 無鎖 double-check 單例 | 低(最多重建一次,last-write-wins) |
| `_live_view_sessions` | control_panel_app.py:62,1725-1749 | 裸 dict,Flask thread 與 WS thread 競爭 | 低-中(per-ip key,get 可能拿到拆除中的 session) |

> 修正前次分析的誇大:這三個**不是** critical data corruption。都是 per-device key、
> 一 key 一 writer,GIL 下基本安全。問題是 `bot_state` 立的鎖紀律完全沒沿用到隔壁模組,
> 一致性破了,且 `CONNECT_FAILURE_COUNTS` 是真的重複狀態。

### 1.5 封裝外洩(範圍其實很小)

外部直接戳內部結構的點**只有 3 處**,所以封死很便宜:
- `control_panel_app.py:1277,1283` — 直接寫 `bot_state._states[remote_id]["logs"|"avg_screenshot_ms"]`,
  繞過 `update_state()`,不刷 `last_update` → 心跳偵測 drift。
- `utils/pause_guard.py:104` — 直接讀 `bot_state._pause_events.get(ip)`(有 noqa,屬刻意,
  但應走 accessor)。

### 1.6 爆炸半徑(call-site 普查)

- one-shot signals (skip/force/web_close/manual_release) 被消費點橫跨 ~10 模組,~25 處
  (control_panel / device_wrapper / new_main_v2 / miner.mining_service / worker_webhook_api /
  web_session_service / device_runtime_service / wake_up_handler / dungeon_scheduler)。
- web_launch / online_check 生命週期橫跨 ~7 模組,~25 處。

→ **call site 太多,不能直接改簽名。** 重構必須在穩定 public API 背後進行,
shim 保留舊函式名,call site 變動 = 0。

---

## 2. 目標架構

### 2.1 把 4 個 one-shot flag 收成一個 `DeviceSignals`

```python
from enum import Enum, auto

class Signal(Enum):
    SKIP_SLEEP = auto()
    FORCE_SLEEP = auto()
    WEB_CLOSE = auto()
    MANUAL_RELEASE = auto()

_signals: Dict[str, set[Signal]] = {}   # 由 _global_lock 護衛

def raise_signal(ip: str, sig: Signal) -> None:
    with _global_lock:
        _signals.setdefault(ip, set()).add(sig)

def consume_signal(ip: str, sig: Signal) -> bool:
    with _global_lock:
        s = _signals.get(ip)
        if s and sig in s:
            s.discard(sig)
            return True
        return False
```

舊函式變 shim(call site 不動):

```python
def set_skip_sleep(ip):    raise_signal(ip, Signal.SKIP_SLEEP)
def check_skip_sleep(ip):  return consume_signal(ip, Signal.SKIP_SLEEP)
def request_web_close(ip): raise_signal(ip, Signal.WEB_CLOSE)
def check_web_close(ip):   return consume_signal(ip, Signal.WEB_CLOSE)
# manual_release 同理
```

**核心收益:清理變一行,不可能再漏 channel:**

```python
# clear_offline_devices / sweep_stale_states 內:
_signals.pop(ip, None)        # 取代 4 個分開的 *_flags.pop()
```

⚠️ **鎖陷阱(務必寫進實作):** `request_force_sleep` 在**單一** `_global_lock` 臨界區內
同時做「升旗 + 取消 web_launch + set pause event + 改 state」。`_global_lock` 是普通 `Lock`
不是 `RLock`,所以 `request_force_sleep` **不能呼叫 `raise_signal()`**(會重入死鎖),
必須 inline `_signals.setdefault(ip, set()).add(Signal.FORCE_SLEEP)`。其餘 side-effect 維持原樣。

### 2.2 `paused` 改為衍生值(單一來源)

- `set_pause()` 不再寫 `_states[ip]["paused"]`,只動 `_pause_events[ip]`。
- `get_all_states()` 快照時計算:`state["paused"] = not _pause_events[ip].is_set()`。
- source of truth 收斂到 Event;UI 拿到的 key 不變,消費端零改動。
- `pause_guard.py:104` 讀 Event 屬正確(讀的是 source of truth),改走新 accessor `get_pause_event(ip)`。

### 2.3 `refresh_needed` — 讓兩層關係顯性化(不合併)

- 保留兩個 flag,但加註解定義:`bot_state._refresh_needed` = 本機 scan loop 觸發;
  `_global_commands["refresh_needed"]` = master→worker 線路訊號。
- **移除埋在內部的隱性寫入**:`request_web_launch:446` 與 `submit_online_check_request:534`
  目前直接 `global _refresh_needed; _refresh_needed = True`,改為呼叫 `set_refresh_needed()`,
  讓「設旗」只有一個入口函式(消除 1.3 標記的不一致)。
- `control_panel` 的雙寫包成一個 helper `_broadcast_refresh()`,語意自證。

### 2.4 裸全域收編

- `CONNECT_FAILURE_COUNTS`:先確認是否與 `_states["adb_consecutive_failures"]` 同義;
  是 → 併入 bot_state(`bump_connect_failure(ip)->int` / `reset_connect_failure(ip)`,鎖內操作);
  否 → 至少加一個 module 級 `threading.Lock`。
- `_mumu_controller`:init 包鎖(process 單例)。
- `_live_view_sessions`:加一個 `_live_view_lock`,或包成極小的 `LiveViewRegistry`。

### 2.5 封裝外洩補 accessor

- 新增 `update_remote_metrics(ip, logs=None, avg_screenshot_ms=None)`,取代 control_panel:1277/1283 直寫。
- 新增 `get_pause_event(ip)`,給 pause_guard 用。

---

## 3. 分階段遷移計畫(依 ROI 排序)

每階段:**獨立 PR、獨立可回滾、結束時測試全綠**。

### Phase 0 — 安全網(先做,不改 production code)
- 盤點現有測試:`test_bot_state_safety`、`test_pause_routing_and_weblaunch`、
  `test_web_close_request`、`test_online_check_immediate_wake`、`test_wake_loop_escape`。
- 補 characterization test 蓋住:清理後 flag 歸零、paused 一致性、force_sleep 連鎖 side-effect。
- 這層測試是後續所有重構的回歸基準。

### Phase 1 — Correctness 修正(最小、最安全、最高 ROI)
1. 修 1.1 清理漏 channel(即使不收斂,先把漏的 pop 補上)。
2. 補 2.5 accessor,封死 control_panel 直寫 `_states`。
3. 收編 2.4 三個裸全域(加鎖 / 併 CONNECT_FAILURE_COUNTS)。
- 爆炸半徑:小。風險:低。立即修掉幽靈指令 + 心跳 drift。

### Phase 2 — 消除重複真相來源
1. `paused` 改衍生(2.2)。
2. `refresh_needed` 兩層顯性化 + 收斂設旗入口(2.3)。
- 爆炸半徑:幾乎全在 bot_state 內。風險:低。

### Phase 3 — 收斂 4 個 one-shot signal 成 `DeviceSignals`(本提案核心)
1. 導入 `Signal` enum + `_signals` + raise/consume + 鎖陷阱處理(2.1)。
2. 舊 8 個函式改 shim,**call site 不動**。
3. 清理路徑改一行 `_signals.pop(ip, None)`。
- 爆炸半徑:call site = 0(靠 shim);改動集中在 bot_state。
- 收益:根治 1.1 那類「加 channel 忘了清」的 bug 類別。

### Phase 4(可選)— 抽出 web_launch / online_check 成獨立模組
- `WebLaunchMailbox`、`OnlineCheckBroker` 兩個小類,從 bot_state 搬出,縮小 god-module。
- 行為中性。降低 bot_state.py 行數與認知負擔。

### Phase 5(未來,暫不建議)— 整包包成 `DeviceStateStore` 類
- 把 module global 全轉單例 class 屬性,徹底封裝。
- 機械但量大(50+ import 點),YAGNI:目前外洩只有 3 處,Phase 1 補 accessor 後封裝已夠。
- 列為「真的需要時再做」,避免為抽象而抽象。

---

## 4. 測試策略

- 每階段先紅後綠;沿用 `LogPaths.with_root(tmp_path)` 與既有 fixture。
- 重點回歸:
  - signal raise → consume 一次性、per-device 隔離。
  - 裝置移除後 `_signals.get(ip)` 為空(覆蓋 1.1)。
  - `request_force_sleep` 仍解除 `check_pause` 阻塞(test_bot_state_safety 既有)。
  - paused 衍生值與 Event 一致。
  - 跑聚焦集合,勿裸 `pytest`(會 import 真 device/Playwright/OCR)。
- 命令範例:
  ```bash
  python -m pytest tests/test_bot_state_safety.py tests/test_pause_routing_and_weblaunch.py \
      tests/test_web_close_request.py tests/test_online_check_immediate_wake.py -q
  python -m py_compile bot_state.py
  ```

## 5. 風險與回滾

- 全程 behavior-preserving;shim 確保 call site 零改動,單階段出問題只回滾該 PR。
- 最大風險點 = 2.1 的鎖重入陷阱(force_sleep);實作時務必 inline,並用既有
  test_bot_state_safety 驗證不死鎖。
- bot 重啟才會載入新 module(sys.modules cache);驗證時對照 new_main_v2.py 啟動時間。

## 6. 明確不做的事(YAGNI 護欄)

- 不引入外部狀態庫 / actor framework / asyncio 重寫 — 殺雞用牛刀。
- 不把 web_launch 與 online_check 硬塞進同一個 mailbox 抽象(三種不同形狀:fire-and-forget /
  status-lifecycle / RPC-with-result,強行統一會比現在更糟)。
- 不在本輪改任何排程 / 喚醒 / 任務邏輯。
