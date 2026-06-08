# WS Token Task Batch — Design Spec (2026-06-09)

> 一句話：在已驗證的 `ws_token/` 地基(`WSGameClient` + `codec`)上,照 `redpack.py`
> 的同一套模式,做 5 個新的純 WS 任務 + 1 層 bot 整合。不開 App、不靠螢幕(無 OCR/CNN)。
>
> 前置閱讀:`docs/WS_TOKEN_BACKEND_PLAN.md`、`tasks/ws_token_backend_todo.md`、
> `docs/protocol/WS_TASKS_RECON_2026-06-08.md`、memory `project_ws_token_backend`。

## 0. 範圍與順序

使用者指定的核心三項 + 加碼三項,全部納入:

| # | 任務 | 模組 | 協議現況 |
|---|------|------|----------|
| 1 | 家園管家採購掃蕩 | `ws_token/steward.py` (worker_common 0x49) | cmd 齊;**欄位號需 1 次 CDP 匯出** |
| 2 | 家族大廳每日 | `ws_token/guild.py` (guild 0x1D) | 欄位全齊(GUILD+TYPE schema) |
| 3 | 挖礦 over WS | `ws_token/mining.py` (home.home_mine_*) | cmd 已知;欄位號**待查/匯出** |
| 4 | 家族魔法劇場自動領 | `ws_token/magic_theater.py` (家族?) | **需 recon** |
| 5 | 主畫面自動領任務 | `ws_token/main_tasks.py` (task?) | **需 recon** |
| D | 接進 bot | `device_wrapper.py` / `new_main_v2.py` | plumbing 建一次,各任務插入 |

**順序**:Phase 0 recon → Phase 1 build(1、2 先,3/4/5 隨後)→ Phase 2 live 驗證 →
Phase 3 整合。整合 plumbing 只建一次;steward/guild 先接進去跑起來,其餘任務之後插同一層(增量交付)。

## 1. 統一架構(每個任務都長這樣)

每個 `ws_token/<task>.py` 坐在 `WSGameClient` 上,複用 `codec`,結構與 `redpack.py` 對齊:

- **parse s2c**:`codec.walk_dict(body)` → `@dataclass(frozen=True)`
- **build c2s body**:`codec.pb_uint/pb_str/pb_msg`(repeated 欄位 = 多次同 fid)
- **decision logic**:純函數(無 I/O,好測)
- **orchestrator**:`client.call(cmd, body)` / `client.call_for(cmd, body, expect_cmds=...)`(多 reply cmd 用後者,例如成功 cmd 或 0x0201 error)
- **測試**:`tests/test_ws_token_<task>.py`,用 `tests/fakes/ws_fakes.py` 的 `FakeTransport` + `login_responder`,離線、確定性
- **smoke**:`ws_token/<task>_smoke.py`,live runner,**dry_run 預設**,危險動作要顯式旗標(`--sweep`/`--donate`/...)

共用工具(已存在,別重造):`codec`(framing+XOR+protobuf)、`WSGameClient`(login+心跳+收發+重連+`set_push_handler`)、`creds`(load/refresh)、`transport`(可注入 fake)。

## 2. 地基現況(已 DONE,不動)

- `WSGameClient`(`ws_token/client.py`):active byte → role_login(cmd257) → 背景心跳(cmd260,5s)→ `call`/`call_for` 依 cmd id 對應回應 → reconnect/close。已 live 驗(小寶,code=0,心跳 130s 不掉)。
- `codec.py`:與 LIVE PoC byte-parity(測試鎖)。
- 既有任務範本:`redpack.py`(列表+領取,已 live 領到)、`lamp.py`(神燈 combo→套裝,已 live)。

## 3. Live 驗證策略(裝置與踢人政策)

- **借測機**:5554(閃電,@google,ADB)、5556(菜雞,ADB)。用 `tools/adb_token_login.py --device <serial>` 撈 ticket(冷啟 App ~30s,**會踢該機自己的 session,但不碰小寶**)。
- **小寶(7fe98fc6,web_h5,CDP 9226)**:read-only schema/config 匯出走 CDP(`tools/_auth_capture_probe.py 9226 --await`),**不踢**。使用者已同意「需要時可踢小寶」→ steward 的 live 驗證若只有小寶有在期服務,直接連小寶驗。
- **順手**:任一次對小寶/借測機的 WS 登入,讀一次道具表回報真實神燈(gtid 1001)數量(澄清交接檔「燈=0」是否屬實)。

## 4. 任務細節

### 4.1 家園管家採購掃蕩 — `ws_token/steward.py`(worker_common 0x49 / module 73)

**cmd 表**(id 已從 bundle `MSG_TO_ID_MAP` line 7835 確認;**欄位號需 Phase 0 CDP 匯出**):

