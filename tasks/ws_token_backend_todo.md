# ws_token backend — 狀態 / 交接 (更新 2026-06-09 夜)

> 這份是 ws_token 後端的**當前權威狀態**。設計全文 `docs/superpowers/specs/2026-06-09-ws-token-task-batch-design.md`
> (含 §12 live 驗證更正);整合說明 `docs/WS_TOKEN_INTEGRATION.md`;教訓 `tasks/lessons.md` 2026-06-09 節。
> memory:`project_ws_token_backend`、`reference_ws_proto_schemas`、`reference_adb_sdk_token_extraction`。

## 是什麼
新後端 `ws_token/`:用 ADB/CDP 撈的登入 ticket **直連遊戲 WebSocket 送 protobuf 跑任務**,不開 App、不靠螢幕(無 OCR/CNN)。純 Python 自組 protobuf+XOR+framing(`ws_token/codec.py`,與 LIVE PoC byte-parity)。

## 地基(DONE)
- `ws_token/client.py` `WSGameClient`:active byte → role_login(257)→ 背景心跳(260,5s)→ `call`/`call_for`/`set_push_handler` → reconnect/close。
- `ws_token/codec.py`、`creds.py`、`transport.py`。`tests/fakes/ws_fakes.py`(FakeTransport + responder)。

## 任務模組(全部 DONE:離線 build + 測試 + commit + live 驗證)

| 任務 | 模組 | cmd 重點 | 測試 | live 驗證(5554 @google;部分小寶) |
|------|------|----------|------|------|
| 領紅包 | `redpack.py` | red_brief_list 0x2605 / red_grab 0x2603 | ✅ | 早先已 live 領到 |
| 開神燈 v2 | `lamp.py` | equip_box_open_all 0x0509 / equip_wear/shop | ✅ | combo→套裝(equip-path 未 live 換過) |
| **家族大廳** | `guild.py` | 捐獻 7441 / 寶箱 7459-7460 / 求助 7452-7454 | 27 | 讀✅、捐獻✅(5次到上限 err159)、求助解析✅、寶箱休眠 skip |
| **家園管家採購掃蕩** | `steward.py` | worker_common 0x49(info/buy_service/shopping/dungeon_sweep) | 26 | 讀✅、購物掃蕩✅、副本掃蕩✅(chapter2→52神燈)、`sweep_list[].id=chapter id` |
| **魔法劇場+烈焰山洞寶箱** | `league_solo.py` | dungeon_league_solo_get_reward 0x0E0F {type1-4} | 19 | 讀✅、領✅(5554 領94幻劇+3熔岩 / 小寶領94+18) |
| **主畫面領任務** | `main_tasks.py` | task 0x0A01-0A0A(push-based collect + claim) | 23 | 讀✅(181任務)、領每日任務✅(7/7)、功績門檻未達被退(graceful) |
| **挖礦(離線層+adapter)** | `mining.py` + `mining_adapter.py` | home_mine_info 0x0C01 / use_goods 0x0C03 | 25 | 讀盤面✅、adapter→7×6 grid✅、plan_v4 跑通✅(**未真挖,human-supervised**) |

**commit**:spec 4670bf1a / worker_common schema c5d0d48e / guild aacfb37b / steward 86816f63 / schema(task/collection/home/act2) b603fd1b / guild_smoke fix 9e3bb6b6 / docs a6bd56c0 / league_solo 53dc4701 / main_tasks f1c0a7e0 / mining c9c465f7。

## 協議 schema(欄位號權威,docs/protocol/*_PROTO_SCHEMA.json)
GUILD / DUNGEON / EQUIP / ARENA / SHOP / RED / TYPE(既有)+ **WORKER_COMMON / TASK / COLLECTION / HOME / ACT2(本次 CDP 匯出)**。重匯工具:`tools/_steward_dump.py`、`tools/_schema_dump.py`(gitignored)。
家園管家 config:`ws_token/data/housekeeper_config.json`。

## 帳號 / 裝置對照
| 暱稱 | device | 帳號 | 備註 |
|------|--------|------|------|
| 小寶 | `7fe98fc6` | kenken@gmail `27399634` | web_h5,CDP 9226;**神燈 71 萬**;管家在期 |
| 閃電 | `emulator-5554` | @google `27353216` | 主要 live 驗證機;管家在期 |
| 菜雞 | `emulator-5556` | — | **未連上 adb / 無 ticket**,本次無法驗 |
| 手機 | `fc65396d` | @google user0 + @apple user999 | adb 連著(TLS);可 `adb_token_login` 刷 @google/u999 ticket |

ticket 檔:`auth_state/_auth_capture_<device>.json`(reusable 數小時)。

## 還沒做 / 待續
- **整合 runner**(`ws_token/runner.py`,進行中 by subagent):一次登入跑完一裝置的每日 ws_token 任務(免費領取預設,`--spend` 才花費)。完成後 commit。
- **接進 bot**(`device_wrapper`/`new_main_v2`):**尚未做,也不該半夜自動接**(會踢同帳號 session)。整合步驟見 `docs/WS_TOKEN_INTEGRATION.md`,等使用者審後再接。ticket 刷新 + 同帳號異地登入錯開排程要一起定。
- **挖礦 live 執行**:adapter/planner 對真實盤面通了,但**真挖未做**(要人盯,且 inventory 0x0402 push 需確認現量;adapter viewport/terrain/goods_id 有 live-calibration 缺口)。

## Live-confirm 清單(已知未驗/待釘)
- steward `buy_service.day_num` 續期語義:30=天數 vs 檔位 index(兩服務在期沒機會驗;真 renew 前先驗)。**使用者:續期買 30 天檔位。**
- steward `sweep_list[].level/times` 來源(times=1/level=1 server 收;多章/高 level 自動推導未做)。
- main_tasks `task_req_daily_box` 成功 reply cmd(假設 2566;若改走 push 需調整);功績「可領」判定需 config 門檻(現以 get_id!=now_id 推測,會誤觸 0x0201 但 graceful)。
- guild 求助 `help_type` 查詢值(預設 0 可取回);捐獻每日上限(實測 5 次到 err159);寶箱休眠時 server 不回(已容錯 skip)。
- 各任務失敗碼:**0x0201 error_code=159 = 已領/已滿**(捐獻、league_solo 共用),當「已領」跳過不 abort。

## 怎麼跑(smoke,dry-run 預設)
```
python -m ws_token.guild_smoke       --device emulator-5554            # 讀;--donate/--treasure/--answer-help 才動作
python -m ws_token.steward_smoke     --device emulator-5554            # 讀;--shopping/--sweep id:level:times/--renew
python -m ws_token.league_solo_smoke --device emulator-5554            # 讀;--claim [--types 1,2,3,4]
python -m ws_token.main_tasks_smoke  --device emulator-5554            # 讀(收 push);--all/--claim-tasks/...
python -m ws_token.mining_smoke      --device emulator-5554            # 唯讀盤面+grid+plan(無真挖)
python -m ws_token.runner            --device emulator-5554 [--spend]  # (建中) 一次跑完每日任務
```
⚠ WS 登入會踢同帳號 session;在借測機/已授權可踢的帳號上跑。
