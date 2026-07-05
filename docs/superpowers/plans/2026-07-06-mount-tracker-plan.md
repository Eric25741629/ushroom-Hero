# Mount Tracker 實作 Plan

Spec: `docs/superpowers/specs/2026-07-06-mount-tracker-design.md`（已核准 2026-07-06）
Date: 2026-07-06

## Global Constraints（每個 task 都適用）

- 不加任何新第三方套件（只用 stdlib + 專案既有模組）。
- 讀 JSON 一律 `encoding="utf-8-sig"`（多檔帶 BOM）。
- pytest 必指定測試檔（pre-hook 擋裸 pytest）；每個 task 只跑自己新增/相關的測試檔。
- 只 `git add` 該 task 真正動到的檔（**絕不 `git add -A`**：repo 有 ~80 WIP 檔 + `auth_state/` secrets）。
- 不 push、不加 attribution footer、commit **不可 `--no-verify`**。
- 無 hot-reload：改 runtime 模組要重啟 `new_main_v2.py` 才生效（收尾提醒使用者）。
- 純函式優先、依賴注入：凡需 WS / bot_state / 時間 / sleep 的邏輯，設計成可注入 fake，讓測試不碰 live device / Playwright / 真 socket。
- Traditional Chinese 註解與 log；函式簽名加 type hints。

## 介面契約（跨 task 共用，先固定簽名）

```python
# ws_token/parking_bonus.py
def lot_bonus(skin_list: list[tuple[int,int]]) -> dict[str,int]: ...   # -> {"coin","mod","spec","protect"}

# ws_token/mount_scan.py
CMD_GUILD_SEARCH=7427; CMD_GUILD_MEMBERS=7440; CMD_ROLE_OTHERS=780
def enc_guild_search(type_:int, page:int, key:str="") -> bytes: ...
def enc_guild_members(guild_id:int) -> bytes: ...
def enc_role_others(role_ids:list[int], source:int=1) -> bytes: ...
def parse_guild_search(body:bytes) -> dict: ...   # {"page_num":int,"guilds":[{guild_id,name,leader_id,member_num,guild_level}]}
def parse_guild_members(body:bytes) -> list[dict]: ... # [{role_id,name}]
def parse_role_others(body:bytes) -> dict[int,dict]: ... # {role_id:{level,server,power}}
def parse_lot_occupants(body:bytes) -> dict: ...   # {"skin_list":[(id,lev)],"spaces":[{pos,role_id,start_time,name}]}
def weight(p:dict, target_guilds:set[str]) -> float: ...  # p = known_players entry (+role_id)

# runtime_services/mount_tracker_service.py
class MountTrackerStore:            # ws_state/_mount_tracker.json，RLock 保護
    def __init__(self, device_key="_mount_tracker", state_dir=None): ...
    def get_targets(self)->list[dict]; def add_target(self,t:dict); def remove_target(self,role_id:int)
    def get_known(self)->dict; def upsert_known(self,role_id:int,**fields)
    def get_results(self)->dict; def set_results(self,results:dict); def set_last_run(self,info:dict)
    def snapshot(self)->dict                       # 供 dashboard 讀
def scan_cycle(store, reader, idle_picker, sleeper, now_fn, *,
               top_n=1600, budget_s=1500, cooldown_s=3.0, max_per_target=5) -> dict
    # reader(device, owner_role_id) -> parse_lot_occupants dict | None（None=讀失敗）
    # idle_picker() -> device_id | None（無 idle 回 None）
    # sleeper(seconds) -> None（測試注入 no-op）；now_fn()->float
def ensure_mount_tracker_started() -> None         # 冪等 daemon 啟動
```

---

## Task 1 — `ws_token/parking_bonus.py`（車位加成公式，純函式）

**檔案**：新增 `ws_token/parking_bonus.py`；新增 `tests/test_parking_bonus.py`。

