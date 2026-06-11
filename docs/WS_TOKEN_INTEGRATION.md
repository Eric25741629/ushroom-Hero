# WS Token 後端整合指南 (`ws_token.runner.run_device`)

把 `ws_token` 的「單裝置每日任務 orchestrator」(`ws_token/runner.py`)接進
bot 的目前實作說明。WS 可作為兩種入口：

- `game_actions.ws_phase`: ADB/Web H5 喚醒後、Playwright 前先跑一輪 WS，成功項
  由 Playwright daily pipeline 跳過。
- `runtime_services.ws_runner_service`: `backend: "ws_token"` / `use_ws_runner`
  的純 WS 裝置 loop，不啟動 ADB/Playwright daily pipeline。

## 1. `run_device` 是什麼

```python
from ws_token.runner import run_device, RunReport

rep: RunReport = run_device(device, spend=False)   # 免費讀取 + 領取
rep = run_device(device, spend=True,               # 額外送花費動作
                 sweep_list=[(1, 5, 10)])           # 副本管家章節(選填)
rep = run_device(device, mining_config={            # 挖礦 opt-in
    "enabled": True,
    "allow_bomb": True,
    "allow_drill": True,
    "max_steps": 200,
})
```

- 用 `load_creds(device)` 載入 `auth_state/_auth_capture_<device>.json` 的 ticket。
- 建 **一個** `WSGameClient`、`connect()` 一次(背景單一心跳)、跑完所有任務、
  `finally close()`。
- 每個任務各自 `try/except`(含 `WSTimeoutError`):**一個任務失敗 / 休眠不影響
  其他**,結果或錯誤摘要收進 `RunReport`。
- 預設 `spend=False`:只跑免費讀取 + 領取,**不送任何花費動作**。
- `mining` 現在可由 `mining_config.enabled=True` opt-in。它使用同一個 WS client
  接到的 `0x0402` 庫存 snapshot；若本輪沒有拿到鎬子現量，會 skip，不會猜。

### 任務順序與花費閘門

| 順序 | 任務 | 免費 (`spend=False`) | 花費 (`spend=True` 才送) |
|------|------|----------------------|--------------------------|
| 1 | `main_tasks` | `collect_state` → `claim_daily_tasks` + `claim_daily_box` + `claim_weekly_box` + `claim_achievement` | — |
| 2 | `league_solo` | `claim_available`(領 type 1-4 寶箱) | — |
| 3 | `guild` | `help_all`(求助) | `donate_until_capped`(捐獻);寶箱 `open_all_treasure` 只在 `list_treasure` 回報有 round 時(event-gated,休眠 skip) |
| 4 | `steward` | `read_info` | `run_shopping`(購物管家);`run_dungeon_sweep`(副本管家,需 `sweep_list`);`renew` 只在服務過期時 |
| opt-in | `mining` | — | `mine_until_pickaxe_empty`;需 `mining_config.enabled=True`，炸彈/鑽頭另需 `allow_bomb` / `allow_drill` |
| opt-in | `lamp` | — | `open_lamp=True` 才開神燈 |

> `guild` 寶箱與 `steward` 續期都是**雙重閘門**:`spend=True` 之外,還要事件
> 活躍(寶箱有 round)/ 服務已過期(續期)才會真正送出。`run_dungeon_sweep`
> 第三重閘門:必須提供 `sweep_list`(steward 不自推 level/times),否則即使
> `spend=True` 也跳過。

### `RunReport` 結構

```python
@dataclass(frozen=True)
class RunReport:
    device: str
    login_ok: bool
    spend: bool
    tasks: dict[str, Any]    # {task_name: 該任務 orchestrator 的回傳摘要}
    errors: dict[str, str]   # {task_name | "login": "ExcType: msg"};成功為 {}
```

- `tasks` 鍵為成功跑完的任務名;`errors` 鍵為失敗的任務名(或 login 失敗時的
  `"login"`)。
- login 失敗:`login_ok=False`、`errors={"login": ...}`、`tasks={}`,且不跑任何任務。

### CLI

```bash
python -m ws_token.runner --device 7fe98fc6              # 免費讀取 + 領取
python -m ws_token.runner --device 7fe98fc6 --spend      # 加捐獻 / 採購 / 續期
python -m ws_token.runner --device 7fe98fc6 --spend --sweep 1:5:10   # 加副本掃蕩
python -m ws_token.runner --device 7fe98fc6 --mine --mine-allow-bomb --mine-allow-drill
```

