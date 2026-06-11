# tasks/todo.md（2026-06-11 重整）

> 已完成項目（含完整 Review）已移至 `tasks/archive/todo_archive_2026-06-11.md`。
> ws_token 家園三件待辦另見 `tasks/ws_token_home_todo.md`。
> 重構優先序真相來源：`docs/REFACTORING_OPPORTUNITIES.md`。

---

## 🔒 另一 session 進行中（本 session 勿動）

- **挖礦 WS**：由另一個 Claude session 處理中（2026-06-11）。涉及 `ws_token/mining*.py`
  （mining.py / mining_adapter.py / mining_h5_executor.py / mining_supervised.py）與對應測試。
  本 session 不要編輯這些檔案、不要重複規劃挖礦 WS 相關工作。

---

## ⚠ 待重啟生效（改完未重啟 = 白改）

- master（infinite）+ worker（desktop_ov0asq4）的 `new_main_v2.py` 需重啟，套用：
  1. 掉線 1h 判離線 fix（2026-06-11）
  2. 手機離線 WS 降級 + 純 adb 被動撈 token（2026-06-11）
  3. control_panel_app blueprint 拆分（2026-06-11；重啟後做 dashboard live smoke = P3-CP-8）
- 重啟後待驗：passive token scrape 實機 live 驗證（等手機/模擬器下次冷啟）。

---

## 進行中 / 待辦

### WS 後端整合（branch `feat/ws-token-integration` / `feat/dragon-realm`）

**已 live 跑的 runner 任務**：main_tasks、league_solo、guild、steward、redpack、lamp（gate `ws_token_open_lamp`）。

**recon 完成、待 build（6 features，schema 已在 docs/protocol/）**：
| feature | 關鍵 cmd | 風險/備註 |
|---|---|---|
| 轉盤金幣 | `ad_wheel_info`5635 / `ad_wheel_spin`5636 | 只能轉免費次數 |
| 掛機/離線獎勵 | `main_chapter` 3333/3334 | 離線 type2=server push；8h 上限 |
| 跨界停車 | `car_park` 12801/12847 | space_list 空位欄位待釘；只停跨界 type==3 |
| 深淵之門 | `dungeon` 3591/3592/sweep 3596 | ⚠ anti-cheat，優先試掃蕩 |
| 週副本(萬神) | `dungeon` type=23，門票 gtid 1081 | ⚠ 同上 |
| 農場/打工 | home_farm 3077/3078/3081 + 18689 | WORKER_COMMON cmd_ids 漏 18689-91 待補 |

**待 live 釘的 config 值**：深淵 type、農場 seed_id/fertilizer_id/team_cfg_id、豐收卡 shop_type/id、轉盤獎品表。

**共通風險**：副本 anti-cheat（勿無腦送 result=0）；看廣告加倍 WS 拿不到。

**kick/異地登入（建構中）**：被踢訊號 = cmd 259（0x103, body `{1:20}`）+ 斷線 → 30 分冷卻 → online-check 再查。

**整合步驟殘項**（S5b 解耦 online-check 已完成，見 archive）：
- [~] S0 `ws_token/online_guard.py` 純 WS 在線檢查 — 建構中(subagent)
- [ ] S6b 神燈接進 runner（可選；num=20 預設已滿足）
- [ ] guild_members_info(cmd 7440) 補進 `ws_token/guild.py`（online_guard 一併）
- [ ] S5 手機帳號「撈 token → 下線 → 純 WS 跑到 token 失效」流程接線
      （前置已齊：online_guard live 驗過、降級模式+被動撈 token 已落地）
- [ ] S7 5556 pilot live 驗證（撈 ticket → 跑一輪）

### 降運算 Phase 2（需 live 驗證，等拍板）

- [ ] 2.1 Chrome `--disable-gpu` / headless（本機最大 GPU 削減；需驗 Cocos 畫面+OCR）
- [ ] 2.2 Cocos `cc.game.setFrameRate` 限 FPS（該 build 是否支援未驗）
- [ ] 2.4 `cv2.matchTemplate` 前 2× 縮圖（需重調 0.8 門檻）
- [ ] 殘留重疊：`wake_up_handler.py:322` 舊分流延遲與 F4 啟動錯峰疊加（動時須保 5554 online-check 提前 return）

### 重構待辦（真相來源 docs/REFACTORING_OPPORTUNITIES.md，依其 Phase 順序；細節/陷阱以該文件為準）

