# ws_token 接入主 workflow 實作計畫（pilot：小寶 7fe98fc6）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每輪喚醒先跑純 WS 任務（含家園三件 + 送光奶茶/玫瑰），Playwright 階段跳過 WS 已完成的任務。

**Architecture:** 兩階段 wake cycle——`game_actions/ws_phase.run_ws_phase(ip)` 在瀏覽器啟動前跑 `ws_token.runner.run_device`，把 RunReport 轉成 pipeline skip-set；`daily_pipeline._run_tasks` 逐項查 `ctx.ws_done` 跳過。Playwright 階段載入完成後從 page 回寫新 ticket（自癒迴圈）。

**Tech Stack:** Python 3 / pytest（fake transport 在 `tests/fakes/ws_fakes.py`）/ Playwright page.evaluate。

**Spec:** `docs/superpowers/specs/2026-06-10-ws-token-workflow-integration-design.md`

**工作目錄：** `C:\Users\Eric\ws-token-home`（worktree）。**ADB 模式（spec §4）不在本計畫**——pilot 驗證後另開計畫。

**本 repo 慣例（務必遵守）：**
- 測試只跑 focused target（`python -m pytest tests/test_xxx.py -q`），**禁止裸 pytest**。
- 讀 JSON 用 `encoding="utf-8-sig"`（很多檔帶 BOM）。
- 編輯 .py 後 hook 會自動 py_compile + ruff。
- Commit 訊息 conventional commits，無 attribution footer。

---

### Task 0: 建分支 feat/ws-backend

**Files:** 無（git 操作）

- [ ] **Step 0.1: 確認 integration 分支沒有 home 缺的 commit**

```bash
cd C:\Users\Eric\ws-token-home
git merge-base feat/ws-token-home feat/ws-token-integration
git log --oneline feat/ws-token-home..feat/ws-token-integration -- ws_token tests
```

Expected: 第二個指令**無輸出**（home 是 superset）。若有輸出 → 先 `git merge feat/ws-token-integration` 解掉再繼續。

- [ ] **Step 0.2: 切新分支**

```bash
git checkout -b feat/ws-backend
```

---

### Task 1: couple.give_all_in_hand（每批 20、封頂、code 3 結束）

**Files:**
- Modify: `ws_token/couple.py`（`give_all` 之後加新函式）
- Test: `tests/test_ws_token_couple.py`（檔尾 append）

- [ ] **Step 1.1: 寫失敗測試**（append 到 `tests/test_ws_token_couple.py` 檔尾；複用檔內既有 `_client` helper 與 `tests/fakes/ws_fakes` 的 `s2c`。import 區把 `give_all,` 那行後面加上 `give_all_in_hand,`）

```python
# --- give_all_in_hand: batches of 20, server caps, code 3 = done -------------

def _err_s2c(code):
    """error_info_s2c {error_code#1} on the 0x0201 channel."""
    return s2c(CMD_ERROR, codec.pb_uint(1, code))


def test_give_all_in_hand_stops_on_code3_after_batches():
    # 2 batches succeed (server capped them to inventory), 3rd batch -> code 3.
    replies = [
        [s2c(CMD_GIVE_FLOWER, b"")],
        [s2c(CMD_GIVE_FLOWER, b"")],
        [_err_s2c(ERR_NOT_ENOUGH_ITEM)],
    ]
    calls = []

    def responder(body):
        calls.append(body)
        return replies[len(calls) - 1]

    c, fake = _client({CMD_GIVE_FLOWER: responder})
    try:
        out = give_all_in_hand(c, friend_id=111, flower_id=MILK_TEA, spacing=0)
        assert out == {"batches_ok": 2, "stopped_reason": "error_code=3"}
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_GIVE_FLOWER]
        assert len(sent) == 3
        for b in sent:  # every batch is num=20 {friend_id#1, flower_id#2, num#3}
            assert codec.walk_dict(b) == {1: 111, 2: MILK_TEA, 3: 20}
    finally:
        c.close()


def test_give_all_in_hand_empty_inventory_first_batch():
    c, _ = _client({CMD_GIVE_FLOWER: lambda _b: [_err_s2c(ERR_NOT_ENOUGH_ITEM)]})
    try:
        out = give_all_in_hand(c, friend_id=111, flower_id=FLOWER, spacing=0)
        assert out == {"batches_ok": 0, "stopped_reason": "error_code=3"}
    finally:
        c.close()


def test_give_all_in_hand_success_notice_369_counts_as_ok():
    # live: give success replies on 0x0201 with code 369 (贈送成功) — must count
    # as a successful batch, then a real code 3 ends the loop.
    replies = [[_err_s2c(369)], [_err_s2c(ERR_NOT_ENOUGH_ITEM)]]
    calls = []

    def responder(body):
        calls.append(body)
        return replies[len(calls) - 1]

    c, _ = _client({CMD_GIVE_FLOWER: responder})
    try:
        out = give_all_in_hand(c, friend_id=111, flower_id=MILK_TEA, spacing=0)
        assert out == {"batches_ok": 1, "stopped_reason": "error_code=3"}
    finally:
        c.close()


def test_give_all_in_hand_max_batches_guardrail():
    c, _ = _client({CMD_GIVE_FLOWER: lambda _b: [s2c(CMD_GIVE_FLOWER, b"")]})
    try:
        out = give_all_in_hand(c, friend_id=111, flower_id=MILK_TEA,
                               max_batches=4, spacing=0)
        assert out == {"batches_ok": 4, "stopped_reason": "max_batches"}
    finally:
        c.close()
```

- [ ] **Step 1.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_token_couple.py -q`
Expected: FAIL（ImportError: cannot import name 'give_all_in_hand'）

- [ ] **Step 1.3: 實作**（`ws_token/couple.py`，加在 `give_all` 函式後面；模組常數區加 `_GIFT_BATCH = 20`、`_GIFT_MAX_BATCHES = 20`）

```python
def give_all_in_hand(
    client: WSGameClient, *,
    friend_id: int, flower_id: int,
    batch: int = _GIFT_BATCH,
    max_batches: int = _GIFT_MAX_BATCHES,
    spacing: float = _DEFAULT_SPACING,
    timeout: Optional[float] = None,
) -> dict:
    """送光手上全部 ``flower_id``：以 ``batch``(=20) 為單位連送（使用者指定）。

    Server 對超量 num 自動封頂到庫存（user live-confirmed 2026-06-10），所以每批
    實送 min(batch, 在手)；庫存歸零後下一批回 0x0201 code 3 物品不足 —— 那是正常
    結束訊號，不是錯誤。回傳 {batches_ok, stopped_reason}。
    """
    batches_ok = 0
    stopped_reason = "max_batches"
    for _ in range(max_batches):
        out = give_flower(client, friend_id=friend_id, flower_id=flower_id,
                          num=batch, timeout=timeout)
        if not out["ok"]:
            stopped_reason = f"error_code={out['error_code']}"
            break
        batches_ok += 1
        if spacing:
            time.sleep(spacing)
    logger.info("ws_token couple: give_all_in_hand flower_id=%s batches_ok=%d %s",
                flower_id, batches_ok, stopped_reason)
    return {"batches_ok": batches_ok, "stopped_reason": stopped_reason}