**測試（先寫，AAA）** `tests/test_parking_bonus.py`：
```python
from ws_token.parking_bonus import lot_bonus, parse_desc_bonus
def test_multi_bonus_desc_splits_by_clause():
    # "菇車幣和改裝點收益提高##1%，獲取額外奇遇獎勵概率提高##2%" parm=[5,3]
    b = parse_desc_bonus("在本人私人車位獲取菇車幣和改裝點收益提高##1%，獲取額外奇遇獎勵概率提高##2%",[5,3])
    assert b == {"coin":5,"mod":5,"spec":3,"protect":0}
def test_battle_desc_contributes_nothing():
    assert parse_desc_bonus("在菇菇車位中戰鬥時攻擊提高##1%",[8]) == {"coin":0,"mod":0,"spec":0,"protect":0}
def test_flash_lot_bonus_matches_145_72_68():
    # 閃電(下不維力炸醬麵) 實測 skin_list -> 菇車幣145 改裝72 奇遇68（固定樣本，見 fixture）
    from tests.fixtures.mount_tracker_fixtures import FLASH_SKIN_LIST
    b = lot_bonus(FLASH_SKIN_LIST)
    assert (b["coin"], b["mod"], b["spec"]) == (145, 72, 68)
```
- Fixture：新增 `tests/fixtures/mount_tracker_fixtures.py`，含 `FLASH_SKIN_LIST`（閃電的 68 筆 (id,level)，見 spec §8；從 scratchpad `compute_bonus2.py` 輸出照抄）。

**實作規格**：
- `parse_desc_bonus(desc:str, parm:list|None) -> dict[str,int]`：`re.split(r'##(\d+)', desc)`；對每個 `##N`，取其前一段文字，凡含「菇車幣」→coin、「改裝點」→mod、「奇遇」→spec、「保護」→protect，各加 `parm[N-1]`（越界或無 parm 記 0）。
- 載入 catalog：`docs/protocol/PARKING_DESIGN_CATALOG.json`（`utf-8-sig`），建 `(id,level)->parse_desc_bonus(desc,desc_parm)` 與 `id->level0` fallback。用 module-level lazy load（第一次呼叫才讀，避免 import 副作用）。
- `lot_bonus(skin_list)`：對每個 (id,lev) 查 catalog（(id,lev) 優先、否則 (id,0)），四類加總回 dict。
- 不觸網、不 import device/cv2/torch。

**commit**：`feat(mount-tracker): 車位加成公式 parking_bonus（閃電145/72/68 驗證）`

---

## Task 2 — `ws_token/mount_scan.py`（純 WS encode/parse + 權重）

**檔案**：新增 `ws_token/mount_scan.py`；新增 `tests/test_mount_scan.py`。

**測試**（用 `ws_token.codec` 自組 protobuf bytes 當輸入，不碰 socket）：
```python
from ws_token import codec
from ws_token import mount_scan as ms
def test_enc_role_others_repeated_ids():
    body = ms.enc_role_others([111,222], source=1)
    d = codec.walk(body)  # 兩個 field#1 + field#2
    assert [v for f,v in d if f==1] == [111,222]
def test_parse_guild_search_extracts_level_and_name():
    entry = codec.pb_uint(1,900)+codec.pb_str(3,"羽皇居")+codec.pb_uint(8,15)+codec.pb_uint(10,81)
    body = codec.pb_uint(3,90)+codec.pb_msg(4,entry)   # page_num=90, one guild
    r = ms.parse_guild_search(body)
    assert r["page_num"]==90 and r["guilds"][0]["name"]=="羽皇居" and r["guilds"][0]["guild_level"]==15
def test_parse_lot_occupants_reads_space_role_and_start():
    space = codec.pb_uint(1,3)+codec.pb_uint(2,555)+codec.pb_uint(5,1783267198)+codec.pb_str(9,"曇花一現")
    body = codec.pb_msg(7,space)+codec.pb_msg(8, codec.pb_uint(1,10002)+codec.pb_uint(2,10))
    r = ms.parse_lot_occupants(body)
    assert r["spaces"][0]=={"pos":3,"role_id":555,"start_time":1783267198,"name":"曇花一現"}
    assert (10002,10) in r["skin_list"]
def test_weight_prioritises_recent_host_then_guild():
    hi = ms.weight({"role_id":1,"host_hits":3,"coin":100,"guild":"羽皇居","level":200}, {"羽皇居"})
    lo = ms.weight({"role_id":2,"host_hits":0,"coin":100,"guild":"路人","level":200}, {"羽皇居"})
    assert hi > lo
```