**前置（最先做）**
- [ ] 收尾未 commit 工作區項：`oracle()` 優化、`get_stage` OCR 合併、bot_state Phase 2（皆已實作+測試，待 commit）
      ⚠ 工作區目前混有挖礦 WS session 的改動，等該 session 收尾後再 commit，避免 diff 互混

**Phase 0 — 死碼刪除 / cruft（✅ 2026-06-11 完成，commit `25542e41`；審查 APPROVED）**
- [x] `git rm --cached` 4 個 `tests/__pycache__/*.pyc.NNNNN`
- [x] `git rm main.py` + 移除 `SCRIPT_ARCHITECTURE.md` 條目
- [x] 刪 `game_state/detector.py` `new_stage_check()`（grep 確認零 .py 呼叫者）
- [x] scratch 腳本歸位（fix_prints/quick_test 刪除；test.py/dashboard_test→tools/scratch/；
      benchmark_screenshot→tools/；lian_shan_example→docs/examples/；5 個 root test_*.py 去前綴→tools/debug/；
      spin_and_send_gold_single_runner 依規未動）
- [x] root `pyproject.toml` testpaths=["tests"]；`test_item_placement_guards.py` 已一併移入 tests/
      並修 stale import（miner.planning.executor / miner.scripts）+ 斷言對齊現行 base_label 契約
      （註：`test_json_manager.py` 為 print-based scratch，doc 未指派去向，留 root，TESTING.md 已記載）
- [x] 刪孤兒 worktree 目錄 ×5（~37.7k 檔；註冊 worktree miner-reverse-search/bugfix/feature 確認完好）
- [x] 刪空目錄 found_matches/、'2026-01-20 195013'/、trash/；刪 miner/rl/rl_logs/（live miner/rl_logs/ 未動）
- [x] gitignore cosmetic 補充（capture 目錄 + 時間戳 glob，內容未 purge）
- [ ] （等 bot 停機）live-tree sync-conflict sweep（~3.6k 檔，在 active profile/logs 內，bot 跑時勿刪）；
      源頭：`miner/dataset/`、`playwright_profile/`、`logs/` 加進 Syncthing `.stignore`

**Phase 1 — 去重（✅ 2026-06-11 完成，commit `92c1cf8b`；審查 APPROVED，125 tests passed）**
- [x] device-id 正規化 ×2 改 `LogPaths.safe_device_id()`（manager_factory.py 已 commit；
      ⚠ new_main_v2.py 兩處同主題改動只在工作區 — 該檔混有他 session diff，待其收尾後與前置一起 commit）
- [x] `opengold_v2/ui_controller.py` 改呼叫 `sea_v2.navigator.world_to_pixel`（round() 保留、frame=(540,960)、公式等價已測）
- [x] 新 `utils/json_io.read_json_bom_safe`：json_manager/base.py 與 equipment_cache.py 讀側已導入
- [x] per-device `{ip}.json` 走 `JsonDataManager`：Mission.py load_data/record（flat schema/檔名不變）、
      fight_car.flush_logs 改 `_atomic_write_json`；new_main_v2.temporary_reset_cycles 同上 ⚠ 在工作區待 commit
- [x] 主頁 9 點像素守衛抽 `utils/main_page_guard.is_main_page_with_popup(img)`（device.py + Mission.py 兩份已換，
      profile byte-identical 驗過；battle/manager.py、park.py、tools.py 三份 inline 副本留待後續）

**Phase 2 — 效率（熱路徑；多數已完成，見 archive）**
- [x] config mtime 快取 / park sleep / 頁面 CNN inference_slot+device-aware（commit `4d2766e3`）
- [ ] `oracle()` / `get_stage` OCR 合併 → 在工作區，併入上方「前置 commit」
- [x] OCR 詞表向 `OpenGoldConfig` 收斂（✅ 2026-06-11，commits `50a7ada2`+stub fix；UNWANTED_COMBOS/SKILL_MAP/
      pair_rewrite 由 OpenGoldConfig 派生，量→暈 server alias 保留；STAGE_TEXT_REPLACEMENTS 屬 server 專用刻意不收斂）

