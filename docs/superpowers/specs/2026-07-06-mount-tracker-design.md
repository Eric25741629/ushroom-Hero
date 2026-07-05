# Mount Tracker（菇車幣車位坐騎追蹤器）Design Spec

Date: 2026-07-06
Status: DRAFT — 待使用者過目

## 1. 目標 / User Story

使用者要在 dashboard 上追蹤「指定目標玩家的坐騎現在停在誰的家園車位」，用來找出**已過 170 分鐘保護、打得到**的坐騎（車位戰鬥 PvP）。

- 目標玩家可在 dashboard **增刪**（用 roleId / UID / 名字）。
- 每小時自動掃描一次，找出每個目標的坐騎（每人最多 5 台）停在哪。
- 顯示：目標 → 每台坐騎（停在哪個房東家 / 家族 / 第幾格 / 已停多久），**>170 分標「可打」，未滿標倒數**。
- 掃描是純 WS（WSGameClient，不經瀏覽器），**借用 idle 裝置的帳號**，每個 WS call 冷卻 3 秒，權重高的先掃，某目標 5 台全找到就停掃該目標。

## 2. Non-Goals（YAGNI，v1 不做）

- **不做跨界（跨界停車）車位掃描**：v1 只掃家園私人車位（`car_park_info type=0`）。實測 13/15 台都在家園車位；跨界池留待 v2（會另外標記「可能在跨界，未掃」）。
- **不做主動攻擊/搶車位**：只做「找到 + 顯示」，打不打由使用者手動。純唯讀掃描。
- **不做即時 push**：dashboard 用輪詢（既有慣例，無 dashboard push）。
- **不做 ticket 自動刷新**：ticket 死掉就本輪跳過該裝置（refresh_creds 需 adb 且會踢 session）。
- **不做每輪全服重掃**：每輪有時間預算（~20 分）+ 權重優先 + 滾雪球，不追求單輪 100% 覆蓋。
- **不改動 per-device 喚醒流程**：掃描器是獨立 master-only daemon，對既有 runner / wake / sleep 零副作用（唯一共享資源 = idle 帳號的 WS session，由 idle 閘保護）。

## 3. 資料模型

單一全域檔 `ws_state/_mount_tracker.json`（`ws_token.state.save_state("_mount_tracker", data)` 原子寫；服務內以 `threading.RLock` 保護 read-modify-write，因 dashboard thread 與掃描 thread 都會動它）。

```jsonc
{
  "targets": [                       // dashboard 可增刪
    {"role_id": 89559731801117, "name": "星夜楓", "uid": "C010D"}
  ],
  "known_players": {                 // 滾雪球累積的候選房東
    "<role_id>": {
      "name": "...", "guild": "羽皇居|null", "level": 200|null,
      "coin": 145|null,              // 菇車幣加成%（掃到其車位時算）
      "last_scanned_ts": 1783000000, // 上次讀其車位的時間
      "host_hits": 3                 // 最近 K 輪當過任一目標房東的次數（權重用）
    }
  },
  "results": {                       // 每個目標當前找到的坐騎
    "<target_role_id>": [
      {"owner_role_id": ..., "owner_name": "...", "owner_guild": "...",
       "pos": 3, "start_time": 1783267198, "found_ts": 1783270000}
    ]
  },
  "last_run": {"ts": ..., "scanned": 1600, "found": 13, "duration_s": 1180,
               "devices_used": ["7fe98fc6", ...], "note": "..."},
  "running": false
}
```

- 開關 `mount_tracker_enabled` 存 `dashboard_settings.json`（與其他 toggle 同店，走 admin API pattern），**不**放這個檔。
- `.runtime/` 不用（改用 `ws_state/`，因掃描器屬 ws_token 世界、且 `ws_token.state` 已保證原子寫）。

## 4. 權重公式（掃描順序，高的先掃）

```
weight(player) = 100 * min(host_hits, 5)     # 最近當過目標房東 → 最強訊號（目標會回同一批高價車位）
               +   1 * (coin or 0)           # 目標愛停高菇車幣車位
               +  30 * same_guild_as_target  # 與任一目標同盟
               + 0.2 * (level or 0)
```

- `known_players` 依 weight 由高到低排序，取前 N（時間預算內）掃。
- 新雪球玩家初始 `host_hits=0`；若已知 guild/coin 則帶入，否則靠下次掃到其車位補齊。

## 5. 掃描週期演算法

每小時 `Event.wait(3600)` 醒來；若 `mount_tracker_enabled`：跑一輪 `scan_cycle()`。

