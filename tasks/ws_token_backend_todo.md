# ws_token backend — 狀態 / 交接 (更新 2026-06-09 深夜 / batch 2 + live 驗證)

> 這份是 ws_token 後端的**當前權威狀態**。設計全文 `docs/superpowers/specs/2026-06-09-ws-token-task-batch-design.md`;
> 整合說明 `docs/WS_TOKEN_INTEGRATION.md`;教訓 `tasks/lessons.md` 2026-06-09 節。
> memory:`project_ws_token_backend`、`reference_ws_proto_schemas`、`reference_adb_sdk_token_extraction`。

## ⚠ 程式在哪 / 下個 session 怎麼接

- **所有 batch-2 程式碼在隔離 worktree:`C:\Users\Eric\ws-token-integration`(branch `feat/ws-token-integration`)。**
- 主 checkout `C:\nas同步_project\菇勇者全自動掛機` 現在在 `feat/dragon-realm`,**沒有這些 commit**。下個 session 要動 ws_token 一律進 worktree。
- **未 merge、未接 new_main_v2/device_wrapper、未重啟 bot。** production 完全沒受影響。
- ticket 檔(`auth_state/*.json`)是 gitignored,worktree 已複製一份;過幾小時可能要重撈(見最下「ticket 刷新」)。

## 是什麼
新後端 `ws_token/`:用 ADB/CDP 撈的登入 ticket **直連遊戲 WebSocket 送 protobuf 跑任務**,不開 App、不靠螢幕(無 OCR/CNN)。純 Python 自組 protobuf+XOR+framing(`ws_token/codec.py`,與 LIVE PoC byte-parity)。

## 地基(DONE)
- `ws_token/client.py` `WSGameClient`:active byte → role_login(257)→ 背景心跳(260,5s)→ `call`/`call_for`/`set_push_handler` → reconnect/close。**batch-2 加:`CMD_KICKED=259` 異地登入偵測 + `is_kicked()`/`on_kick`。**
- `ws_token/codec.py`、`creds.py`、`transport.py`。`tests/fakes/ws_fakes.py`(FakeTransport + responder)。

## 任務模組(全部 DONE:離線 build + 測試 + commit)

### Batch 1(2026-06-09 早,5554/小寶 live 驗過)
| 任務 | 模組 | cmd 重點 | live |
|------|------|----------|------|
| 領紅包 | `redpack.py` | red_brief_list 0x2605 / red_grab 0x2603 | ✅ 領到 |
| 開神燈 v2 | `lamp.py` | equip_box_open_all 0x0509 / equip_wear | ✅ combo→套裝(equip-path 未 live 換) |
| 家族大廳 | `guild.py` | 捐獻7441/寶箱7459-60/求助7452-54 | ✅ 捐獻到 err159、求助、寶箱休眠 skip |
| 家園管家採購掃蕩 | `steward.py` | worker_common 0x49 | ✅ 購物+副本掃蕩(chapter2→52神燈) |
| 魔法劇場+烈焰山洞 | `league_solo.py` | dungeon_league_solo_get_reward 0x0E0F {type1-4} | ✅ 領 94 幻劇+熔岩 |
| 主畫面領任務 | `main_tasks.py` | task 0x0A01-0A0A(push collect+claim) | ✅ 領每日 7/7 |
| 挖礦(離線層) | `mining.py`+`mining_adapter.py` | home_mine 0x0C01/0x0C03 | 讀+plan✅ 真挖未做 |

### Batch 2(2026-06-09 深夜,本 session,小寶 read-only live 驗)
| 任務 | 模組 | cmd 重點 | 測 | live(小寶 7fe98fc6,**只讀**) |
|------|------|----------|----|------|
| 深淵之門+萬神試煉 | `dungeon.py` | list 0x0E01 / sweep(掃蕩優先) / battle(anti-cheat) | 25 | ✅ list 23 個解析;**day_times 是累計非剩餘** |
| 轉盤金幣 | `turntable.py` | ad_wheel_info 5635 / spin 5636 | 12 | ✅ num=5 免費 |
| 掛機/離線獎勵 | `idle_reward.py` | main_chapter reward_info 3333 / claim 3334 | 13 | ✅ **離線登入即 push(type2=45600金+物)**;在線 claimable |
| 農場/打工 | `farm.py` | home_farm 3077/plant/harvest + worker 18689 | 22 | ✅ 真實生長作物(state 0空/4長/2熟,seed_id=102) |
| 跨界停車(只停不收) | `carpark.py` | lot 12801 / my_car 12802 / start 12847(新)/12832(舊) | 20 | mount✅(修 bug 後 22 隻);**lot/discovery 卡 event 休眠** |
| 異地登入被踢 + 30分冷卻 | client/runner/ws_runner_service | CMD_KICKED 259 | 16 | ✅ 強登小寶:收 cmd259 reason20→on_kick 1次+is_kicked+socket 關 |

