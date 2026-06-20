# 後端架構審計 + 重構交付 (2026-06-21)

> 角色:接手後端的資深架構工程師。範圍 = 前端以外的一切。
> 方法:獨立 worktree (`feat/backend-arch-audit`,從 `main@008895f7` 切,NAS 外) +
> 多代理審計 workflow(8 叢集 × 對抗式驗證,28 agent)對**當前 main** 逐項驗證,
> 而非重做三週前的舊分析。所有改動逐段 TDD + focused 測試 + 獨立 commit。
> 與前端工程師(`feat/ui-design-system`)以 worktree 隔離,全程監看其分支提交,不碰前端檔
> (`templates/`、`static/`、`*.html/css`、`bot_config.json`)。

---

## 0. 一句話結論

這個後端**已經處於良好狀態**:既有重構/效能 backlog(`docs/REFACTORING_OPPORTUNITIES.md`,3 週前)
**約 85% 已落地到 main**(perf 分支 `4d2766e3`/`ac16284a`/`4f624c18`、dedup `92c1cf8b`、
control_panel blueprint 拆分、Phase 0 cruft `25542e41`)。資深工程師的價值不在重做,而在:

1. **抓出一個真實資料完整性缺陷**(非原子 JSON 寫 → 靜默清空每日/每週閘)並修復。
2. **收尾仍開放的低風險去重**(3 處內嵌守衛、1 個巢狀閉包)。
3. **把已 drift 的文件導正成事實**(本文件 + INDEX 修正)。
4. **為高風險殘項給出已驗證的執行策略**(需停機窗或使用者拍板)。

---

## 1. 架構概覽(對當前程式碼驗證,非依文件)

### 1.1 進入點與主迴圈 — `new_main_v2.py`(618 行,曾 ~1187,瘦身進行中)

`__main__`(L563-618):rotate logs → 起 push server → `mode==master` 則起 control_panel
`run_server(5002)` thread + `online_check_service`;否則起 worker webhook+sync。
`configure_torch_runtime` + `set_inference_concurrency`(GPU 分流)→ `ensure_local_model` 載 CNN →
30s 掃描迴圈呼叫 `runtime_services.device_scan_service.scan_and_start_devices`
(**唯一 thread 生成點**;registry `_running_threads` 在 new_main_v2,鎖在 `runtime_services.thread_registry`)。

掃描注入 3 類非 ADB 裝置:web_h5(`get_web_backend_devices`)、純 WS runner
(`get_ws_runner_devices`)、offline_fallback ADB 手機(`get_ws_fallback_devices`)。
ADB 缺席 >1h offline、dead-device gate 阻止 >3h 後重啟。

### 1.2 每裝置主迴圈 — `main(ip, ...)`(L117)

- `use_ws_runner` → `runtime_services.ws_runner_service.run_ws_device_loop`(純 WS,無 ADB/Playwright)後 return。
- 否則:setup logger → `_handle_startup_sleep` → `initialize_runtime_device`(adb `connect_u2_with_retries`
  或 web_h5 `PlaywrightGameDevice`),帶 `before_web_device_start` hook **在開瀏覽器前先跑 WS-first 階段**
  (WS 登入會踢掉其他 session,順序是 load-bearing)。
- 內層 `while(1)`:force_sleep/web_close/pending_web_launch 檢查 → `run_ws_phase` →
  `handle_device_wakeup` → in_game/啟動遊戲/`handle_game_startup_pages` →
  `ws_ticket_refresh`(web_h5)或 `adb_token_scrape`(adb)→ `daily_pipeline.run(DailyContext(..., ws_done))`。
- 例外階梯:`WakeLoopInterrupted`/`ForceSleepRequested`/`PhoneUnreachableError`/`StartupBypassError`/
  啟動&執行期 `LoginConflict` 各對應一種 sleep 策略;尾端 `run_sleep_cycle`。

### 1.3 任務管線 — `game_actions/daily_pipeline.py`

