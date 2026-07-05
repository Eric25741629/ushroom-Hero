# Plan: main_tasks 每輪醒來領取（修每日任務/活躍度寶箱漏領）

Spec: `docs/superpowers/specs/2026-07-05-main-tasks-every-wake-design.md`

## Global Constraints

- 不加新套件；JSON 讀取用 `utf-8-sig`。
- pytest 必指定測試檔（hook 擋裸 pytest）。
- 只 stage 動到的檔案（絕不 `git add -A`）；不 push、不加 attribution footer。
- TDD：先寫失敗測試再改實作。

## Task 1: 移除 main_tasks 一天一次 gate + 尾端二次領取

### 實作（`ws_token/runner.py`）

1. `_run_main_tasks`（:147）：
   - 刪除 `last_date` 早退（:160-164）與 `st["main_tasks"] = ...` / `save_state`（:174-175）。
   - 保留 `now.hour < 8` gate（:158-159）。
   - 簽名瘦身：`device` / `state_dir` 參數只剩 ws_state 在用，一併刪除
     （改為 `def _run_main_tasks(client, collector, *, now=None)`），更新 docstring：
     「每輪喚醒都領；claim 函式 state-gated，無可領時不送 frame」。
   - 更新 caller（:1489-1490）。
2. `TASK_ORDER`（:82-87）尾端加 `"main_tasks_late"`。
3. `run_device` 在最後一個 `_step`（lamp）之後加：
   `_step("main_tasks_late", lambda: _run_main_tasks(client, collector))`
   —— 補領本輪 mining/lamp 等任務完成後才變可領的每日任務與活躍度寶箱。

### 測試（`tests/test_ws_token_runner.py`）

- 改：若有依賴 date-gate 的既有測試（搜 `last_date` / `already done` 於 main_tasks 相關），
  更新為新行為。
- 新增 `test_main_tasks_runs_every_wake`：同日連跑兩次 `run_device`，
  兩次都有 `("main_tasks", "collect_state")` 呼叫（不再被 date gate 擋）。
- 新增 `test_main_tasks_late_runs_after_mining_lamp`：`run_device` 一輪內
  `calls` 中 main_tasks 的 claim 動作出現兩組，且 `rep.tasks` 含 `main_tasks_late`；
  task 順序上 `main_tasks_late` 在 `mining` / `lamp`（若啟用）之後。
- 既有 `test_run_device_main_tasks_collects_then_claims`（:312）的
  `mt.count("collect_state") == 2` 會因 late pass 變 4 → 更新斷言或改為只取第一組。
- 驗證 `tests/test_ws_phase.py` / `test_ws_phase_resume.py` 不受影響（main_tasks_late
  未映射 pipeline，`_WS_TO_PIPELINE` 不用改）。

### 驗證指令

```
python -m pytest tests/test_ws_token_runner.py tests/test_ws_phase.py tests/test_ws_phase_resume.py -q
python -m py_compile ws_token/runner.py
```

### Commit

`fix(ws-tasks): main_tasks 每輪醒來領取 + 尾端二次領取（修每日任務/活躍度寶箱漏領）`