**實作規格**：
- encode：用 `codec.pb_uint/pb_str/pb_msg`；`enc_role_others` = `b"".join(pb_uint(1,r) for r in ids)+pb_uint(2,source)`。
- parse：用 `codec.walk`；`parse_lot_occupants` 走 top #7（spaces：#1 pos,#2 role_id,#5 start,#9 name；role_id==0 跳過）+ top #8（skin：#1 id,#2 lev）。`parse_role_others` 走 #1 repeated，每筆 #1 role_id + #2 info_list(p_role_change kv：{1:attr,2:val})→ 取 1001 level/1006 server/1020 power。`parse_guild_search`/`parse_guild_members` 照 spec §8 欄位。
- `weight` 照 spec §4 公式；缺欄位以 0/空字串容錯。
- name 解碼容錯（utf-8，非可印回 None）。純函式，無 IO。

**commit**：`feat(mount-tracker): 純 WS encode/parse + 權重 mount_scan`

---

## Task 3 — `MountTrackerStore`（狀態持久化）

**檔案**：新增 `runtime_services/mount_tracker_service.py`（本 task 只放 `MountTrackerStore`；scan_cycle/daemon 後續 task 補）；新增 `tests/test_mount_tracker_store.py`。

**測試**（用 `tmp_path` 當 state_dir，走 `ws_token.state` 慣例）：
```python
from runtime_services.mount_tracker_service import MountTrackerStore
def test_add_remove_target_roundtrip(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.add_target({"role_id":1,"name":"A","uid":"X"})
    assert s.get_targets()[0]["role_id"]==1
    s2 = MountTrackerStore(state_dir=str(tmp_path))         # 重新載入 = 有持久化
    assert s2.get_targets()[0]["name"]=="A"
    s2.remove_target(1); assert s2.get_targets()==[]
def test_upsert_known_merges_fields(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    s.upsert_known(9, name="曇花一現"); s.upsert_known(9, guild="羽皇居", coin=145)
    k = s.get_known()["9"]
    assert k["name"]=="曇花一現" and k["guild"]=="羽皇居" and k["coin"]==145
def test_snapshot_has_targets_results_status(tmp_path):
    s = MountTrackerStore(state_dir=str(tmp_path))
    snap = s.snapshot()
    assert set(snap) >= {"targets","results","known_count","last_run"}
```

**實作規格**：
- 用 `ws_token.state.load_state/save_state`（傳 `state_dir`），device_key `"_mount_tracker"`。module-level `threading.RLock` 保護每個 public method 的 read-modify-write。
- known_players key 為 `str(role_id)`（JSON 物件 key 必字串）；`upsert_known` 合併非 None 欄位、更新 `last_scanned_ts` 由呼叫端決定或參數帶入。
- `snapshot()` 回精簡 dict 給 dashboard（targets、results、known_count、last_run、running）。
- 不做 enabled（那在 dashboard_settings，Task 6）。

**commit**：`feat(mount-tracker): MountTrackerStore 狀態持久化（ws_state/_mount_tracker.json）`

---

## Task 4 — idle 裝置借用判定

**檔案**：改 `runtime_services/mount_tracker_service.py`（加 `is_safe_to_borrow(ip)` / `pick_idle_device(candidates)`）；新增 `tests/test_mount_tracker_idle.py`。