```

（`_DEFAULT_SPACING` 已存在 = 0.2。）

- [ ] **Step 1.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_token_couple.py -q`
Expected: 全綠（既有 24+ 新 4）

- [ ] **Step 1.5: Commit**

```bash
git add ws_token/couple.py tests/test_ws_token_couple.py
git commit -m "feat(ws_token): couple.give_all_in_hand 每批20封頂送光, code3=結束訊號"
```

---

### Task 2: ws_token/state.py（per-device JSON 狀態，12h 輪換用）

**Files:**
- Create: `ws_token/state.py`
- Test: `tests/test_ws_token_state.py`

- [ ] **Step 2.1: 寫失敗測試**

```python
"""Tests for ws_token.state — tiny per-device JSON cadence store."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import state  # noqa: E402


def test_load_missing_returns_empty(tmp_path):
    assert state.load_state("devA", state_dir=tmp_path) == {}


def test_save_then_load_roundtrip(tmp_path):
    state.save_state("devA", {"workshop": {"last_rotate_ts": 123, "parity": 1}},
                     state_dir=tmp_path)
    assert state.load_state("devA", state_dir=tmp_path) == {
        "workshop": {"last_rotate_ts": 123, "parity": 1}}


def test_load_corrupt_file_returns_empty(tmp_path):
    (tmp_path / "devA.json").write_text("{not json", encoding="utf-8")
    assert state.load_state("devA", state_dir=tmp_path) == {}


def test_save_creates_dir(tmp_path):
    state.save_state("devA", {"x": 1}, state_dir=tmp_path / "sub")
    assert state.load_state("devA", state_dir=tmp_path / "sub") == {"x": 1}


def test_load_tolerates_utf8_bom(tmp_path):
    (tmp_path / "devA.json").write_text('{"x": 1}', encoding="utf-8-sig")
    assert state.load_state("devA", state_dir=tmp_path) == {"x": 1}
```

- [ ] **Step 2.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_token_state.py -q`
Expected: FAIL（ModuleNotFoundError: ws_token.state）

- [ ] **Step 2.3: 實作 `ws_token/state.py`**

```python
"""Tiny per-device JSON state store for ws_token runner cadence tracking.

Used by the runner for things that must survive across runs without a server
read — e.g. the workshop 12h recipe-rotation timestamp/parity. One file per
device under ``ws_state/`` (repo root, gitignored-friendly), UTF-8, BOM
tolerated on read. Corrupt/missing state degrades to {} — callers must treat
state as advisory, never required.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_DIR = Path(__file__).resolve().parents[1] / "ws_state"


def load_state(device: str, *, state_dir: Path = STATE_DIR) -> dict:
    """Load ``ws_state/<device>.json``; missing or corrupt -> {}."""
    path = Path(state_dir) / f"{device}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        logger.warning("ws_token state: corrupt state file %s — treating as empty",
                       path, exc_info=True)
        return {}


def save_state(device: str, data: dict, *, state_dir: Path = STATE_DIR) -> None:
    """Write ``ws_state/<device>.json`` (creates the dir on first use)."""
    d = Path(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{device}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 2.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_token_state.py -q`
Expected: 5 passed

- [ ] **Step 2.5: Commit**

```bash
git add ws_token/state.py tests/test_ws_token_state.py
git commit -m "feat(ws_token): per-device JSON state store (ws_state/) for cadence tracking"
```

---

### Task 3: workshop.rotate_team_recipes（兩配方輪流指派小隊加工）

**Files:**
- Modify: `ws_token/workshop.py`（`switch_recipe` 之後）
- Test: `tests/test_ws_token_workshop.py`（檔尾 append；該檔已有 fake client 與 wire helper，沿用其 `_client`/info body builder；若無對應 helper 就照 test_ws_token_couple.py 的 `_client` 模式建）

- [ ] **Step 3.1: 寫失敗測試**（append；import 區補 `rotate_team_recipes`）

```python
# --- rotate_team_recipes: 12h 輪換的 wire 邏輯（cadence 由 runner 管）---------

def _pw_worker(team_cfg_id, worker_status):
    """p_worker {team_cfg_id#1, worker_status#3}."""
    return codec.pb_uint(1, team_cfg_id) + codec.pb_uint(3, worker_status)


def _info_s2c_body(team_ids):
    """worker_pw_info_s2c with one running p_worker per team_cfg_id."""
    out = b""
    for t in team_ids:
        out += codec.pb_msg(2, _pw_worker(t, 1))
    return out


def _dining_s2c_body(foods):
    """dining_hall_s2c {food_list#1 repeated p_key_value{k,v}}."""
    out = b""
    for k, v in foods:
        out += codec.pb_msg(1, codec.pb_uint(1, k) + codec.pb_uint(2, v))
    return out


def _rotate_client(team_ids, foods):
    return _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, _info_s2c_body(team_ids))],
        CMD_DINING_HALL: lambda _b: [s2c(CMD_DINING_HALL, _dining_s2c_body(foods))],
        CMD_CANCEL_WORK: lambda _b: [s2c(CMD_CANCEL_WORK, b"")],
        CMD_CHOOSE_FOOD: lambda _b: [s2c(CMD_CHOOSE_FOOD, b"")],
    })


def test_rotate_parity0_assigns_8001_then_8005():
    c, fake = _rotate_client([6001, 6002, 6003], [(8001, 7), (8005, 4)])
    try:
        out = rotate_team_recipes(c, parity=0)
        # 手動加工 6001 untouched; 6002 -> 8001, 6003 -> 8005
        assert [s["food_id"] for s in out["switched"]] == [8001, 8005]
        assert [s["team_cfg_id"] for s in out["switched"]] == [6002, 6003]
        chosen = [codec.walk_dict(b) for _sid, cmd, b in fake.framed_sent()
                  if cmd == CMD_CHOOSE_FOOD]
        # choose_food bodies: food kv nested at #1, workshop wire id at #2
        assert [d[2] for d in chosen] == [2, 3]  # configWorkshop.id, NOT team_cfg_id
    finally:
        c.close()


def test_rotate_parity1_swaps_recipes():
    c, _ = _rotate_client([6002, 6003], [(8001, 7), (8005, 4)])
    try:
        out = rotate_team_recipes(c, parity=1)
        assert [s["food_id"] for s in out["switched"]] == [8005, 8001]
    finally:
        c.close()


def test_rotate_no_team_workshops_is_noop():
    c, fake = _rotate_client([6001], [(8001, 7)])
    try:
        out = rotate_team_recipes(c, parity=0)
        assert out["switched"] == []
        assert all(cmd != CMD_CHOOSE_FOOD for _s, cmd, _b in fake.framed_sent())
    finally:
        c.close()
```

- [ ] **Step 3.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_token_workshop.py -q`
Expected: FAIL（ImportError: rotate_team_recipes）

