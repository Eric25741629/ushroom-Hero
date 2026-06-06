# AUTH_HANDSHAKE_SPEC — 登入握手 + WS 加密/framing 規格

> 來源:逆向 `docs/game_client_sources/mushroomh5.acenetgame.com_assets_script_index.966f5.js`
> 模組:IOHandler.ts、SocketClient.ts、NetManager.ts、LoginControl.ts、LoginDataCache.ts、ByteArray.ts
> 目的:評估「純原生 client(Kotlin/B 路線)直連協議」可行性。本檔是 B 路線的地基。
> 狀態:**已 LIVE 驗證(2026-06-04,裝置 7fe98fc6)**。純 Python 無瀏覽器登入成功,見 §7。

## ✅ 驗證結論(2026-06-04)

**B 路線成立(半原生)。** 用一支純 Python script(`tools/_login_poc.py`,只用本檔 §2/§3 解出的 framing+XOR,完全不開瀏覽器),拿 capture 來的 `{uid, uname, plat, loginGameId, isWhiteIp, pKey, loginTicket, loginSceneId, roleId}` + gateway WS url,送 `role_login_c2s` → 收到 `role_login_s2c code=0 (SUCCESS)`,role_id/serv_time 正確帶回。

**更勁爆的發現:`time` 欄位 server 完全不驗。** 測了 +100000s(遠未來)、原始 6 小時前、甚至 -1e8s(2023 年)四種 time,**全部 SUCCESS**。所以連「改 time」都不必,既有 ticket 直接重用即可。唯一未知:ticket 的**絕對 TTL**(SDK 簽發後多久失效),目前已知 ≥6 小時,需跨日測(§7 #3)。

---

## 0. TL;DR 結論(先看這個)

| 層 | 狀態 | 對 B 的影響 |
|----|------|------------|
| WS framing | **完全解出**,big-endian | 容易,可直接在 Kotlin 重現 |
| 封包加密 | **完全解出**,固定 256-byte XOR table | 容易,金鑰是寫死的常數,非握手協商 |
| protobuf 編解碼 | 機制清楚(protobufjs + JSON schema) | 中,需另外取出 proto JSON + name→id 表 |
| WS 登入封包 (`role_login_c2s`) | 欄位全知道 | 中,欄位值靠下一步 |
| **帳號 SDK 登入(取得 token/ticket)** | **這是生死線** | **B 最大的坑,見 §5** |

**一句話**:加密和 framing 不是問題(都是靜態的),問題在「**怎麼拿到 `token`/`p_key` 這組登入憑證**」——它來自第三方 FN 帳號 SDK,且很可能有時效。這決定 B 是「中等專案」還是「離不開瀏覽器」。

---

## 1. WS 連線

- `new WebSocket(host)`,`binaryType = "arraybuffer"`(二進位幀)。
- `host` 形如 `wss://<gateway-ip>?token=<game_server>`,其中 `<game_server>` = server_list 裡選定 server 的 `game_server` 欄位(注意:**這個 token ≠ 登入 ticket**,只是 gateway 路由用)。
- 連線成功(onopen)後,client **先送一個 raw byte**(不經加密/framing):
  - `0x00` = 全新連線
  - `0x01` = 重連
  - 來源:`SocketClient._sendActiveMessage()`。
- 心跳:每 5 秒送 `login.heart_beat_c2s {svr_time}`(cmd 260)。
- 逾時:**120 秒(120000ms)沒收到任何資料 → client 主動斷線**(`SocketClient.update` 的 `12e4` 判斷)。原生實作必須維持心跳,否則被踢。

---

## 2. Framing(封包外層,big-endian / 網路序)

`ByteArray.littleEndian = false` → 所有 `writeInt/writeShort/readInt/readShort` 都是 **big-endian**。

### 送出(client → server)`IOHandler.genPacket(cmd, body)`

```
[ int32  totalLen ]   // = body.length + 2(sendID) + 2(cmd);不含這 4 個長度位元組本身
[ int16  sendID   ]   // 自增序號 1..65535 循環(每送一包 +1,到 65535 回 1)
[ int16  cmd      ]   // protoId(訊息 id)
[ bytes  encBody  ]   // XOR 加密後的 protobuf body(見 §3)
```

### 接收(server → client)`IOHandler.dispatchRequest`

```
[ int32  totalLen ]   // 後續位元組數 = 2(cmd) + 1(flag) + bodyLen
[ int16  cmd      ]   // protoId
[ uint8  compFlag ]   // 0 = 未壓縮;1 = body 需 decompress(zlib/uncompress)
[ bytes  encBody  ]   // XOR 加密的 protobuf body;先 XOR 解,再(若 flag=1)解壓
```

> 注意送出/接收 header 不對稱:送出是 `sendID(2)+cmd(2)`,接收是 `cmd(2)+flag(1)`。
> totalLen 都是「長度欄位之後的所有位元組數」。
> 接收時若 `compFlag==1`,XOR 解密後還要 `ByteArray.uncompress()`(zlib inflate)。

---

## 3. 封包加密(XOR,靜態金鑰)

加密**只作用在 body**(header 不加密)。keystream 起點 = `cmd % 256`,逐位元組 XOR,索引每步 +1 mod 256。

```
key = KEY_256   // 見下,固定 256 bytes
h = cmd % 256
for i in range(len(body)):
    body[i] ^= key[h]
    h = (h + 1) % 256
```

送出與接收用**同一把金鑰、同一個演算法**(genPacket 與 dispatchRequest 的迴圈完全相同),只是 cmd 來源不同(送出用你要發的 cmd,接收用收到的 cmd)。

### KEY_256(直接從 client 抄出,`IOHandler` 內的陣列 `s`)

```
[153,234,171,122,153,37,54,178,41,143,55,117,108,2,144,122,
 103,79,15,148,253,85,47,52,9,227,214,212,84,65,207,5,
 7,13,14,252,144,156,100,171,224,228,203,149,76,184,103,203,
 19,101,153,173,165,19,69,154,1,240,209,164,106,118,6,157,
 239,63,246,239,221,68,81,194,149,53,25,35,43,61,235,197,
 86,70,116,6,150,244,237,81,252,85,153,107,4,30,147,86,
 7,220,152,169,158,183,214,193,240,242,51,14,204,137,81,139,
 102,158,158,203,141,17,97,90,221,81,226,85,146,57,198,233,
 204,36,84,131,71,224,52,233,29,174,213,163,211,25,222,189,
 45,20,134,25,36,228,86,163,170,148,140,19,47,150,12,176,
 20,144,97,115,12,124,208,59,225,102,232,64,81,190,17,98,
 254,14,108,231,105,199,12,56,148,242,123,24,26,82,193,199,
 154,87,211,92,63,147,90,224,164,243,216,137,19,118,7,31,
 106,244,41,113,160,17,117,247,126,26,200,86,45,115,199,58,
 133,235,184,217,245,247,9,198,200,34,71,174,175,125,77,129,
 35,234,7,143,112,142,138,121,100,149,203,142,137,116,243,225]
```

> 這把金鑰是 build 時寫死的。若遊戲改版換金鑰,要重抄。但同一版內固定,不隨 session 變 → **加密層對 B 沒有阻力**。

---

## 4. protobuf 編解碼

- NetManager 用 **protobufjs**:`protoRoot = protobuf.Root.fromJSON(protoJson)`。
- 訊息名 ↔ cmd id:`MSG_TO_ID_MAP`(name→id)/ `ID_TO_MSG_DICT`(id→name),在 `protoregister.ts`(bundle 行 7835)。
  - 已知:`login.role_login_c2s = 257`、`login.role_reconnect_c2s` 落在 258/259、`login.heart_beat_c2s = 260`。
- `send(name, obj)`:查 id → `protoClass.encode(create(obj)).finish()` → `_cnet.sendMessage(id, bytes)`。
- 收到:`protoClass.decode(bytes)` → emit 事件名。
- 另有 JSON 通道:`json_proto.json_proto_c2s`,把任意訊息包成 `{proto_id, msg: JSON.stringify(obj)}` 送出(debug/相容用)。

**B 要做的**:把那份 proto JSON(描述所有訊息欄位)和 `MSG_TO_ID_MAP` 從遊戲資源抽出來。它們是 runtime 載入的 asset(`setProtoJson` 的輸入),不在這支 index.js 內 → 待找(通常是 `proto.json` / `protoData` 之類的 bundle 資源)。

---

## 5. 登入鏈(HTTP → WS)——B 的生死線

### 共用:ticket 雜湊 `_getTicket(kv[])`

```
salt P = "fc0faf1cb1478a85d5ab7089ff3233d0"   // LoginControl 內常數
sort kv by key 升冪
s = 串接所有 value
ticket = MD5_hex(s + P)
```

### Step 1 — 取 server list url(HTTP GET,CDN)
`LoginDefine.LOGIN_SERVER_LIST_*` 其中一個 url(依 `SERVER_LIST_TYPE`)。
回 `{ list:[{ip, cp, cp2}], gateway }` → 設 centerServer / bgp1 / bgp2 / gateWayInfo。

### Step 2 — server_list(HTTP GET)
`{centerServer}[/xyx_tw]/client/server_list?time=..&uid=&plat=2002&ticket=MD5(time+""+plat+P)`
回 `{ server_list:[{id, game_server, ...}] }`。

### Step 3 — login_auth(HTTP GET)**← token/p_key 從這裡來**
兩條路,看 `ENABLE_SDK`:

- **非 SDK(`ENABLE_SDK=0`,測試/內部用)**:
  `{center}[/xyx_tw]/exe/login_auth?name={uid}&uid={uid}&ticket=MD5(uid+uid+P)&platform=2002&device=..&t=..&version=..`
  → 憑證只是 `uid`。**若 H5 正式服走這條,B 幾乎無痛**(ticket 可自算)。但正式服極可能走下面 SDK 路。

- **SDK(`ENABLE_SDK=1`,正式 FN 帳號)**:
  需要 `sdkExt = sdkControl.login()` 回的 JSON:`{username, suid, suidSignStr, timestamp, fngid}`。
  `{center}[/xyx_tw]/oversea/login_auth?ticket=MD5(name+uid+ext+P)&platform=2002&username=..&suidSignStr=..&suid=..&device=..&timestamp=..&big_version=..&version=..&did=..&game_id=..`
  → **`suid`/`suidSignStr`/`timestamp` 由第三方 FN 帳號 SDK 簽出,client 自己造不出來。這就是 B 的天花板。**

login_auth 回:`{ code, uid, uname, is_white_ip, time, token, scene_id, p_key, ip, role_id, server_list? }`
→ 存進 LoginDataCache:`loginTicket=token`、`pKey=p_key`、`loginTime=time`、`loginSceneId=scene_id`、`roleId`、`loginGameId=fngid`。

### Step 4 — 連 WS gateway
`host = gateWayInfo.ip(+ip2/bgp 切換) + "?token=" + loginServer.game_server`。

### Step 5 — WS 送 `login.role_login_c2s`(cmd 257)
```jsonc
{
  "uid": <uid>,
  "game_id": <loginGameId>,        // = fngid
  "server_id": <loginSceneId>,
  "uname": <uname>,
  "plat": "2002",
  "is_white_ip": <0/1>,
  "time": <loginTime>,             // login_auth 回的 time
  "p_key": <pKey>,                 // login_auth 回的 p_key
  "ticket": <loginTicket>,         // login_auth 回的 token  ← 核心憑證
  "role_id": <roleId>,
  "machine_info": { device, device_id, device_name, game_version, os, os_version, nm, screen, ip },
  "proto_version": 1
}
```
server 回 `login.role_login_s2c {code, role_id, server_id, serv_time, time_zone, open_time, ...}`。`code==SUCCESS` 才算進遊戲。

### 重連:`login.role_reconnect_c2s`
較輕:`{uid, server_id, role_id, time, ticket, machine_info, p_key}`。

---

## 6. 對 B 路線的判斷

### 容易的(靜態,已全解)
- WS framing、XOR 加密(金鑰寫死)、心跳、序號、壓縮旗標 → Kotlin 直接重現,§2/§3 就是規格。

### 中等(要再抽資源)
- proto JSON + `MSG_TO_ID_MAP`:從遊戲 asset 取出,才能編 `role_login_c2s` 和飛寵 `66_*`。

### 生死線(§5 Step 3 SDK)
- 正式服若 `ENABLE_SDK=1`,登入憑證 `token`/`p_key` 必須先過 **FN 第三方帳號 SDK**(`suid`/`suidSignStr` 是它簽的)。原生 Kotlin **無法自己生成這組簽章**。

### 因此 B 的兩種收斂
1. **半原生(務實)**:用一次瀏覽器/WebView 跑完 SDK + Step 1-3,**capture `{uid, token, p_key, time, scene_id, role_id, gateway, game_server}`**,餵給 Kotlin client 做 Step 4-6。
   - 風險:`token`/`p_key` 內含 `time`,**極可能有時效**。若幾分鐘就過期 → 每次都要重跑瀏覽器登入 → 沒有真正擺脫瀏覽器,只是把「遊戲渲染」換成「登入那一下」。**這點必須 live 驗證**:抓一組憑證,隔 X 分鐘/小時後單用 Kotlin 流程重連,看 server 認不認。
2. **全原生(困難)**:連 FN SDK 的帳號簽章一起逆/重放。若 SDK 用裝置綁定或伺服器簽章,基本不可行或極脆。

### 下一步建議(投 Kotlin 前)
1. **找出 proto JSON 資源**(§4)——沒有它連 `role_login_c2s` 都編不出來。
2. **live 抓一次登入**,確認正式服走 SDK 還非 SDK、`token` 時效多長。
3. 用 Python(現成 `web_game_api` 的 framing/XOR 能力)先寫一個**「不靠瀏覽器、純 socket」的登入 PoC**:讀 capture 來的憑證 → 連 WS → 送 active byte → 送 `role_login_c2s` → 看能不能收到 `role_login_s2c code=SUCCESS`。**這個 PoC 通過 = B 成立;不通過 = 別碰 B,回去用電腦/瀏覽器方案。**

---

## 7. LIVE 驗證紀錄(2026-06-04,裝置 7fe98fc6)

工具:
- `tools/_auth_capture_probe.py` — CDP eval 小工具,連 web_debug_port 9226 的遊戲分頁。
- `tools/_login_poc.py` — 純 socket 登入 PoC(無瀏覽器),用 §2/§3 自組封包。
- 憑證 capture:`auth_state/_auth_capture_7fe98fc6.json`(gitignored,含 token,勿外流)。

### 撈憑證的正解(供日後重撈)
`LoginDataCache` **不在** `ISInclude`,要走 SystemJS:
```js
const c = IS((await System.import("chunks:///_virtual/LoginDataCache.ts")).LoginDataCache);
// c.uid / c.loginTicket / c.pKey / c.loginTime / c.loginSceneId / c.roleId / c.loginGameId
// c.uname / c.plat / c.isWhiteIp / c.gateWayInfo / c.loginServer.game_server
```
WS url 直接讀 `netManager._cnet._socket.url`(= gateway + `?token=game_server`)。
proto schema:`netManager.protoRoot.lookupType('login.role_login_c2s').toJSON()`。
cmd id map:`(await System.import("chunks:///_virtual/protoregister.ts")).MSG_TO_ID_MAP`。

### 環境事實(此帳號)
- `ENABLE_SDK = 1`(正式 FN 帳號路線)、`PLAT_TAG=none`、`SERVER_LIST_TYPE=2`、`FULL_VERSION=9.0.2.12596`。
- `role_login_c2s = role_login_s2c = cmd 257`(同 id,方向看上下文;257-260 在 `addMsgQueueIgnore`)。
- `LoginState.SUCCESS = 0`。

### 三項測試結果
| # | 測試 | 送出 time | 結果 |
|---|------|----------|------|
| 1 | 重用 ticket + 當前時間 | now | **SUCCESS (code 0)**,role_id/serv_time 正確 |
| 2a | 容忍窗:遠未來 | now+100000 | **SUCCESS** |
| 2b | 容忍窗:原始 6h 前 | loginTime(舊) | **SUCCESS** |
| 2c | 容忍窗:3 年前 | now-1e8 | **SUCCESS** |
| 3 | 絕對 TTL | — | **未測**,需跨日;目前已知 ticket 壽命 ≥6h |

**結論**:`time` 不參與驗證;ticket+p_key 是唯一憑證。半原生模型成立——瀏覽器/SDK 登入「偶爾」跑一次刷新 ticket,期間純原生 client 可無限重連。

### 待辦(B 真正落地前)
1. **#3 跨日測**:隔 1 天 / 3 天 / 7 天用同一份 capture 跑 `_login_poc.py`,定位 ticket 失效點 → 決定多久要重開瀏覽器刷新一次。
2. **proto JSON 全量匯出**:目前只匯出 login 幾個 type;飛寵 `66_*` 的 c2s/s2c schema 要一併匯出(同 `lookupType` 法),Kotlin 才能編飛寵指令。
3. **異地登入策略**:PoC 登入會踢掉現有 session;原生 client 與 bot/dashboard 不可同帳號並存,要排程錯開。

## 8. 連線生命週期 + 怎麼發指令(原生 client 怎麼跟 server 溝通)

**所有遊戲指令(飛寵 `16896+N`、挖礦、神燈…)都沒有無狀態 HTTP 端點,必須走「一條已登入的 WS 連線」送出。** 不能憑空丟一條指令;要先在該連線上 `role_login_c2s` 成功,才能在同一條連線上發指令並收回應。

### 短連線(connect-on-demand)— 開 App / 倒數歸零的正解
平常完全斷線、不溝通;要做事時才連一下:

```
觸發
 1. 開 WS → 送 1 byte 0x00              (active message)
 2. role_login_c2s(重用 ticket)→ SUCCESS(code 0)
 3. 在同一條連線連續發指令,各自收回應(用 cmd id + 自增 sendID 對應):
       fly_hybrid_partner_shelves_c2s (16920) → 搭檔清單
       fly_hybrid_start_c2s (16923) {base_id,fly_a_id,fly_b_id} → 繁殖
       fly_hybrid_get_c2s (16924) → 收取
 4. 關 WS
```
整批通常數秒內完成。**登入後一條連線可發任意多條指令。**

### 心跳規則
- server **120 秒**沒收到任何封包就踢。短連線整批 <120s 免心跳。
- 若需久等(例如等繁殖完成),每 5 秒補 `login.heart_beat_c2s {svr_time}`(cmd 260)。

### 長連線
只有「要即時收 server 推播」才需要;每 5 秒心跳維持。你的 on-demand 情境不需要。

### 獨佔帳號
client 一登入,同帳號的 bot/dashboard 會被異地登入踢掉。短連線把「連著」壓到最短 → 衝突窗最小。

### ✅ 已驗證(2026-06-04,`tools/_login_poc.py 0 --list`)
純 Python(無瀏覽器)走完整鏈:`active byte → role_login_c2s → SUCCESS → fly_pet_info_c2s(16898 空 body)→ fly_pet_info_s2c 收到 337 隻飛寵`(id/config_id/level 正確)。證明「登入 + 發指令 + 收回應」整條原生可行。發指令的程式碼與登入相同:`gen_packet(cmd, body)`,body 照 `FLYPET_PROTO_SCHEMA.json` 組。

## 附:本檔涉及的 bundle 行號(同一支 index.966f5.js)
| 模組 | 行 |
|------|----|
| ByteArray.ts | 4097 |
| ControlMgr.ts | 5837 |
| IOHandler.ts | 7097 |
| LoginControl.ts | 7201 |
| LoginDataCache.ts | 7203 |
| NetManager.ts | 7473 |
| protoregister.ts (MSG_TO_ID_MAP) | 7835 |
| SocketClient.ts | 8657 |