**測試**（monkeypatch `bot_state.get_all_states` / `ws_session.is_active` / protected set）：
```python
import runtime_services.mount_tracker_service as mt
def test_sleeping_device_is_safe(monkeypatch):
    monkeypatch.setattr(mt, "_states", lambda: {"d":{"task":"休眠中"}})
    monkeypatch.setattr(mt, "_ws_active", lambda ip: False)
    monkeypatch.setattr(mt, "_protected_roles", lambda: set())
    monkeypatch.setattr(mt, "_now", lambda: 1000.0)
    assert mt.is_safe_to_borrow("d") is True
def test_online_device_is_busy(monkeypatch): ...   # task="每日任務" -> False
def test_about_to_wake_is_skipped(monkeypatch): ...# next_wake_at=now+60 -> False（<120s）
def test_held_by_ws_session_is_skipped(monkeypatch): ... # _ws_active True -> False
```

**實作規格**：
- 薄封裝間接層（供測試 monkeypatch）：`_states()` = `bot_state.get_all_states()`；`_ws_active(ip)` = `control_panel.ws_session.is_active(ip)`（import 失敗容錯回 False）；`_protected_roles()` = `ws_token.online_monitor.resolve_protected_role_ids()`（容錯回 set()）；`_now()` = `time.time()`。
- `is_safe_to_borrow(ip)`：idle（無 row / status OFFLINE / task∈{"休眠中","啟動後休眠"}）AND next_wake_at 為 None 或 >120s 外 AND `_ws_active` False AND 該裝置 roleId 不在 protected。roleId 由 `ws_token.creds.load_role_id(ip)` 取；無 creds → 不安全（跳過）。
- `pick_idle_device(candidates)`：回第一個 `is_safe_to_borrow` 為 True 的；否則 None。常數 `_HANDOFF_LEAD_SEC=120`、`CANDIDATES=["7fe98fc6","emulator-5554","emulator-5556","emulator-5560"]`。

**commit**：`feat(mount-tracker): idle 裝置借用判定（休眠/未醒/未被借/非保護）`

---

## Task 5 — `scan_cycle`（核心邏輯，依賴注入）

**檔案**：改 `runtime_services/mount_tracker_service.py`（加 `scan_cycle`）；新增 `tests/test_mount_tracker_scan.py`。

**測試**（全 fake：reader 回 canned lots、idle_picker 固定回 "d"、sleeper no-op、now_fn 遞增）：
```python
def test_finds_target_and_early_exits_at_5(...):
    # targets=[T]; reader 讓 5 個 owner 各停 T 一台，第 6 個也停 T -> 只收 5、且第6個不再比對
    # 斷言 results[T] 長度==5，且 reader 呼叫次數在達 5 後停止對 T 的收錄
def test_snowball_adds_new_occupants(...):
    # reader 回一個 lot 有未知 occupant U -> known 之後含 U
def test_respects_budget(...):
    # now_fn 讓時間很快超過 budget_s -> 提早 break，scanned < top_n
def test_skips_when_no_idle_device(...):
    # idle_picker 回 None 幾次後回 "d" -> 不崩、最終仍掃到
```

**實作規格**：
- `scan_cycle(store, reader, idle_picker, sleeper, now_fn, *, top_n, budget_s, cooldown_s, max_per_target)`：
  1. targets = store.get_targets()；空 → 回 `{"skipped":"no_targets"}`。
  2. target_guilds = 由 known 中 targets 的 guild 組成 set。
  3. queue = known players（含 targets 自身 role_id 當 owner）依 `mount_scan.weight` 排序取 top_n。
  4. deadline = now_fn()+budget_s；found={t:[] for t}。
  5. 迴圈 owner：超 deadline 或 all-found → break；`dev=idle_picker()`；dev None → `sleeper(cooldown_s)`、continue；`reader(dev,owner)`；None → continue；每次 reader 前 `sleeper(cooldown_s)`。
  6. 對 spaces：occupant.role_id 在 targets 且 found 未滿 → append(owner,pos,start_time,found_ts)；一律 `store.upsert_known(occupant.role_id, name=...)`。
  7. owner 的 coin = `parking_bonus.lot_bonus(skin_list)`；`store.upsert_known(owner, coin=..., last_scanned_ts=now)`。
  8. 結束：host_hits 更新（本輪當過目標房東 +1、其餘 decay 但不 <0）、`store.set_results(found)`、`store.set_last_run({...})`。回摘要 dict。
