# WS Token Backend — 需求與交接 (2026-06-08)

> **一句話目標**：做一個新後端 `ws_token`，用「從手機 ADB 撈到的登入憑證」直連遊戲 WebSocket 送指令跑任務，
> **完全不開 App、不靠螢幕自動化(無截圖/OCR/CNN-on-pixels)**。第一批任務:**領紅包 / 停車 / 挖礦**。
>
> 這份是給「新視窗(乾淨 context)」的單一交接來源。動工前先讀這份 + 它引用的協議文件。

---

## 0. 決策脈絡(為什麼走這條,別再回頭)

- **web_h5 cookie 固化(用 token 生 cookies)= 不可行**。H5 自動登入固化的是**上游 IdP(Google/Apple)瀏覽器 session**,
  不是遊戲 token;working `auth_state/*.json` 裡只有 Google cookies,**零遊戲 token**。FN H5 SDK 整支只有 1 個 `setItem`,token 只在記憶體。
- **手動 Apple 登入 = 使用者無法**(沒有 Apple 帳號存取權)。所以 @apple(user999)唯一的路就是 **WS**。
- @google(user0, suid 27353216)**早就是 web_h5 了 = `emulator-5554`**(同帳號),不需要動。
- 因此:**WS 直連**是讓 @apple 這類帳號自動化、且讓任意帳號「螢幕解耦」的正解。

---

## 1. 已驗證的地基(可直接用,別重做)

### 1.1 登入鏈 + 封包格式 — 全解,LIVE 驗證過
- 規格:`docs/protocol/AUTH_HANDSHAKE_SPEC.md`
  - §2 framing(big-endian `[int32 len][int16 sendID][int16 cmd][XOR(body)]`)
  - §3 XOR 加密(寫死的 `KEY_256`,keystream 起點 `cmd % 256`)
  - §5 登入鏈(server_list → login_auth → WS),salt `P=fc0faf1cb1478a85d5ab7089ff3233d0`
  - §8 連線生命週期(active byte `0x00` → role_login → 心跳 → 指令)
  - §9 **root ADB / logcat 撈憑證(本專案這次新增)**
- PoC:`tools/_login_poc.py` — 純 Python(無瀏覽器)送 `role_login_c2s`(cmd 257)→ 收 `role_login_s2c code=0`。
  也驗過送 `fly_pet_info_c2s`(16898 空 body)收 337 隻飛寵。**證明 connect+login+send/recv 整條原生可行。**
  - 可重用的 building blocks(WSGameClient 直接搬):`_xor / _vint / _f_str / _f_var / _f_msg / gen_packet / drain_packets / _walk`
  - **`time` 欄位 server 不驗**;ticket 可重用 **≥6 小時**(只需偶爾刷新)。

### 1.2 憑證撈取 — `tools/adb_token_login.py`(這次新增,LIVE 驗證過)
- 原理:原生 App 冷啟動把整條登入鏈**明文吐到 logcat**(`D Cocos … D/ JS:`)。撈三行解析成 WS ticket。
- 用法(repo root,`tools` 非 package 要直接給檔案):
  ```
  python tools/adb_token_login.py --device <serial>                 # 一般 user0
  python tools/adb_token_login.py --device <serial> --user 999      # 小米雙開 XSpace
  python tools/adb_token_login.py --device <serial> --verify        # 撈完實測 WS 登入(會踢該帳號 session)
  ```
- 產出:`auth_state/_auth_capture_<device>.json` = `{"creds":{uid,uname,plat,loginGameId,loginSceneId,roleId,
  isWhiteIp,loginTime,pKey,loginTicket,ip,device_id,game_server,gateway,_ws_url,_user,...}}`,
  **與 `_login_poc.py` 同格式 = drop-in**。
- **不需要 root**(只讀 logcat,不碰 App 私有檔):已在真機(小米 MIUI 非 root)實測 `code=0 SUCCESS`。
  前提:adb 連得到 + App 是會印 log 的 build(目前版會)。
- 已撈到的帳號(creds 檔在 `auth_state/`,gitignored):
  | 帳號 | suid | role_id | 來源 |
  |------|------|------|------|
  | @google | 27353216 | 89555436834913 | emulator-5554 / 手機 user0(同帳號) |
  | @apple | 27424846 | 89565100509472 | 手機 fc65396d **user999(XSpace 雙開)** ← WS 的主要目標 |
- 細節:`docs/protocol/AUTH_HANDSHAKE_SPEC.md` §9、memory `reference_adb_sdk_token_extraction`。

