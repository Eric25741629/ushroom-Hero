# WS 階段可被「開啟瀏覽器」中斷 + 持久化續做 設計

日期：2026-06-15
狀態：設計已過使用者核可（持久化 ledger 續做；resume 有效視窗 30 分鐘）

## 1. 需求

web_h5 + ws_token 裝置喚醒後會先跑純 WS 階段（`game_actions/ws_phase.py` →
`ws_token/runner.run_device`），依 `TASK_ORDER` 逐項同步跑完才返回。其中
**mining（最多 200 步，每步一次 WS round-trip + 規劃）** 是實際會卡數分鐘的長任務。
期間使用者按 dashboard「開啟瀏覽器」不會被理會，必須等整個 WS 階段跑完。

需求：

1. WS 階段要能被「開啟瀏覽器」請求即時中斷（對齊既有規則：任何等待 ≤2s 內可被
   `bot_state.has_pending_web_launch_request(ip)` 中斷，見 memory
   `feedback_open_browser_always_responsive`）。
2. 中斷時記錄當前進度（哪些任務已完成 / 哪些待續），持久化以利重啟存活。
3. 讓使用者開瀏覽器手動操作；操作結束重新上線後，**只續做未完成的任務**，
   已完成的跳過。
4. 跨喚醒週期 / 隔日要正確重置，避免把每輪該重跑的任務（regen 類）誤跳。

adb 裝置（手機fc）沒有瀏覽器，「開啟瀏覽器」對它不適用；同一鉤子對它無害
（`has_pending_web_launch_request` 永不為真 → 永不中斷）。

## 2. 現況（已存在的零件，勿重造）

| 零件 | 位置 | 現況 |
|------|------|------|
| WS-first 階段 | `game_actions/ws_phase.py` | 喚醒先跑純 WS；失敗自然降級回空 skip-set |
| WS 任務編排 | `ws_token/runner.py` `run_device` | 依 `TASK_ORDER` 逐項跑；每任務有 try/except 隔離；`progress` 回呼逐任務（lamp 逐批）回報 |
| 開瀏覽器處理 | `runtime_services/web_session_service.py` `handle_pending_web_launch` | 僅 web_h5；`consume_web_launch_request` 取出請求 → 開頁（可手動 hold 到關閉）|
| 中斷旗標 | `bot_state.has_pending_web_launch_request` / `consume_web_launch_request` | peek 不消費 / 消費；既有可中斷點清單見 memory |
| per-device 持久狀態 | `ws_token/state.py` `load_state`/`save_state` | carpark/couple/workshop/rogue/mail 已用；key 為 device(=ip) |
| 主迴圈 WS 接線 | `new_main_v2.py:265,276-280` | `handle_pending_web_launch` 在迴圈頂端；WS 區塊在其後 |
| init 先跑 WS | `new_main_v2.py:156-159` | `_run_initial_ws_phase_before_web_start` 設 `pre_runtime_ws_done` |

**缺口**：`run_device` 一旦進入就同步跑完，沒有任何中斷點，也沒有「跳過已完成任務」的入口。

## 3. 設計

分三層：機制（runner，純機制不含政策）、政策（ws_phase，持久化 ledger）、接線（new_main_v2）。

### 3.1 機制層（`ws_token/runner.py` + 兩個長任務）

- **新例外**：`ws_token/abort.py`
  ```python
  class WSRunAborted(Exception):
      """外部要求（如待處理的開瀏覽器請求）中斷 WS 連跑時，由任務內部或任務邊界拋出。"""
  ```
  放獨立模組是為了避免 `runner` ↔ `lamp`/`mining_supervised` 循環匯入
  （runner 於頂層 import mining_supervised）。

- **`RunReport`** 新增欄位 `aborted: bool = False`。

- **`run_device(...)`** 新增兩個關鍵字參數，**預設 None → 行為與今日逐位元相同**
  （CLI、`use_ws_runner` 純 WS 裝置、現有測試零影響）：
  - `should_abort: Optional[Callable[[], bool]] = None`
  - `skip_tasks: Optional[Iterable[str]] = None`