- [ ] **Step 3.3: 實作**（`ws_token/workshop.py`，`switch_recipe` 之後）

```python
def rotate_team_recipes(
    client: WSGameClient, *, parity: int, timeout: Optional[float] = None,
) -> dict:
    """12h 配方輪換（使用者 2026-06-10 指定：兩類別 12hr 切一次）。

    把 RECIPE_FOOD_IDS (8001 脆脆餅乾 / 8005 精英拼盤) 輪流指派給每個小隊加工
    （team_cfg_id 6002/6003；手動加工 6001 一律不動）：parity 偶數 = 依序
    [8001, 8005, ...]，奇數 = 反序。每個 workshop 走已驗的 switch_recipe
    （cancel → dining_hall → choose，count = 餐廳現有全量）。

    CADENCE 不在這裡：呼叫端（runner）用 ws_token.state 記 last_rotate_ts/parity，
    12h 未到就不呼叫本函式。Returns {parity, switched: [...]}。
    """
    info = read_info(client, timeout=timeout)
    teams = [w for w in info.workshops
             if w.team_cfg_id in TEAM_TO_WORKSHOP_ID and w.team_cfg_id != 6001]
    order = RECIPE_FOOD_IDS if parity % 2 == 0 else tuple(reversed(RECIPE_FOOD_IDS))
    switched: list[dict] = []
    for i, w in enumerate(teams):
        food_id = order[i % len(order)]
        result = switch_recipe(client, team_cfg_id=w.team_cfg_id,
                               food_id=food_id, timeout=timeout)
        switched.append({"team_cfg_id": w.team_cfg_id, **result})
    logger.info("ws_token workshop: rotate_team_recipes parity=%d switched=%d",
                parity % 2, len(switched))
    return {"parity": parity % 2, "switched": switched}
```

- [ ] **Step 3.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_token_workshop.py -q`
Expected: 全綠（既有 29 + 新 3）

- [ ] **Step 3.5: Commit**

```bash
git add ws_token/workshop.py tests/test_ws_token_workshop.py
git commit -m "feat(ws_token): workshop.rotate_team_recipes 兩配方輪流指派小隊加工"
```

---

### Task 4: runner 接 spirit / workshop / couple 三任務

**Files:**
- Modify: `ws_token/runner.py`
- Test: `tests/test_ws_token_runner.py`（append；沿用該檔既有 fake/factory 模式）

- [ ] **Step 4.1: 寫失敗測試**（append 到 `tests/test_ws_token_runner.py`。該檔已有以 `_make_client` 注入 fake 的 run_device 測試——沿用同一個 fake client builder；下面以 monkeypatch 模組函式的方式測 wiring，不碰 wire bytes）

```python
# --- spirit / workshop / couple wiring ---------------------------------------

def test_task_order_has_home_features_before_lamp():
    from ws_token import runner
    order = list(runner.TASK_ORDER)
    assert order.index("spirit") > order.index("carpark")
    assert order[-4:] == ["spirit", "workshop", "couple", "lamp"]


def test_run_couple_no_partner_skips(monkeypatch):
    from ws_token import runner
    monkeypatch.setattr(runner.couple, "read_favor_info", lambda c: [])
    monkeypatch.setattr(runner.couple, "read_partner", lambda c: 0)
    out = runner._run_couple(object(), gifts=True, forge_ring=False)
    assert out["skipped"] == "no partner"


def test_run_couple_gifts_milk_tea_then_flower(monkeypatch):
    from ws_token import runner, couple

    sent = []
    monkeypatch.setattr(
        runner.couple, "read_favor_info",
        lambda c: [couple.Partner(role_id=111, name="P", favor_lv=5, favor=1)])
    monkeypatch.setattr(
        runner.couple, "give_all_in_hand",
        lambda c, *, friend_id, flower_id: sent.append((friend_id, flower_id))
        or {"batches_ok": 1, "stopped_reason": "error_code=3"})
    out = runner._run_couple(object(), gifts=True, forge_ring=False)
    assert sent == [(111, couple.MILK_TEA), (111, couple.FLOWER)]
    assert out["ring"] is None


def test_run_couple_forge_ring_gated(monkeypatch):
    from ws_token import runner, couple
    monkeypatch.setattr(
        runner.couple, "read_favor_info",
        lambda c: [couple.Partner(role_id=111, name="P", favor_lv=5, favor=1)])
    monkeypatch.setattr(
        runner.couple, "give_all_in_hand",
        lambda c, **kw: {"batches_ok": 0, "stopped_reason": "error_code=3"})
    called = []
    monkeypatch.setattr(runner.couple, "forge_ring_until_empty",
                        lambda c: called.append(1) or {"forges": 2})
    runner._run_couple(object(), gifts=True, forge_ring=False)
    assert called == []
    runner._run_couple(object(), gifts=True, forge_ring=True)
    assert called == [1]


def test_run_workshop_rotates_only_after_12h(monkeypatch, tmp_path):
    from ws_token import runner
    rotated = []
    monkeypatch.setattr(runner.workshop, "rotate_team_recipes",
                        lambda c, *, parity: rotated.append(parity)
                        or {"parity": parity % 2, "switched": []})
    # first run: no state -> rotates with parity 0
    out1 = runner._run_workshop(object(), device="devA", state_dir=tmp_path,
                                now=1_000_000.0)
    assert rotated == [0] and out1["rotated"] is True
    # 1 hour later: gated
    out2 = runner._run_workshop(object(), device="devA", state_dir=tmp_path,
                                now=1_000_000.0 + 3600)
    assert rotated == [0] and out2["rotated"] is False
    # 12h+ later: rotates with parity 1
    out3 = runner._run_workshop(object(), device="devA", state_dir=tmp_path,
                                now=1_000_000.0 + 12 * 3600 + 1)
    assert rotated == [0, 1] and out3["rotated"] is True


def test_run_spirit_draws_free(monkeypatch):
    from ws_token import runner
    monkeypatch.setattr(runner.spirit, "draw_all_free",
                        lambda c: {"pools_drawn": 2, "rewards": {}, "results": []})
    assert runner._run_spirit(object())["pools_drawn"] == 2
```

- [ ] **Step 4.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_token_runner.py -q`
Expected: FAIL（no attribute `_run_couple` 等）

- [ ] **Step 4.3: 實作 runner 修改**（`ws_token/runner.py`）

(a) import 區改為：

```python
from ws_token import (
    carpark, couple, dungeon, farm, guild, idle_reward, lamp, league_solo,
    main_tasks, redpack, spirit, steward, turntable, workshop,
)
from ws_token import state as ws_state
```

(b) 常數區加：

```python
# workshop 12h 配方輪換間隔（使用者 2026-06-10 指定：兩類別 12hr 切一次）
_WORKSHOP_ROTATE_S: float = 12 * 3600.0
```

(c) `TASK_ORDER` 改為：

```python
TASK_ORDER: tuple[str, ...] = (
    "main_tasks", "league_solo", "redpack", "idle_reward", "turntable", "farm",
    "dungeon", "guild", "steward", "carpark", "spirit", "workshop", "couple",
    "lamp")
```