- 不直接開 WS / 不睡真的（全注入）。真正的 reader/idle_picker/sleeper 在 Task 6 用真元件包起來。

**commit**：`feat(mount-tracker): scan_cycle 核心（權重排序/早停/雪球/預算，依賴注入可測）`

---

## Task 6 — daemon + 真 IO 綁定 + toggle + 接線

**檔案**：改 `runtime_services/mount_tracker_service.py`（`ensure_mount_tracker_started` + 真 reader/idle_picker/sleeper）；改 `utils/dashboard_settings.py`（toggle）；改 `new_main_v2.py`（master 接線）；新增 `tests/test_mount_tracker_daemon.py`。

**測試**：
```python
def test_toggle_roundtrip(tmp_path, monkeypatch):
    # monkeypatch dashboard_settings 路徑到 tmp -> set True/False 讀回一致
def test_ensure_started_idempotent(monkeypatch):
    # 連叫兩次只起一條 thread（monkeypatch Thread 記次數 / 檢查 _started 冪等）
def test_real_reader_uses_carpark_parser(monkeypatch):
    # monkeypatch WSGameClient.call 回 canned car_park_info bytes -> reader 回 parse_lot_occupants 結果
```

**實作規格**：
- `utils/dashboard_settings.py`：加 `get_mount_tracker_enabled()->bool` / `set_mount_tracker_enabled(bool)`（照 `get_host_role/set_host_role` 的 `_LOCK`+`load_settings`+`_save` 樣板，key `"mount_tracker_enabled"`，預設 False）。
- real reader：借 dev → `WSGameClient(load_creds(dev)).connect()` → `client.call(12801, enc car_park_info body)` → `parse_lot_occupants`；`finally client.close()`；被踢/例外回 None。**同一 dev 連續讀多 owner**：real reader 由一個 per-device 連線包裝提供（連線重用、每 call 前重檢 `is_safe_to_borrow`，不安全就換）。實作以「每輪先挑 idle 裝置清單、各開一條短連線、輪流餵 owner」的 pool；pool 細節可在服務內，但仍讓 `scan_cycle` 只看到 `reader/idle_picker`。透過 `ws_session.ensure(dev)`/`disconnect(dev)` 註冊借用（讓喚醒禮讓）。
- `ensure_mount_tracker_started()`：module `_thread/_started/_start_lock`（照 `online_check_service:183`）；`_run_loop`：`while True: try: if get_mount_tracker_enabled(): _run_one_cycle() except: log; _wake.wait(3600)`（用 `threading.Event().wait` 可停可催）。master-only。
- `new_main_v2.py`：master 區塊（接在 `ensure_online_check_service_started()` / monitor 之後，約 `:625`）加 `from runtime_services.mount_tracker_service import ensure_mount_tracker_started; ensure_mount_tracker_started()`。
- 冷卻 3s、被踢 backoff 沿用 `Event.wait`。

**commit**：`feat(mount-tracker): hourly daemon + toggle + master 接線`

---

## Task 7 — dashboard blueprint（頁面 + API）

**檔案**：新增 `control_panel/routes_mount_tracker.py`；改 `control_panel_app.py`（註冊）；新增 `tests/test_routes_mount_tracker.py`。