`run(ctx)` → `_run_tasks`。實際順序(已超出文件「20 項」的說法):
T0 紅包 → T0.5 carpark reconcile + click_white → 5558 switch_skill → T1 地獄之門 → T2 農場 →
T3 寶箱 → T4 家族(stage 被 T5/T6 重用)→ T5 守護靈 → T6 抽技能夥伴 → T7 商店 → T8 坐騎強化 →
T9 每日加速 → T10 競技場 → T11 挖礦/Oracle → T12 所有日常(20-23h)→ T13 菇菇武道會 →
T13.5 菇菇雕像(週五)→ T14 航海 → T14.5 龍骸聖域 → T14.6 煩惱消 →
T14.7 天梯每週獎勵(週二 WS 0x4001)→ T15 萬神試煉 → T16 雲端戰鬥 → T17 雙週副本 →
T18 好友禮物 → T19 開神燈 → T20 轉盤。
`ws_done` frozenset 讓 WS 已完成任務透過 `_ws_skip()` 跳過;WS 完成項回寫 dashboard 每日進度
(`ws_phase._record_daily_done`)。連續 >=4 次非主頁 → `_ConsecutiveMismatchAbort` 強制停 app。

### 1.4 WS-first 階段 — `game_actions/ws_phase.run_ws_phase`

由 `ws_token.enabled` 控;每次喚醒 lazy 跑一次 `ws_token.runner.run_device`;把 `RunReport` 映射成
管線 skip-set。可中斷(web_h5 輪詢 `pending_web_launch`)+ 持久 resume ledger(`ws_state` ws_resume,
30 分/同日 TTL)。`ws_token/runner.py` `TASK_ORDER` 有 **25 項**(遠超其 docstring 列的 10 項):
carpark, main_tasks, league_solo, redpack, mail, idle_reward, ad_rewards, turntable, tycoon, farm,
dungeon, rogue, statue, guild, steward, relic, relic_sprint, gacha, gacha_free, kungfu_store, spirit,
workshop, couple, mining, lamp。carpark 第一個跑以搶 10:00 跨服窗。

### 1.5 後端抽象 / Master-Worker / 排程

- **後端**:`device_wrapper.MonitoredDevice`(L359)包兩種後端;`PlaywrightGameDevice`(L729,
  `_start` L786)是 web_h5;路由 key `bot_state.is_local_device`(L97,**非** `':' in ip`)。
  `device_wrapper.py` 仍 1626 行(god-module,`_start` 仍 ~140 行)。
- **Master/Worker**:`control_panel_app.py` 已是 **149 行 façade**;路由拆進 `control_panel/`
  blueprint(`routes_status/config/control/worker/web_session/live_view/labeler/fly_pet/pages/inventory/
  ad_reward/relic_sprint/tools_optimize` + `shared/{auth,cdp,command_queue}`),迴圈註冊
  (control_panel_app.py:80-96),`bootstrap/api_services.py:30-32` 起 server。
  `online_check_service` 為 master-only 解耦(裝置不為線上檢查醒來)。
- **排程**:`runtime_services/sleep_service.calc_aligned_wake_ts` 對齊整點窗 + parity 分流
  (已抽到 `runtime_services/wake_parity`,與 startup_sleep 共用)。`bot_state` per-device registry + 信號信箱。

### 1.6 資料流(熱路徑)

```
device thread
 ├─ WS-first: ws_token.runner → 各 ws_token/*.py（純 WS RPC, server-authoritative）
 │            cadence 落 ws_state/<device>.json（每日/每週閘 ledger）
 ├─ 喚醒/辨識: new_cnn.cnn_model.predict_image（CPU, inference_slot 序列化）
 │            detector.get_stage → img_tools.get_all_text_with_results（單次 OCR）
 │            OCR/config 走 config_manager 記憶體 mtime 快取（穩態零 NAS I/O）
 └─ daily_pipeline: 逐任務；JSON 狀態走 json_manager.JsonDataManager（_atomic_write_json）
```