(d) per-task runners 區（`_run_lamp` 前）加三個函式：

```python
def _run_spirit(client) -> dict:
    """守護靈免費召喚: draw_all_free 只用 free_times, 不買招喚貨幣 (800003 不存在)."""
    return spirit.draw_all_free(client)


def _run_workshop(client, *, device: str, state_dir=None, now=None) -> dict:
    """加工坊 12h 配方輪換 (spec §2.2; cadence 存 ws_state/<device>.json).

    state {"workshop": {"last_rotate_ts": float, "parity": int}}; 12h 未到 →
    {"rotated": False}; 到了 → rotate_team_recipes(parity+1) 並回寫 state。
    """
    import time as _time
    now = _time.time() if now is None else now
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    st = ws_state.load_state(device, **kw)
    wst = st.get("workshop") or {}
    last_ts = float(wst.get("last_rotate_ts") or 0)
    if now - last_ts < _WORKSHOP_ROTATE_S:
        hours = (now - last_ts) / 3600.0
        return {"rotated": False, "reason": f"rotated {hours:.1f}h ago (<12h)"}
    parity = (int(wst.get("parity") or 0) + 1) % 2 if last_ts else 0
    out = workshop.rotate_team_recipes(client, parity=parity)
    st["workshop"] = {"last_rotate_ts": now, "parity": parity}
    ws_state.save_state(device, st, **kw)
    return {"rotated": True, **out}


def _run_couple(client, *, gifts: bool, forge_ring: bool) -> dict:
    """伴侶: 奶茶+玫瑰送光 (give_all_in_hand, 每批20封頂) + 戒指錘鍊 (spend 類).

    默契考驗 (Marry type 6) 已由 _run_main_tasks 的 claim_marry_tasks 領取。
    無伴侶 (favor list 空且 lover_id=0) → skip。
    """
    partners = couple.read_favor_info(client)
    friend_id = partners[0].role_id if partners else couple.read_partner(client)
    summary: dict = {"partner": friend_id, "milk_tea": None, "flower": None,
                     "ring": None}
    if not friend_id:
        return {**summary, "skipped": "no partner"}
    if gifts:
        summary["milk_tea"] = couple.give_all_in_hand(
            client, friend_id=friend_id, flower_id=couple.MILK_TEA)
        summary["flower"] = couple.give_all_in_hand(
            client, friend_id=friend_id, flower_id=couple.FLOWER)
    if forge_ring:
        summary["ring"] = couple.forge_ring_until_empty(client)
    return summary
```

(e) `run_device` 簽名加參數（`carpark_target` 之後）：

```python
               carpark_target: Optional[int] = None,
               couple_gifts: bool = True,
               forge_ring: bool = False,
               workshop_rotate: bool = True) -> RunReport:
```

(f) `run_device` 的 `_safe` 序列在 carpark 之後、`if open_lamp:` 之前加：

```python
        _safe(tasks, errors, "spirit", lambda: _run_spirit(client))
        if workshop_rotate:
            _safe(tasks, errors, "workshop",
                  lambda: _run_workshop(client, device=device))
        _safe(tasks, errors, "couple",
              lambda: _run_couple(client, gifts=couple_gifts,
                                  forge_ring=forge_ring))
```

(g) docstring（模組與 run_device）的任務清單同步補三行描述；CLI `main()` 加旗標並傳入：

```python
    ap.add_argument("--no-couple-gifts", dest="couple_gifts", action="store_false",
                    help="伴侶送禮 (奶茶+玫瑰送光) 預設開; 此旗標關閉")
    ap.add_argument("--forge-ring", action="store_true",
                    help="戒指錘鍊: 消耗全部真愛之石 (預設關)")
    ap.add_argument("--no-workshop", dest="workshop_rotate", action="store_false",
                    help="加工坊 12h 配方輪換預設開; 此旗標關閉")
```

並在 `run_device(...)` 呼叫處傳 `couple_gifts=args.couple_gifts, forge_ring=args.forge_ring, workshop_rotate=args.workshop_rotate`。

- [ ] **Step 4.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_token_runner.py tests/test_ws_runner_wiring.py -q`
Expected: 全綠

- [ ] **Step 4.5: CLI 煙霧檢查（lessons：argparse bug 單測抓不到）**

Run: `python -m ws_token.runner --help`
Expected: usage 正常列出新旗標，exit 0

- [ ] **Step 4.6: Commit**

```bash
git add ws_token/runner.py tests/test_ws_token_runner.py
git commit -m "feat(ws_token): runner 接 spirit/workshop(12h輪換)/couple(送光) 三任務"
```

---

### Task 5: config_manager 加 ws_token 預設區塊

**Files:**
- Modify: `config_manager.py:64`（`DEFAULT_DEVICE_CONFIG` 尾端）
- Test: `tests/test_ws_phase_config.py`（新檔）

- [ ] **Step 5.1: 寫失敗測試**

```python
"""ws_token nested device-config defaults (config_manager.DEFAULT_DEVICE_CONFIG)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_manager  # noqa: E402


def test_default_device_config_has_ws_token_disabled():
    ws = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]
    assert ws["enabled"] is False
    assert ws["spend"] is False
    assert ws["open_lamp"] is False
    assert ws["couple_gifts"] is True
    assert ws["forge_ring"] is False
    assert ws["dungeon_sweeps"] == []
    assert ws["farm"] is None
    assert ws["carpark_target"] is None
```

- [ ] **Step 5.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_phase_config.py -q`
Expected: FAIL（KeyError: 'ws_token'）

- [ ] **Step 5.3: 實作**（`config_manager.py` 的 `DEFAULT_DEVICE_CONFIG`，在 `"sleep_max_hours"` 行後加）

```python
    "ws_token": {  # WS-first 階段 (game_actions/ws_phase.py)；enabled=False 完全不影響舊行為
        "enabled": False,       # 喚醒後先跑純 WS 任務，成功項由 Playwright 階段跳過
        "spend": False,         # 家族捐獻/管家代購/掃蕩/續約 等花費類
        "open_lamp": False,     # WS 開神燈（一批，取代 Playwright 開神燈）
        "farm": None,           # {"seed_id": int, "team_cfg_id": int}；填 seed_id 才 skip 農場任務
        "dungeon_sweeps": [],   # [[type, dungeon_id, num], ...]；有配才 skip 萬神試煉
        "carpark_target": None, # 跨界停車 master_id（只停不收）
        "couple_gifts": True,   # 伴侶奶茶+玫瑰送光（每批20，server 封頂）
        "forge_ring": False,    # 戒指錘鍊（消耗全部真愛之石）
    },
```

- [ ] **Step 5.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_phase_config.py -q`
Expected: 1 passed

- [ ] **Step 5.5: Commit**

```bash
git add config_manager.py tests/test_ws_phase_config.py
git commit -m "feat(config): DEFAULT_DEVICE_CONFIG 加 ws_token 巢狀區塊 (預設 disabled)"
```

---

### Task 6: game_actions/ws_phase.py（WS 階段 + skip-set 對照）

**Files:**
- Create: `game_actions/ws_phase.py`
- Test: `tests/test_ws_phase.py`（新檔；monkeypatch `ws_token.runner.run_device`，不碰真連線）

- [ ] **Step 6.1: 寫失敗測試**

```python
"""game_actions.ws_phase — WS-first 階段與 RunReport→pipeline skip-set 對照。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_manager  # noqa: E402
from game_actions import ws_phase  # noqa: E402
from ws_token.runner import RunReport  # noqa: E402


