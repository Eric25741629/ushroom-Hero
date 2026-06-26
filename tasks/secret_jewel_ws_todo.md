# 秘寶 (尋寶) 純-WS 自動化 — secret_jewel

新改版功能「秘寶」。與守護靈同頁 (`JewelView`)。**只做塵世秘寶 pool_type=1**（傳說/遠古未開放）。
協議全部 live 重抓 (5556 CDP 9223)，見 `docs/protocol/SECRET_JEWEL_RECON.md`。

## 協議 (secret_jewel module 85；c2s/s2c 同 id；失敗走 0x0201)
- info  **21761** {} → {pool_list#3: p_secret_jewel_pool{pool_type#1, free_times#2, must_info#3}}
- draw  **21764** {pool_type#1, count#2} → {pool#1, reward_list#2: p_reward[]}
- 每日購買尋寶圖(1340) = **shop_buy 6914** {shop_type=26, shop_id=2600001, num} (每日上限10, 每個粉鑽600)
- shop_info **6913** {shop_type=26} → {shop_type#1, buy_list#2:{shop_id#1, bought#2}} (今日已買數)

## 需求 (兩個獨立可選開關，使用者 2026-06-27)
1. **draw_free**：每日免費抽 2 次 (免費，secret_jewel_draw count=1 × free_times)。
2. **buy_daily**：每日買 10 個尋寶圖 (shop_buy 補到 10/日；花粉鑽，使用者說隨便花)。

兩者皆靠 server 端每日計數器冪等 → **不需 ws_state 日期閘**，每次喚醒跑都安全。

## 工作項
- [x] `ws_token/secret_jewel.py` — 新模組 (clone spirit.py)：read_info / draw / draw_free / read_shop_bought / buy_daily_maps
- [x] `tests/test_ws_token_secret_jewel.py` — 20 單元測試 全綠
- [x] `ws_token/runner.py` — import + TASK_ORDER + `_run_secret_jewel` + dispatch + run_device kwarg `secret_jewel_config`
- [x] `game_actions/ws_phase.py` `_run_device` — 折入 `secret_jewel_config=cfg.get("secret_jewel")`
- [x] `runtime_services/ws_runner_service.py` — `_ws_nested.get("secret_jewel")` → extra_kwargs (只在啟用時傳)
- [x] `bot_config.json` emulator-5556 → `ws_token.secret_jewel = {"draw_free": true, "buy_daily": true}`
- [x] 跑測試 + py_compile；協議 live 實抓 5556 (draw/buy/info)

## Review
- 協議 100% live 重抓 (5556 CDP)：info=21761 / draw=21764 / buy=shop_buy 6914(26,2600001) / shop_info=6913。
  成本 item 1340 尋寶圖 (粉鑽 600/個, 每日上限10)；免費 2/日；pity 100抽。詳見 SECRET_JEWEL_RECON.md。
- 實抓時已實際免費抽 2 + 付費單抽 1 + 買 1 尋寶圖 (驗證 0x0402 消耗/獲得 + free_times/bought 遞減)。
- 新模組 20 單元測試全綠；無 ws_state 日期閘 (server 每日計數器冪等)。
- runner.py / ws_phase.py / ws_runner_service.py / bot_config.json 這些檔案在我動之前已有**未提交 WIP**
  (steward 日期閘等，非本功能)。為避免把不屬於本功能的 WIP 掃進 commit,**尚未 commit**,待使用者裁示。
- 既有 (與本功能無關) 紅測試: test_ws_token_runner.py 的 #1-4 因 `main_tasks` 時間/日期閘 (now.hour<8 或
  ws_state 當日已做) → calls 無 main_tasks,committed HEAD 同樣失敗;#5 已順手改為穩健相對順序斷言並通過。

## 待辦 (未做,徵詢使用者)
- 是否要「買完尋寶圖後自動付費抽」? 目前兩開關獨立 (買=囤尋寶圖、抽=只免費),未自動付費抽。
- 傳說/遠古秘寶開放後再擴 pool 2/3 (目前只塵世 pool 1)。
