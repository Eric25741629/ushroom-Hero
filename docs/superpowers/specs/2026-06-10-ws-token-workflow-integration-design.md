# ws_token 接入主 workflow 設計（取代 Playwright 可替代任務）

日期：2026-06-10
狀態：使用者已逐節核可（送禮批次法 / 第三 backend / 序列化補跑 / skip 等價表 / ADB 模式；
2026-06-10 修訂：使用者拍板**不做 online_guard 前置檢查，直接登入**）
Pilot 裝置：小寶 `7fe98fc6`（bot 內的 web_h5 裝置；非使用者手機上的常用帳號）

## 0. 目標與範圍

- 每輪喚醒週期改為兩階段：**WS 階段先跑**（純 WebSocket，不開瀏覽器/App），跑完斷線後
  **Playwright/App 階段補跑** WS 蓋不到的任務；WS 成功的任務由 skip 表跳過。
- 伴侶送禮（奶茶 1106 / 玫瑰 1614）改為**每天送光全部**，以 **10 為單位批次送**（使用者指定）。
- 家園三件（守護靈 spirit / 加工坊 workshop / 伴侶 couple）接進 `ws_token/runner.py`。
- web_h5 與 ADB 裝置都支援，token 刷新路徑不同（§6/§7）。
- 先 pilot 小寶一台，穩定後逐步擴。

不做（YAGNI）：`backend:"ws_token"` 新裝置類、WS 自動挖礦、dashboard 新 UI、
買召喚貨幣（goods 800003 不存在）。

## 1. 分支策略

- 基準：`feat/ws-token-home`（已是 `feat/ws-token-integration` 的 superset——runner 含
  claim_marry_tasks + 家園三件模組）。實作前用 `git merge-base` 確認 integration 沒有
  home 缺的 commit，有就先 merge 進來。
- 新工作分支 `feat/ws-backend`（從 home 切）。主 repo 修改（new_main_v2 /
  daily_pipeline / config_manager）同分支。完成後一次 merge 回 main。

## 2. ws_token 端新增

### 2.1 couple.give_all_in_hand（送光全部，每批 20，server 封頂）

使用者 2026-06-10 確認：**server 對超量 num 自動封頂到庫存**，批次單位用 20。

```python
give_all_in_hand(client, *, friend_id, flower_id,
                 batch=20, max_batches=20, spacing=0.2) -> dict
# {batches_ok, stopped_reason}
```

- 迴圈 `give_flower(num=20)`：每批 server 自動送 min(20, 庫存)；
- 庫存歸零後下一批回 `0x0201 code 3 物品不足` = 正常結束訊號（非錯誤）；
- 封頂吃掉殘量，**不需 1 顆收尾**；護欄 `max_batches=20`（上限 400 個）；
- `0x0201 code 369 = 贈送成功`（成功走錯誤通道）已由 `_mutate` 處理；
- 不用大數一發送光（使用者否決 num=999）。

### 2.2 runner 接三個新任務

`TASK_ORDER` 改為：`main_tasks, league_solo, redpack, idle_reward, turntable, farm,
dungeon, guild, steward, carpark, spirit, workshop, couple, lamp`

| 任務 | 行為 | 閘門 |
|---|---|---|
| spirit | `spirit.draw_all_free()` 免費召喚 | 無（只用免費次數） |
| workshop | `workshop.rotate_team_recipes()`：**每 12 小時輪換一次配方**（使用者指定；兩類別 8001 脆脆餅乾 ↔ 8005 精英拼盤，輪流指派給各小隊加工 6002/6003），走已驗的 `switch_recipe`（cancel → dining_hall → choose，wire id 用 `configWorkshop.id`）。上次輪換時間/parity 存 `ws_state/<device>.json`；12h 未到 → skip。手動加工(6001)不動；collect/crops_transfer 仍 live-unconfirmed，不在本期 | `ws_token.workshop_rotate` |
| couple | `read_partner` 無伴侶即 skip；有 → 奶茶+玫瑰各 `give_all_in_hand`；默契考驗已在 main_tasks（claim_marry_tasks, type 6）；戒指錘鍊 `forge_ring_until_empty`（消耗全部真愛之石） | 送禮 `couple_gifts`（預設 on）；錘鍊掛 `spend` |

### 2.3 不做在線前置檢查（使用者拍板）

小寶不是使用者手機上的帳號，使用者明確指示**不用 online_guard 在線檢查，直接登入**
（WS 登入若踢掉手機端 session 可接受）。若日後要對某裝置加禮讓，再用現成的
`ws_token/online_guard.py` 補，不在本設計範圍。

## 3. 主 repo 接入

### 3.1 config（bot_config.json 裝置級 + config_manager DEFAULT_DEVICE_CONFIG）

```json
"ws_token": {
  "enabled": true, "spend": true, "open_lamp": true,
  "farm": {"seed_id": null, "team_cfg_id": null},
  "dungeon_sweeps": [], "carpark_target": null,
  "couple_gifts": true, "forge_ring": false,
  "workshop_rotate": true
}
```

### 3.2 WS 階段插入點（new_main_v2.py）