def _cfg(monkeypatch, ws):
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  {"ws_token": ws}.get(k, d)})())


def _report(tasks, errors=None, login_ok=True):
    return RunReport(device="dev", login_ok=login_ok, spend=False,
                     tasks=tasks, errors=errors or {})


def test_disabled_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": False})
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_success_tasks_map_to_pipeline_names(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report({
        "redpack": {}, "farm": {}, "idle_reward": {}, "guild": {},
        "spirit": {}, "steward": {}, "main_tasks": {}, "couple": {},
        "lamp": {}, "turntable": {},
    }))
    skips = ws_phase.run_ws_phase("dev")
    assert skips == frozenset({
        "紅包檢查", "點擊寶箱", "家族任務", "領取守護靈", "商店購買",
        "所有日常任務", "好友每日禮物", "開神燈", "轉盤金幣",
    })
    # farm 沒配 seed_id → 農場任務不 skip（spec §8）
    assert "農場任務" not in skips


def test_farm_skips_only_with_seed_id(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "farm": {"seed_id": 4001}})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg: _report({"farm": {}}))
    assert "農場任務" in ws_phase.run_ws_phase("dev")


def test_dungeon_skips_only_with_sweeps_configured(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "dungeon_sweeps": [[2, 100, 3]]})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg: _report({"dungeon": {}}))
    assert "萬神試煉" in ws_phase.run_ws_phase("dev")
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg: _report({"dungeon": {}}))
    assert "萬神試煉" not in ws_phase.run_ws_phase("dev")


def test_errored_task_not_skipped(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report(
        {"redpack": {}}, errors={"lamp": "WSTimeoutError: x"}))
    skips = ws_phase.run_ws_phase("dev")
    assert "紅包檢查" in skips and "開神燈" not in skips


def test_login_failure_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report(
        {}, errors={"login": "boom"}, login_ok=False))
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_any_exception_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    def _boom(ip, cfg):
        raise RuntimeError("creds missing")
    monkeypatch.setattr(ws_phase, "_run_device", _boom)
    assert ws_phase.run_ws_phase("dev") == frozenset()
```

- [ ] **Step 6.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_phase.py -q`
Expected: FAIL（ModuleNotFoundError: game_actions.ws_phase）

- [ ] **Step 6.3: 實作 `game_actions/ws_phase.py`**

```python
"""WS-first 階段：喚醒後、Playwright 瀏覽器啟動前，先跑純 WS 任務。

run_ws_phase(ip) 讀裝置的 ws_token config，in-thread 跑 ws_token.runner.run_device
（不需瀏覽器/App；WS 登入會踢同帳號其他 session，所以必須在開瀏覽器之前跑），
再把 RunReport 轉成本輪 daily_pipeline 可跳過的任務名集合。

任何失敗（creds 缺/登入失敗/例外）→ frozenset()，Playwright 階段全跑 —— WS 階段
只會替 pipeline 減工作，永遠不會讓它漏工作（天然降級）。

Skip 對照（spec §3.3；含使用者確認的等價：Store==管家代購、好友每日禮物==伴侶送禮）。
farm / dungeon 採條件式 skip：沒配 seed_id / dungeon_sweeps 就不跳（WS 只做了部分）。
"""
from __future__ import annotations

import logging
import time

import config_manager

logger = logging.getLogger(__name__)

# RunReport 任務鍵 → 該鍵成功時 daily_pipeline 可跳過的任務名（無條件部分）。
WS_TO_PIPELINE_SKIPS: dict[str, tuple[str, ...]] = {
    "redpack": ("紅包檢查",),
    "idle_reward": ("點擊寶箱",),
    "guild": ("家族任務",),
    "spirit": ("領取守護靈",),
    "steward": ("商店購買",),       # 使用者確認 Store == 管家代購
    "main_tasks": ("所有日常任務",),
    "couple": ("好友每日禮物",),     # 使用者確認 == 伴侶送禮
    "lamp": ("開神燈",),
    "turntable": ("轉盤金幣",),
}


def _run_device(ip: str, cfg: dict):
    """間接層：lazy import + 參數展開，tests monkeypatch 這裡。"""
    from ws_token.runner import run_device
    return run_device(
        ip,
        spend=bool(cfg.get("spend", False)),
        open_lamp=bool(cfg.get("open_lamp", False)),
        farm_config=cfg.get("farm") or None,
        dungeon_sweeps=cfg.get("dungeon_sweeps") or None,
        carpark_target=cfg.get("carpark_target") or None,
        couple_gifts=bool(cfg.get("couple_gifts", True)),
        forge_ring=bool(cfg.get("forge_ring", False)),
    )


def run_ws_phase(ip: str) -> frozenset[str]:
    """跑 WS 階段並回傳本輪 pipeline 的 skip-set；任何失敗回空集合。"""
    cfg = config_manager.get_device_config(ip).get("ws_token") or {}
    if not cfg.get("enabled", False):
        return frozenset()
    started = time.time()
    try:
        report = _run_device(ip, cfg)
    except Exception as exc:  # noqa: BLE001 — WS 階段失敗必須降級、不能炸 wake loop
        logger.warning("[%s] WS 階段失敗，本輪 Playwright 全跑: %s", ip, exc,
                       exc_info=True)
        return frozenset()
    if not report.login_ok:
        logger.warning("[%s] WS 登入失敗 (%s)，本輪 Playwright 全跑",
                       ip, report.errors.get("login"))
        return frozenset()

    skips: set[str] = set()
    for key, names in WS_TO_PIPELINE_SKIPS.items():
        if key in report.tasks and key not in report.errors:
            skips.update(names)
    # 條件式：WS farm 沒配種子就只收成 → Playwright 農場照跑補種
    if "farm" in report.tasks and (cfg.get("farm") or {}).get("seed_id"):
        skips.add("農場任務")
    # 條件式：有配掃蕩才算把萬神試煉做完
    if "dungeon" in report.tasks and "dungeon" not in report.errors \
            and cfg.get("dungeon_sweeps"):
        skips.add("萬神試煉")

    logger.info(
        "[%s] WS 階段完成 (%.1fs): ok=%s errors=%s kicked=%s skip=%s",
        ip, time.time() - started, list(report.tasks), list(report.errors),
        report.kicked, sorted(skips))
    return frozenset(skips)
```