**測試**（Flask test client + 登入 session stub）：
```python
def test_results_envelope(client_logged_in):
    r = client_logged_in.get("/api/mount_tracker/results")
    assert r.get_json()["status"]=="ok" and "targets" in r.get_json()
def test_add_and_remove_target(client_logged_in): ...   # POST targets -> GET 有 -> DELETE/POST remove -> 無
def test_toggle_admin_only(client_logged_in, client_admin): ... # 非 admin 403 / admin ok
```

**實作規格**：
- `bp = Blueprint("mount_tracker", __name__)`。用共享 `MountTrackerStore` singleton（服務層提供 `get_store()`）。
- `GET /mount-tracker`（`@_fly_pet_auth`）→ `render_template("mount_tracker.html", frontend_version=_get_frontend_version())`。
- `GET /api/mount_tracker/results`（`@_fly_pet_auth`）→ `{"status":"ok", **store.snapshot(), "enabled":get_mount_tracker_enabled()}`。
- `GET/POST /api/mount_tracker/targets`（`@_fly_pet_auth`）：POST body `{role_id}` 或 `{uid}` 或 `{name}`；UID→roleId 低20bit 反解 + friend_search 唯一才接受，多筆回 `{"status":"error","candidates":[...]}` 讓前端挑；`{"remove":role_id}` 移除。
- `POST /api/mount_tracker/toggle`（`@require_admin`）→ `set_mount_tracker_enabled(body["enabled"])`。
- `control_panel_app.py`：import tuple + register loop 各加 `routes_mount_tracker,`。
- 回應一律 `{"status":"ok"|"error",...}`。

**commit**：`feat(mount-tracker): dashboard blueprint（頁面 + targets/results/toggle API）`

---

## Task 8 — dashboard 模板 + UI review

**檔案**：新增 `templates/mount_tracker.html`；（選配）改 `templates/dashboard.html` 加 tab。

**實作規格**：
- head 照 `templates/inventory.html:1-11`：fonts + `{% include '_assets_head.html' %}` + 頁內 `<style>`（在 include 後）。title「坐騎追蹤 · Mount Tracker」。
- 目標管理區：新增（roleId/UID/名字輸入）+ 清單（可移除）。
- 每個目標一張卡：表格列出找到的坐騎（房東名/家族/pos/已停多久/狀態）。**倒數前端每秒 tick**：`remain = 170*60-(Date.now()/1000 - start_time)`；`remain>0` 顯示「保護中 mm:ss」，否則綠標「可打（已停 Hh Mm）」。用 `UI.esc` 包所有插值。
- 開關（admin 才顯示/可用）：`UI.apiPost('/api/mount_tracker/toggle',{enabled})`；狀態列顯示 last_run / running / known_count。
- `setInterval` 輪詢 `/api/mount_tracker/results`（~5s）刷資料；倒數獨立 1s tick。
- **完成後**：派 Opus 跑 `dashboard-ui-review` skill 完整流程（5003 live + Lighthouse + 對比度）；CRITICAL/HIGH + 便宜 MED 退回修。

**commit**：`feat(mount-tracker): dashboard 模板（目標管理 + 可打倒數表）`

---

## 執行順序 / 依賴

1 → 2 →（3、4 可平行但一次一個）→ 5（需 2,3,4）→ 6（需 5 + toggle）→ 7（需 6 的 store/toggle）→ 8（需 7）。
每 task：Opus implementer（`model:"opus"`）→ 我讀 `git diff` + 複跑該 task 測試 → ledger + TaskUpdate。Task 8 後跑 dashboard-ui-review，再全分支最終 review → merge。

## Test 指令（每 task 只跑相關檔）

```
python -m pytest tests/test_parking_bonus.py -q
python -m pytest tests/test_mount_scan.py -q
python -m pytest tests/test_mount_tracker_store.py tests/test_mount_tracker_idle.py -q
python -m pytest tests/test_mount_tracker_scan.py -q
python -m pytest tests/test_mount_tracker_daemon.py -q
python -m pytest tests/test_routes_mount_tracker.py -q
```