| cmd | id (hex/dec) | body | 用途 |
|-----|------|------|------|
| `housekeeper_info` | 0x4904 / 18692 | `{}` | 讀服務在期狀態 → `{buy_housekeeper_info:[{k:service_id, v:expiry_ts}]}` |
| `housekeeper_buy_service` | 0x4905 / 18693 | `{day_num, id}` | 買/續 30 天服務(花家園幣);`day_num`=1-based 價格檔位,`id`=configHousekeeper row |
| `housekeeper_shopping` | 0x4908 / 18696 | `{}`(空) | **購物管家採購掃蕩**(server 照已存清單一次掃)→ reward |
| `housekeeper_shop_info` | 0x4909 / 18697 | `{shop_type}` | 讀某商店分頁採購設定 |
| `housekeeper_set_shop_item` | 0x490A / 18698 | `{shop_type, item_list:[{k,v}]}` | 設定要採購的商品(k=configMall id, v=數量) |
| `housekeeper_dungeon_setting_info` | 0x490B / 18699 | `{}` | 讀副本管家設定 |
| `housekeeper_dungeon_sweep` | 0x490D / 18701 | `{sweep_list:[{id, level, times, use_ad}]}` | **副本管家採購掃蕩** → reward |

**行為**:
1. `info` 讀 `buy_housekeeper_info`,以 tabId 判服務是否在期(`expiry_ts > serv_time`)。tabId:購物管家=1、副本管家=2。
2. **過期 → 自動續期**(使用者決策):送 `buy_service{day_num=<30天檔位>, id=<service row>}`。30 天檔位與 service row id 來自 `configHousekeeper`(Phase 0 匯出)。續完重讀 `info` 確認在期。
3. 在期 →
   - 購物管家:送 `shopping{}`(空 body)。前提是已設過採購清單;若 `shop_info` 顯示清單為空,先 `set_shop_item`(採購清單來源:使用者指定或 config 預設,Phase 0 釐清)。
   - 副本管家:讀 `dungeon_setting_info` → 組 `sweep_list`(每個開啟的副本一筆:`id`=configHousekeeper_chapter row、`level`=可掃層、`times`=min(門票數, 上限)、`use_ad`=0)→ 送 `dungeon_sweep`。
4. 解析 reward s2c,log 收穫。

**門票/前置**:功能閘 `FUNC_HOUSERKEEPER=112`;副本掃蕩 `times` 受門票/鑰匙夾(league_solo gtid 7002、dark-trial 240011 等);`use_ad=1` 需 NO_ADS 特權,預設 0。

**Phase 0 依賴**:worker_common 欄位號(`lookup('worker_common').toJSON()`)、`configHousekeeper`/`configHousekeeper_shopping`/`configHousekeeper_chapter` 三張 config 表(runtime `.datas`,同飛寵 configFly 撈法)。

**live 風險**:greenfield 協議,`shopping_c2s` 是否真完全空 body、`sweep_list` 線上欄位名是否等於 JS key(`{id,level,times,use_ad}`)、失敗碼是否走 0x0201 — 全部 live 確認。**dry_run 預設,先驗讀路徑再驗掃蕩**。

### 4.2 家族大廳每日 — `ws_token/guild.py`(guild 0x1D)

**cmd + 欄位**(GUILD_PROTO_SCHEMA.json + TYPE_PROTO_SCHEMA.json,**欄位號權威**):

| 動作 | cmd | c2s | s2c |
|------|-----|-----|-----|
| 捐獻 | `guild_donate` 7441 | `{}` | `{donate_sum#1, donate_week#2, donate_count#3}` |
| 寶箱讀 | `guild_treasure_info` 7459 | `{}` | `{is_new#1, round#2, cfg_id#3, my_open#4, countdown#5, box_list#6:p_guild_treasure_box[]}` |
| 寶箱開 | `guild_treasure_open` 7460 | `{round#1, n#2}` | `{round#1, n#2, code#3}` |
| 求助讀 | `guild_help_info` 7452 | `{type#1}` | `{type#1, daily_count#2, help_list#3:p_guild_help[]}` |
| 求助 | `guild_help` 7454 | `{help_id#1}` | `{new_daily_count#1}` |

`p_guild_treasure_box{n#1, pos#2, open_num#3, open_limit#4, role_list#5}`;`p_guild_help{id#1, role_id#2, name#3, head#4, type#5, ...}`。錯誤走 `error.error_info_s2c` 0x0201。

**行為**:
1. **捐獻**:送 `donate{}`(空,server 選等級)。讀 `donate_count`;以 guard 迴圈嘗試到 0x0201 或 count 不再變(每日上限)。
2. **寶箱**:`treasure_info` 取 `round` + `box_list`;對 `open_num < open_limit` 的箱送 `treasure_open{round, n}`,以 `my_open` 為個人上限,逐箱開到上限/無箱。
3. **求助**:`help_info{type}` 取 `help_list`;對每筆送 `help{help_id=p_guild_help.id}`,以 `daily_count` 為上限。