退出碼:`login_ok` 為 0,否則 1。

## 2. bot 設定

### 2.1 `bot_config.json` 新增 `ws_token` 裝置

沿用既有 per-device 結構,把 `backend` 設為 `"ws_token"` 或設
`use_ws_runner: true`。`ws_token` 裝置不需要 `web_url` / `web_profile_dir` /
`web_channel` 等 Playwright 欄位，只需要一個能對應到
`auth_state/_auth_capture_<device>.json` 的裝置 key，以及排程欄位:

```jsonc
"7fe98fc6": {
  "backend": "ws_token",
  "name": "小寶(WS)",
  "wake_hour_parity": "odd",        // 與其 web_h5/ADB 雙胞胎錯開(見 §2.3)
  "wake_minute_offset": 5,
  "ws_token_spend": false,
  "ws_token_mining": {
    "enabled": true,
    "allow_bomb": true,
    "allow_drill": true,
    "max_steps": 200
  }
}
```

`runtime_services.ws_runner_service.run_ws_device_cycle()` 會把
`ws_token_mining` 原樣傳給 `run_device(..., mining_config=...)`。設定不存在
或 `enabled=false` 時不跑挖礦。

WS-first phase 裝置則使用 nested `ws_token`：

```jsonc
"7fe98fc6": {
  "backend": "web_h5",
  "ws_token": {
    "enabled": true,
    "spend": false,
    "open_lamp": false,
    "mining": {
      "enabled": true,
      "allow_bomb": true,
      "allow_drill": true,
      "max_steps": 200
    }
  }
}
```

`game_actions.ws_phase.run_ws_phase()` 會把 nested `ws_token.mining` 傳入 runner。
當 `RunReport.tasks["mining"]` 成功且不是 self-skipped，Playwright 階段會跳過
`"挖礦任務"`。

### 2.2 裝置 thread 呼叫方式

`ws_token` 裝置一輪 = 一次 `run_device()`:醒來 → `run_device` →
睡到下一個 wake。範例(僅示意,非要求落地的程式碼):

```python
# 在 new_main_v2.py 的 device-thread 派發點,for backend == "ws_token":
from ws_token.runner import run_device

rep = run_device(
    device,
    spend=device_cfg.get("ws_token_spend", False),
    mining_config=device_cfg.get("ws_token_mining") or None,
)
logger.info("ws_token %s login_ok=%s tasks=%s errors=%s",
            device, rep.login_ok, list(rep.tasks), list(rep.errors))
# rep 可推到 dashboard / push server 當該裝置的狀態摘要。
```

`run_device` 是同步阻塞、會自行 `close()`,所以**不需要**接 `_running_threads`
的 web shutdown 路徑(`shutdown_web_devices()`)——它沒有長駐 Playwright session。

### 2.3 ticket 刷新策略

`run_device` 內部用 `load_creds(device)` 讀現成 ticket,**自己不刷新**。ticket
可重用數小時(`AUTH_HANDSHAKE_SPEC` §7:`time` 欄位不被驗證),過期才需重刷:

| 裝置類型 | 刷 ticket 方式 |
|----------|----------------|
| 有 ADB 的模擬器 / 真機 | `python tools/adb_token_login.py --device <dev>`(冷重啟 App ~30s → 抓 logcat 明文 → 寫 `auth_state/_auth_capture_<device>.json`) |
| web_h5(無 ADB) | `tools/_auth_capture_probe.py`:對已開的 CDP port 注入 JS,從 cocos `netManager` 撈 ticket 寫同一份 JSON |

- ticket 過期的訊號:`run_device` 回 `login_ok=False` 且 `errors["login"]` 含
  `role_login failed: code=...` 或 timeout。可在 bot 端據此觸發一次刷新後重跑。
- `ws_token.creds.refresh_creds(device)` 是 `adb_token_login.py` 的薄包裝(會冷
  重啟 App),整合時可選用,但它會踢掉 App 當前 session(見 §2.4)。
- 建議策略:**先用既有 ticket 跑;只有 login 失敗才刷**,避免每輪都冷重啟 App。

### 2.4 同帳號異地登入(關鍵約束)

