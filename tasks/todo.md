# tasks/todo.md（2026-06-20 壓縮）

> 已完成項目（含 Review）已移至 `finish.md`（2026-06-20 archive 區塊）。
> 重構真相來源：`docs/REFACTORING_OPPORTUNITIES.md`。
> 其他檔案各自追蹤：見末段。

---

## 🔧 2026-06-24 航海/龍骸聖域 dashboard 燈接線 + 航海改日曆錨點

問題:dashboard 每日進度的「航海」燈不亮、龍骸聖域形同沒接。根因:
- 航海檔期判斷用 `sea_cycle_start`(各裝置「上次跑」時間)推 4 週 → 隨 run 歷史漂移、與遊戲檔期脫鉤、各裝置不一致。本週(06-22)實際開航海,但磁碟 `sea_cycle_start=06-17` 讓它算 off-cycle → badge 被藏 + run 排程也不會跑航海。
- 點亮用 `check_is_today`:多週活動只在跑的當天亮,隔天又變 ⏳。

修法(run 排程 + dashboard 共用日曆錨點,全裝置一致):
- [x] `json_manager/scheduling.py`:新增 `is_sea_week()`(錨點週一 2026-06-22、28 天一輪,與龍骸 `_is_dragon_week` 同形);`should_execute_sea_with_cooldown` 改吃 `is_sea_week`,丟掉 `sea_cycle_start` 週期數學(保留時段視窗 + 4h 冷卻)。
- [x] `json_manager/__init__.py`:export `is_sea_week`。
- [x] `control_panel/routes_status.py` `daily_progress`:航海 gate 改 `sea_week`(日曆)、龍骸維持 `triweekly`;兩者點亮改 `period="week"` → `manager.is_same_week`(檔期週整週維持 ✅)。
- [x] 測試:`tests/test_sea_week_anchor.py`(is_sea_week + run gate 7 例)、`test_daily_progress_badge.py` 補 `is_same_week` + week-predicate 例。

Review:
- 全綠:本批 18 例 + `test_json_manager.py` 85 例(既有航海視窗測試用 05-29=錨點前整 4 週,仍判為航海週,不受影響)。
- 只在檔期顯示(沿用隱藏 gate),非檔期週仍不顯示。
- ⚠ 需重啟 `new_main_v2.py` + 中控才生效(無 hot-reload)。
- 範圍外但同雷:`periodic_tasks.should_execute_sea` / `week_events`(legacy,daily_pipeline 未用)、武道會/坐騎衝刺 badge 仍用 today-predicate + cycle-from-last-run,未動。
- 錨點若與遊戲實際檔期不符,只改 `_SEA_ANCHOR_MONDAY` / `_SEA_CYCLE_DAYS` 兩常數。

### code-review 跟進(xhigh workflow,57 agents)
- [x] #1 時鐘分裂(CONFIRMED):dashboard 龍骸閘用 naive 本地、點亮用台北 → 抽 `_compute_daily_progress(manager, device_id, today=)`,把台北 today 同餵 `is_sea_week`/`_is_dragon_week`。(UTC+8 主機本無影響,屬 latent)
- [x] #2/#3 覆蓋缺口:`get_daily_progress` 抽成可測純函式,新增 4 例真實端對端(航海週顯示+點亮/本週非今天仍亮/非檔期隱藏/龍骸週);移除原套套邏輯測試。
- [x] #4 視窗測試巧合耦合:`TestShouldExecuteSeaWindow._at` 改由 `_SEA_ANCHOR_MONDAY` 推算 + precondition assert。
- [x] #5 wall-clock flaky:`_FakeManager` 加 `now` 注入。
- [ ] 未改(記錄):#8 `is_sea_week` 與 `_is_dragon_week` DRY 重複(可抽 `is_calendar_cycle_week`,但動到龍骸 run-gate TZ,verifier 判 pure-style);#6 `is_same_week` 少 flat-scalar 容錯(sea/dragon 紀錄恆 dict,低風險);#7 `week_events` legacy 仍用舊漂移錨(未 wired live);#9 dead `cycle_weeks` 參數;#10 sea_week/period 旗標冗餘。

---

## 🔧 2026-06-24 web_h5 WS 憑證初次種子自動化

問題:5558(web_h5)WS 階段一直噴「no captured creds」→ fallback Playwright。根因:
- `refresh_from_device`(Playwright 回寫)只刷新既有 capture,缺檔即跳過(page 讀不到 uname/plat,湊不出第一份)。
- `bootstrap_token`(冷啟 App 撈 logcat)只對 adb 生效(`_should_bootstrap` 寫死 backend=="adb")。
- 故 web_h5 缺「第一份種子」的自動路徑;其他 web_h5 模擬器是當初 adb_token_login 手動種的。

修法(使用者核准):
- [x] `game_actions/ws_phase.py`:加 `_should_seed_web_h5(ip, backend, cfg)` = web_h5 + bootstrap_token 旗標 + **缺 capture**(`_has_ws_creds`) + **adb 可達**(`_adb_reachable`,複用 `ws_runner_service._is_adb_reachable`)。`run_ws_phase` 在 adb bootstrap 後加 best-effort 種子步驟:成功本輪續跑 WS,失敗只 log → 降級 Playwright(行為同舊)。種完 has_creds 為真 → 不再冷啟。
- [x] 純雲端 web(web-xxx 不在 adb devices)自然被排除,免每輪空跑 adb_token_login。
- [x] 測試:`tests/test_ws_phase.py` +4(不可達不種/缺檔可達種/有檔不種/旗標關不種);全檔 49 綠。