```
scan_cycle():
  targets = load targets;  if empty: return
  # 5a. bootstrap known_players（僅當 known_players 過小，一次性）
  if len(known_players) < BOOTSTRAP_MIN:
      guild_scan()   # guild_search(7427) 全頁 → level>=5 家族 members(7440) → role_others(780) 補 level/guild
                     # 只在有 idle 裝置時做；~數百 call；建立初始候選集
  # 5b. rank
  queue = known_players sorted by weight desc  (+ 一律含 targets 自己的 role_id 當房東)
  found = {t: [] for t in targets}
  deadline = now + CYCLE_BUDGET_S (預設 1500s ≈ 25min)
  for owner in queue:
      if now > deadline: break
      if all(len(found[t]) >= 5 for t in targets): break        # 全找到就停
      dev = pick_idle_device()                                   # 見 §6
      if dev is None: sleep_short(); continue                    # 沒 idle → 緩緩
      spaces = read_lot(dev, owner)                              # car_park_info type=0；每 call 前 Event.wait(3.0)
      for s in spaces:
          if s.role_id in targets and len(found[s.role_id]) < 5:
              found[s.role_id].append({owner, pos, start_time, ...})
          upsert known_players[s.role_id]  (snowball: name; guild/coin later)
      known_players[owner].coin = lot_bonus(spaces.skin_list)    # 順手算 coin 供權重
      known_players[owner].last_scanned_ts = now
  # 5c. persist
  update host_hits (owners that hosted a target this cycle += 1; others decay)
  save results + known_players + last_run
```

- 冷卻：每個 WS call 之間 `Event.wait(3.0)`（可被停機打斷）；多台 idle 裝置各自並行、各自 3s。
- 早停：某目標 `found` 達 5 就不再比對它；全部達 5 → 整輪結束。
- 錯誤：`read_lot` 逐 call try/except，逾時/CMD_ERROR → 跳過該 owner；被踢 → 釋放該裝置、換下一台。

## 6. 借用 idle 裝置（安全機制）

候選裝置 = `["7fe98fc6","emulator-5554","emulator-5556","emulator-5560"]`（皆 web_h5、有完整 creds）。
注意 `emulator-5554` 與 `fc65396d` 同 roleId（同帳號），不可同時登入。

`pick_idle_device()` 對每台檢查（照既有 `online_check_service._is_idle` + `online_monitor._about_to_wake`）：
```
idle       = no bot_state row OR status==OFFLINE OR task in ("休眠中","啟動後休眠")
not_waking = next_wake_at is None OR (next_wake_at - now) > 120     # _HANDOFF_LEAD_SEC
not_held   = ws_session.is_active(ip) == False
not_human  = role_id not in online_monitor.resolve_protected_role_ids()
safe = idle and not_waking and not_held and not_human
```
- 借用：`WSGameClient(load_creds(dev))` one-shot；**透過 `ws_session` 註冊**（或維持一個掃描器自有 active-borrow set 並讓 `ws_phase._dashboard_ws_active` 也查它），使喚醒路徑 `wait_for_dashboard_ws_release` 禮讓而非互踢。
- 連線後每台可連續讀多個 owner（各 call 間 3s），但**每次讀前重檢 `not_waking`**；一旦該裝置即將醒來 → `close()` 釋放。
- 被踢（`client.is_kicked()`）/ `WSLoginError` → close、標記該裝置本輪不用、換下一台。
- 全程 `finally: client.close()`；掃描保持短連線，壓小互踢窗口。

## 7. Dashboard

- Blueprint `control_panel/routes_mount_tracker.py`（`bp = Blueprint("mount_tracker", __name__)`；註冊進 `control_panel_app.py` import tuple + register loop）。
- 頁面 `GET /mount-tracker`（`@_fly_pet_auth`，`render_template("mount_tracker.html", frontend_version=_get_frontend_version())`）。
- API（皆 `@_fly_pet_auth`，回 `{"status":"ok"|"error",...}`）：
  - `GET  /api/mount_tracker/results` — 輪詢用；回 targets + 每目標坐騎清單（含 owner/guild/pos/start_time）+ 掃描狀態（enabled/last_run/running）。
  - `GET/POST /api/mount_tracker/targets` — 列出 / 新增 / 移除目標（body 帶 role_id 或 UID 或 name；UID→roleId 用低 20bit hex 反解 + friend_search 驗證）。
  - `POST /api/mount_tracker/toggle` — enable/disable（寫 `dashboard_settings.set_mount_tracker_enabled`；可 `@require_admin`）。
  - （選配）`POST /api/mount_tracker/scan_now` + `GET /api/mount_tracker/job/<id>` — 手動立即掃一輪（job pattern，`UI.pollJob`）。
