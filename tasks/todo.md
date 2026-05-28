# 專案統整 / 重構 計畫

**Date**: 2026-05-19
**Trigger**: `/goal 檢查程式碼複雜度 把需要的融合起來 功能不同的切分開來 統整整個專案`
**Status**: Phase 1 COMPLETE. Phase 2/3/4 pending re-prioritization.

---

## 0. Audit Findings (read-only)

5 parallel audits ran on park / battle / lamp / god-module / cleanup clusters. Top-level numbers:

| Cluster | Live files | Dead files (verified 0 imports) | God modules to split |
|---|---|---|---|
| park | `park.py`, `new_park.py` | `park_test.py` (657L), `detect_parking_p.py` (86L) | — |
| battle | `new_battle.py`, `fight_car.py` | `battle.py` (69L), `fight_car_task.py` (229L) | `new_battle.py` (1001L) |
| lamp | `Open_gold_paddle_ocr.py` (V1), `opengold_v2/` (V2) | `Open_gold.py` (296L) | `Open_gold_paddle_ocr.py` after V2 migration |
| infra | `new_main_v2.py`, `control_panel_app.py`, `device_wrapper.py`, `json_manager.py` | — | all four |
| repo root | — | 25 sync-conflict files, 7 Untitled-*, 8 tmp/trash dirs, 6 empty source dirs, aborted `refactor/` | — |

Pre-existing context discovered during audit:
- A `REFACTOR_ROADMAP.md` was drafted on **2026-05-16** but only survives as a `*.sync-conflict-*` copy; canonical file is missing. The roadmap there proposes a similar P0–P4 plan (threading locks, dedupe, json_manager split, dead-code purge). **This plan supersedes that draft** — the draft will be folded in as Phase 2.
- An aborted `refactor/` scaffold from **2026-04-24** (mostly empty `__init__.py` + READMEs in `adb_layer/`, `core/`, `game_init/`, `game_modules/`, `utils/`) exists. Not imported by anything. Treated as dead.
- The empty top-level dirs (`core/`, `mission/`, `find_img/`, `reward_get/`, `partner/`, `dataset/`) are the same aborted refactor's scaffolding leaking into the repo root. Also dead.

---

## Phase 0 — Inventory & safety net (no deletions yet)

- [ ] **0.1** Stop the bot if running (rotated logs / atomic writes assume single writer)
- [ ] **0.2** `git status` clean, commit current state on branch `chore/consolidation-2026-05-19`
- [ ] **0.3** Tag baseline `pre-consolidation-2026-05-19` (rescue point)
- [ ] **0.4** Run `pytest` to record green baseline; record count + duration in this file
- [ ] **0.5** Decide on `farm_v2/` and `miner/v2/` (see "Decisions needed" below) before Phase 1

**Estimated impact**: 0 code changes. ~10 min.

---

## Phase 1 — Cleanup-only (low-risk, reversible by git revert)

All items below were verified by audit to have **zero imports** in production code (`new_main_v2.py`, `runtime_services/`, `game_actions/`, `control_panel_app.py`, `device_wrapper.py`).

### 1A. Delete root-level scratch / sync-conflict artifacts
- [x] **1A.1** All `*.sync-conflict-*` files at repo root (25 deleted) + tests/ (43 deleted) — total 68 files
  - REFACTOR_ROADMAP draft folded into Phase 2 of this file before deletion
  - Files were gitignored (`*.sync-conflict-*` rule), so no commit needed — physical cleanup only
  - **Verified**: pytest 392 pass / 8 skip / 15.02s — identical to baseline (was 43 collection errors before)
- [ ] **1A.2** `Untitled-*.py`, `Untitled-*.ipynb` (7 files)
- [ ] **1A.3** `#config set.py` (0 bytes)
- [ ] **1A.4** `.tmp_head_control_panel_app.py` (29KB orphan partial)

### 1B. Delete throwaway directories — DONE (commit 6d61ec47)
- [x] **1B.1-8** All done. Writer in utils/ws_listener.py migrated to
  `logs/_archive/ws_capture/auto/`; argparse defaults in
  build_equipment_cache.py and verify_lamp_via_playwright.py updated;
  10 valuable docs/sources preserved via `git mv` to
  `docs/protocol/` and `docs/game_client_sources/`; ~24 000 files
  removed including tmp_ws_capture/, tmp_crops/, tmp_flow_imgs/,
  tmp_lamp_verify/, tmp_rl_test/, trash/, 2026-01-20 195013/,
  新增資料夾/. Tests: 392 pass / 8 skip.