- [ ] **Step 6.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_phase.py tests/test_ws_phase_config.py -q`
Expected: 全綠

- [ ] **Step 6.5: Commit**

```bash
git add game_actions/ws_phase.py tests/test_ws_phase.py
git commit -m "feat(ws_phase): WS-first 階段 + RunReport→pipeline skip-set 對照 (失敗即降級)"
```

---

### Task 7: daily_pipeline 接 ws_done skip

**Files:**
- Modify: `game_actions/daily_pipeline.py`（`DailyContext` + `_run_tasks` 內 11 個任務點）

> 此檔測試靠 py_compile + live cycle（直接單測會 import cv2/Playwright 重依賴，
> 違反本 repo 測試慣例）。改動是純機械式 guard，模式固定。

- [ ] **Step 7.1: `DailyContext` 加欄位**（`family_manager: Any` 之後）

```python
    ws_done: frozenset = frozenset()  # WS 階段已完成的任務名（ws_phase 對照表輸出）
```

- [ ] **Step 7.2: `_run_tasks` 開頭（`_streak = [0]` 之前）加 helper**

```python
    def _ws_skip(task_name: str) -> bool:
        """WS 階段已完成 → 記 log + 更新狀態並跳過該任務。"""
        if task_name in ctx.ws_done:
            logger.info(f"[{ip}] {task_name}: WS 階段已完成，跳過")
            bot_state.update_state(ip, task=task_name, step="WS 已完成，跳過")
            return True
        return False
```

- [ ] **Step 7.3: 逐點包 guard**（11 處，精確修改如下）

1. Task 0 紅包檢查（`run_redpack_check_if_due(d, ip)` 行）：
```python
    if not _ws_skip("紅包檢查"):
        run_redpack_check_if_due(d, ip)
```
2. Task 2 農場任務（整段 `stage = _guarded_run(...)`）：
```python
    if not _ws_skip("農場任務"):
        stage = _guarded_run(
            task_name="農場任務",
            mismatch_reason="農場任務前不在主頁面",
            fn=lambda: farm_manager.farm(d, ip, Cnn_model),
            step="準備進入",
        )
```
3. Task 3 點擊寶箱（包 `_guarded_run` 呼叫；`_tap_chest` 定義不動）：
```python
    if not _ws_skip("點擊寶箱"):
        _guarded_run(
            task_name="點擊寶箱",
            mismatch_reason="點擊寶箱前不在主頁面",
            fn=_tap_chest,
            step="領取獎勵",
        )
```
4. Task 4 家族任務（**注意**：Task 5/6 複用此 stage，跳過時要補抓）：
```python
    if not _ws_skip("家族任務"):
        stage = _guarded_run(
            task_name="家族任務",
            mismatch_reason="家族任務前不在主頁面",
            fn=family_manager.go_to_family,
            step="執行中",
        )
    else:
        stage = _track(get_stage_with_check(d, ip, Cnn_model))
```
5. Task 5 領取守護靈（第一個 `if not _DEVICE_SKIP_GUARDIAN...` 條件加一項）：
```python
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False) and not _ws_skip("領取守護靈"):
```
（區塊內容不動；第二個 `if not _DEVICE_SKIP_GUARDIAN`（抽技能夥伴）**不加**——WS 沒做。）
6. Task 7 商店購買（整段含 `stage = _track(...)` 到 else 的 error log 全包）：
```python
    if not _ws_skip("商店購買"):
        stage = _track(get_stage_with_check(d, ip, Cnn_model))
        if stage == "主頁面":
            ...（原內容整段內縮一層，不改邏輯）...
        else:
            ...（原 else 整段內縮）...
```
7. Task 12 所有日常任務（時段條件後追加）：
```python
    if 20 <= current_time.tm_hour < 23 and not _ws_skip("所有日常任務"):
```
8. Task 15 萬神試煉：
```python
    if not _ws_skip("萬神試煉"):
        stage = get_stage_with_check(d, ip, Cnn_model)
        _run_weekly_dungeon(d, ip, stage, enable_dungeon_manager, current_time)
```
9. Task 18 好友每日禮物（跳過時仍要為 Task 19 取 stage）：
```python
    if not _ws_skip("好友每日禮物"):
        stage = _guarded_run(
            task_name="好友每日禮物",
            mismatch_reason="好友每日禮物前不在主頁面",
            fn=lambda: daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1),
            step="領取中",
        )
        if stage == "主頁面":
            stage = get_stage_with_check(d, ip, Cnn_model)
    else:
        stage = get_stage_with_check(d, ip, Cnn_model)
```
10. Task 19 開神燈：
```python
    if not _ws_skip("開神燈"):
        _run_lamp_if_due(d, ip, stage)
```
11. Task 20 轉盤金幣（包 `_guarded_run` 呼叫；`_spin_wheel` 定義不動）：
```python
    if not _ws_skip("轉盤金幣"):
        _guarded_run(
            task_name="轉盤金幣",
            mismatch_reason="轉盤金幣執行前不在主頁面",
            fn=_spin_wheel,
            step="執行中",
        )
```

- [ ] **Step 7.4: 語法檢查**

Run: `python -m py_compile game_actions/daily_pipeline.py`
Expected: 無輸出（exit 0）

- [ ] **Step 7.5: Commit**

```bash
git add game_actions/daily_pipeline.py
git commit -m "feat(pipeline): DailyContext.ws_done — WS 階段已完成任務逐點跳過 (11 處 guard)"
```

---

### Task 8: new_main_v2 插入 WS 階段

**Files:**
- Modify: `new_main_v2.py`（import 區 + wake loop 兩處）

- [ ] **Step 8.1: import 區**（`from game_actions import daily_pipeline` 行後）加：

```python
from game_actions.ws_phase import run_ws_phase
```

- [ ] **Step 8.2: wake loop 插入 WS 階段**——`_maybe_resume_sleep` 的 `if _skip: continue` 之後、`# --- 喚醒與解鎖手機 ---` 註解之前加：

```python
                # --- WS 階段：純 WS 先跑（瀏覽器啟動前；WS 登入會踢頁面，順序不可反）---
                # ws_token.enabled=False 時 run_ws_phase 直接回空集合，零影響。
                ws_done = frozenset()
                try:
                    bot_state.update_state(ip, task="WS 階段", step="純 WS 任務執行中")
                    ws_done = run_ws_phase(ip)
                except Exception as ws_exc:
                    logger.warning(f"[{ip}] WS 階段未預期錯誤（降級，全跑 Playwright）: {ws_exc}")
```

（`run_ws_phase` 內部已有 try/except，這層是雙保險，維持 wake loop 不可炸的不變量。）

- [ ] **Step 8.3: DailyContext 傳入**——`daily_pipeline.run(daily_pipeline.DailyContext(` 呼叫處 `family_manager=family_manager,` 後加：

```python
                    ws_done=ws_done,
```

- [ ] **Step 8.4: 語法檢查**

Run: `python -m py_compile new_main_v2.py`
Expected: 無輸出

- [ ] **Step 8.5: Commit**

```bash
git add new_main_v2.py
git commit -m "feat(main): wake loop 插入 WS 階段 (瀏覽器啟動前) 並把 ws_done 傳入 pipeline"
```

---

### Task 9: Playwright 階段回寫 ticket（自癒迴圈）

