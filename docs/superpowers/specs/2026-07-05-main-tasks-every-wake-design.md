# 每輪醒來領取每日任務/活躍度寶箱（修漏領）

日期：2026-07-05
範圍：使用者指定「每日任務 + 每日活躍度寶箱」漏領問題。

## 問題

`ws_token/runner.py` `_run_main_tasks` 以「≥08:00 且一天一次」gate（`last_date == today`
寫入 `ws_state`）。8 點後首次喚醒領完即封鎖當日；當天稍晚（競技場 20:00、挖礦等）
才完成的每日任務與後續活躍度寶箱門檻，WS 不再領。ADB「所有日常任務」catch-all
（20:00–23:00）又被 WS-skip（`ws_phase.py:29` 對照表）擋掉 → 漏領。

## 修法

1. **移除一天一次 gate**：刪 `_run_main_tasks` 的 `last_date` 早退與寫入
   （runner.py:160-164、:174-175）。每次喚醒都 snapshot → 領可領項。
   各 claim 函式已是 state-gated（只領 `STATE_CLAIMABLE` / `BOX_CLAIMABLE`），
   無可領時不送任何 commit frame，重複跑安全。成本：每輪多 2 次 snapshot（~3s）。
2. **保留 ≥08:00 gate**：凌晨喚醒產生的可領項會在 8 點後首次喚醒補領，當日內不漏。
3. **補尾端二次領取**：`TASK_ORDER` 末端加 `main_tasks_late`（同一 runner 函式，
   帶不同 task name）。main_tasks 排第 2、mining/lamp 在後 — 本輪挖礦/神燈完成的
   每日任務與新增活躍度，若只在輪首領，當天最後一輪（~23:xx）產生的可領項會被
   午夜重置吃掉。尾端再領一次即閉環。

## 不做（YAGNI）

- 主線任務(TYPE_MAIN)/FlyPet 等其他類型的 claim：使用者只指定每日+活躍度寶箱。
- dashboard 開關：這是 bug fix（行為修正），非 opt-in 新功能，不加 toggle。
- 失敗重試機制：移除 date gate 後，單次 0x0201 失敗自然由下一輪喚醒補領。

## 影響面

- `ws_token/runner.py`：`_run_main_tasks` 刪 gate；`TASK_ORDER` 尾端加 `main_tasks_late`。
- `game_actions/ws_phase.py`：`_WS_TO_PIPELINE` 的 `main_tasks` 映射不變；
  `main_tasks_late` 不需映射（同名 pipeline skip 已由 main_tasks 觸發）。
- 測試：更新 `tests/test_ws_token_runner.py` 中 date-gate 相關測試；
  新增「每輪都領」與「尾端二次領取」測試。
