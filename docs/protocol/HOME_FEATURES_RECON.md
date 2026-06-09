# 家園功能 WS recon (2026-06-09, CDP 小寶 9226)

Branch `feat/ws-token-home` (off `feat/ws-token-integration`). 要建：守護靈(spirit)、加工坊(workshop)。
比格先生(贈禮)未定位(下方)。打工 team_cfg_id 已解(下方)。

## CDP 解碼法(可復用)
`Get-Content x.js | python tools/_auth_capture_probe.py 9226`(小寶 web_h5,Runtime.evaluate;
UTF-8 要 `$env:PYTHONIOENCODING='utf-8'` + `[Console]::OutputEncoding=[Text.Encoding]::UTF8`)。
- error 碼:`window.configErrorInfo.getDataByKey(code)._data[1]` = langId → `window.GetStrFromConfig(langId)`。
  **已解:90=冷卻時間未到 / 159=次數不足 / 173=活動已結束**。失敗一律走 0x0201 帶 error_code。
- cmd 號:H5 WS 斷線時可用 fake-cnet 法離線抓(`tools/_cdp_cmds.js`):
  暫換 `netManager._cnet = {state:2, sendMessage:(cmd,body)=>capture}` + 給 `_protoClass[name]` 塞 dummy
  encode,然後 `netManager.send('<family>.<msg>_c2s', {})` → capture 到的就是 cmd(proto_id)。
  驗證:`home.home_mine_info_c2s` = 3073 ✓。cmd = module*256 + N,c2s/s2c 共用 id。

## 守護靈 spirit (module 77) — schema: SPIRIT_PROTO_SCHEMA.json
使用者要:**每天 2 次免費抽取** + **每日買 10 個招喚貨幣**。
- `spirit_draw_info` **19743** c2s{} → s2c{draw_list#1: p_spirit_draw[]}。
  `p_spirit_draw {draw_id#1, free_times#2, must_info#3:p_key_value[]}` → **free_times = 今日剩餘免費抽**。
- `spirit_draw` **19744** c2s{draw_id#1, count#2} → s2c{new_draw#1:p_spirit_draw, reward#2:p_reward[]}。
  免費抽 = 對每個 pool 抽 min(free_times, N) 次(count)。
- 招喚貨幣 = `shop_buy` **6914** c2s{shop_type#1, shop_id#2, num#3=10}。**shop_id 是 configMall 的招喚貨幣項,live-confirm**
  (CDP `window.configMall` 找招喚貨幣;或當參數)。買貨幣會花錢(鑽石?)→ 預設別買,要 flag/參數。
- 其他 spirit cmd(本批用不到但記著):info 19713、choose_tab 19719、active 19723、battle 19724、
  level 19725、reset 19726、reshape 19727、craft 19729、lock 19730。
- 建議模組 `ws_token/spirit.py`:read_draw_info / draw_free(只用 free_times,免費)/ buy_summon_currency
  (gated on shop_id + 明確 flag,因為花錢)。免費抽是 free reward;買貨幣是 spend。

## 加工坊 workshop (module 72 worker_processing_workshop) — schema: WORKER_PROCESSING_WORKSHOP_PROTO_SCHEMA.json
- `worker_pw_info` **18434** c2s{} → s2c{auto_use_food_list#1:uint32[], food_info#2:p_worker[]}。
  `p_worker {team_cfg_id#1, worker_base#2, worker_status#3, auto_feed#4, unlock_slot_num#5, ...,
   pw_worker_info#7:p_worker_pw_food_info, ...}` → 加工坊狀態在 pw_worker_info#7。
- `worker_pw_dining_hall` **18441** c2s{} → s2c{food_list#1:p_key_value[]}(可選食物 k=foodId v=數量?)。
- `worker_pw_choose_food` **18435** c2s{food_list#1:p_key_value, workshop_id#2} → 指派食物開工。
- `worker_pw_cancel_work` **18438** c2s{workshop_id#1}。
- 其他:crops_auto_transfer 18433、auto_add_materials 18439、crops_transfer 18440(收成出貨?)、
  unlock_workshop 18443、add_materials 18444、food_auto_use 18445。
- 加工坊 = 把作物加工成食物;有 加工小隊(team)。日常動作大概率:讀 info → (收完成品/crops_transfer)
  → 重新 choose_food 開工。**完整循環要 live-probe**(smoke dry-run 讀 info/dining_hall 先看真資料)。
- 建議模組 `ws_token/workshop.py`:read_info / read_dining_hall / collect(crops_transfer)/ start(choose_food)。
  食物/小隊 id 為 live-confirm 參數。

## 打工 team_cfg_id(順手解了)
打工小隊 = 飛寵小隊 + 隊長。**team_cfg_id 就是 p_worker.team_cfg_id**,可從 worker info 讀回現有值
(worker_common module 73 的 info,或上面 p_worker)。建 farm `start_work` 時帶這個值。不必猜。

## 比格先生(贈禮)— 未定位 ❌
langId:6903「比格先生」/ 4254「貝肯熊贈禮」/ 6781「贈禮10次」/ 6782「贈禮20次」(貝肯熊=Backkom 授權熊 IP)。
82 個 proto family 裡**沒有**叫 gift/biger/bacon 的;gift 機制只在 act(christmas/tanabata)、friend
(送好友禮)、marry(送花好感)。研判 比格先生贈禮 是**季節/限時活動**(像休眠的跨界停車),目前 client 沒載入。
**待:活動開時再 recon(找 act/act2 對應 family + cmd),或抓真實 WS 封包**。

## error 碼速查(configErrorInfo,權威)
90=冷卻時間未到 / 159=次數不足 / 173=活動已結束。可失敗 mutate 一律 `call_for(cmd, 0x0201)`。