ponytail 上限:缺 App 但 adb 可達的 web_h5 會每輪重試(~2min);模擬器實務上都有 App,種一次即止 → 暫不加退避。
⚠ 需重啟 `new_main_v2.py` 生效;之後 5558 下一輪 WS 會自動種子,不必再手動 adb_token_login。

---

## ✅ log 確認已 LIVE（bot 自 2026-06-19 21:57 起跑此 checkout，2026-06-20 log 核對）

下列已在 5 台（5554/5556/5560/小寶 7fe98fc6/手機 fc）log 確認實際執行、無 error：
- WS 全 pipeline 每輪跑完（ok 含 carpark/main_tasks/farm/dungeon/relic/relic_sprint/mining/lamp/... ）
- carpark_plan 跨界停車 + 倉庫收益領取（carpark task + carpark_scheduler 車位檢查）
- relic_sprint（每輪 clean 完成，無 error；但 spend 細節未入 log → #25 仍待驗）
- 農場 WS：read_work_status、buy 407/408 to daily target、豐收卡循環「開始」+ ws_farm.log 落檔（#22）
- **offline_fallback（手機 fc）**：「手機 ADB 不可達，啟用離線純 WS 備援」→ 整輪 WS 跑完，多次重複 OK = #6 LIVE 驗證 ✅
- **WS 挖礦 hold_floor deadlock 修**：pickaxe 實際遞減（8→7→6）、盤面變動、unconfirmed 自動換格續挖、無卡死 = LIVE 驗證 ✅
- token bootstrap：手機 fc 走 WS 備援皆成功（token 可用）

## ⚠ 仍需「再次重啟」才生效（on-disk 已超前 running process）

running 進程是 06-19 21:57 那版；之後 commit 的改動尚未套用。log 鐵證：
- **ad_reward 完全沒在 log 出現**，但 on-disk 已 wired（runner.py:84 TASK_ORDER「ad_rewards」）+ bot_config 5 台已開（L152/301/472/616/773）→ **running 版的 TASK_ORDER 沒有它，需重啟才會跑**（#15 卡在這）。
- 豐收卡循環只 log「開始」，缺 on-disk 的 `[1/7]..[7/7]` 詳細步驟 log（running farm.py 較舊；循環本身仍有跑，farm task=ok）。
- 其餘待套用：工具面板/遺物升級工具（中控 routes）、徽章 WS 回寫（#19）、online_guard WS online-check（需 `online_check_via_ws:true`）、control_panel 拆分 smoke（P3-CP-8）、遺物本期活動結束日 end_ts。

**動作**：重啟 master(infinite)+worker(desktop_ov0asq4) `new_main_v2.py` + 中控 → ad_reward 才會 live；之後即可驗 #15/#25。

**待 commit**：2026-06-19 夜間批次（237 測試綠，commit hold 原因已解除）。
純新檔（ad_reward.py / relic_sprint.py / routes_ad_reward.py / routes_relic_sprint.py + 各新測試）可單獨乾淨 commit；
共用檔（config_manager / bot_config.json / logging_utils / log_paths / ws_phase / daily_pipeline）混工作區，待使用者過目全 diff 再決定範圍。

---

## 進行中 / 待辦（live 驗證 + config 釘值）

### 手機fc 純 WS 停車
- [ ] #13 開窗（台灣 10:00-22:00）後跑 `python tools/carpark_cluster_probe.py` 採樣占用者 attrs，確認同服欄位 id；若 kv 不含 server_id 要修 `count_same_server`。

### offline_fallback / token bootstrap
- [x] offline_fallback：log 已確認 LIVE（手機 fc 多次「ADB 不可達→WS 備援→整輪跑完」，2026-06-20）。
- [ ] token bootstrap：fc 走 WS 備援皆成功（token 可用），但「creds 缺失時 refresh→force-stop」這條 bootstrap 路徑本身未單獨在 log 看到觸發（目前都已有 creds）。低優先。⚠ fc 與 5554 同帳號，WS 登入互踢，兩台同開 ws 需錯開。

### WS 後端整合（程式零殘項，待 live 釘 config 值）
- [ ] S7 5556 pilot live 驗證（撈 ticket → 跑一輪，等停機窗）。
- [ ] live 釘值（未填則該功能自動 skip）：深淵/萬神 dungeon type+id+num（掃蕩 only）、農場 seed_id/team_cfg_id、豐收卡 shop_type/id。
- [ ] #25 遺物衝刺 live 驗：count 單位（碎片量 vs 次數）、4 輪精確門檻、當期 act_type(13/269)、領獎回應形式。若 count=次數，run_relic_sprint 的 accrued 扣抵邏輯要改。協議 `docs/protocol/RELIC_SPRINT_RECON.md`。