**live 確認點**:`donate` 每日上限與是否花幣;`help_info` 的查詢 `type` 值(預設先試 `help_status` 0x1D1C 回的 type,或 0);寶箱 `n`/`round`/`my_open` 語意。**dry_run 預設**。

### 4.3 挖礦 over WS — `ws_token/mining.py`(home.home_mine_*)

`home_mine_info`(盤面,專案已抓 0x0c01)→ 重用 `miner/v4/planner.py`(bounded DFS)→ `home_mine_hole_update`(挖)。不開 App/不 CNN/不 OCR。

**行為**:長連線(需心跳)。`info` 解盤面 → 轉成 v4 planner 的輸入格式 → 取 plan → 逐步送 `hole_update` → 重讀盤面 → 迴圈到能量/回合耗盡 → `home_mine_get_reward`。道具(炸彈/鑽頭)走 `home_mine_use_goods`。

**Phase 0 依賴**:`home.home_mine_info`/`hole_update`/`use_goods`/`get_reward` 的 cmd id 與欄位號(HOME family 未在已匯出清單;查 `utils/ws_validator` 既有 capture + 必要時 CDP 匯出)。盤面格式對齊既有 0x0c01 capture。

**注意**:v4 planner 的輸入是 CNN 盤面格式;需一層 adapter 把 WS 盤面轉成 planner 期望的結構(複用,不改 planner)。

### 4.4 家族魔法劇場自動領 — `ws_token/magic_theater.py`(待 recon)

家族頁面的「魔法劇場」獎勵自動領。**Phase 0 recon**:bundle 搜 `魔法劇場/MagicTheater/Theater/劇場/法劇`,找 View → `netManager.send` → cmd 名 → MSG_TO_ID_MAP 查 id → 判 family/是否已匯出。recon 完才定 module 與 build 細節。可能屬 guild 或獨立 family。

### 4.5 主畫面自動領任務 — `ws_token/main_tasks.py`(待 recon,使用者標「研究」)

主畫面每日/成就任務自動領取。**Phase 0 recon**:bundle 搜 `任務/task/daily/成就/achievement/領取/get_reward/活躍`,找主畫面任務面板 View → cmd。recon 完評估能否純 WS 領(有些「任務」獎勵需先完成條件,只能領「已達成未領」的)→ 若可行才 build,否則回報限制。

## 5. Phase 0 — Recon / 匯出(全 read-only,平行 subagent)

產出餵給各任務的 build spec:
1. **steward**:CDP 匯出 `worker_common` 欄位號 + `configHousekeeper*` 三表 → 寫 `docs/protocol/WORKER_COMMON_PROTO_SCHEMA.json` + `ws_token/data/housekeeper_config.json`(同 redpack 的 `red_packet_types.json` 模式)。
2. **magic_theater**:bundle recon → cmd 名/id/欄位名 + flow。
3. **main_tasks**:bundle recon → cmd 名/id/欄位名 + 可行性評估。
4. **mining**:確認 home_mine 系列 cmd id/欄位號(查既有 capture,缺則 CDP 匯出)。

CDP 匯出需小寶瀏覽器在跑且 9226 可連;若沒開,請使用者開「啟動網頁」後再跑。

## 6. Phase 1 — Build(每任務一個 subagent,TDD)

每任務:先寫 `tests/test_ws_token_<task>.py`(RED)→ 實作 `ws_token/<task>.py`(GREEN)→ 寫 `<task>_smoke.py`。
順序:steward、guild 先(核心);mining、magic_theater、main_tasks 隨後(recon 完成後)。
每個 subagent 拿到:本 spec 對應節 + Phase 0 產出 + `redpack.py` 範本 + `ws_fakes.py` 用法。離線完成(`py_compile` + 該檔測試綠)才算 Phase 1 done。

## 7. Phase 2 — Live 驗證

- 對 5554/5556 用 `adb_token_login.py` 撈 ticket;steward 視在期服務所在帳號(可能小寶)。
- 每任務先驗**讀路徑**(info/list 解析正確),再驗**動作**(掃蕩/捐獻/開箱/挖),smoke 從 dry_run → 顯式旗標。
- 記錄真實 reward/回應,回填各任務「live 確認點」。順手讀神燈道具數回報。

## 8. Phase 3 — 整合(D)

- `device_wrapper.py`:支援 `backend:"ws_token"` 裝置;載入 creds(`auth_state/_auth_capture_<dev>.json`),建 `WSGameClient`。
- **ticket 刷新策略**:ADB 裝置 → `ws_token.creds.refresh_creds()` 包 `adb_token_login.py`;web_h5(無 ADB)→ CDP probe。ticket 可重用數小時,過期才刷。
- **排程錯開**:同帳號 WS 登入會踢 dashboard/App;ws_token 裝置與其 web_h5/ADB 雙胞胎錯開時段(複用既有 wake 排程概念)。
- 任務註冊進主迴圈:plumbing 建一次,steward/guild 先接;mining/magic_theater/main_tasks 後插同層。