- **`_step` 改寫**（仍是 `run_device` 內的 closure，持有 `nonlocal aborted`；
  `run_device` 開頭先 `skip_set = set(skip_tasks or ())`）：
  ```python
  def _step(name, fn):
      nonlocal aborted
      if aborted:
          return                      # 已中斷 → 後續任務全部不跑（留作 pending）
      if should_abort is not None and should_abort():
          aborted = True
          _notify(name, "aborted", "pending web launch")
          return
      if name in skip_set:
          _notify(name, "skip", "resume: already done")
          return                      # 續做：已完成 → 不重跑、不記錄
      try:
          _safe(tasks, errors, name, fn, notify=_notify)
      except WSRunAborted:
          aborted = True
          _notify(name, "aborted", "in-task")
          # 不寫入 tasks/errors → 該任務維持 pending，resume 時重跑
  ```
  `_safe` 對 `WSRunAborted` 改為 **re-raise**（不可被當作一般任務錯誤吞進 `errors`）：
  ```python
  except WSRunAborted:
      raise
  except Exception as exc:
      errors[name] = ...
  ```

- **長任務串接 `should_abort`**（命中即 `raise WSRunAborted`，已落地的開箱/挖步是伺服器
  端結果，resume 自然接續）：
  - `ws_token/lamp.py` `open_lamp(...)`：新增 `should_abort=None`，於 batch 迴圈
    （`for batch_index in range(max_batches)`，line ~423）開頭檢查。
  - `ws_token/mining_supervised.py` `mine_until_pickaxe_empty(...)`：新增
    `should_abort=None`，於 step 迴圈（`for _idx in range(limit)`，line ~334）開頭檢查。
  - `runner._run_lamp` / `_run_mining` 把 `should_abort` 透傳下去。
  - 其餘任務有界且短（單一或小 N round-trip），靠任務邊界檢查即可。最壞中斷延遲 =
    單一有界任務時間，遠優於現況「整個 WS 階段」。tycoon（max_rolls 50，預設關）
    若日後常開可比照串接，本案不做。

### 3.2 政策層（`game_actions/ws_phase.py`，持久化 ledger）

持久化到既有 `ws_token/state.py`，device key 下新增 `ws_resume`：
```json
{"ws_resume": {"date": "YYYY-MM-DD", "ts": <abort_epoch>, "done": ["main_tasks", ...]}}
```

常數：
```python
_RESUME_TTL_SEC = 30 * 60                 # 有效視窗 30 分鐘
_RESUME_EXEMPT = frozenset({"carpark", "idle_reward"})  # 永遠重跑，不被 ledger 跳過
```

- **`should_abort`**：`run_ws_phase` 內建
  `should_abort = lambda: bot_state.has_pending_web_launch_request(ip)`，傳入 `_run_device`。
  （adb 永遠 False；不另檢查 force-sleep，維持本案範圍。）

- **載入 / 計算 `skip_tasks`**（跑 run_device 前）：
  讀 `ws_resume`；**有效** = `date == 今天` 且 `now - ts < _RESUME_TTL_SEC` 且 `done` 非空。
  有效 → `skip_tasks = set(done) - _RESUME_EXEMPT`；無效/不存在 → `skip_tasks = set()`。
  無效時順手不視為續做（stale 一律全跑）。

- **「實質完成」判定**：`_substantive_done(report) = {name for name, res in report.tasks
  if not (isinstance(res, dict) and "skipped" in res)}`。只有真的做了事的任務才進
  ledger / 才貢獻 pipeline-skip；回 `{"skipped": ...}`（如 couple 無伴侶、dungeon 沒配
  掃蕩、carpark 沒開窗）的任務不進 ledger，resume 時照常重跑（便宜且結果相同）。

- **pipeline-skip 沿用續做成果**：以
  `effective_done = set(先前有效 ledger.done) | _substantive_done(report)`
  重算現有的 `WS_TO_PIPELINE_SKIPS` 與 farm/dungeon 條件式（把現行
  `if key in report.tasks` / `if "farm" in report.tasks` 改成查 `effective_done`），
  確保 resume 跳過的任務也能讓 daily_pipeline 正確略過，不會用瀏覽器重做一次。

- **寫入時機**：
  - `report.aborted` → 寫 `ws_resume = {date: today, ts: now, done: sorted(effective_done)}`，
    並 `bot_state.update_state(ip, task="WS 階段", step="WS 已暫停(開啟瀏覽器)，待續: <pending>")`。
  - 未中斷（完整跑完）→ 清掉 `ws_resume`（pop key 後 save），讓下一個喚醒週期回到全新狀態。
  - adb / 一般無中斷裝置 → 永遠走「清空」分支（若本來就沒 key 則不寫，至多一次空寫）。

### 3.3 接線層（`new_main_v2.py`，兩處小改）