### 1C. Delete empty / aborted refactor scaffolding — partial DONE (commit f57ea8da)
- [x] **1C.1** `partner/` deleted (empty); `mission/*.png` stale PNGs removed (writer commented out)
- [~] **1C.1 KEEP**: `find_img/`, `reward_get/`, `dataset/` — audit was wrong; these have live runtime writers (img_tools.py:413, reward_manager.py:27, config/paths.py)
- [x] **1C.2** `core/` deleted (8 zero-byte sync-conflict files only)
- [x] **1C.3** `refactor/` deleted (`git rm -r`, 17 files; 2026-04 aborted scaffold, audit confirmed zero imports)
- [x] **1C.4** pytest: 392 pass / 8 skip — same as baseline

### 1D. Delete dead top-level Python files (verified 0 imports) — DONE in commit 9becba70
- [x] **1D.1** `battle.py` (69L) — superseded by `new_battle.BattleManager`
- [x] **1D.2** `fight_car_task.py` (229L) — orphan experimental; `fight_car.py` stays
- [x] **1D.3** `park_test.py` (657L) — **NOT a test**; legacy duplicate of `park.py`
- [x] **1D.4** `detect_parking_p.py` (86L) — orphan blue-P detector
- [x] **1D.5** `Open_gold.py` (296L) — zero callers; legacy `easyocr` reader
- [x] **1A.2-4** also folded into commit 9becba70 (7 Untitled-*, #config set.py, .tmp_head_control_panel_app.py)

**Commit**: `9becba70 chore(cleanup): remove dead scratch files and superseded modules`
**Tests**: 392 pass / 8 skip — identical to baseline

**Estimated impact**: ~3 200 LOC + ~24 000 binary files removed. No behavior change. Bot start/stop should be identical.

---

## Phase 2 — Threading & dedup fixes (mostly DONE per recent commits)

**Already landed** (verified via `git log`):
- ✅ `bot_state.request_force_sleep()` lock fix — commit 6c07ab96
- ✅ `bot_state.check_pause()` TOCTOU fix — commit 4d7d8893
- ✅ `json_manager._atomic_write_json()` — commit a8817e06
- ✅ `push_project` subscription lock — commit 2c707e99
- ✅ `navigate_to_main_page()` shared utility — commit a9fbb149 + delegations in farm (906326b8), farm_v2 (01477b50), miner_action (a85693a4)
- ✅ `should_purchase` extracted to `game_actions.shop_manager` — commit 55492348
- ✅ `DeviceConfig` dataclass — commit 01e1e3b0
- ✅ `device_wrapper` 3 silent excepts logged — commit d9d81236

**Remaining**:
- [ ] **2C.3** Convert 7 bare `except:` in `device_wrapper.py` to `except Exception as e: logger.warning(...)`
- [ ] **2C.4** Replace remaining ~25 silent `pass` blocks in `device_wrapper.py` with warning logs (3 done, ~25 to go)
- [ ] **2B.2** Extract `poll_stage(d, target, timeout)` — replaces 4+ stage-poll copies (not done yet — search for `current_stage ==` polling loops)
- [ ] **2B.3** `clear_offline_devices()`: merge two-stage lock window in `bot_state.py` (verify if still applicable post-4d7d8893)

**Estimated impact**: ~100 LOC delta. Targeted tests required. Bot logs become noisier — acceptable.

---

## Phase 3 — God-module splits (higher risk; one PR per module)

Each split is **rename + move only** — preserve every public symbol via re-exports from the old module path so existing imports keep working. After one stable release cycle, remove the re-exports.

### 3A. `json_manager.py` (732L → 4–5 modules)
- [ ] **3A.1** Extract base `JsonDataManager` + atomic write → `json_manager/base.py`
- [ ] **3A.2** Extract `_ts_same_day/week`, `_parse_recorded_date`, `should_execute_*` → `json_manager/time_tracking.py`
- [ ] **3A.3** Extract `ParkMarketDataManager` → `json_manager/park.py`
- [ ] **3A.4** Extract `FamilyMarketDataManager` → `json_manager/family.py`
- [ ] **3A.5** Extract `StoreDataManager`, `TimeRecordDataManager` → `json_manager/store.py`, `json_manager/time_record.py`
- [ ] **3A.6** Make old `json_manager.py` a thin `from json_manager.* import *` shim
- [ ] **3A.7** Consolidate `should_execute_cycle` and `should_execute_cycle_from_record` into one parameterised function

### 3B. `new_battle.py` (1001L → 4 modules under `battle/`)
- [ ] **3B.1** Extract `BattleManager` class (L231-443) → `battle/manager.py`
- [ ] **3B.2** Extract biweekly bounty road logic (L137-230, slot key helpers) → `battle/biweekly_dungeon.py`
- [ ] **3B.3** Extract weekly cloud + friend-help (L445-790) → `battle/weekly.py`
- [ ] **3B.4** Extract hell_door + snow country (L891+) → `battle/special.py`
- [ ] **3B.5** Make `new_battle.py` a re-export shim (or rename callers to `from battle import …`)
- [ ] **3B.6** Address `BattleManager.capture_screenshot()` (hard-coded 9-pixel colour check) — extract to named constants

### 3C. `control_panel_app.py` (1473L → routes + workers + brokers)
- [ ] **3C.1** Extract all `@app.route` handlers → `control_panel/routes.py`
- [ ] **3C.2** Extract `_run_web_login_worker` (240L, deepest nesting) → `control_panel/web_login_worker.py` with a `WebLoginConfig` dataclass for the 30-param unpack
- [ ] **3C.3** Extract `_run_labeler_once_worker` + `_run_trainer_worker` → `control_panel/subprocess_workers.py`
- [ ] **3C.4** Extract `queue_command` + `_push_to_worker_webhook` + state → `control_panel/device_command_broker.py`
- [ ] **3C.5** Extract `check_ocr_server` → `control_panel/ocr_health.py`
- [ ] **3C.6** Old `control_panel_app.py` becomes a thin `app = Flask(...)` + blueprint registration

### 3D. `device_wrapper.py` (1134L → 4 modules)
- [ ] **3D.1** Extract `PlaywrightContextConfig` + `PlaywrightContextAdapter` (L44-147) → `device/playwright_context.py`
- [ ] **3D.2** Extract `MonitoredDevice` (L148-476) → `device/monitored.py`
- [ ] **3D.3** Extract `PlaywrightGameDevice` (L489-1174) → `device/playwright_game.py`
- [ ] **3D.4** Extract trace/WS frame plumbing → `device/action_tracing.py`
- [ ] **3D.5** Keep `device_wrapper.py` as a re-export shim
- [ ] Note: `_WEB_DEVICE_LOCK` must stay an `RLock` (CLAUDE.md L?? — re-entrant requirement)

### 3E. `new_main_v2.py` (1086L → coordinator + 4 modules)
- [ ] **3E.1** Extract `initialize_runtime_device` + backend selection → `main_loop/device_init.py`
- [ ] **3E.2** Extract sleep cycle logic (L202-397) → `main_loop/sleep_scheduler.py`
- [ ] **3E.3** Extract `_run_daily_tasks` (248L, 20 task blocks) → `main_loop/task_orchestrator.py`; consider a registry/list-of-tasks pattern over the giant if-chain
- [ ] **3E.4** Extract `save_error_screenshot`, `log_main_page_mismatch` → `main_loop/error_logging.py`
- [ ] **3E.5** Reduce `main()` (L820-1119, 300L) to thin coordinator

**Estimated impact**: ~6 000 LOC moved across files. **High** PR review burden — propose one module per PR + run pytest + smoke run after each.

---

## Phase 4 — Lamp V1 retirement (gated on V2 adoption)

- [ ] **4.1** Flip `use_opengold_v2 = true` for the two remaining devices (`use_phone_ocr_lamp_mode` device + `emulator-5560`)
- [ ] **4.2** Port `is_compare=False` path to `opengold_v2.LampService` if missing
- [ ] **4.3** Soak test ≥1 week on V2 across all 6 devices
- [ ] **4.4** Remove V1 branch from `game_actions/lamp_scheduler.py:32-33` and `_run_lamp` in `new_main_v2.py:290-297`
- [ ] **4.5** Delete `Open_gold_paddle_ocr.py` (1239L)
- [ ] **4.6** Update CLAUDE.md OCR section ("Open_gold_paddle_ocr.py 已改用 img_tools 共用 fallback") to reflect retirement

**Gate**: must NOT be started until 4.3 passes.

---

## Decisions made (2026-05-19)

1. ✅ **`farm_v2/`** → wired in, `farm/` retired. Commit `c1f01d8e`. Renamed `run_farm` → `farm` to match call-site signature. Test stub updated. Tests 392 pass / 8 skip.
2. ✅ **`miner/v2/`** → keep (flag-gated experimental).
3. ✅ **`miner_test/`** → delete (research sandbox, not production).
4. ✅ **`tmp_ws_capture/`** → migrate writer to `logs/_archive/ws_capture/` (per `LogPaths`) then `rm -rf tmp_ws_capture/`. Same for any other writers (`utils/ws_listener.py`, `utils/web_game_api.py`, `tools/build_equipment_cache.py`, `device_wrapper.py`).
5. ✅ **Branch**: single PR for Phase 0 + Phase 1 + remaining Phase 2; splits (Phase 3) one PR per module; lamp V2 retirement (Phase 4) separate later.

## Pre-flight findings (2026-05-19 audit)

- Git is dirty with 2 uncommitted intentional changes (web_h5 init interruptible backoff + 5560 V2→V1 revert) — those stay untouched on the cleanup branch.
- Recent `git log` (last 30 commits) shows the user is already 1–2 weeks into this refactor — see Phase 2 "Already landed" list. **My job is to extend that work, not duplicate it.**
- **Infra blocker (out of scope, flag to user)**: Syncthing has been syncing `.git/` itself across machines, producing 1 051 sync-conflict files inside `.git/objects/`. Doesn't break git operation but is the **root cause** of the source-tree sync-conflicts. Recommend adding `.git/**` to Syncthing's per-folder ignore patterns and then `find .git/objects -name '*.sync-conflict-*' -delete`. Worktrees probably have the same issue.

---

## Review (after execution)

_Filled in as phases land. Each phase ends with: what changed, what tests proved it, regressions found._

### Phase 0
- [ ]

### Phase 1 — DONE 2026-05-19

Commits on branch `chore/consolidation-2026-05-19`:

| SHA | Phase | Files | Net LOC |
|---|---|---|---|
| `ef5cc8aa` | P1F miner_test sandbox | 24 | −9.6 MB / RL artifacts |
| `6d61ec47` | P1B ws_capture migration + tmp/trash/dated purge | ~24 000 | path moved, captures regenerate |
| `c1f01d8e` | P1E farm_v2 wire-in / farm/ retired | 13 | −281 |
| `f57ea8da` | P1C refactor/ scaffold + core/ + partner/ + mission PNGs | 28 | −370 |
| `9becba70` | P1A+1D dead .py modules + scratch | 14 | −1 600 |
| (no commit) | P1A sync-conflicts at root + tests/ (gitignored) | 68 | — |

**Net**: ~24 200 files removed, ~3 700 LOC of code/scripts deleted, 10 protocol docs preserved at `docs/protocol/` and `docs/game_client_sources/`. Tests held at **392 pass / 8 skip** throughout.

Audit corrections made on the fly:
- `find_img/`, `reward_get/`, `dataset/` originally flagged for delete — kept (live runtime writers).
- 2 sync-conflict files in `tools/` missed by initial sweep — caught in P1B commit.
- `farm_v2/run_farm` renamed to `farm` to match legacy call signature.

### Phase 2 — pending
- [ ] device_wrapper.py ~25 remaining silent `pass` blocks → warning log
- [ ] device_wrapper.py 7 bare `except:` → typed catches with log
- [ ] Extract `poll_stage(d, target, timeout)` shared helper
- [ ] `bot_state.clear_offline_devices()` two-stage lock merge (verify still applicable post 4d7d8893)

### Phase 3 — 2/5 done, 3 deferred
- [x] json_manager.py 878L → `json_manager/` package (7 files) — commit `8c12cac0`
- [x] new_battle.py 1093L → `battle/` package (7 files) + shim — commit `9b56f620`
- [~] control_panel_app.py 1722L — **deferred**. Flask app with 3 worker-thread state dicts (_web_login_state, _labeler_state, _trainer_state) and only 3 indirect tests. Reasonable next step: extract `_run_web_login_worker` (L517-758, 241L) into `control_panel/web_login_worker.py` in its own focused PR, paired with new unit tests for the worker's pause/resume/backup paths. Routes+broker stay in main file until coverage exists.
- [~] device_wrapper.py 1134L — **deferred**. Playwright lifecycle module just touched by Phase 2 (`0da9b9d3`); needs to stay stable while runtime soaks the new logging. Plus `_WEB_DEVICE_LOCK` RLock invariant (CLAUDE.md) means any restructure risks reentrancy bugs. Revisit after a week of green production runs.
- [~] new_main_v2.py 1086L — **deferred**. Splitting collides with the unstaged WIP web_h5-init interruptible backoff at L869. Land that first, then revisit `_run_daily_tasks` (248L) extraction into a task registry.

### Phase 4 — pending
- [ ] Flip use_opengold_v2=true for remaining 2 devices
- [ ] Port phone-OCR + 5560 paths to opengold_v2 if missing
- [ ] Soak-test 1 week
- [ ] Remove V1 branch from lamp_scheduler + new_main_v2
- [ ] Delete Open_gold_paddle_ocr.py (1239L)