## 9. 測試策略

- **單元/整合**:全離線,`FakeTransport` + scripted responder;parse/build/decision 各自 AAA;一個 assertion 為主。
- **契約**:c2s body 的 byte 結構鎖測(欄位順序/wire type),對齊 Phase 0 匯出的欄位號。
- **不跑真實 device 測試**:測試只碰 `ws_token` 純函數與 fake transport(專案規範:別讓 pytest import 真實 device/Playwright)。
- 跑法:`python -m pytest tests/test_ws_token_<task>.py -q`;改檔配 `py_compile`。

## 10. Out of scope（本批不做）

- 競技場跳戰(arena,recon 過但本批未選)。
- 烈炎山洞每日領箱 + `utils/family_lieyan.py` bug 修(recon 過,本批未選;留 TODO)。
- 停跨界車(car_park,本批未選)。
- 管家「設定要採購什麼商品」的 UI/策略最佳化(只做:若清單空則用 config 預設或使用者指定;不做智慧選品)。
- 收車/領倉庫/自動收 toggle(明確範圍外)。

## 11. 風險與未決(Phase 0/2 收斂)

- worker_common / home_mine / 魔法劇場 / 主畫面任務 的**欄位號**未釘 → Phase 0 匯出/ recon 解決;在此之前 build spec 標「待釘號」。
- steward 採購清單來源(config 預設 vs 使用者指定)→ Phase 0 看 config 後決定。
- 主畫面任務能否純 WS 領 → recon 後評估,可能只能領「已達成未領」。
- 各 greenfield 任務的失敗碼(0x0201 vs 任務內 code)→ live 確認。

## 12. Recon + Live 驗證更正 (2026-06-09 當日)

**Recon 釘死(CDP read-only 匯出,欄位號權威)**:`WORKER_COMMON_PROTO_SCHEMA.json`(家園管家)、`TASK/COLLECTION/HOME/ACT2_PROTO_SCHEMA.json`。

**§4.3 挖礦更正**:挖一格的 cmd 是 `home_mine_use_goods` **0x0C03** `{goods_id#1, block_id#2}`,**不是** `home_mine_hole_update`(0x0C21 = `is_notice` 通知開關)。盤面 `home_mine_info_s2c {max_num#1, next_time#2, area#3, baseline#4, actives#5, area_info#6:p_key_value[], blocks#7:p_mine_block[], holes#8:p_mine_hole[]}`。既有解碼器 `utils/web_game_api.parse_mine_board`。道具現量靠 0x0402 push(非 max_num)。

**§4.4 魔法劇場更正**:不是 act2 riceParty。魔法劇場(幻劇寶箱)與烈焰山洞(熔岩寶箱)**共用** `dungeon.dungeon_league_solo_get_reward` **0x0E0F** `{type#1}`:type **1/2 = 熔岩(烈焰山洞,每日)**、**3/4 = 幻劇(魔法劇場,每週)**。協議已在 `DUNGEON_PROTO_SCHEMA.json`。→ **烈炎山洞(原 §10 out-of-scope)自動併入同一個 `league_solo` 任務**。box `p_league_solo_box{type,count,got_count,rare_offer_name}`,可領=count>got_count,領滿回 error 159。

**Phase 2 Live 驗證(5554 @google,真實 WS 連線):**
- **steward**:login code=0✅;購物管家(id1)+副本管家(id2)**都在期**;購物採購掃蕩✅(掃 9 店,買 item 1024×1 / 6021×10);副本掃蕩✅(送 `2:1:1` → chapter 2「突襲神燈小偷」code=0,獎勵 **{1001: 52} = 52 顆神燈**)→ **`sweep_list[].id` = chapter id(1-12)確認**;`level=1/times=1` server 照收。`buy_service.day_num` 續期語義**未驗**(兩服務在期、不需 renew)。
- **guild**:login✅;help_info 解出真實求助(`num=20`)✅;help_status✅;**捐獻✅(捐 5 次到上限,error 159)**;treasure 休眠(該家族無尋寶輪→server 不回→已改容錯 skip)。
- **修正**:`guild_smoke.py` `--help`→`--answer-help`(argparse 與內建衝突)、`_read_only` 容錯 `WSTimeoutError`(休眠功能 skip 不 crash)。
- 帳號對照:小寶 `7fe98fc6`=`27399634 kenken@gmail`(獨立);5554 閃電=`27353216 @google`。**小寶神燈=710,000**(交接檔「燈=0」為誤)。