WS `role_login` 會**踢掉同帳號在 dashboard / App / web_h5 的現有 session**
(`--verify` / 任何 WS 登入都會)。因此:

- 同一個遊戲帳號**不能**同時跑 `ws_token` runner 和它的 web_h5 / ADB 雙胞胎。
- `ws_token` 裝置要與雙胞胎**錯開時段**:複用既有 wake 排程概念
  (`wake_hour_parity` 偶/奇數小時分流、`wake_minute_offset`),讓兩者醒來時段
  不重疊。例如 ADB 雙胞胎 `even` 小時、`ws_token` 設 `odd` 小時。
- 刷 ticket(`adb_token_login.py --verify` 或 `refresh_creds`)同樣會踢 session,
  排程時要把刷新視窗也算進錯開區間。

### 2.5 挖礦庫存與工具規則

鎬子現量抓得到，但來源不是 `0x0c01.max_num`。`max_num` 是上限；真實現量來自
`0x0402` item push：

- `evt=9800004`: 登入/道具 snapshot-update，例如 2026-06-11 read-only probe
  看到 `4001 -> 35`。
- `evt=9800001`: 消耗後的新數量。
- `evt=9800009`: 獲得後的新數量。

`ws_token.mining.InventoryTracker` 會接在 runner 的 composite push handler 上。
`mine_until_pickaxe_empty()` 只有在 tracker 已看過 `4001` 現量時才啟動；沒有
snapshot 時回 `{"skipped": "inventory snapshot missing"}`，避免猜數量。

工具對應：

| goods_id | 工具 | runner 預設 |
|---:|---|---|
| 4001 | 鎬子 | 允許，挖到 0 停 |
| 4002 | 鑽頭 | 需 `allow_drill=true` |
| 4003 | 炸彈 | 需 `allow_bomb=true` |

每一步都走 `home_mine_use_goods (0x0c03)` send-only，接著 refresh board 確認盤面
有變化，確認後才扣本地剩餘量並重新規劃下一步。

## 3. 已 LIVE 驗證的事實(供整合參考)

下列子任務已對真實 server 驗過(5554 `@google` 帳號 + 部分小寶 `7fe98fc6`):

- **login**:`role_login_s2c code=0` SUCCESS;心跳維持 130s+;337 隻寵。
- **main_tasks**:領每日任務(`task_commit`)已驗。讀取為 PUSH-based —— runner 在
  `connect()` 前掛 `TaskCollector` 當 `push_handler`,接 login 當下推來的
  `task_all` / `daily_point` / `weekly_box` 幀。
- **league_solo**:領寶箱(`get_reward` 0x0E0F)已驗,**一次領該 type 全部**;
  over-claim 回 error_code 159 視為「已領」跳過,不 abort。
- **guild**:捐獻(`guild_donate`)已驗,捐到上限回 error(觀測 code 159);求助
  / 寶箱 schema 已 byte-lock(`tests/test_ws_token_guild.py`)。
- **steward**:購物管家 sweep、副本掃蕩已對真實 server 驗過。`renew` 的
  `day_num` 語義(字面天數 vs 價格 tier 索引)仍標 `# live-confirm`,首次續期前
  請再確認(見 `ws_token/steward.py` `RENEW_DAY_NUM`)。
- **mining inventory**:`0x0402 evt=9800004` 會給登入/道具 snapshot；2026-06-11
  read-only probe 看到 `4001` 鎬子現量 35。`4001/4002/4003` 均為已確認工具 ID。

## 4. 未決 / 待確認點

- **guild 寶箱 / 求助 daily cap**:`donate_count` 平台、`daily_count` 等以 server
  s2c 回報為準(guard loop 收斂),非硬編;首次跑 `spend=True` 請觀察實際上限。
- **steward `RENEW_DAY_NUM=30`**:`day_num` 是字面 30 天還是 configHousekeeper
  tier 索引,續期前要 live-confirm。
- **副本掃蕩 `sweep_list`**:steward 不自推 `level/times/門票`,需 caller 提供
  `[(id, level, times[, use_ad]), ...]`;runner 預設不帶,要靠 `--sweep` /
  `run_device(..., sweep_list=...)` 餵入。`sweep_list[].id` 對應章節 id vs
  chapter_type、門票消耗仍標 live-confirm。