設定/狀態持久化的「schema」即各 `JsonDataManager` 的 per-device `{ip}.json` + `bot_config.json`
(host override)+ `ws_state/<device>.json`(WS cadence)+ `ws_token/data/*.json`(擷取的封包/選擇)。

---

## 2. 審計結論:backlog 現況(對 main 驗證)

| 類別 | 已 DONE(只剩文件 drift) | 仍 OPEN |
|------|--------------------------|---------|
| cruft (Phase 0) | #1 .pyc、#2 main.py、#3 new_stage_check、cruft-4 junk dirs、cruft-5 rl_logs、#11 pyproject testpaths | (無實碼;只剩 backlog/INDEX 文字未更新) |
| dedup (Phase 1) | dup-1 world_to_pixel、dup-2 {ip}.json→JsonDataManager、dup-3 safe_device_id、dup-5 json_io BOM | **dup-0**(3 處內嵌守衛,本次修)、cocos_navigator 4 walker(延後) |
| 複雜度 | cx-0 control_panel blueprint 拆分 | cx-1 Flask 封套、cx-3 V1 神燈死碼、cx-4 park_one_silver、**cx-7**(本次修)、cx-2 carpark JS |
| 效率 | eff-0~6 全在 main(config mtime 快取、park sleep、oracle、get_stage、頁面 CNN gate/CPU) | (無;eff-5 已被 eff-4 連帶解決) |

對抗驗證另外**抓出 finding 本身的錯誤建議**(避免照做出包):
- cruft-1 建議「把 root `test_json_manager.py` 移進 tests/」→ 其實 `tests/test_json_manager.py` 已存在會撞檔,
  且 root 那個是 `input()` 互動 CLI(移進去會被收集然後 hang);正解是**刪**,不是移。
- cx-4 宣稱「有 pinning test 守護」→ 實際測試在 `_click_silver_lot_by_idx` 回 False 時就 `continue`,
  **沒覆蓋到要重構的 recovery 分支**;必須先補測試再動。
- eff-5「每次 OCR 重讀 NAS」前提已失效:`load_config` mtime 快取後那些 resolve 變記憶體 deepcopy。

---

## 3. 問題領域(仍開放,分類)

### 3.1 資料完整性 — HIGH(本次已修)

- **非原子 JSON 狀態寫**(`ws_token/state.py`、`ws_token/ladder_reward.py`):裸 `write_text`。
  NAS/SMB 上 crash/kill/SMB 抖動中途中斷 → torn 檔 → `load_state` 判 corrupt 回 `{}` →
  **靜默清空每日/每週閘** → once-per-day 消耗任務(情侶禮物、雕像果)重觸發。
  `mine_terrain.TerrainModel.save` 早已用 tmp+`os.replace`,只是 state/ladder 沒跟上。

### 3.2 重複(maintainability)

- **dup-0**(本次修):3 處 byte-identical 9 點主頁守衛(`park.py`/`tools.py`/`battle/manager.py`)→
  `utils.main_page_guard.is_main_page_with_popup`。
- cocos_navigator 4 個 `find` walker(`_CLICK_JS`/`_VIEW_STATE_JS`/`_DISMISS_TOP_POPUPS_JS`/`_FIND_CLOSE_BTN_JS`)
  + carpark_auto 24 個內嵌 walker + 10 個 worldToScreen 字面量。**延後**:見 §5。
- `ws_token.runner.run_device` ~35 個 kwarg 被 `ws_phase._run_device` 與 `ws_runner_service` 各自手抄轉傳
  → 已有歷史漏傳整批任務的紀錄(docstring 自陳)。應抽 `build_run_kwargs(cfg)` 單一來源。
- ws_token 5 處 once-per-day 閘 boilerplate(`main_tasks/rogue/statue/gacha_free/couple`)手抄
  (couple 因複製漂移用 `gift_date` 而非 `last_date`)。應抽 `already_done_today/mark_done_today`。

### 3.3 複雜度 / 結構