**Files:**
- Create: `utils/ws_ticket_refresh.py`
- Modify: `new_main_v2.py`（遊戲啟動成功處）
- Test: `tests/test_ws_ticket_refresh.py`

- [ ] **Step 9.1: 寫失敗測試**

```python
"""utils.ws_ticket_refresh — 從 Playwright page 回寫 _auth_capture JSON。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import ws_ticket_refresh as wtr  # noqa: E402


class _FakePage:
    def __init__(self, result):
        self._result = result

    def evaluate(self, _js):
        return self._result


class _FakeDevice:
    def __init__(self, page):
        self._page = page


_FRESH = {
    "uid": "u1", "loginGameId": "g1", "roleId": 42, "pKey": "newkey",
    "loginTicket": "newticket", "loginSceneId": 1, "isWhiteIp": 0,
    "loginTime": 1770000000, "_ws_url": "wss://x/?token=abc",
}


def _seed_capture(auth_dir, extra=None):
    creds = {"uid": "u1", "uname": "name", "plat": "android",
             "loginGameId": "g1", "roleId": 42, "pKey": "oldkey",
             "loginTicket": "oldticket", "loginSceneId": 1, "isWhiteIp": 0,
             "loginTime": 1760000000, "_ws_url": "wss://x/?token=old"}
    creds.update(extra or {})
    (auth_dir / "_auth_capture_dev1.json").write_text(
        json.dumps({"creds": creds, "_source": "adb_logcat"}), encoding="utf-8")


def test_refresh_updates_ticket_preserves_uname_plat(tmp_path):
    _seed_capture(tmp_path)
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(_FRESH)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is True
    data = json.loads((tmp_path / "_auth_capture_dev1.json")
                      .read_text(encoding="utf-8-sig"))
    creds = data["creds"]
    assert creds["loginTicket"] == "newticket"
    assert creds["pKey"] == "newkey"
    assert creds["loginTime"] == 1770000000
    assert creds["_ws_url"] == "wss://x/?token=abc"
    assert creds["uname"] == "name" and creds["plat"] == "android"  # 保留
    assert data["_source"] == "playwright_refresh"


def test_refresh_no_capture_file_is_noop(tmp_path):
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(_FRESH)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is False


def test_refresh_no_page_is_noop(tmp_path):
    _seed_capture(tmp_path)
    ok = wtr.refresh_from_device(_FakeDevice(None), "dev1", auth_dir=tmp_path)
    assert ok is False


def test_refresh_page_eval_error_is_noop(tmp_path):
    _seed_capture(tmp_path)

    class _Boom:
        def evaluate(self, _js):
            raise RuntimeError("page closed")

    ok = wtr.refresh_from_device(_FakeDevice(_Boom()), "dev1", auth_dir=tmp_path)
    assert ok is False


def test_refresh_missing_ticket_in_result_is_noop(tmp_path):
    _seed_capture(tmp_path)
    bad = dict(_FRESH, loginTicket="")
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(bad)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is False
```

- [ ] **Step 9.2: 跑測試確認失敗**