**Phase 3 — 高風險拆分（每項獨立一輪審查；多數需停 bot 視窗）**
- [ ] `control_panel_app.py`(2902L) blueprint 漸進拆分 → **詳細計畫見下方「control_panel_app 拆分計畫」段落（2026-06-11 規劃，待使用者過目）**
      附帶：`831-835` cv2/numpy 重 import 改 lazy、刪 `943` 冗餘 flask import（各自一 commit）
- [ ] Flask 錯誤封套：抽 `_cdp_action(ip, js)` + `_cdp_err_code(err)`（先做 4 個 fire-and-forget CDP route；
      `@json_endpoint` 套 21 處延後分批、勿同 commit）
- [ ] carpark/cocos JS walker 共用化（**熱路徑**）：新 `utils/carpark_js.py` 放 `_FIND_WALKER`/`_WORLD_TO_SCREEN`；
      先重構 `cocos_navigator` 自身 4 份內部副本證明 helper，再逐點遷 carpark（path_parts 用 JS 陣列參數、
      每點先 log 新舊座標 assert 相等）；勿同 pass 動 farm_v2/sea_v2
- [ ] carpark `park_one_silver` 抽 `_reenter_silver_detail_list()`（兩分支 6 行逐字重複回復塊）
- [ ] carpark `reconcile()`：`_build_snapshot_summary` 升 module-level 純函式（勿抽純 _reconcile_plan）
- [ ] `PlaywrightGameDevice._start`(150L) 分解：先抽 `utils/web_profile_paths.py` 共用 path 正規化
      （統一採 normpath + 單測兩路徑一致），再抽 `_launch_persistent_context_with_fallback`（純 code-motion）
- [ ] V1 神燈死碼移除（gate：V2 prod log 穩定）：刪 `Open_gold_paddle_ocr.py` 4 個函式 + `__main__`、
      退休 `lian_shan_example.py`、修 stale banner/test docstring
- [ ] 舊計畫殘項：device_wrapper ~25 silent `pass` → warning log、7 個 bare `except:` → typed catch、
      抽 `poll_stage(d, target, timeout)` 共用 helper

**每 Phase 共同規則**：完成後獨立審查 + 重啟 `new_main_v2.py`；Phase 間勿混同一 commit。

### control_panel_app 拆分計畫（2026-06-11 規劃，待使用者過目後執行）

> 盤點結論：2902 行、63 條路由 + 1 條 WS、18 個 module-level 全域狀態、~20 個跨路由 helper。
> 對外相容面：`new_main_v2.py` 用 `app`/`run_server`；tests 用 `app.test_client()` + monkeypatch `_FLY_PET_ICON_DIR`。
> **原則：path 全部不變、`control_panel_app.app` / `run_server` 介面不變（外觀 façade），測試零改動即綠。**

**目標結構**

```
control_panel_app.py            # façade：建 app、註冊 blueprints、run_server、re-export 測試用符號
control_panel/
├── __init__.py
├── shared/
│   ├── cdp.py                  # _cdp_evaluate / _cdp_json_response / _FLY_PET_LOCK_JS
│   ├── command_queue.py        # _remote_commands/_global_commands/_worker_webhook_endpoints
│   │                           #   + _commands_lock(RLock) + queue_command/_is_local_command_target
│   │                           #   + _push_to_worker_webhook/_push_remote_command_if_possible
│   └── auth.py                 # _fly_pet_auth + _FLY_PET_USERS
├── routes_pages.py             # / /updates /war-room* /fly-pet 登入登出頁 + frontend_version/bug_feedback
├── routes_status.py            # /api/status /device_data /daily_progress /analyze_stage + check_ocr_server
├── routes_control.py           # pause/resume/skip_sleep/wake_delay/manual_release/force_sleep/recover
├── routes_config.py            # /api/config /api/ocr_config
├── routes_worker.py            # poll_commands/report_status/register_device/refresh_devices
├── routes_web_session.py       # web_login*/web_backup_state/web_launch/web_close + _run_web_login_worker(287L)
├── routes_live_view.py         # /ws/live_view + /api/live_view/*/stop（_live_view_sessions/_live_view_lock）
├── routes_labeler.py           # labeler/trainer 全部路由 + worker threads + _LineLogWriter
└── routes_fly_pet.py           # 17 條 /api/fly_pet_*（~750L，最大群）+ /api/fly_pet_icon + cdp_evaluate
```

**關鍵風險與對策**