- `device_wrapper.py` 1626 行 god-module;`_start` ~140 行做 5 件事;web profile 路徑解析與
  `control_panel/routes_web_session.py:47-73` 重複且 **normpath 不一致**(dashboard 用 normpath、`_start` 沒有)。
- `daily_pipeline._run_tasks` ~320 行(20+ 任務內聯)。
- `Open_gold_paddle_ocr.py` 1481 行,V1 神燈 4 函式 + `__main__` 仍在(已非 live 路徑)。
- Flask 31 處 hand-rolled `jsonify error` 封套;CDP `code=400/502/500` 三元式仍重複於 `routes_fly_pet.py:417`。

### 3.4 效能(現況良好,只剩微項)

- 熱路徑均已優化(見 §2 效率列)。殘留微項:`miner/models/classifier.py:26` board 分類器仍
  `cuda-if-available`(其餘 CNN 已 CPU-default;可能是 batch 路徑刻意,需確認 GPU 政策);
  `relic_sprint` 每輪重複讀 sprint 快照一次(多一個 6572 WS call)。皆 LOW。

### 3.5 安全 — HIGH/MEDIUM(需使用者拍板,見 §5)

- `control_panel/shared/auth.py:6` **明文帳密** `{"infinite": "infiniteroot"}`(已進版控)。
- `control_panel_app.py:39` secret_key = `sha256(b"mushroom-fly-pet-dashboard-key")` 靜態 → session 可偽造,
  繞過 `_fly_pet_auth` 閘,**不需密碼**。
- `control_panel_app.py:149` 綁 `0.0.0.0:5002`,把 CDP-evaluate + worker-command endpoint 暴露到全網段。
  三者疊加 = 全網段可偽造 session 控制儀表板。

---

## 4. 本次已執行的改進(commit on `feat/backend-arch-audit`)

| commit | 內容 | 測試 |
|--------|------|------|
| `32a024c9` | **fix**: `ws_token/state.py` + `ladder_reward.py` 原子寫(tmp+os.replace),修 torn-write 清閘缺陷 | +2 鑑別測試(`test_save_uses_atomic_replace` 等),17 passed |
| `20518dc0` | **refactor(dup-0)**: park/tools/battle.manager 3 處內嵌守衛 → `main_page_guard`,各保留迴圈/dismiss 語意 | py_compile + 8 main_page_guard 測試 |
| `0a33e842` | **refactor(cx-7)**: carpark `_build_snapshot_summary` 巢狀閉包 → module-level 純函式,dict shape 不變 | 43 carpark pinning 測試 |
| (本文件 + INDEX 修正) | 文件導正:架構/問題/策略交付 + INDEX planner 預設、control_panel 行數 drift | doc-only |

選擇原則(ponytail):只執行**已對抗驗證為安全 + 高/明確價值**的項;
凡「失敗只在 live 才現形且測試抓不到」(cocos JS 熱路徑)或「需停機窗/使用者決策」(_start、auth)一律延後並給策略。

---

## 5. 重構策略(延後項 + 執行條件)

> 原則:每項獨立審查 + 改 runtime 檔必重啟 `new_main_v2.py`(sys.modules 快取);Phase 間勿混 commit。

### 立即可做(低風險,本次未做但隨時可接)

- **dup-3 收尾**(cosmetic):`battle/_helpers.py:130` `ip.replace(...)` → `LogPaths.safe_device_id`(等價,純一致性)。
- **ws_token DRY**:抽 `build_run_kwargs(cfg)` 給 ws_phase + ws_runner_service 共用(消 35-arg 雙手抄漂移),
  抽 `already_done_today/mark_done_today` 消 5 處日閘 boilerplate(順帶把寫入集中到已修的原子寫)。純重構 + 測試。

### 需「先補測試再動」(live 金錢/熱路徑)