### 1.3 心跳(地基的一部分)
- `login.heart_beat_c2s {svr_time}` = **cmd 260**,每 **5 秒**送;server **120 秒**沒收到任何封包就踢。
- `on_heart_beat_s2c` 回 `svr_time`(存起來,下次帶回)。
- 短連線(<120s,如領紅包/收車)免心跳;**挖礦(6 分鐘)一定要**。

---

## 2. 要蓋的東西 + 建構順序

### Step 1 — `WSGameClient` 基礎層(共同地基,先做這個)
把 `_login_poc.py` 的一次性流程包成可重用 class:
```
connect(ws_url) → send active byte 0x00 → role_login(creds)→ 等 code==0
  → 背景心跳 loop(每5s cmd260,僅長連線需要)
  → send(cmd, body, 自增 sendID) / recv 用 (sendID 或 cmd) 對應回應
  → reconnect(ticket 重用) / close
```
- 載入 creds:讀 `auth_state/_auth_capture_<dev>.json`。
- ticket 過期/被踢 → 重撈:
  - **ADB 裝置**:`ws_token.creds.refresh_creds()` wrapper 呼叫 `adb_token_login.py`(冷啟 App ~30s)。
  - **web_h5 裝置(無 ADB,如 7fe98fc6)**:用 CDP 直接讀 live page 的 `LoginDataCache`——
    `python tools/_auth_capture_probe.py <web_debug_port> --await < capture.js`,JS 取
    `IS((await System.import("chunks:///_virtual/LoginDataCache.ts")).LoginDataCache)` +
    `netManager._cnet._socket.url`,輸出 `{"creds":{...}}` 直接覆寫 capture 檔。**讀 page 不踢 session,WS 登入才踢。**
- **驗收 ✅ DONE (2026-06-08, 小寶/7fe98fc6)**:`code=0` 登入 + 維持心跳 130s 不被踢 + `fly_pet_info`(16898)
  收到 337 隻。實作:`ws_token/`(codec/creds/transport/client/smoke),282 離線測試綠(codec 與 LIVE PoC byte-parity)。
  跑法:`python -m ws_token.smoke --device <dev> --hold 130`。

### Step 2 — 領紅包(最完整,拿來驗 send/recv loop)
- 協議:`docs/protocol/REDPACK_SCHEMA.md`(已驗證)
  - `red.red_brief_list_c2s` = **0x2605**(空 body)→ s2c 列表(`RedBagEntry{bag_id, ?, type=2, sender_id, sender_name, unix_ts}`)
  - `red.red_grab_c2s` = **0x2603** `{id:int64, type:int32}` → 領取;失敗回 **0x0201** `{error_code}`(2=已領完)
- ⚠ 坑:REDPACK_SCHEMA §「必須用 netManager.send」是指**遊戲內 CDP 低階 sock.sendMessage 會跳過 XOR/framing**。
  純 Python `gen_packet`(自己組 protobuf + XOR + framing,跟 role_login 同法)**理論上 OK**(role_login 有 body 也是這樣成功的),
  但 grab **要 live 驗一次**確認 server 收(沒有 per-packet 簽章的話就會過)。
- 既有(僅供對照,走的是 web_h5 in-game CDP,不是純 WS):`utils/redpack_detector.py`
- **驗收 ✅ DONE (2026-06-09, 小寶/7fe98fc6 純 WS 真的領到 `num=102`)**:`ws_token/redpack.py`(+`redpack_smoke.py`),300 離線測試綠。
  - **修正**:`red_grab_c2s` 是 **`{type#1, id#2}`(type 在前!)**,舊 doc 寫反 → 之前 `code=2` 是欄位錯位不是已領/malformed。**純 WS gen_packet 可行,不必 netManager.send**。
  - grab `type` = `configRed_packet[cfg_id].type`(client 表,值域{0,1};存 `ws_token/data/red_packet_types.json`);brief 的「field2」是 cfg_id、「type」其實是 state。
  - 全 schema 已 CDP 匯出 `docs/protocol/RED_PROTO_SCHEMA.json`;修正全文見 `docs/protocol/REDPACK_SCHEMA.md` 頂部。

### Step 3 — 停跨界車(需校準 protobuf 欄位)
> **使用者範圍 (2026-06-08)**:這套 WS 後端的停車任務只做「**自動停跨界車(跨界/cross-realm 車位)**」,
> **不停一般車位**,且 **不做自動收車(收/collect)**(「這不是你該做的」)。所以實作聚焦在:
> 讀車位狀態 → 找跨界空位 → 把自己的車停進跨界位。收車 / 領倉庫 / 自動收 toggle 都**不實作**。
- 協議:`docs/protocol/CARPARK_GUILD_NODES.md`(cmd 名已知,**欄位號/cmd-id 需 capture 校準**)
  - 拿狀態:`car_park.car_park_info_*`
  - 停車(本任務核心):`car_park.car_park_parking_*`(停進指定車位;欄位需 capture 校準)
  - 跨界車位判定:看 `nodeCorss.active`(memory `scrollhorse_cross_detection`,別看 stale 字串)
  - ~~一鍵收車 `car_park_parking_stop_all_c2s`~~ / ~~自動收車 toggle `car_park_collect_c2s`~~ / ~~一鍵領倉庫~~ → **範圍外,不做**
  - 完整 78 cmds 在 bundle line 7503(搜 `"car_park\."`)