Run: `python -m pytest tests/test_ws_ticket_refresh.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 9.3: 實作 `utils/ws_ticket_refresh.py`**

```python
"""Playwright 階段順手回寫 ws_token creds（自癒迴圈, spec §5）。

遊戲頁載入完成後，page 內的 LoginDataCache 持有最新 login ticket。
refresh_from_device(d, ip) 用 page.evaluate（in-process，不踢 session、不需 CDP
attach）讀出來，merge 回 auth_state/_auth_capture_<ip>.json —— 只更新會過期的
欄位（loginTicket/pKey/loginTime/...），保留 page 上讀不到的 uname/plat。
下一輪 WS 階段永遠拿到 <1 cycle 舊的 ticket。

一律 best-effort：任何失敗只 log 回 False，絕不打斷 wake cycle。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUTH_DIR = Path(__file__).resolve().parents[1] / "auth_state"

# 與 tools/_auth_capture_probe.py 同源：LoginDataCache 是 chunks 虛擬模組。
# netManager 的 ws url 帶著當前 session 的 token (AUTH_HANDSHAKE_SPEC §7.1)。
_CAPTURE_JS = """
async () => {
  const mod = await System.import('chunks:///_virtual/LoginDataCache.ts');
  const L = mod.LoginDataCache;
  let ws = '';
  try { ws = netManager._cnet._socket.url || ''; } catch (e) {}
  return {
    uid: String(L.uid ?? ''),
    loginGameId: String(L.loginGameId ?? ''),
    roleId: Number(L.roleId ?? 0),
    pKey: String(L.pKey ?? ''),
    loginTicket: String(L.loginTicket ?? ''),
    loginSceneId: Number(L.loginSceneId ?? 0),
    isWhiteIp: Number(L.isWhiteIp ?? 0),
    loginTime: Number(L.loginTime ?? 0),
    _ws_url: ws,
  };
}
"""

# merge 進 capture 的欄位（page 讀得到、且會過期/變動的）
_REFRESH_KEYS = ("uid", "loginGameId", "roleId", "pKey", "loginTicket",
                 "loginSceneId", "isWhiteIp", "loginTime", "_ws_url")


def refresh_from_device(d, ip: str, *, auth_dir: Path = AUTH_DIR) -> bool:
    """從 d._page 讀 LoginDataCache 並 merge 回 capture 檔。成功回 True。"""
    page = getattr(d, "_page", None)
    if page is None:
        logger.debug("[%s] ws ticket refresh: no _page on device, skip", ip)
        return False
    path = Path(auth_dir) / f"_auth_capture_{ip}.json"
    if not path.exists():
        logger.info("[%s] ws ticket refresh: 無既有 capture 檔 (%s)，跳過", ip, path)
        return False
    try:
        fresh = page.evaluate(_CAPTURE_JS)
    except Exception as exc:  # noqa: BLE001 — page 可能剛好關閉/導航，不能炸 wake cycle
        logger.warning("[%s] ws ticket refresh: page.evaluate 失敗: %s", ip, exc)
        return False
    if not isinstance(fresh, dict) or not fresh.get("loginTicket"):
        logger.warning("[%s] ws ticket refresh: 讀不到 loginTicket，跳過", ip)
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        creds = dict(data.get("creds") or {})
        for k in _REFRESH_KEYS:
            if fresh.get(k) not in (None, "", 0) or k in ("isWhiteIp",):
                creds[k] = fresh[k]
        data["creds"] = creds
        data["_source"] = "playwright_refresh"
        data["_captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        age_h = (time.time() - float(creds.get("loginTime") or 0)) / 3600.0
        logger.info("[%s] ws ticket refresh: 已回寫 ticket (loginTime age %.1fh)",
                    ip, age_h)
        return True
    except (OSError, ValueError) as exc:
        logger.warning("[%s] ws ticket refresh: 寫回失敗: %s", ip, exc)
        return False
```

- [ ] **Step 9.4: 跑測試確認通過**

Run: `python -m pytest tests/test_ws_ticket_refresh.py -q`
Expected: 5 passed

- [ ] **Step 9.5: 接到 new_main_v2**——`result = handle_game_startup_pages(...)` 成功分支（`logger.info(f"[{ip}] 遊戲已進入可操作狀態")` 行後）加：

```python
                        if backend_kind == "web_h5":
                            from utils.ws_ticket_refresh import refresh_from_device
                            refresh_from_device(d, ip)
```

- [ ] **Step 9.6: 語法檢查 + Commit**

Run: `python -m py_compile new_main_v2.py utils/ws_ticket_refresh.py`

```bash
git add utils/ws_ticket_refresh.py tests/test_ws_ticket_refresh.py new_main_v2.py
git commit -m "feat(ws_token): Playwright 階段回寫 ticket — 自癒迴圈 (best-effort, 不打斷 wake cycle)"
```

> **live-confirm（Task 11 一併驗）**：`_CAPTURE_JS` 的 LoginDataCache 屬性名
> （uid/pKey/loginTicket/...）依 AUTH_HANDSHAKE_SPEC §7.1 重建，上線前先對小寶
> 的 live page 用 `tools/_auth_capture_probe.py 9226` 跑同一段 JS 驗欄位都非空。

---

### Task 10: bot_config.json 開小寶 pilot + 全測一輪

**Files:**
- Modify: `bot_config.json`（`7fe98fc6` 區塊）

- [ ] **Step 10.1: 7fe98fc6 加 ws_token 區塊**（`"online_check_interval": 5` 後）

```json
            "ws_token": {
                "enabled": true,
                "spend": true,
                "open_lamp": true,
                "farm": null,
                "dungeon_sweeps": [],
                "carpark_target": null,
                "couple_gifts": true,
                "forge_ring": false
            }
```

（farm/dungeon_sweeps 留空 → 農場任務/萬神試煉照舊 Playwright 跑；live 驗證時
CDP 抓到小寶的免費 seed_id 後再填，填了 skip 才生效。）

- [ ] **Step 10.2: 焦點測試全跑一輪**

Run: `python -m pytest tests/test_ws_token_couple.py tests/test_ws_token_workshop.py tests/test_ws_token_runner.py tests/test_ws_token_state.py tests/test_ws_phase.py tests/test_ws_phase_config.py tests/test_ws_ticket_refresh.py tests/test_ws_runner_wiring.py -q`
Expected: 全綠

- [ ] **Step 10.3: Commit**

```bash
git add bot_config.json
git commit -m "feat(config): 小寶 7fe98fc6 開 ws_token pilot (spend+lamp on; farm/sweep 待 live 填)"
```

---

### Task 11: Live 驗證（小寶；依 manual-hold 慣例）

**Files:** 無（驗證 + 補 config）

- [ ] **Step 11.1: 驗 ticket capture JS**（用既有 probe 跑 Task 9 的 JS，欄位須全非空）

```bash
# 需要小寶的瀏覽器在線（web_debug_port 9226）；mojibake 防護照 lessons:
$env:PYTHONIOENCODING='utf-8'
echo "System.import('chunks:///_virtual/LoginDataCache.ts').then(m=>JSON.stringify({t:!!m.LoginDataCache.loginTicket,p:!!m.LoginDataCache.pKey,u:m.LoginDataCache.uid}))" | python tools/_auth_capture_probe.py 9226 --await
```

Expected: `{"t":true,"p":true,"u":"<uid>"}`。欄位名不符 → 修 `_CAPTURE_JS` 後重跑 Task 9 測試。

- [ ] **Step 11.2: CDP 抓小寶農場免費 seed_id**（`window.config*`/`*DataCache`，照 lessons「先問 client」）→ 填進 `bot_config.json` 的 `ws_token.farm.seed_id`，commit `feat(config): 小寶 farm seed_id`

- [ ] **Step 11.3: runner 全任務 live**

```bash
python -m ws_token.runner --device 7fe98fc6 --spend --open-lamp
```

Expected: `login_ok=True`；`tasks_ok` 含 `spirit/workshop/couple`；couple 摘要
`milk_tea/flower` 的 `stopped_reason=error_code=3`（送光）；`errors` 只允許
event-gated 類（treasure dormant 等）。**逐項核對輸出，有 error 先修再往下。**

- [ ] **Step 11.4: 完整 wake cycle**——重啟 bot（lessons：改檔必重啟），等小寶下一個 odd-hour 喚醒（或 dashboard 手動觸發），看 `logs/7fe98fc6/main.log`：

Expected 順序：`WS 階段 → WS 階段完成 (...) skip=[...] → 遊戲已進入可操作狀態 → ws ticket refresh: 已回寫 → <skip 任務逐條「WS 階段已完成，跳過」> → 挖礦/武道會等照跑`

- [ ] **Step 11.5: 觀察數日**——確認：每輪 ticket age log < 2.5h、無 WSLoginError 連發、奶茶/玫瑰每天送光（couple batches_ok ≥1 只在每日第一輪）、workshop 12h 輪換 log 一天兩次。

- [ ] **Step 11.6: 記錄**——驗證結果寫進 `tasks/todo.md` review 段；TTL 相關觀察記進 `docs/protocol/AUTH_HANDSHAKE_SPEC.md` §7。

---

### Task 12: 文件同步 + 收尾

- [ ] **Step 12.1: CLAUDE.md** Key Modules 表加一行：

```markdown
| WS-first 階段 | `game_actions/ws_phase.py` | 喚醒後先跑純 WS 任務（`ws_token/runner.py`），成功項由 daily_pipeline 跳過；ticket 由 Playwright 階段回寫（`utils/ws_ticket_refresh.py`）。per-device config `ws_token.enabled` |
```

- [ ] **Step 12.2: `tasks/ws_token_backend_todo.md`** 補「workflow 接入完成」段（含 pilot 結果與 ADB 模式待辦指回 spec §4）。

- [ ] **Step 12.3: Commit**

```bash
git add CLAUDE.md tasks/ws_token_backend_todo.md
git commit -m "docs: ws_phase 模組導覽 + ws_token workflow 接入進度"
```

---

## 自我審查結果（已跑）

- **Spec 覆蓋**：§2.1→Task 1；§2.2→Task 3/4；§2.3（不檢查在線）→無任務（正確）；§3.1→Task 5/10；§3.2→Task 8；§3.3→Task 6/7；§5→Task 9；§7→各 task 測試步 + Task 11；§8 條件式 farm/dungeon skip→Task 6。**§4 ADB 模式刻意不在本計畫**（pilot 後另開）。
- **型別/命名一致**：`give_all_in_hand` 回傳 `{batches_ok, stopped_reason}`（Task 1 定義 = Task 4 測試引用）；`rotate_team_recipes(client, parity=)`（Task 3 = Task 4）；`run_ws_phase(ip) -> frozenset[str]`（Task 6 = Task 8）；`ws_done`（Task 7 = Task 8）。
- **已知風險已標**：`_CAPTURE_JS` 欄位名是重建的 → Task 11.1 先驗；daily_pipeline 無單測 → py_compile + live cycle 把關。