- **cx-4** `park_one_silver` 抽 `_reenter_silver_detail_list`:**先**補測試驅動 `empty_count==0` 與
  `has_cluster_bonus==False` 兩個 recovery 分支(現無覆蓋),再抽。勿動 pass_no/cluster 兩遍語意。
- **cx-2 / carpark walker**:carpark 的 worldToScreen/座標遷移是真實停車金錢熱路徑,需逐點 old-vs-new 座標
  assert 後才刪內嵌,且建議手動接管窗口。cocos_navigator 自身 4 walker **不是 byte-identical**(2 變體:
  `const next` vs `n=n.children.find`),合併雖行為等價,但 mock 測試抓不到 JS 執行期破壞 → 也建議在 live 驗證下做。

### 需停機窗(改 live launch)

- **cx-5** `_start` 拆分 + web profile 路徑去重:**行為改變**(統一到 normpath 會改傳給 Chrome 的
  `user_data_dir`/`storage_state` 字串,Windows 上 `/`→`\\`),可能讓裝置解析到不同 profile → 開到
  無登入的新 profile → 撞啟動上限 30 分睡眠。必須:先寫 characterization 測試鎖現有兩邊輸出 →
  刻意決定是否正規化 → 抽單一 helper 給 `_start` + `routes_web_session` 共用(別變第三份)。

### 需使用者拍板

- **cx-3 V1 神燈死碼刪除**:scope 有衝突 — banner(line 7)仍宣稱保留 `python Open_gold_paddle_ocr.py`
  debug CLI,而 `__main__` 正是該 CLI 且呼叫 `open_the_gold`;`docs/examples/lian_shan_example.py` 也 import
  `open_the_gold`。**請決定**:(A)整個退役 V1 debug CLI(刪 4 函式 + `__main__` + 退 example + 改 banner),
  或(B)保留 CLI,什麼都不刪。`tools/verify_lamp_via_playwright.py` 只用 1076 行以上的常數/helper,務必保留。
- **安全 §3.5**:正確修法會**使現有 dashboard session 全失效 + 需你設新密碼**,且應從 env/config 讀(不動 `bot_config.json`)。
  建議:secret_key/帳密改讀環境變數(預設 fallback 保現狀不中斷)+ 雜湊密碼 + 輪換已外洩憑證 + 評估改綁 127.0.0.1。
  這是 outward-facing + 難復原,需你確認方式後我再實作。

---

## 6. 效能備註

- **NAS round-trip 已消除**:`config_manager.load_config` 的 `st_mtime_ns` 快取(`4d2766e3`)讓穩態 = 1 次 `os.stat()` + deepcopy;
  所有 OCR/config 解析連帶受惠(eff-4 → eff-5)。
- **CNN**:頁面分類器(`new_cnn/cnn_model.py`)CPU-default + `inference_slot()` 序列化 + `inference_mode()`(`ac16284a`);
  挖礦 board 分類器仍 cuda-if-available(待確認 GPU 政策)。
- **截圖/OCR**:`oracle()` 改 1 截圖 1 推論;`get_stage` 同幀 OCR 3-4 次 → 1 次(`4f624c18`)。
- **輪詢**:`park.goto_park` 30s busy-wait 加 `sleep(1.0)`。
- 本次新增的原子寫對效能無影響(cadence ledger,低頻);無新熱路徑開銷。

---

## 7. 給團隊的後續清單(優先序)

1. (低風險)ws_token `build_run_kwargs` + `already_done_today` 去重 — 消任務漏接 + 集中原子寫。
2. (需測試)cx-4 park_one_silver recovery 分支補測 → 抽 `_reenter_silver_detail_list`。
3. (需停機窗)cx-5 `_start` + web profile 路徑單一 helper(先 characterization 測試)。
4. (需拍板)安全三項(auth/secret_key/bind)。
5. (需拍板)cx-3 V1 神燈 debug CLI 退役決策。
6. (live 驗證下)cocos_navigator / carpark JS walker 共用化。
7. (doc)`docs/INDEX.md` 其餘 drift(daily task 數、ws_token runner docstring 10→25)。