### Dashboard 設定重整 — 收尾
- [ ] 重整頁面 live 驗一輪（改 template，reload 即生效，不需重啟 bot）。
- [ ] （低優先）farm/dungeon_sweeps 後端 sanitizer（目前 frontend 控型別 + runner 防呆 passthrough）。

### 菇菇雕像每週消耗（statue_weekly）
- [ ] `bot_config.json` 兩台（5554、adb-fc65396d）`statue_weekly.amount` 1 → 7000。
- [ ] 修 ADB flow `send_keys(clear=True)` 不穩（ADB_KEYBOARD_CLEAR_TEXT null ref）：改不帶 clear 直接輸入或走 `adb shell input text`（`statue_weekly.py:425` 附近）。

### v1 挖礦 follow-up（非 deadlock，屬效率）
- [ ] `mine_until` 遇首個 unconfirmed 即停 → 學 deplete_pickaxes 重讀續挖。
      註：runner 走的 `mining_supervised` 路徑 log 顯示已會在單步內換多個候選格續挖（2026-06-20，refresh_attempts 最多 9 + 換 block_id），此 follow-up 只剩 standalone `mine_until` 那條。低優先。

---

## 研究待續（未拍板）

### 地獄之門 純 WS（小寶 7fe98fc6）
協議已解（`main_chapter` 模組 13；docs/protocol/MAIN_CHAPTER_PROTO_SCHEMA.json）。使用者決策：不自造封包偷跑，先真打一場被動抓包。
- [ ] 下個 :00-:20 窗：小寶 H5(CDP 9230) → `python tools/watch_ws.py --port 9230 --seconds 800 --out logs/hellgate_capture.jsonl` → 真打一場。
- [ ] 解碼 3331 send frame 的 operators#4：非空回放=純 WS 不可行（維持 ADB/H5）；空/極簡仍 code=0=可行。
- [ ] （若可行）`ws_token/hellgate.py` + 測試 + runner/config/ws_phase/daily_pipeline 接線（注意 sleep ~10 分不可 block 其他任務）。

### 萬神試煉(Beta) rogue（module 76）
協議已解（docs/protocol/ROGUE_PROTO_SCHEMA.json，34 cmd 無掃蕩；live 打一場閉環，server 接受 client result）。
- [ ] 純 WS 可行性：result c2s 只有 {result, precent}，但 client 算勝負 + checkCheat()，偽造勝利風險未知，需專門 live 驗（送 result=0 看 server）。優先級低（roguelike 多關連打不適合純 WS）。

### 純視覺農場退役計畫（待 WS farm 三塊 live 驗後執行）
取代者：ws_token ad_reward(15) / farm.harvest_card_cycle / farm.buy(407/408)。
- [ ] 前置：5556/5560/7fe98fc6 接 WS farm 三塊並 live 驗；adb-fc65396d 確認是否接 WS（不接則 ADB/視覺路徑全留）。
- [ ] **接線缺口（退役前必修）**：`game_actions/ws_phase.py:271` 農場 skip 條件只看 `farm.seed_id`，但 WS 農場用 `farm.buy`/`harvest_card_cycle`（無 seed_id）→ 改成 harvest_card_cycle 跑成功才 skip。未修則刪視覺農場會讓農場任務無人做。
- [ ] 可刪清單（A 低風險 / B 保留 ADB 後備 / C 順手清死碼）詳見舊版本（git 歷史）；待刪處已加 `# remove-after-ws-farm-verify(2026-06-19)` 註解。**weekly_card.py 不可整檔刪**（check_if_parttime 仍被 harvest_card 用）。

### #20 主 dashboard 重設計（待使用者過目，刻意未動手）
裝置卡片設定收斂成幾個按鈕/開關。屬改 live 主控面 + config 存檔的結構性重寫，高度依賴外觀驗收。
- [ ] 盤點 dashboard.html 卡片欄位 + routes_config 存檔對應；ws_token 子開關收進摺疊「WS 任務」分頁；方案用 segmented buttons；進階欄位摺疊；分階段（先一張卡片 → 過目 → 套全部）。

### 進階設定 副本掃蕩改版（後續單獨對話）
- [ ] 用詞白話化 + 是否改逐欄表單取代 JSON。本次重整不碰。

### 降運算 Phase 2（需 live 驗證，等拍板）
- [ ] 2.1 Chrome `--disable-gpu`/headless（需驗 Cocos 畫面+OCR）；2.2 `cc.game.setFrameRate` 限 FPS（build 是否支援未驗）；2.4 `cv2.matchTemplate` 前 2× 縮圖（需重調 0.8 門檻）。
- [ ] 殘留重疊：`wake_up_handler.py:322` 舊分流延遲與 F4 啟動錯峰疊加（動時須保 5554 online-check 提前 return）。

---

## 重構 Phase 3（高風險拆分，真相來源 docs/REFACTORING_OPPORTUNITIES.md）