- 5554 自動停車目標 + utils:memory `project_carpark_goal`、`utils/carpark_*.py`(注意目標含一般位,WS 版只取跨界)
- **驗收**:WS 送 `car_park_info` 解出車位狀態 + 找到跨界空位 + 送停車指令把車停進跨界位並收回應。

### Step 4 — 挖礦(翻盤點:盤面也走 WS)
- 協議:`home.home_mine_*` family
  - `home.home_mine_info_*` ← **盤面從這拿**(權威,還能修正 CNN 漏判 3x3)
  - `home.home_mine_hole_update_*` ← 挖(dig action)
  - `home.home_mine_use_goods_*` / `home_mine_auto_use_goods_*` ← 用道具(炸彈/鑽頭)
  - `home.home_mine_get_reward_*` ← 領獎;`home_mine_update_recover_*` ← 恢復
- **專案早就在抓盤面**:`miner/mining_service.py` 拿 CNN 盤面跟 WS 抓的 **`0x0c01`** 對照(`utils/ws_validator.validate_against_captures`,另有 0x0402/0x0c11/0x4202)。
- **planner 直接重用**:`miner/v4/planner.py`(bounded DFS)。挖礦變成:`home_mine_info`(盤面)→ v4 planner → `home_mine_hole_update`(挖)。**不開 App、不 CNN、不 OCR。**
- **驗收**:WS 拿 `home_mine_info` 解出盤面(對齊 CNN/0x0c01 capture)+ 送 `home_mine_hole_update` 挖一格收回應 → 接 planner。

---

## 3. 共用工具 / cmd-id 對照
- cmd 名 ↔ id:`MSG_TO_ID_MAP`(bundle `protoregister.ts`,line 7835)。login 257-260 已知。其他 family 的 id 要從這表查或 capture。
- proto schema 來源:bundle 內各 view 的 `netManager.send("<name>", body)` / `addEventListener("<name>_s2c")`;
  欄位號**不要憑變數名臆測**,capture 一次 round-trip 校準(`utils/ws_listener` / live-protocol-decoder skill)。
- bundle:`docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`(22MB,grep 別整讀)。

## 4. 已知坑 / 注意
- **獨佔帳號**:WS 登入會把同帳號的 bot/dashboard/App 異地登入踢掉。@apple(user999)沒在別處跑,衝突最小;@google 在 emulator-5554 跑,要錯開。
- **ticket 刷新**:可重用數小時;過期用 `adb_token_login.py` 重撈(手機要連著、會冷啟 App ~30s)。
- **gen_packet vs in-game send**:純 WS 一律自己 `gen_packet`(protobuf+XOR+framing),不要走遊戲內 sock。
- **bot_config 已還原**(2026-06-08):沒有 ws_token 裝置、沒有 #999;接這套時再新增 `backend:"ws_token"` 裝置設定。

## 5. 現狀(交接當下)
- `bot_config.json` 已還原成原樣,使用者跑舊腳本中。
- **Step 1 完成 (2026-06-08)**:`ws_token/` 套件 = codec(framing+XOR+protobuf,與 LIVE PoC byte-parity)
  / creds(load+refresh)/ transport(websocket-client 包裝 + 可注入 fake)/ client(`WSGameClient`:
  login+心跳+收發+重連,context manager)/ smoke(live 驗收 CLI)。測試:`tests/test_ws_token_{codec,creds,client}.py`
  共 282 綠。Live 驗收過(小寶/7fe98fc6,見 Step 1 §)。**尚未接進 bot**(device_wrapper / new_main_v2),這留到任務步驟。
- 其他既有 building block:`tools/adb_token_login.py`(ADB 撈 ticket)、`tools/_auth_capture_probe.py`(CDP 撈 ticket,web_h5 用)、
  `tools/token_viewer.py`、`docs/protocol/AUTH_HANDSHAKE_SPEC.md` §9。
- **下一步(使用者指定)**:Step 3「自動停跨界車」(只停跨界、不收車,見 Step 3 範圍註)。Step 2 領紅包順序待確認。