**Batch-2 commit(branch feat/ws-token-integration)**:
schema f106e396 / dungeon eae2a2e3 / turntable 867b4de9 / idle_reward 97350564 / farm b1c82542 / carpark b20c40c9 / kick e9b8516d / S5b docs c0930ef6 / **carpark fix 82051c07** / **runner 接5任務 27e6db45** / lesson 17f414a6。

## ⭐ 本 session live 抓到 + 修掉的真 bug
`carpark.parse_my_mounts`:伺服器對**空閒** mount 也送全零 `parking_data#5`,舊判定 `d.get(5) is not None` 把每隻當「已停車」排除(6→0),`auto_park_cross` 會永遠 `no_available_mount`(靜默壞,離線測還綠因 fake 對空閒 mount 沒送 #5)。改 `_is_parking()`(有非零欄位才算)+ 真實 wire 回歸測;修後 live 22 mounts 正常(82051c07)。

## runner 整合現況(DONE,worktree)
`ws_token/runner.py` `run_device(device, *, spend, sweep_list, open_lamp, farm_config, dungeon_sweeps, carpark_target)`。TASK_ORDER:
- **免費常開**:main_tasks / league_solo / redpack / **idle_reward** / **turntable** / **farm(harvest)** / guild(help)/ steward(read)
- **gated(要設定值才動)**:farm 種植/打工(`farm_config={seed_id,team_cfg_id}`)、dungeon 掃蕩(`dungeon_sweeps=[(type,dungeon_id,num)]`,**只掃蕩永不戰鬥**)、carpark(`carpark_target=cross master_id`)、lamp(`open_lamp`)、guild 捐獻/steward 採購掃蕩(`spend`)
- 每任務 try/except 容錯;composite push handler 接 idle 離線 push;被踢→`RunReport.kicked`→`ws_runner_service` 30分冷卻(`_KICK_COOLDOWN_SEC=1800`)→下輪 `_protected_player_online` 再查在線才恢復。
- `runtime_services/ws_runner_service.py` 讀 config:`ws_token_spend/sweep_list/open_lamp/farm_config/dungeon_sweeps/carpark_target`。`use_ws_runner` per-device toggle(預設 off,legacy 不變)。

## ✅ mutate 驗證結果(2026-06-09 深夜,4 台帳號,可再生資源授權)
四台 (小寶/5554/5556/5560) 都能登入 (code=0)。逐項真跑「領/種/轉/掃」:
- **idle_reward 完全可用** ✅:小寶 `--claim-online --claim-offline` 兩個都 `success=True`
  (online claim{1} cmd 0x0d06、offline claim{2} from login push)。離線獎勵登入即 push 確認。
- **turntable 可 spin 但有冷卻** ✅(修了 bug):小寶實得 slot 5;spin 後 `cd` 設未來時間,
  下次 spin 回 0x0201(code 90/173)。**舊 spin_once 用 call(只等 5636)→ 冷卻時 timeout
  整個 task crash**;已改 call_for(5636,0x0201) 優雅回 None + spin_all_free 遇拒即停(74e3ad23)。
- **farm plant/harvest/work robust + read-once** ✅(修了 2 個 bug,935a8839):
  (1) 被拒的 plant 回 **0x0201 code 173**(非 3078)→ 舊 call crash;改 `_farm_action` call_for 逐格記 code。
  (2) **`home_farm_info`(3077)一個 session 只答一次**,第二次 read 必 timeout → 加 `info=` 重用快照,
  `_run_farm` 只 read 一次。live:小寶 plant 5 格各記 code(173/3)不再 crash(planted=0 因無備用 seed 102)。
  ⚠ harvest-of-mature 還沒實領到(四台目前都 0 成熟地);打工 `start_work` 需 team_cfg_id 仍未取。
- **dungeon sweep = 活動未開** ❌(c4d57b84):5554 `sweep type=2 dungeon_id∈{150,1,0}` 一律 0x0201 code 173
  = **「活動已結束」**(掃蕩 3596 是限時掃蕩活動,現未開;日常 深淵/萬神 要走 battle 3591/3592,anti-cheat)。
  run_sweep 不 crash。另 CDP 釘到:**day_times 是累計非剩餘、深淵每日上限=2、門票 gtid=1003**。

**🔑 error 碼已 CDP 解碼(configErrorInfo,權威;159=次數不足 對上已知 → 表正確):**
**`90 = 冷卻時間未到` / `159 = 次數不足` / `173 = 活動已結束`。** 三功能釐清:
  - 轉盤 = **事件輪盤**:開時可轉(live 得 slot 5),spin 後 90 冷卻;活動關時 173。
  - 農場 plant 102 = **事件作物**,活動結束 → 173。常態用 **打工 (免費種)**,非手動種事件作物。
  - 深淵/萬神 **掃蕩活動未開** → 173;日常自動化得走 battle(anti-cheat)或等掃蕩活動。
  解法:`Get-Content x.js | python tools/_auth_capture_probe.py 9226`(CDP eval),
  `configErrorInfo.getDataByKey(code)._data[1]` = langId → `GetStrFromConfig(langId)` = 中文。

## ▶ 下一個 session 從這裡開始(優先序)

1. **harvest-of-mature 實領 + 打工**:等某帳號有成熟作物再驗 `--harvest` 真給獎;CDP 讀 configFarmWorker /
   或 worker 模組找 `team_cfg_id` → 驗 `start_work` 真啟動 管家(免費種)。這是農場的常態路徑。
2. **深淵/萬神 日常自動化決策**:掃蕩活動多半沒開 → 要嘛等活動、要嘛做 battle(3591/3592)路徑並驗
   anti-cheat 是否接受 client result。門票 gtid 已知(深淵 1003 / 萬神 1081),每日上限 2。
3. **carpark 補 discovery + 等 cross 活動**:`carpark.py` 缺 `cross_car_park_preview`(12830)→`park_list`。
   preview 12830 本 session 全 timeout = 活動休眠;等跨界活動開時實測 + 拿真 target_id + 驗 park。
   mount 解析 bug 已修(82051c07,live 22 隻正常)。
4. **接進 bot + 排程**(等上面 + 你審查):merge `feat/ws-token-integration`、設 `use_ws_runner` per-device、
   定 ticket 刷新 + 同帳號異地登入錯開排程,重啟。`docs/WS_TOKEN_INTEGRATION.md`。
5. (低優先)挖礦真挖、lamp equip-path live 換、steward 續期 day_num 語義。

> **CDP 解碼技巧(可復用)**:小寶 web_h5 port 9226 的 H5 client 可隨時 `Runtime.evaluate`
> (`tools/_auth_capture_probe.py 9226`,UTF-8 要 `$env:PYTHONIOENCODING='utf-8'`)。所有 config 表都在
> window(`configErrorInfo`/`configChapter_type`/`chapterDataCache`/`configFarm*`…),錯誤碼、副本狀態、
> 門票、上限都查得到 —— 別再用猜的,直接 CDP 讀。

**本 session 新增 fix commit**:carpark mount 82051c07 / runner 接5任務 27e6db45 / turntable 74e3ad23 /
farm 935a8839 / dungeon 註記 92faddbc。idle_reward 與 turntable spin、farm plant 路徑皆 live 跑過。

## Live-confirm 清單(已知未驗/待釘)
- **batch-2 mutate 已驗**:idle claim{1}/{2} ✅成功、turntable spin ✅(有冷卻)、farm plant ✅(graceful)。
  **未驗**:farm harvest-of-mature(無成熟地)、farm `start_work`(無 team_cfg_id)、dungeon(掃蕩活動未開)。
- **error 碼已解**(CDP configErrorInfo):90=冷卻時間未到 / 159=次數不足 / 173=活動已結束。
- **carpark**:cross 活動休眠→preview/lot 無法驗;target_id 來源未解;新(12847)/舊(12832)body 切換條件(client `checkNewCrossOpen`)未 live 確認;mount 資格只排除「已在停車」,等級/外觀條件未建模。
- **dungeon**:`day_times` = 累計非剩餘;abyss(type2)門票 gtid、battle anti-cheat(client result=0 是否被接受)未驗 → **runner 只掛 sweep,battle 永不自動**。
- **farm**:`FarmInfo.role_id` 讀回 0(s2c 沒回查的 role_id,我們本來就自己傳,不影響);取消打工的 cmd 未知。
- steward:`buy_service.day_num` 續期=30天 vs 檔位 index(使用者:買 30 天檔);`sweep_list[].level/times` 多章自動推導未做。
- 失敗碼:**0x0201 error_code=159 = 已領/已滿**(捐獻、league_solo 共用),當「已領」跳過不 abort。
- **看廣告加倍 WS 沒有**(無 ad SDK),只能領基礎額。

## 帳號 / 裝置對照
| 暱稱 | device | 帳號 | 備註 |
|------|--------|------|------|
| 小寶 | `7fe98fc6` | kenken@gmail `27399634` | web_h5,CDP 9226;**神燈 71 萬**(confirmed,opengold_v2 `_LAMP_STATE_JS` 讀到 711,664);**未在玩,可強登**;batch-2 live 驗都在這台 |
| 閃電 | `emulator-5554` | @google `27353216` | 主 live 驗證機;管家在期 |
| 菜雞 | `emulator-5556` | — | batch-1 時無 ticket;本 session 主 checkout 18:09 有刷新 |
| 賤狗 | `emulator-5560` | — | 借測機 |
| 手機 | `fc65396d` | @google user0 + @apple user999 | adb 連著;可 `adb_token_login` 刷 ticket |

ticket 檔:`auth_state/_auth_capture_<device>.json` + 完整 `auth_state/<device>.json`(reusable 數小時)。

## ticket 刷新(過期了怎麼撈)
- web_h5 無 adb(小寶 9226):`tools/_auth_capture_probe.py <port> --await < js`,JS 讀 `LoginDataCache` + `netManager._cnet._socket.url` → 覆寫 capture 檔。讀 page 不踢,WS 登入才踢。
- adb 裝置:`tools/adb_token_login.py`(冷啟 App ~30s 撈 logcat ticket)。

## 怎麼跑(smoke,dry-run 預設;在 worktree 跑)
```
cd C:\Users\Eric\ws-token-integration
python -m ws_token.dungeon_smoke     --device 7fe98fc6                 # 讀 list;--sweep type:dungeon_id:num
python -m ws_token.turntable_smoke   --device 7fe98fc6                 # 讀 num/cd;--spin
python -m ws_token.idle_reward_smoke --device 7fe98fc6                 # 讀;--claim-online/--claim-offline
python -m ws_token.farm_smoke        --device 7fe98fc6                 # 讀;--plant SEED/--harvest/--work TEAM
python -m ws_token.carpark_smoke     --device 7fe98fc6 --target <id>   # 讀 lot(需 cross 活動);--park id[:pos]
python -m ws_token.runner            --device 7fe98fc6 [--spend] [--open-lamp] \
      [--farm-seed N] [--farm-team N] [--dungeon-sweep type:id:num] [--carpark-target N]
```
⚠ WS 登入會踢同帳號 session;在借測機/已授權可踢的帳號上跑(小寶可)。
gitignored 探針:`tools/_kick_probe_v2.py`(驗 cmd259)、`tools/_carpark_preview_probe.py`(驗 12830/12802)。

## 2026-06-10 Workflow 接入（feat/ws-backend，主線整合完成）

- **兩階段 wake cycle 已實裝**：`new_main_v2` 喚醒後、瀏覽器啟動前跑 `game_actions/ws_phase.run_ws_phase(ip)` → `daily_pipeline`（`ctx.ws_done`，11 處 guard）跳過 WS 成功項。失敗/登入失敗/自宣告 skipped 一律降級全跑。
- **新任務**：runner 接 `spirit`（免費召喚）/ `workshop`（12h 兩配方輪換，cadence 存 `ws_state/`）/ `couple`（奶茶+玫瑰 `give_all_in_hand` 每批 20 封頂送光；錘鍊掛 spend）。
- **ticket 自癒**：Playwright 階段載入後 `utils/ws_ticket_refresh.refresh_from_device` 回寫 capture 檔（`_CAPTURE_JS` 欄位名待 live page 驗，spec plan Task 11.1）。
- **Dashboard**：「方案」選擇器 adb / adb+ws / h5 / h5+ws（= backend + ws_token.enabled；前端 stash-merge 巢狀 ws_token），卡片 `+WS` 標記（中控要重啟）。
- **Pilot**：小寶 7fe98fc6 `ws_token.enabled=true, spend=true, open_lamp=true`。
- **Live 2026-06-10**：29.5h 舊 ticket 登入 SUCCESS（TTL ≥29.5h）；12/14 任務 ok（紅包 222 金/掛機/轉盤/守護靈抽2/管家代購/神燈 20 開 20 賣/couple code3 優雅）；workshop idle-cancel 不回包 bug 修復中；farm 3077 首讀 timeout 待觀察。
- **待辦**：小寶 farm seed_id（CDP 抓後填 config 才 skip 農場）；_CAPTURE_JS live 驗；ADB 模式（離線降級常駐 thread）= spec §4 phase 2 另開計畫。