每項獨立一輪審查 + 重啟 `new_main_v2.py`；Phase 間勿混同 commit。
- [ ] 前置 commit：`oracle()` 優化、`get_stage` OCR 合併（工作區已實作+測試，待 commit）。
- [ ] Flask 錯誤封套：抽 `_cdp_action(ip, js)` + `_cdp_err_code(err)`（先 4 個 fire-and-forget CDP route；`@json_endpoint` 套 21 處延後分批）。
- [ ] carpark/cocos JS walker 共用化（熱路徑）：新 `utils/carpark_js.py`；先重構 cocos_navigator 4 份副本證明 helper，再逐點遷 carpark（每點 log 新舊座標 assert 相等）；勿同 pass 動 farm_v2/sea_v2。
- [ ] carpark `park_one_silver` 抽 `_reenter_silver_detail_list()`；`reconcile()` 的 `_build_snapshot_summary` 升 module-level 純函式。
- [ ] `PlaywrightGameDevice._start`(150L) 分解：先抽 `utils/web_profile_paths.py`，再抽 `_launch_persistent_context_with_fallback`（純 code-motion）。
- [ ] V1 神燈死碼移除（gate：V2 prod log 穩定）：刪 `Open_gold_paddle_ocr.py` 4 函式 + `__main__`、退休 lian_shan_example.py。
- [ ] device_wrapper ~25 silent `pass` → warning log、7 個 bare `except:` → typed catch、抽 `poll_stage(d, target, timeout)` helper。
- [ ] Phase 0 殘：live-tree sync-conflict sweep（~3.6k 檔，在 active profile/logs，bot 跑時勿刪；源頭加進 Syncthing `.stignore`）。

---

## Review — 純WS挖礦地形重建（2026-06-20, d2a9c81c）
- [x] 釘死根因：WS 0x0c01 不送未挖格地形型別（201/202 全 count==0 已挖、僅 401 礦坑 count>0、未挖=純 active）。planner 盲填 dirt、永不對未挖石頭下炸彈 = 效率天花板。
- [x] 模型：client 把 12 個相異 7×6 模板垂直 7-列堆疊；每 band = 單一模板（CNN 28/28 + WS 22/22 雙重驗證）。
- [x] 自學：邊挖邊用已挖格 config_id 比中模板、推回未挖地形。phase=1/row 經 14 對齊中唯一無矛盾驗證（5556 帳號）。
- [x] 安全整合：未挖 active 只在重建為 STONE 時 dirt→rock，其餘不動 → 絕不退步；稀疏資料=no-op。
- [x] `ws_token/mine_terrain.py` + `mining_adapter`/`mining_supervised` 接線、per-device cache、68 測試綠、live 實測鎖定 phase 並重建 4 個 rock 格。
- [ ] 待生效：new_main_v2 重啟後上線；隨各帳號挖礦自動累積 band→template（summary log 看 `terrain=`）。
- 限制（使用者已同意自學）：只重建「已揭露足量」的 band，全未挖的遠方 band 需 client RNG seed（未做）。

## Result — 「以礦為路徑」延伸視野：實測否決（2026-06-20）
- 量過 7 列 vs 12 列（masked：下方只給礦位置、地形未知）：score +3%(1288→1328) 但**效率變差**(0.36→0.32 pit/鏟)、鏟耗↑(193→217)。raw 分靠多花鏟換、礦沒多收。
- 機制：下方只知礦位置不知土/岩 → 朝礦衝會撞到未知石頭白花鏟；且礦本來就會隨下挖捲進視野。
- 效能：v1 A* 到 14 列穩(<25ms)，17+ 偶爆 220ms。
- **結論：不做 ore-path。** pure-WS 下「每 band 進視野後即時最佳化」已是效率天花板。
- 改為補完「視野內被遮擋格用模板填」(commit a12c6144) —— 這才是真正提升每-band 最佳化的那一塊。
- 工具留 `tools/eval_ore_lookahead.py`（要更大樣本重驗可用）。

## Plan — 「以礦為路徑」延伸視野規劃（已否決，保留紀錄）
目標：planner 不再只看 7 列視窗，而是朝下方礦規劃下挖路線（效率最大化收尾）。
現況：planner v1 只吃 7 列；WS `map_pits` 給下方 ~17 列礦坑位置但只當 telemetry；未挖 band 地形未知（加權隨機）。
- [進行中] 上界實驗（零 live 風險）：`tools/eval_ore_lookahead.py` 餵 plan_smart 加高地圖（7/14/21 列，god-mode 真實 tape）量分數增益，判斷「拉長視野」值不值得做。
- [ ] 若上界有顯著增益 → 做「真實版」：地圖 = 視窗重建地形 + WS map_pits（下方礦坑）+ 已識別 band 的重建地形；未識別 band 地形以平均成本代入。只執行可達（視窗內）挖步，深層只影響下挖方向/炸彈時機。
- [ ] 在 `mining_adapter.plan` 組「加高 grid」餵 plan_smart；步驟座標映射回 block_id；補測試 + sim 評測對照 v1 baseline。
- [ ] 限制（誠實）：未挖 band 地形不可預測，下方只能「朝已知礦坑方向」最佳化，視野上限 ~17 列。
- 動 live 演算法前先讓使用者過目本 plan。

## 工作慣例提醒
- TDD；subagents 一律 model:"opus"；計畫/進度走本檔。
- 動 live bot（new_main_v2/device_wrapper/排程）先把 plan 寫此檔給使用者過目。
- 改完 runtime 檔必提醒重啟（sys.modules cache）。