- 位置：喚醒後、**Playwright 瀏覽器/App 啟動之前**（順序關鍵：WS 登入踢頁面，先 WS 後開）。
- 整段 try/except：WS 階段任何失敗 → 空 skip-set → 後段全跑（**天然降級**）。
- `daily_pipeline.DailyContext` 加 `ws_done: frozenset[str]`，`_run_tasks` 逐項查表跳過。
- skip-set 只收 RunReport 中**成功**的任務鍵（errors 內的不收）。

### 3.3 Skip 對照表（含使用者確認的等價關係）

| WS 任務成功 | Playwright/App 跳過 |
|---|---|
| redpack | #0 紅包檢查 |
| farm | #2 農場任務 |
| idle_reward | #3 點擊寶箱 |
| guild | #4 家族任務 |
| spirit | #5 領取守護靈 |
| steward | #7 商店購買（使用者確認 Store==管家代購） |
| main_tasks | #12 所有日常任務 |
| dungeon（有配 sweep） | #15 萬神試煉 |
| couple | #18 好友每日禮物（使用者確認 ==伴侶送禮） |
| lamp | #19 開神燈 |
| turntable | #20 轉盤金幣 |

不跳：#0.5 停車調和（WS 只停跨界車，Playwright 還管一般位/收車）、地獄之門、
抽技能夥伴、坐騎強化、每日加速、競技場、挖礦、武道會、雕像、航海、龍骸聖域、
雲端戰鬥、雙週副本。

## 4. ADB 裝置模式（phase 2，pilot 之後）

每輪喚醒：

```
ADB 在線？
├─ 是 → 現存 token 試 WS 登入
│        ├─ 成功 → WS 已驗任務 → 斷線 → 開 App 補跑剩餘（挖礦/戰鬥類...）
│        └─ WSLoginError(過期) → adb_token_login 冷啟重撈(~30s+,會踢) → WS → App 補跑
└─ 否 → 現存 token 跑純 WS（剩餘任務本輪放掉）
         └─ token 也過期 → 本輪跳過 + warning，等裝置回線再重撈
```

- **Lazy refresh**：token 能登就直接用（實測 ≥6h 有效，絕對 TTL 未測），只在登入失敗
  且 ADB 在線時才冷啟重撈——效果同「跑到過期為止」，省每輪 30s+ 冷啟與無謂踢 session。
- **常駐 thread**：`ws_token.enabled` 的裝置不再依賴 ADB 掃描才有 thread；
  thread 每輪自查 ADB 在線與否決定走哪條分支。

## 5. Ticket 生命週期（web_h5）

- creds 檔：`auth_state/_auth_capture_<device>.json`（`ws_token/creds.load_creds`）。
- **自癒迴圈**：每輪 Playwright 階段遊戲載入完成後，用 CDP 讀 `LoginDataCache`
  （同 `tools/_auth_capture_probe.py` 邏輯，~2s、不踢 session）把新 ticket 寫回 creds 檔
  → 下輪 WS 階段永遠拿到 <2h 的 ticket（小寶 odd-hour 喚醒）。
- WS 登入失敗 → 本輪 WS 空轉、Playwright 全跑並刷新 ticket → 下輪自動恢復。

## 6. 錯誤處理摘要

| 情境 | 行為 |
|---|---|
| WS 階段任何例外 | 空 skip-set，後段全跑（降級） |
| WSLoginError | 同上；web 靠 §5 自癒、adb 走 §4 重撈 |
| kicked=True（執行中被踢） | 任務照 _safe 收 error；後段照常 |
| 送禮 code 3 | 正常結束訊號，非錯誤 |
| 批次送禮迴圈 | max_batches 護欄防無限迴圈 |

## 7. 測試計畫

離線（fake client）：
- `give_all_in_hand`：10→code3→1→code3 邊界、開局即 code3、369 成功通道、護欄上限。
- runner wiring：TASK_ORDER 三新任務、couple 無伴侶 skip、各 config 閘門。
- 主 repo：`ws_done` skip 生效（stub pipeline）、WS 失敗→全跑、config 預設值。

Live（小寶，依 manual-hold 慣例取得獨佔）：
1. 單獨 live 驗批次送禮全流程（封頂行為使用者已確認，驗 code 3 結束訊號與摘要正確）。
2. `python -m ws_token.runner --device 7fe98fc6`（含三新任務）全跑。
3. 主 repo 完整 wake cycle：看 WS 階段 → skip log → Playwright 補跑。
4. 觀察數日 → 擴到下一台。

## 8. 開放問題（2026-06-10 使用者答覆後更新）

- ~~give_flower 超量行為~~ → **已定案：封頂**，批次 20（§2.1）。
- ~~workshop 食物選擇策略~~ → **已定案：兩類別 12hr 輪換一次**（§2.2）。
- ticket 絕對 TTL → **用 production log 追蹤**：WS 登入（成功與失敗）都 log
  ticket age（now − loginTime/_captured_at），TTL 從長期 log 浮現，不做專測。
- workshop collect（crops_transfer 的 material_id/num 來源）仍 live-unconfirmed，
  本期不做收成品。
- 小寶 farm `seed_id`（免費種子 id）：live 驗證時 CDP 抓一次填進 config；
  填好前 `農場任務` 不進 skip 表（mapping 對 farm 採條件式 skip）。