1. 主迴圈 WS 區塊後（line ~280 計算完 `ws_done` 之後）：
   ```python
   if bot_state.has_pending_web_launch_request(ip):
       device_logger.info(f"[{ip}] WS 階段偵測到開啟瀏覽器請求，回頂端處理開瀏覽器")
       continue
   ```
   回頂端讓 `handle_pending_web_launch` 開頁；瀏覽器（或手動 hold）結束後續輪自然重跑
   WS 階段（讀 ledger 續做未完成）。同時覆蓋 `pre_runtime_ws_done` 分支（檢查在賦值之後）。

2. init 的 `_run_initial_ws_phase_before_web_start`：呼叫後若
   `has_pending_web_launch_request(ip)` 為真（init 那輪被中斷）→ **不要**快取
   `pre_runtime_ws_done`（留 None），確保進主迴圈後會重跑 WS 做 resume，而非沿用半套
   結果：
   ```python
   def _run_initial_ws_phase_before_web_start():
       nonlocal pre_runtime_ws_done
       if pre_runtime_ws_done is None:
           result = _run_ws_phase_for_wake(ip, device_logger)
           if not bot_state.has_pending_web_launch_request(ip):
               pre_runtime_ws_done = result
   ```

- `runtime_services/ws_fallback_service.py`（adb 離線備援）**不動**：adb 不會觸發中斷。

### 3.4 為何用 TTL(30min) + 清空雙保險

正常流程 ledger 幾乎永遠為空（只存在於 abort→resume 之間，完成即清）。EXEMPT 讓
carpark（10:00 搶位窗）/ idle_reward（每輪累積）在 resume 時仍重跑。TTL/date 只是替
「中斷後沒走到完整完成就重啟 bot」這種罕見情況設上界：超過 30 分鐘的 ledger 視為
stale → resume 改成安全地全跑（重跑當日 daily 任務皆為 no-op；mining 30 分內 regen
極少，誤跳影響可忽略）。

### 3.5 降級保證

- `run_device` 兩參數預設 None → 未走 ws_phase 的呼叫端（CLI、`use_ws_runner`、測試）
  零行為差異。
- ws_phase 任何 ledger 讀寫失敗只記 log、退回「不續做、全跑」（never 讓 WS 階段炸 wake loop，
  維持現有「WS 只會替 pipeline 減工作、不會漏工作」的天然降級不變式）。
- `WSRunAborted` 只在 `should_abort` 提供時可能被觸發；未提供則永不拋出。

## 4. 測試

1. `tests/test_ws_runner_abort.py`（新）：
   - `should_abort` 在第 K 個任務邊界觸發 → `report.aborted is True`、前 K 個在 `tasks`、
     其餘不在 `tasks`/`errors`。
   - `skip_tasks={...}` → 指定任務被跳過（不在 tasks、不報錯、不呼叫其 fn）。
   - lamp/mining 迴圈內 `should_abort` 觸發 → 拋 `WSRunAborted` → 該任務不被記為完成、
     `report.aborted is True`。
   - 預設（兩參數 None）→ 與既有 run_device 行為相同（回歸保護）。
2. `tests/test_ws_phase_resume.py`（新，或併入 `test_ws_phase.py`）：
   - abort → 寫入 `ws_resume`（date/ts/done 為實質完成且不含 `{"skipped"}` 任務）。
   - 有效 ledger → 計算出的 `skip_tasks = done - EXEMPT`；EXEMPT(carpark/idle_reward) 不被跳。
   - resume 完整跑完 → `ws_resume` 被清空；pipeline-skip 用 `effective_done` 沿用先前完成。
   - stale（隔日 / 逾 TTL）→ 忽略、全跑、不續做。
   - ledger 讀寫例外 → 降級全跑、WS 階段不炸。
3. `tests/test_wake_loop_escape.py` 追加（仿既有）：WS 後 `has_pending_web_launch_request`
   為真 → 主迴圈 `continue`（`handle_pending_web_launch` 被觸達）；init 被中斷 →
   `pre_runtime_ws_done` 不被快取。

## 5. 關聯事項

- 改 `new_main_v2.py` / `game_actions/ws_phase.py` / `ws_token/runner.py` 需重啟
  master+worker 才生效（見 memory `feedback_bot_restart_after_file_fix`）。
- 與 memory：`feedback_open_browser_always_responsive`（本案把 WS 階段補進「可中斷點」
  清單）、`feedback_ws_first_recon_strategy`、`project_ws_token_backend`。