- 模板 `templates/mount_tracker.html`：`{% include '_assets_head.html' %}`、表格用 `UI.esc`、`setInterval` 輪詢 results、**倒數在前端每秒 tick**（`countdown = 170*60 - (now - start_time)`；>0 顯示倒數，<=0 顯示「可打 已停 Xh」）。
- （選配）dashboard 主頁加一個 tab（iframe `/mount-tracker`）。
- UI 完成後跑 `dashboard-ui-review` skill（設計系統 / 對比度 / a11y）。

## 8. 資料來源與既有元件重用

- `ws_token/carpark.py` `parse_car_park_info(body)` / `CMD_LOT_INFO=12801`；`Space`：pos#1, role_id#2, mount_id#3, mount_lev#4, start_time#5, car_master_name#9。
- 可打判定：`elapsed = now - start_time`；`attackable = elapsed > 170*60`；倒數 `= 170*60 - elapsed`（前端算）。
- 車位加成公式（已驗證，閃電 145/68/72 完全吻合）：讀 `car_park_info` 的 `skin_list#8`，對 `docs/protocol/PARKING_DESIGN_CATALOG.json` 每個裝飾照 `desc` 拆 `##N` 子句，凡含「菇車幣/改裝點/奇遇」關鍵字者各加 `desc_parm[N-1]`。→ 新增小模組 `ws_token/parking_bonus.py`（純函式 + 載 catalog）。
- guild 名不在 `Space` 內；由 `known_players[owner].guild` 提供（guild scan 時填）。
- cmd id（c2s==s2c）：guild_search 7427、guild_members_info 7440、role_others 780、car_park_info 12801。

## 9. 檔案清單（實作時 plan 會拆成 tasks）

新增：
- `ws_token/parking_bonus.py` — 車位加成公式（catalog 解析）+ 純函式。
- `ws_token/mount_scan.py` — 純 WS 讀：guild scan、lot 占用讀、occupant 解析（重用 carpark + role_others helper）。
- `runtime_services/mount_tracker_service.py` — master-only hourly daemon（idle 借用、冷卻、權重、早停、雪球、持久化、state 讀寫 helper 給 blueprint 用）。
- `control_panel/routes_mount_tracker.py` — blueprint（頁面 + API）。
- `templates/mount_tracker.html` — 前端頁。
- `tests/test_mount_tracker.py`（+ 可能拆 `test_parking_bonus.py`）— 權重排序、早停、雪球 upsert、bonus 公式（閃電固定樣本斷言 145/72/68）、idle 判定、170 分界。

改動：
- `utils/dashboard_settings.py` — `get/set_mount_tracker_enabled`。
- `control_panel_app.py` — 註冊 blueprint（import tuple + register loop 各一行）。
- `new_main_v2.py` — master 區塊 `ensure_mount_tracker_started()`（接在 online_check / monitor 之後）。
- `ws_token/creds.py`（若需）device 白名單常數；或常數放服務內。
- （選配）`templates/dashboard.html` — 加 tab。

## 10. Global Constraints（實作 plan 帶入）

不加新套件；JSON 讀 `utf-8-sig`；pytest 必指定檔案（hook 擋裸 pytest）；只 stage 動到的檔（絕不 `git add -A`）；不 push、不加 attribution footer；commit 不可 `--no-verify`。無 hot-reload：改 runtime 模組要重啟 `new_main_v2.py`。

## 11. 待確認 / Open Questions

1. **時間預算**：預設每輪 `CYCLE_BUDGET_S=1500`（~25min）、`SCAN_TOP_N≈1600`。可調。
2. **UID→roleId 反解**：UID 只帶 roleId 低 20bit（合服後非唯一），新增目標若用 UID，需 `friend_search`(3850) 回傳多筆時讓使用者挑（或要求直接貼 roleId）。v1 建議：新增目標優先用 roleId；UID 僅在唯一時接受，多筆則提示。
3. **bootstrap 來源**：首次 known_players 是否用一次 guild scan 建（~6-10min），或接受純雪球慢啟動？建議 guild scan bootstrap（較快見效）。
4. **toggle 權限**：`@require_admin` 還是一般登入即可？（PvP 敏感，建議 admin。）

---
請過目。確認後我寫 plan（拆 tasks + 每 task 測試碼/實作規格/commit message），開 worktree，Opus 逐 task 實作、我審。