## 其他既有待辦（檔案各自追蹤）
- `tasks/ws_token_home_todo.md` — 家園三件（守護靈/加工坊/伴侶），feat/ws-token-home 未 merge
- `tasks/carpark_adb.md` / `tasks/carpark_skip_silver.md` / `tasks/mount_sprint_todo.md` / `tasks/sea_v2_todo.md` / `tasks/mining_ore_ab.md`

## 萬神試煉 重寫（rogue Beta，2026-06-20 CDP 實測驗證）

### 根因
舊 `battle/weekly_trials.py::fight_test` 跑舊版 7 場 UI（開始/確定/結束本局/秘寶閣每輪），但遊戲已改成 roguelike「萬神試煉Beta」(RogueView)。OCR 子字串命中「萬神試煉」→ 入場 OK，但進場後按鈕序列全不符 → 空轉 5 分鐘 + 無條件 `time_recording` → 整週鎖死。`dungeon_scheduler.py` 排程決策原用 root `logging`（已改 per-device `logger`，2026-06-20 補 log）。

### 已實測驗證的 OCR 流程（全用內建 `img_tools.click_str_by_server`，canvas 540x960）
進場（兩種狀態都要吃）：
- 暖啟（有進行中局，如 5554）：主面板「繼續」(270,744) → 關卡視圖
- 冷啟（無局，如 小寶 7fe98fc6）：主面板「開始」(271,844) → 開局獎勵頁「開始」(270,724) → 提示「是否確認開啟新一局」→「確定」(370,553) → 「恭喜獲得」頁「進入遊戲」(380,847)〔可選「刷新」重骰〕 → 提示「是否確認進入本次萬神試煉」→「確定」(370,554) → 獎勵 toast「點擊空白處關閉」(269,606) → 關卡視圖

戰鬥迴圈（打到不能打為止）：
- `開始挑戰`(271,711) → 戰鬥 client 自動跑 ~3-7s（可選點「跳過」）→ 結果「勝利」→「點擊…關閉」→ 自動進下一關 → 重複
- dismiss 文字有變體：5554=「點擊任意位置關閉」、小寶=「點擊空白處關閉」→ 用子字串「點擊」匹配

### 進度
- [x] 重寫 `fight_test`(`battle/weekly_trials.py`)：副本→入場(萬神試煉+277,75)→`_advance_to_stage()`(繼續/開始雙路徑+確認鏈通用輪點)→`_battle_loop()`(開始挑戰→點擊關閉直到找不到開始挑戰或偵測失敗，MAX_STAGES=80，可帶 max_stages 測試限關)→`buy_god_everyweek`。回傳 bool。
- [x] success-gating：`dungeon_scheduler._run_weekly_dungeon` 只在 `fight_test` 回 True 才 `time_recording`(防失敗也鎖一週)。
- [x] 單元測試：`tests/test_dungeon_scheduler.py` 13 passed(含新增 fight 失敗→不記錄)。
- [x] live：真 `fight_test` 用 production OCR 跑通；事件關閉時優雅回 False(中止,不誤記錄)。entry+battle loop 已於週六 5554/小寶 驗證連打多關。
- 異體字非問題：opencc s2t 把「秘」「祕」都正規化→`buy_god_everyweek('秘寶閣')` 命中新 UI「祕寶閣」。

### 退出/結算流程(2026-06-21 實測)
- 右下角紅色箭頭(≈510,920，OCR 常誤判為 'G') → 提示「選擇暫時離開或結束本局」→ `結束本局` → 提示「是否確認結算本局」→ `確定` → 回副本頁。
- 對話框按鈕**吃 mouse.click**，但 dialog 轉場需 ~4-5s 等待(2.5s 太短會誤判點不動)。`暫時離開` 在事件已結束時為 no-op(只 `取消`/`結束本局` 有效)。
- 注意：`RogueView/btnClose`、`RogueEndTipsView` 按鈕的 `emit('click')` 無效(整條祖先鏈無 click listener，cc.Button 走 editor clickEvents)→ 要驅動只能座標點擊+足夠等待，別用 emit。

### 待補實測(事件只到週六 23:59:59，週日關閉；下輪週一下午開)
- [ ] 自然停止訊號 = 點掉結果窗後「開始挑戰」是否再現(勝→再現續打；敗/次數用盡→消失停)。**不辨識勝敗字**(勝/敗窗長得一樣)。待驗：真實失敗後「開始挑戰」確實消失、本局正常結束(帳號太強沒打到，下輪排程跑+看新 log 補)
- [ ] 失敗/結束時是否需在 `_battle_loop` 後補「紅箭頭→結束本局→確定」結算(目前靠自然結束)；待真實失敗路徑驗
- [ ] 結尾 `buy_god_everyweek` 在新 RogueView 後是否走得到祕寶閣(loss 後可能在結算頁非主面板)；待驗
- [ ] H5 驗證後回測 ADB(小寶手機 adb-fc65396d 純 OCR 場景)
- 探測工具：`tools/_rogue_drive.py`(步驟驅動)、`tools/_test_fight_test.py`(端到端跑真 fight_test，限關)，皆 `ROGUE_PORT` 指定埠
- ⚠ runtime 改過 `battle/weekly_trials.py` + `game_actions/dungeon_scheduler.py`，需重啟 new_main_v2 生效(sys.modules cache)