1. **全域狀態唯一性**：`_commands_lock` 與三個 command dict 被 worker 路由 + control 路由共用。
   拆分後**必須只存在一份**（shared/command_queue.py 是唯一真相），blueprint 一律 `from ... import` 模組
   再以 `module.attr` 存取（不要 `from x import dict` 後重新賦值）。
2. **flask-sock**：`Sock(app)` 必須在 app 建好後初始化，`/ws/live_view` 路由註冊要在 façade
   或 `routes_live_view.init(sock)` 內延後綁定；flask_sock 缺席時優雅降級（現行為保留）。
3. **測試 monkeypatch 面**：`_FLY_PET_ICON_DIR` 等符號要在 `control_panel_app.py` re-export，
   且 icon 路由讀取時要透過模組屬性（不可在 import 時凍結成 local），否則 monkeypatch 失效。
4. **無 factory**：維持 module-level `app`（new_main_v2 直接 import）；不引入 create_app，降低改動面。
5. **bot 在跑**：每步落地後需重啟 master/worker 才生效；安排在停機窗執行。

**執行紀錄（2026-06-11 實作完成，7 個 Opus subagents 平行抽取 + façade 整合）**

- [x] P3-CP-0 `control_panel/` 骨架 + shared/cdp.py、shared/auth.py、shared/command_queue.py
- [x] P3-CP-1 fly_pet blueprint（840L；test_fly_pet_template/breed_template 的 CONTROL_PANEL 路徑跟著指向新檔）
- [x] P3-CP-2 labeler/trainer blueprint（435L，自含）
- [x] P3-CP-3 web_session blueprint（585L，含 _run_web_login_worker）
- [x] P3-CP-4 worker_sync blueprint（225L）
- [x] P3-CP-5 control blueprint（84L）
- [x] P3-CP-6 live_view blueprint（161L；WS 路由經 `init_ws(sock)` 延後綁定）
- [x] P3-CP-7 pages/status/config blueprints（142/327/53L）；façade 縮到 139L
- [ ] P3-CP-8 （待停機窗）重啟 master+worker、dashboard 全功能 live smoke（狀態頁/暫停/飛寵頁/live view）
- [ ] （未 commit）等挖礦 WS session 收尾、工作區 diff 解纏後 commit

**Review（2026-06-11）**
- 80 tests 全綠（fly_pet 全系列 + smoke_config + worker_routes + pause_routing + register_disabled
  + dashboard_template + bootstrap_api_services）。
- 路由 parity 驗證：63+2 條 path 與 HEAD 完全一致（含 /updates/、/war-room/ trailing-slash 變體與
  /ws/live_view/<ip>）；`_FLY_PET_ICON_DIR` 指 repo 根 static/flypet_icons 正確。
- 晚綁定面已驗：`_cdp_json_response`/`_FLY_PET_ICON_DIR`/`_push_to_worker_webhook`/
  `_push_remote_command_if_possible`/`_run_web_login_worker` 全部走 façade 屬性查找，monkeypatch 生效。
- façade 額外 re-export `requests`（tests monkeypatch `cpa.requests.post`）。
- 順手修一個**既有**跨檔測試汙染（與本拆分無關）：`test_bootstrap_api_services` 收集期塞 stub
  `worker_webhook_api` 害 `test_worker_webhook_applies_wake_delay`（掉線判離線 pending diff 新增的測試）
  import 到 no-op stub → 該測試現在 import 前用 `monkeypatch.delitem` 暫時逐出 stub（測後自動還原）。
- 附帶項（lazy cv2 import、冗餘 flask import）：原 943 行冗餘 import 已隨 façade 重寫自然消失；
  cv2/numpy module-level import 照原樣搬進 routes_status（保持 parity，lazy 化另案）。

### 其他既有待辦（檔案各自追蹤）

- `tasks/ws_token_home_todo.md` — 家園三件（守護靈/加工坊/伴侶），feat/ws-token-home 未 merge
- `tasks/carpark_adb.md` / `tasks/carpark_skip_silver.md` / `tasks/mount_sprint_todo.md` /
  `tasks/sea_v2_todo.md` / `tasks/mining_ore_ab.md`

---

## 工作慣例提醒

- TDD；subagents 一律 model:"opus"；計畫/進度走本檔。
- 動 live bot（new_main_v2/device_wrapper/排程）先把 plan 寫此檔給使用者過目。
- 改完 runtime 檔必提醒重啟（sys.modules cache）。