## 天梯每週獎勵 純WS自動選 (2026-06-21)

需求(使用者):打完雙章節天梯(雲纏天梯試煉)後要選每週獎勵;希望未來每週自動照同樣選、走 WS。多帳號(小寶 + 5558)。5558 若沒選滿 25 個 → 用小寶的補滿。

### 協議(live 驗證 小寶 CDP 9226)
- **cmd `0x4001` (16385) double_ladder_select**(module 64):c2s body = repeated `pick{1:難度,2:index}`;s2c echo = 已存清單 + 尾端 `{2:0}` = 成功。
- 小寶實抓:25 個 pick,難度 16~25;重放相同 bytes → echo 結尾 `1000` 成功。**冪等**(重送=重存,不會重複領)。
- 解碼:`難度25:[1,2,4] 24:[1,2] 23:[1,2,3] 22:[1,2,3,4] 21:[1,3] 20:[1,2,3] 19:[1,3] 18:[1] 17:[1,2,3] 16:[1,2]`

### 已完成(不動 bot 行為,安全)
- [x] `ws_token/ladder_reward.py`:encode/decode/merge picks + `apply_selection`(call 0x4001,err 0x0201 不炸 runner)+ 每裝置存檔 `load/save/get_body_hex/record_device`
- [x] `ws_token/data/ladder_reward.json`:小寶 `7fe98fc6` 已記錄(25 picks,source live CDP 9226)
- [x] `tests/test_ladder_reward.py`:5 passed(真實封包解碼 / encode-decode roundtrip / merge 補滿 / 多byte varint)
- 工具:`tools/probe_xiaobao_reward.py`(PROBE_PORT 指定埠;install/drain/decode/replay/state)

### 待辦
- [ ] 抓 5558(CDP,例外):開 5558 瀏覽器到天梯頁 → install 監聽 → 手動選一次 → drain 0x4001 → `merge_picks(5558, 小寶)` 補滿 → apply 套用 → record 5558
- [ ] **runner 接線(需使用者同意,動到正在跑的 bot)**:`ws_token/runner.py` 加 `_run_ladder_reward(client, device)`:gate=該裝置有 record 且 `enabled`(預設 True)+ ws_state 日期閘(每天一次,冪等;daily 閘可覆蓋日/週結算重置)→ `apply_selection`。放 free 任務群(main_tasks 後)。
- [ ] (可選)dashboard 顯示/重抓按鈕
- ⚠ 接線後需重啟 new_main_v2 生效(sys.modules cache)

### 實作完成 (2026-06-21) — 改走 CDP/頁面WS、每週二
依使用者最終指示:頻率=**每週二一次**;5558 走 CDP(無 ws_token);其他可走純ws(許可非強制)。
最小 bot 改動 → **單一 CDP 路徑**接 daily_pipeline 尾段,涵蓋兩台(小寶+5558 都 web_h5)。
- [x] `ws_token/ladder_reward.py`:純邏輯(encode/decode/merge/varint)+ store + **週二閘**(`is_due`,ISO 週 marker 去重)+ `apply_if_due(device, today, page=/client=)`(雙傳輸:page=`WebGameAPI.call_raw` CDP / client=純WS,日後小寶要純ws只需加 runner 5 行)
- [x] `game_actions/ladder_reward_weekly.py`:`run_ladder_reward_if_due(d, ip)`(web_h5 only,拿 `d._page`+today,吞例外)
- [x] `game_actions/daily_pipeline.py`:Task 14.7 尾段呼叫(try/except)
- [x] `tests/test_ladder_reward.py`:10 passed(解碼/roundtrip/merge/週二閘/marker去重/no-transport);全套 + test_daily_pipeline 17 passed
- [x] 兩台已 live 套用本週(小寶 replay echo ok、5558 套小寶模板 echo 25 ok)並記錄 enabled
- ⚠ **需重啟 new_main_v2** 自動排程才生效(sys.modules cache);本週已手動套用,下週二起自動
- 限制:body 綁帳號已達難度(16-25);若帳號升難度或改選擇 → 用 `tools/probe_xiaobao_reward.py`(PROBE_PORT)重抓 `drain`→`record_device` 更新

---

## 重寫 ws_token.farm.run_harvest_card_cycle — WS 4 階段豐收卡 (2026-06-22)

**Context**: 5554 等 WS 豐收卡一直沒效。用 `tools/ws_harvest_step.py`(CDP 接瀏覽器、單步 + sniff/capture)live 反推完整協議 + 對齊正確流程。現有 `run_harvest_card_cycle` 三個結構性問題:漏「清場」、漏「收成 3080」階段、施肥 num=1(該 3)、無 N×5 迴圈。詳見 memory `reference_ws_harvest_fertilize_num3`。

**Live 驗證協議 (5554 2026-06-22, 回應長度都對上親手按鈕)**:
- 種植 = 3078 `{seed_id:103, land_id}` (build_plant_body)
- 施肥 = 3079 `{role_id:0, land_id, fertilizer_id:111, num:3}` (num 原 1=bug;reply new_land#3 state=2 即熟)
- 收成 = 3080 pick `{role_id:0, land_id}` (CMD_PICK;reply 3080 code#1;未熟 code=122)
- 收穫 = 3081 harvest `{land_id}` (build_harvest_body;reply 3081;空地/未熟逾時不回)
- 買卡 = 6914 `{shop_type:11, shop_id:1604, num}` (買 +1 verified)
- 打工 18177/18178 → reply 18184 (非自身 cmd)

**正確流程 (對齊使用者 + farm_v2/operations/harvest_card.py)**:
1. 取消打工 (18178→18184)
2. 清場(打工種的便宜作物不能吃卡的 30 株額度): 施肥(num3 催熟)→ 收成(3080) → 收穫(3081)
3. 買豐收卡 N 張 (N = HARVEST_CARD_BUY_COUNT=3 週上限)
4. 賺取迴圈 × (bought×5 輪, 30株/卡 ÷ 6株/輪): 種植103 → 施肥(num3) → 收成(3080) → 收穫(3081)
5. 恢復打工 (18177→18184)

**Decisions (使用者 2026-06-22)**: 直接跑 cards×5 輪固定,**不偵測 buff 耗盡**(清場後額度全新,基本固定)。land_ids 由開頭 read_farm 讀一次(fresh session 可讀;之後靠 fertilize reply 的 new_land state 判熟,不重讀 3077 因 ~once/session)。

**實作 todo**:
- [ ] `build_pick_body(land_id, role_id=0)` → `{role#1, land#2}`
- [ ] `pick_lands(client, land_ids)` — 送 3080,檢查 reply code#1,per-land 容錯
- [ ] `fertilize_until_mature(client, land_ids, num=3, max_rounds=8)` — 迴圈施肥,解析 3079 reply new_land#3 state==2 即停該塊;code19(已熟)也停;封頂避免肥料暴衝
- [ ] `harvest_lands(client, land_ids)` — 送 3081 per land,短 timeout + 容錯(空地逾時=跳過)
- [ ] worker stop/start expect 加 18184 → `(18184, cmd, 0x0201)`
- [ ] 重寫 `run_harvest_card_cycle`: stop_work → read_farm(land_ids + 有作物者) → 清場 → buy N → rounds=bought×5 迴圈 → start_work;回傳含 cleared/cards_bought/rounds/planted/harvested
- [ ] 更新/加測試 `tests/test_harvest_card_*`(mock client,驗 cmd 序 + 輪數 = bought×5)
- [ ] 不動 ADB 視覺版 farm_v2/operations/harvest_card.py(僅 WS 路徑)

### Review — 完成 2026-06-22
全部 todo 已實作 + 測試:
- ws_token/farm.py: `CMD_WORKER_STATE=18184` + worker stop/start expect 18184;`build_pick_body`;`HARVEST_CARD_WEEKLY_LIMIT=3`/`PLANTS_PER_CARD=30`;新 helper `plant_lands` / `pick_lands` / `harvest_lands`(含逾時重試)/ `fertilize_until_mature`(讀 3079 reply new_land state 判熟);`run_harvest_card_cycle` 重寫為 4 階段(取消打工→清場→買卡→賺取 bought×5 輪→恢復打工)。
- tests/test_ws_token_farm.py: +4 測試(fertilize_until_mature 迴圈到熟 num=3 / pick_lands 3080 role0 / pick code!=0 失敗 / harvest_lands 獎勵加總)→ **45 passed**。
- runner 相容:harvest_card + farm 的 11 個 runner 測試 passed(num_cards/fertilizer_id/inventory_tracker/device_id 簽名向後相容)。
- live 驗證:4 階段協議 + earn 37 增益 end-to-end 在 5554 跑通(tools/ws_harvest_step.py)。
- 既有失敗(非本次):runner 的 main_tasks 4 測試=08:00 時間閘(現 05:59);fake-transport 259 hang。8 點後/環境問題,與本變更無關。
- **待生效**:需重啟 new_main_v2(sys.modules cache);5554 等裝置要在 config 設 `ws_token.farm.harvest_card_cycle.enabled=true` 才會跑(目前 5554=null)。ADB 視覺版 farm_v2 未動。

## 修復:手機登入立刻被 WS 彈出(線上檢查連線打到真人帳號)— 2026-06-25

**問題**:真人在手機登入後立刻被 WS 彈出(異地登入)。
**根因**:線上偵測機制用「真人手機帳號」(roleId 89565100509472)連線:
1. `online_monitor` 預設 `preferred="fc65396d_u999"`(= 手機帳號)持久連線;真人登入踢掉它後,`_loop` 因 `_should_yield(手機)` 看不到 bot_state thread 而判定 idle → 立刻 reclaim 重連 → 又踢真人。死迴圈。
2. `online_check_checkers=['*']` 展開含手機(無 target_pid),一次性 WS 登入也可能用手機帳號。

**帳號別名陷阱**:`fc65396d_u999`/`adb-fc65396d-…_tcp` → roleId 89565100509472(手機);但 `fc65396d` → 89555436834913(其實是 5554)。保護必須以 **roleId** 為準,不能用裝置名字串。

**修復計畫(TDD)**:
- [x] 測試先行:`tests/test_online_monitor.py`(7)+ `tests/test_config_human_played.py`(2)→ 先 RED 後 GREEN
- [x] `bot_config.json`:手機裝置 `adb-fc65396d-…_tcp` 加 `"human_played": true`
- [x] `config_manager.py`:`get_online_check_checkers()` 的 `*` 展開排除 `human_played`;新增 `get_human_played_devices()`
- [x] `ws_token/online_monitor.py`:預設 `preferred="emulator-5554"`;`discover_role_map(protected_role_ids=…)` 過濾;`_connect` 以 roleId 拒絕保護帳號;`resolve_protected_role_ids()` 從 config 解析;`ensure_started`/CLI 預設改 5554
- [x] 新增:切換/重連 5 分鐘冷卻 `switch_cooldown_sec=300`(`_switch_allowed()` 閘住 failover 重連 + yield + reclaim),解「斷線後立刻重連又被彈出」
- [x] 驗證:focused pytest 9/9 綠;真實 config 驗證 phone 不在 checkers、protected={89565100509472}、preferred=emulator-5554

### Review — 完成 2026-06-25
**根因**:線上偵測(online_monitor 持久連線 + online_check_service 一次性登入)會用「真人手機帳號」連 WS → 異地登入踢真人;monitor `_loop` 又因看不到手機 bot_state thread 而判定 idle,斷線後立刻 reclaim 重連 → 死迴圈狂踢。

**修法**(2 層防護 + 冷卻):
1. 以 **roleId** 標記/排除真人帳號(裝置名有別名陷阱:`fc65396d_u999`/`adb-…_tcp`→手機 89565100509472;`fc65396d`→5554)。`human_played:true` → `resolve_protected_role_ids()` → monitor `_connect` 拒登 + `discover_role_map` 過濾;`*` checker 池排除。
2. preferred 改 `emulator-5554`(主路由)。
3. 切換/重連 5 分鐘冷卻,杜絕重連風暴。

**驗證**:`pytest tests/test_online_monitor.py tests/test_config_human_played.py -q` → 9 passed。online-check/config 回歸 64 passed。`test_online_check_immediate_wake` 3 紅 = 既有 stub 缺 `has_pending_web_close_request`(與本修復無關)。
**待生效/手動驗證**:重啟 `new_main_v2`(sys.modules cache);手動開手機帳號 H5,確認不再被彈出(5554 當主路由偵測)。
**未提交**:`bot_config.json`/`tasks/todo.md` 內含先前 session 的 WIP,為免混入無關變更,本次未自動 commit,改動已就緒待使用者決定。

### 追加:偵測器自動切換 + 儀表板顯示 — 2026-06-25
- 儀表板頂列新增「上線偵測」徽章(`/api/status` `online_monitor` → `routes_status._online_monitor_status` + `dashboard.html`),顯示目前負責偵測的裝置/新鮮度。
- 使用者回報「都在跑腳本卻沒自動切換、卡在閃電(=emulator-5554)」。根因:舊 `_loop` 啟動就硬連 preferred(不管它在跑腳本→同帳號互踢),且上一版把 5 分鐘冷卻也套到「讓位」→ 卡死。
- 重寫偵測器選擇(`_select_detector`/`_is_safe_detector`,移除 `_pick_idle_device`/`_should_yield`/`_try_connect`):**只連休眠中的裝置**當偵測器(在跑腳本的一律不選,避免同帳號互踢),preferred 在睡優先用、否則跳別台在睡的、全忙則暫不連線(走一次性備援)。讓位(忙→睡)立即切換;只有「連線失敗/被踢後重連」套 5 分鐘冷卻(防風暴)。
- 測試:`test_online_monitor.py` 加 `_select_detector` 3 案(preferred 在睡優先 / preferred 忙則讓位 / 全忙回 None)→ 10 passed。
- 待生效:重啟 `new_main_v2`。

### 追加 2:切換軌跡 + 刷新倒數 + 每卡在線標 — 2026-06-25
- 切換軌跡:`_set_active()` 記錄偵測器轉移(log `detector switch X->Y`)+ `last_switch`/`get_last_switch()`;徽章 tooltip 顯示「上次切換: X → Y」。測試 `test_last_switch_records_transition`(11 passed)。
- 刷新倒數(使用者選「下次刷新倒數」):`get_poll_sec()` + `/api/status` 回 `poll_sec`/`refresh_in_sec`;徽章改「上線偵測: 閃電（下次刷新 倒數 Ns）」,前端 1s ticker(`renderOnlineMonitor`)以 server 相對 `refresh_in_sec` 錨定本機時鐘倒數,避免時鐘偏差。
- 每卡「當前在線」(使用者選「保留 ONLINE 另加小標」):`/api/status` 每裝置加 `account_online`(由 `_account_presence()` 的 snapshot {role_id:online} + `_device_role_id` 解析,以 roleId 比對);卡片右上角保留 ONLINE,另加 `.acct-presence` 小標(在線=綠/離線=灰,unknown 不顯示)。偵測器本身不在自己好友列表→該卡無標(正常)。
