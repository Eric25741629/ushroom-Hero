# 龍骸聖域自動化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 H5 後端自動化龍骸聖域：探索 → 依事件型別(怪物/遺跡/陷阱)決策 → 拿鑰匙進第2層 → 第2層探到可進第3層即停；陷阱只求助、體力用完即停、協助隊友並領獎。

**Architecture:** 純函式決策核心 (`planner.py`/`state.py`) 對映逆向自客戶端的 auto-explore 決策樹，operate on 我們自定的正規化 `DragonState`，完全可單測且不碰 Playwright。IO 邊界 (`client.py`) 用 `page.evaluate` 呼叫客戶端自身的具名 RPC (`window.netManager.send("dragon_realm.*_c2s", obj)`) 並訂閱 `*_s2c` 推播，重用客戶端的 protobuf 序列化與解析。`service.py` 跑 read→decide→act→wait 迴圈，含 wall-clock 預算與 dead-loop 偵測。接進 `daily_pipeline`，由 `dragon_realm_enabled` flag 閘控，預設 off。

**Tech Stack:** Python 3 / dataclasses、Playwright (page.evaluate)、pytest。對齊既有 `sea_v2/` 分層與 `game_actions/*_scheduler.py` wiring 模式。

**Spec:** `docs/superpowers/specs/2026-06-04-dragon-realm-automation-design.md`

---

## File Structure

| 檔案 | 職責 | 何時建立 |
|------|------|----------|
| `dragon_realm/__init__.py` | package + `use_dragon_realm(ip, config)` flag helper | Task 0 |
| `dragon_realm/constants.py` | EventType / EventDataKey / Choice / Action kind 常數（消滅 magic number） | Task 2 |
| `dragon_realm/state.py` | `DragonEvent` / `DragonConfig` / `DragonState` frozen dataclass + `DragonState.from_raw()` | Task 3 |
| `dragon_realm/planner.py` | `Action` frozen dataclass + 純函式 `decide(state, config, prefs)` | Task 4 |
| `dragon_realm/client.py` | H5 RPC 橋接 (`DragonClient`)：send 具名 c2s + 讀 `window.__dr_state` + 讀 config | Task 5 |
| `dragon_realm/service.py` | `run(ip, d)` 迴圈協調 + 預算/dead-loop/錯誤截圖 | Task 6 |
| `game_actions/dragon_realm_scheduler.py` | `run_dragon_realm_if_due(ip, d)`，flag + 每日冷卻 | Task 7 |
| `game_actions/daily_pipeline.py` | 在 pipeline 尾段加掛呼叫（最小改動） | Task 7 |
| `docs/protocol/DRAGON_REALM_SCHEMA.md` | Task 1 產出的協議文件（s2c 欄位、單例存取、導航、config 值） | Task 1 |
| `tools/_dragon_realm_probe.py` | Task 1 的 live 偵察腳本（dump s2c/config 到 fixture） | Task 1 |
| `tests/fixtures/dragon_realm/*.json` | Task 1 擷取的真實 s2c/config 樣本，供 state/planner 測試 | Task 1 |
| `tests/test_dragon_realm_state.py` | state 解析測試 | Task 3 |
| `tests/test_dragon_realm_planner.py` | 決策樹測試（含我方覆寫） | Task 4 |
| `tests/test_dragon_realm_service.py` | 迴圈/終止/dead-loop 測試（fake client） | Task 6 |

---

## Task 0: Package skeleton + feature flag

**Files:**
- Create: `dragon_realm/__init__.py`
- Test: `tests/test_dragon_realm_flag.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dragon_realm_flag.py
from dragon_realm import use_dragon_realm


def test_flag_defaults_off_when_unset():
    assert use_dragon_realm("emulator-5560", {}) is False


def test_global_flag_enables():
    cfg = {"global": {"dragon_realm_enabled": True}}
    assert use_dragon_realm("emulator-5560", cfg) is True


def test_per_device_overrides_global():
    cfg = {
        "global": {"dragon_realm_enabled": True},
        "devices": {"emulator-5560": {"dragon_realm_enabled": False}},
    }
    assert use_dragon_realm("emulator-5560", cfg) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dragon_realm_flag.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dragon_realm'`

- [ ] **Step 3: Write minimal implementation**

```python
# dragon_realm/__init__.py
"""龍骸聖域 (dragon_realm / ActivityLhsy) 自動化 — H5-first，RPC-driven。

Not wired into runtime by default. Opt in via the ``dragon_realm_enabled`` flag
(per-device overrides global), gated by :func:`use_dragon_realm`.

Design: docs/superpowers/specs/2026-06-04-dragon-realm-automation-design.md
Protocol: docs/protocol/DRAGON_REALM_SCHEMA.md
"""
from __future__ import annotations


def use_dragon_realm(ip: str, config: dict) -> bool:
    """Feature flag: per-device ``dragon_realm_enabled`` overrides
    ``global.dragon_realm_enabled``. Defaults off."""
    dev = (config.get("devices") or {}).get(ip) or {}
    if "dragon_realm_enabled" in dev:
        return bool(dev["dragon_realm_enabled"])
    return bool((config.get("global") or {}).get("dragon_realm_enabled", False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dragon_realm_flag.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add dragon_realm/__init__.py tests/test_dragon_realm_flag.py
git commit -m "feat(dragon_realm): package skeleton + dragon_realm_enabled flag"
```

---

## Task 1: Live protocol recon (probe + schema doc + fixtures)

> 這是唯一依賴 live H5 的偵察任務。產出：協議文件 + 真實 JSON fixtures。後續 Task 3/5 據此實作，**不再有 protocol 未知**。
> 在 manual-hold 獨佔的 H5 裝置上跑（勿挑正在跑的裝置；先看 main.log）。觸發 dual-backend-task-dev / cocos-app-analysis skill 取得 attach 手法。

**Files:**
- Create: `tools/_dragon_realm_probe.py`
- Create: `docs/protocol/DRAGON_REALM_SCHEMA.md`
- Create: `tests/fixtures/dragon_realm/info_no_event.json`, `info_monster_challenge.json`, `info_trap.json`, `info_box.json`, `info_layer2.json`, `config_kv.json`

- [ ] **Step 1: 寫偵察腳本**（attach 既有 CDP session，註冊 listener，dump 原始 s2c 與 config）

```python
# tools/_dragon_realm_probe.py
"""龍骸聖域協議偵察：attach 既有 H5 CDP session，註冊 dragon_realm s2c listener，
把原始 info_s2c / help_event_list_s2c 與 config_kv dump 成 JSON，供 schema 文件與測試 fixture。

用法（在 manual-hold 獨佔的 H5 裝置、活動已開且人已在龍骸聖域內）：
    conda activate mushroom1
    python tools/_dragon_realm_probe.py --cdp http://127.0.0.1:9230 --out tests/fixtures/dragon_realm
然後在遊戲裡手動：開始探索一次 / 觸發怪物挑戰 / 觸發陷阱 / 觸發寶箱 / 進第2層，
每次 stdout 會印出最新 __dr_state，存成對應 fixture 檔。
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright

# 一次性安裝 listener + 暴露讀取器。欄位名以原始 payload 為準（本腳本不正規化）。
INSTALL_JS = r"""
() => {
  const nm = window.netManager;
  if (!nm) return "no netManager";
  if (window.__dr_installed) return "already";
  window.__dr_state = {};
  const cap = (key) => (e) => {
    try { window.__dr_state[key] = { ts: Date.now(), data: JSON.parse(JSON.stringify(e)) }; }
    catch (err) { window.__dr_state[key] = { ts: Date.now(), err: String(err) }; }
  };
  const evts = [
    "dragon_realm.dragon_realm_info_s2c",
    "dragon_realm.dragon_realm_event_update_s2c",
    "dragon_realm.dragon_realm_help_event_list_s2c",
    "dragon_realm.dragon_realm_enter_ceng_s2c",
  ];
  for (const ev of evts) nm.addEventListener(ev, cap(ev), window);
  window.__dr_installed = true;
  // 主動拉一次 info
  nm.send("dragon_realm.dragon_realm_info_c2s", {});
  return "installed";
}
"""

READ_JS = "() => window.__dr_state || {}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        print("install:", page.evaluate(INSTALL_JS))
        print("觀察中。Ctrl+C 結束。每次操作後印出最新 state。")
        try:
            while True:
                time.sleep(args.poll)
                state = page.evaluate(READ_JS)
                print(json.dumps(state, ensure_ascii=False)[:4000])
        except KeyboardInterrupt:
            state = page.evaluate(READ_JS)
            print("\n最終 state 已 dump，請手動存成 fixtures：")
            print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑偵察腳本並擷取各情境**

Run（裝置在龍骸聖域內）：`python tools/_dragon_realm_probe.py --cdp http://127.0.0.1:9230 --out tests/fixtures/dragon_realm`
依序手動觸發：無事件待探索 / 怪物挑戰 / 陷阱 / 寶箱 / 進第2層，把每個情境的 `dragon_realm_info_s2c.data` 物件存成對應 fixture 檔（`info_no_event.json` 等）。help_event_list 存進 `info_*` 或獨立檔皆可。

- [ ] **Step 3: 解 config KV 與單例存取，寫進 schema 文件**

在同一 page 上 evaluate 取出 config（參考 configFly 列舉法；實際 accessor 於此確認）並存成 `config_kv.json`，至少含：`ENTER_TIER_TWO_REQUIRE(key7)`、`ENTER_TIER_THREE_REQUIRE(key8)`、`STAMINA_TIER(key17)`、`BACK_KILL(key16)`、`CHEST_KEY(key15)`、`STAMINA_ITEM(key26)`。

`docs/protocol/DRAGON_REALM_SCHEMA.md` 需記錄：
- `window.netManager.send/addEventListener` 對 dragon_realm 是否可直接用（驗證）。
- `dragon_realm_info_s2c` payload 的**確切欄位路徑**：`ceng`、當前事件 `event_id`/`event_uid`/事件 `data` 陣列(含 EventDataKey k/v)、`help_hp`、`hp`(體力)、`eventList` 元素 shape(`role_id`/`event_id`/`id`/`back_kill_time`)、`server_time`、背包鑰匙數量來源。
- `help_event_list_s2c` 可領事件 id 來源。
- **`event_list` 是否包含「當前 active 事件」**（關鍵）：若包含，planner 的「再次擊殺(列表)」路徑可能蓋過陷阱/怪物 ASK_HELP 覆寫。記錄 active 事件與 `event_list` 元素的關係（uid/event_id 是否重疊）。見 planner.py 的 `NB(live-verify)` 註解。
- `ActivityType.ActivityLhsy` 的 activity id 與進場導航路徑（開活動 → 進龍骸聖域）。
- config KV 的讀取 accessor 與各 key 實際值。
- **背包鑰匙數量讀取**（關鍵，Task 5 已知缺口）：client.py `_READ_JS` 目前讀 `window.__dr_bag`（無人寫入→恆 {}）。需確認如何 live 讀 `BagModel.getGoodsCountByGoodsGtid(gtid)`（單例存取），把 tier2/tier3 鑰匙與寶箱鑰匙 gtid 的數量填進 raw.bag；否則 planner 永遠偵測不到可進下一層。Task 5 的 `_READ_JS` 需據此改成直接查 BagModel 指定 gtid。

**EventDataKey 對照（已逆向確認）**：PveHp=1, TrapTime=2, BackKillTime=3, IsChallenge=4, RoleId=5, MaxHp=6, IsAskHelp=7, Ceng=9
**EventType（已確認）**：PVE=1, PVP=2, BOX=3, TRAP=4, BUFF=5, CAVE=6

- [ ] **Step 4: Commit**

```bash
git add tools/_dragon_realm_probe.py docs/protocol/DRAGON_REALM_SCHEMA.md tests/fixtures/dragon_realm/
git commit -m "feat(dragon_realm): live protocol probe + schema doc + s2c/config fixtures"
```

---

## Task 2: Constants

**Files:**
- Create: `dragon_realm/constants.py`
- Test: `tests/test_dragon_realm_constants.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dragon_realm_constants.py
from dragon_realm import constants as C


def test_event_types():
    assert (C.PVE, C.PVP, C.BOX, C.TRAP, C.BUFF, C.CAVE) == (1, 2, 3, 4, 5, 6)


def test_event_data_keys():
    assert C.K_BACK_KILL_TIME == 3
    assert C.K_IS_CHALLENGE == 4
    assert C.K_ROLE_ID == 5
    assert C.K_IS_ASK_HELP == 7


def test_choices():
    assert (C.CHOICE_ADVANCE, C.CHOICE_DETOUR, C.CHOICE_ASK_HELP) == (1, 2, 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dragon_realm_constants.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dragon_realm.constants'`

- [ ] **Step 3: Write minimal implementation**

```python
# dragon_realm/constants.py
"""龍骸聖域常數（逆向自客戶端 index.966f5.js，2026-06-04）。"""
from __future__ import annotations

# EventType
PVE = 1
PVP = 2
BOX = 3
TRAP = 4
BUFF = 5
CAVE = 6
MONSTER_TYPES = (PVE, PVP)

# EventDataKey（當前事件 data 陣列的 k）
K_PVE_HP = 1
K_TRAP_TIME = 2
K_BACK_KILL_TIME = 3
K_IS_CHALLENGE = 4
K_ROLE_ID = 5
K_MAX_HP = 6
K_IS_ASK_HELP = 7
K_CENG = 9

# event_choice c2s 的 choice 值
CHOICE_ADVANCE = 1   # 前進 / 擊殺 / (掙扎 — 我方不用)
CHOICE_DETOUR = 2    # 繞路（寶箱無鑰匙）
CHOICE_ASK_HELP = 3  # 求助

# Action kind
A_EXPLORE = "explore"
A_CHOICE = "choice"
A_ENTER_CENG = "enter_ceng"
A_PROVIDE_HELP = "provide_help"
A_RECEIVE_HELP = "receive_help"
A_WAIT = "wait"
A_STOP = "stop"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dragon_realm_constants.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add dragon_realm/constants.py tests/test_dragon_realm_constants.py
git commit -m "feat(dragon_realm): event-type / data-key / choice constants"
```

---

## Task 3: State model + normalization

> `from_raw` 的 raw dict 形狀 = Task 1 擷取的 `info_s2c.data`。實作時對照 `DRAGON_REALM_SCHEMA.md` 與 fixtures 調整鍵路徑；測試直接吃 fixtures。下方為**正規化後**的 `DragonState` 結構與一組以正規化中間 dict 表達的測試（不依賴 live 鍵名），確保 from_raw 的輸出契約固定。

**Files:**
- Create: `dragon_realm/state.py`
- Test: `tests/test_dragon_realm_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dragon_realm_state.py
from dragon_realm.state import DragonState, DragonEvent
from dragon_realm import constants as C


def _raw(**over):
    # 正規化前的「中間」形狀：from_raw 接受已攤平的事件 data（k->v dict）。
    base = {
        "activity_open": True,
        "ceng": 1,
        "hp": 30,
        "server_time": 1000,
        "help_hp": 0,
        "event_id": 0,
        "event_type": 0,
        "event_uid": 0,
        "event_data": {},          # k(EventDataKey)->v
        "event_list": [],
        "help_events": [],
        "bag": {},
    }
    base.update(over)
    return base


def test_no_event_state():
    s = DragonState.from_raw(_raw(), my_role_id=777)
    assert s.activity_open and s.ceng == 1 and s.event_id == 0


def test_monster_challenge_flags_extracted_from_event_data():
    raw = _raw(
        event_id=5001, event_type=C.PVE, event_uid=42,
        event_data={C.K_IS_CHALLENGE: 1, C.K_IS_ASK_HELP: 0, C.K_BACK_KILL_TIME: 900},
    )
    s = DragonState.from_raw(raw, my_role_id=777)
    assert s.is_challenge is True
    assert s.is_ask_help is False
    assert s.back_kill_time == 900


def test_event_list_marks_mine_vs_teammate():
    raw = _raw(event_list=[
        {"role_id": 777, "event_id": 5001, "id": 1, "event_type": C.PVE, "back_kill_time": 0},
        {"role_id": 888, "event_id": 5002, "id": 2, "event_type": C.PVE, "back_kill_time": 0},
    ])
    s = DragonState.from_raw(raw, my_role_id=777)
    assert [e.is_mine for e in s.event_list] == [True, False]
    assert s.event_list[0].uid == 1


def test_bag_lookup_helper():
    s = DragonState.from_raw(_raw(bag={1518: 3}), my_role_id=777)
    assert s.bag_count(1518) == 3
    assert s.bag_count(9999) == 0


def test_missing_fields_default_safely():
    s = DragonState.from_raw({"activity_open": True}, my_role_id=777)
    assert s.ceng == 1 and s.hp == 0 and s.event_id == 0 and s.event_list == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dragon_realm_state.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dragon_realm.state'`

- [ ] **Step 3: Write minimal implementation**

```python
# dragon_realm/state.py
"""龍骸聖域狀態模型 + 正規化。純 Python、可單測。

``from_raw`` 接受 client.py 從 ``window.__dr_state`` 攤平後的中間 dict
（事件 data 已轉成 EventDataKey->value 的 dict）。實際 live 鍵路徑見
docs/protocol/DRAGON_REALM_SCHEMA.md；client.py 負責把 raw s2c 攤成此形狀。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from dragon_realm import constants as C


@dataclass(frozen=True)
class DragonEvent:
    role_id: int
    event_id: int
    event_type: int
    uid: int
    back_kill_time: int
    is_mine: bool


@dataclass(frozen=True)
class DragonState:
    activity_open: bool
    ceng: int
    hp: int
    server_time: int
    help_hp: int
    event_id: int
    event_type: int
    event_uid: int
    is_challenge: bool
    is_ask_help: bool
    back_kill_time: int
    event_list: tuple
    help_events: tuple
    bag: Mapping[int, int]

    def bag_count(self, gtid: int) -> int:
        return int(self.bag.get(gtid, 0))

    @staticmethod
    def from_raw(raw: dict, my_role_id: int) -> "DragonState":
        data: Mapping[int, int] = {int(k): int(v) for k, v in (raw.get("event_data") or {}).items()}
        events = []
        for e in (raw.get("event_list") or []):
            rid = int(e.get("role_id", 0))
            events.append(DragonEvent(
                role_id=rid,
                event_id=int(e.get("event_id", 0)),
                event_type=int(e.get("event_type", 0)),
                uid=int(e.get("id", 0)),
                back_kill_time=int(e.get("back_kill_time", 0)),
                is_mine=(rid == my_role_id),
            ))
        return DragonState(
            activity_open=bool(raw.get("activity_open", False)),
            ceng=int(raw.get("ceng", 1)),
            hp=int(raw.get("hp", 0)),
            server_time=int(raw.get("server_time", 0)),
            help_hp=int(raw.get("help_hp", 0)),
            event_id=int(raw.get("event_id", 0)),
            event_type=int(raw.get("event_type", 0)),
            event_uid=int(raw.get("event_uid", 0)),
            is_challenge=bool(data.get(C.K_IS_CHALLENGE, 0)),
            is_ask_help=bool(data.get(C.K_IS_ASK_HELP, 0)),
            back_kill_time=int(data.get(C.K_BACK_KILL_TIME, 0)),
            event_list=tuple(events),
            help_events=tuple(int(x) for x in (raw.get("help_events") or [])),
            bag=dict(raw.get("bag") or {}),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dragon_realm_state.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add dragon_realm/state.py tests/test_dragon_realm_state.py
git commit -m "feat(dragon_realm): DragonState/DragonEvent model + from_raw normalization"
```

---

## Task 4: Planner (決策樹核心)

> 這是任務的心臟，完全可單測、現在就能寫完。port 自客戶端 `autoExploreHandler` + 我方三項覆寫（陷阱只求助、進2層探到可進3層即停、體力不足即停）。

**Files:**
- Create: `dragon_realm/planner.py`
- Test: `tests/test_dragon_realm_planner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dragon_realm_planner.py
from dragon_realm.planner import decide, Action, Prefs
from dragon_realm.state import DragonState
from dragon_realm import constants as C

KEY2 = 1518   # 進第2層鑰匙 gtid（fixture 佔位；實際值見 config_kv.json）
KEY3 = 1519   # 進第3層鑰匙 gtid


class Cfg:
    tier_two_require = (KEY2, 1)
    tier_three_require = (KEY3, 1)
    stamina_tier = (5, 8, 10)     # 每層每次探索體力
    back_kill_cooldown = 600
    chest_key = {7001: 1520}      # event_id -> 鑰匙 gtid
    stamina_item_gtid = 1521


PREFS = Prefs(my_role_id=777, assist_teammates=True, auto_open_box=True)


def _state(**over):
    base = dict(activity_open=True, ceng=1, hp=30, server_time=10000, help_hp=0,
                event_id=0, event_type=0, event_uid=0, is_challenge=False,
                is_ask_help=False, back_kill_time=0, event_list=(), help_events=(), bag={})
    base.update(over)
    return DragonState(**base)


# --- 探索 / 體力 ---
def test_no_event_with_stamina_explores():
    assert decide(_state(hp=30), Cfg, PREFS).kind == C.A_EXPLORE

def test_out_of_stamina_stops_without_using_item():
    a = decide(_state(hp=4, bag={1521: 99}), Cfg, PREFS)   # 體力<5 但有體力道具
    assert a.kind == C.A_STOP
    assert a.reason == "out_of_stamina"

# --- 進層 / 終止覆寫 ---
def test_layer1_with_key_enters_layer2():
    a = decide(_state(ceng=1, bag={KEY2: 1}), Cfg, PREFS)
    assert a.kind == C.A_ENTER_CENG and a.ceng == 2

def test_layer2_reaching_tier3_gate_stops_not_enter():
    a = decide(_state(ceng=2, bag={KEY3: 1}), Cfg, PREFS)
    assert a.kind == C.A_STOP and a.reason == "reached_tier_three_gate"

def test_layer2_without_tier3_key_keeps_exploring():
    assert decide(_state(ceng=2, hp=30, bag={}), Cfg, PREFS).kind == C.A_EXPLORE

# --- 怪物 ---
def test_monster_killable_advances():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=False), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

def test_monster_challenge_asks_help():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=True, is_ask_help=False), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ASK_HELP

def test_monster_already_asked_waits_until_cooldown():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=True,
                      is_ask_help=True, back_kill_time=9999, server_time=10000), Cfg, PREFS)
    assert a.kind == C.A_WAIT

def test_monster_already_asked_rekills_after_cooldown():
    a = decide(_state(event_id=5001, event_type=C.PVE, event_uid=42, is_challenge=True,
                      is_ask_help=True, back_kill_time=1000, server_time=10000), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE and a.uid == 42

# --- 陷阱（覆寫：只求助，絕不掙扎）---
def test_trap_challenge_always_asks_help_never_struggles():
    a = decide(_state(event_id=6001, event_type=C.TRAP, event_uid=7, is_challenge=True), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ASK_HELP

def test_trap_non_challenge_advances():
    a = decide(_state(event_id=6001, event_type=C.TRAP, event_uid=7, is_challenge=False), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

# --- 寶箱 ---
def test_box_with_key_advances():
    a = decide(_state(event_id=7001, event_type=C.BOX, bag={1520: 2}), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

def test_box_without_key_detours():
    a = decide(_state(event_id=7001, event_type=C.BOX, bag={}), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_DETOUR

# --- 遺跡 ---
def test_cave_advances():
    a = decide(_state(event_id=8001, event_type=C.CAVE), Cfg, PREFS)
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE

# --- 協助 / 領獎（優先於進層）---
def test_assist_teammate_when_help_hp_available():
    ev = {"role_id": 888, "event_id": 5002, "id": 2, "event_type": C.PVE, "back_kill_time": 0}
    from dragon_realm.state import DragonEvent
    teammate = DragonEvent(888, 5002, C.PVE, 2, 0, is_mine=False)
    a = decide(_state(help_hp=10, event_list=(teammate,)), Cfg, PREFS)
    assert a.kind == C.A_PROVIDE_HELP and a.role_id == 888 and a.event_id == 5002

def test_assist_skipped_when_no_help_hp():
    from dragon_realm.state import DragonEvent
    teammate = DragonEvent(888, 5002, C.PVE, 2, 0, is_mine=False)
    a = decide(_state(help_hp=0, hp=30, event_list=(teammate,)), Cfg, PREFS)
    assert a.kind == C.A_EXPLORE

def test_receive_help_reward_when_available():
    a = decide(_state(help_events=(321,)), Cfg, PREFS)
    assert a.kind == C.A_RECEIVE_HELP and a.event_id == 321

def test_rekill_from_list_takes_priority():
    from dragon_realm.state import DragonEvent
    mine = DragonEvent(777, 5001, C.PVE, 9, back_kill_time=1000, is_mine=True)
    a = decide(_state(server_time=10000, event_list=(mine,)), Cfg, PREFS)  # cooldown 過
    assert a.kind == C.A_CHOICE and a.choice == C.CHOICE_ADVANCE and a.uid == 9

# --- 活動關閉 ---
def test_activity_closed_stops():
    assert decide(_state(activity_open=False), Cfg, PREFS).kind == C.A_STOP
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dragon_realm_planner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dragon_realm.planner'`

- [ ] **Step 3: Write minimal implementation**

```python
# dragon_realm/planner.py
"""龍骸聖域決策樹（純函式，無 IO）。

port 自客戶端 ActivityLhsyDataCache.autoExploreHandler，含三項覆寫：
  1. 陷阱 IsChallenge 一律求助 CHOICE_ASK_HELP，絕不 CHOICE_ADVANCE 掙扎。
  2. ceng==2 達 tier_three_require 即 STOP，不 enter_ceng(3)。
  3. 體力不足即 STOP，不使用體力道具。
判斷順序對齊客戶端：再次擊殺(列表) → 協助隊友 → 領獎 → 進層/停 → 當前事件。
"""
from __future__ import annotations

from dataclasses import dataclass

from dragon_realm import constants as C
from dragon_realm.state import DragonState


@dataclass(frozen=True)
class Prefs:
    my_role_id: int
    assist_teammates: bool = True
    auto_open_box: bool = True


@dataclass(frozen=True)
class Action:
    kind: str
    choice: int = 0
    uid: int = 0
    ceng: int = 0
    role_id: int = 0
    event_id: int = 0
    reason: str = ""

    @staticmethod
    def explore() -> "Action":
        return Action(C.A_EXPLORE)

    @staticmethod
    def choice(choice: int, uid: int = 0) -> "Action":
        return Action(C.A_CHOICE, choice=choice, uid=uid)

    @staticmethod
    def enter_ceng(ceng: int) -> "Action":
        return Action(C.A_ENTER_CENG, ceng=ceng)

    @staticmethod
    def provide_help(role_id: int, event_id: int) -> "Action":
        return Action(C.A_PROVIDE_HELP, role_id=role_id, event_id=event_id)

    @staticmethod
    def receive_help(event_id: int) -> "Action":
        return Action(C.A_RECEIVE_HELP, event_id=event_id)

    @staticmethod
    def wait() -> "Action":
        return Action(C.A_WAIT)

    @staticmethod
    def stop(reason: str) -> "Action":
        return Action(C.A_STOP, reason=reason)


def _cooldown_passed(state: DragonState, back_kill_time: int, cooldown: int) -> bool:
    return back_kill_time + cooldown - state.server_time <= 0


def decide(state: DragonState, config, prefs: Prefs) -> Action:
    if not state.activity_open:
        return Action.stop("activity_closed")

    # 1. 再次擊殺（列表）：我方 PVE/PVP 事件冷卻已過
    for ev in state.event_list:
        if ev.is_mine and ev.event_type in C.MONSTER_TYPES and ev.event_id:
            if _cooldown_passed(state, ev.back_kill_time, config.back_kill_cooldown):
                return Action.choice(C.CHOICE_ADVANCE, uid=ev.uid)

    # 2. 協助隊友（有 help_hp 時）
    if prefs.assist_teammates and state.help_hp > 0:
        for ev in state.event_list:
            if (not ev.is_mine) and ev.event_id:
                return Action.provide_help(ev.role_id, ev.event_id)

    # 3. 領取協助獎勵
    if state.help_events:
        return Action.receive_help(state.help_events[0])

    # 4. 進入下一層 / 終止（覆寫）
    if state.ceng == 1:
        gtid, need = config.tier_two_require
        if state.bag_count(gtid) >= need:
            return Action.enter_ceng(2)
    elif state.ceng == 2:
        gtid, need = config.tier_three_require
        if state.bag_count(gtid) >= need:
            return Action.stop("reached_tier_three_gate")

    # 5. 當前事件
    if state.event_id == 0:
        need = config.stamina_tier[state.ceng - 1]
        if state.hp >= need:
            return Action.explore()
        return Action.stop("out_of_stamina")

    et = state.event_type
    if et in C.MONSTER_TYPES:
        if state.is_challenge:
            if not state.is_ask_help:
                return Action.choice(C.CHOICE_ASK_HELP)
            if _cooldown_passed(state, state.back_kill_time, config.back_kill_cooldown):
                return Action.choice(C.CHOICE_ADVANCE, uid=state.event_uid)
            return Action.wait()
        return Action.choice(C.CHOICE_ADVANCE)

    if et == C.TRAP:
        if state.is_challenge:
            return Action.choice(C.CHOICE_ASK_HELP)   # 覆寫：永不掙扎
        return Action.choice(C.CHOICE_ADVANCE)

    if et == C.BOX:
        if prefs.auto_open_box:
            gtid = config.chest_key.get(state.event_id)
            if gtid and state.bag_count(gtid) > 0:
                return Action.choice(C.CHOICE_ADVANCE)
        return Action.choice(C.CHOICE_DETOUR)

    if et in (C.BUFF, C.CAVE):
        return Action.choice(C.CHOICE_ADVANCE)

    return Action.wait()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dragon_realm_planner.py -q`
Expected: PASS (all passed)

- [ ] **Step 5: Commit**

```bash
git add dragon_realm/planner.py tests/test_dragon_realm_planner.py
git commit -m "feat(dragon_realm): pure-function decision tree (planner) + full branch tests"
```

---

## Task 5: H5 RPC client bridge

> IO 邊界。對照 Task 1 的 `DRAGON_REALM_SCHEMA.md` 把 raw s2c 攤成 Task 3 的中間 dict。RPC 走 `window.netManager`（與 `utils/web_game_api` 同源）。
> 下方 JS 的 s2c 欄位攤平處 (`_FLATTEN_JS` 的 `pickEventData` 等) 依 schema 文件填入確切鍵路徑；其餘骨架固定。

**Files:**
- Create: `dragon_realm/client.py`

- [ ] **Step 1: 寫 client（無單測；以 Task 6 fake client 與 Task 8 live 驗證）**

```python
# dragon_realm/client.py
"""龍骸聖域 H5 RPC 橋接。

send 具名 c2s + 讀 window.__dr_state（一次性 listener 暫存最新 s2c）。
欄位攤平對照 docs/protocol/DRAGON_REALM_SCHEMA.md（Task 1 產出）。
"""
from __future__ import annotations

import logging
from typing import Optional

from dragon_realm import constants as C
from dragon_realm.planner import Action

logger = logging.getLogger(__name__)

# 一次性安裝 listener；把最新 info/help 存到 window.__dr_state，附 client 端 ts。
_INSTALL_JS = r"""
() => {
  const nm = window.netManager;
  if (!nm) return false;
  if (window.__dr_installed) return true;
  window.__dr_state = {};
  const cap = (k) => (e) => { try { window.__dr_state[k] = {ts: Date.now(), data: JSON.parse(JSON.stringify(e))}; } catch(_) {} };
  nm.addEventListener("dragon_realm.dragon_realm_info_s2c", cap("info"), window);
  nm.addEventListener("dragon_realm.dragon_realm_event_update_s2c", cap("info"), window);
  nm.addEventListener("dragon_realm.dragon_realm_help_event_list_s2c", cap("help"), window);
  window.__dr_installed = true;
  nm.send("dragon_realm.dragon_realm_info_c2s", {});
  return true;
}
"""

# 把 window.__dr_state 攤成 state.from_raw 的中間 dict。
# NB: 下列鍵路徑（info.ceng / cur.event_id / data 陣列 / eventList / 背包）依
#     DRAGON_REALM_SCHEMA.md 確認後填寫；此處為對照 schema 的實作位置。
_READ_JS = r"""
() => {
  const st = window.__dr_state || {};
  const info = (st.info && st.info.data) || null;
  const helpList = (st.help && st.help.data) || null;
  const ts = (st.info && st.info.ts) || 0;
  if (!info) return {ts: 0, raw: null};
  // pickEventData: 當前事件 data 陣列 [{k,v}] -> {k:v}
  const pickEventData = (arr) => {
    const o = {}; (arr || []).forEach(it => { o[it.k] = it.v; }); return o;
  };
  const cur = info.cur_event || info.current || {};
  const raw = {
    activity_open: true,
    ceng: info.ceng || 1,
    hp: info.hp != null ? info.hp : (info.stamina || 0),
    server_time: info.server_time || 0,
    help_hp: info.help_hp || 0,
    event_id: cur.event_id || 0,
    event_type: cur.event_type || 0,
    event_uid: cur.event_uid || cur.id || 0,
    event_data: pickEventData(cur.data),
    event_list: (info.event_list || []).map(e => ({
      role_id: e.role_id, event_id: e.event_id, id: e.id,
      event_type: e.event_type, back_kill_time: e.back_kill_time || 0,
    })),
    help_events: (helpList && helpList.list ? helpList.list : []).map(x => x.event_id || x.id),
    bag: window.__dr_bag || {},
  };
  return {ts: ts, raw: raw};
}
"""

# 送具名 c2s。args 為 [msgName, payloadObj]。
_SEND_JS = r"""
(args) => {
  const nm = window.netManager;
  if (!nm) return false;
  nm.send(args[0], args[1] || {});
  return true;
}
"""

_PREFIX = "dragon_realm."


class DragonClient:
    """Playwright page 上的龍骸聖域 RPC 介面。"""

    def __init__(self, page, my_role_id: int):
        self._page = page
        self.my_role_id = my_role_id

    def install(self) -> bool:
        return bool(self._page.evaluate(_INSTALL_JS))

    def read_raw(self) -> dict:
        """回傳 {ts, raw}；raw 可直接餵 DragonState.from_raw。"""
        return self._page.evaluate(_READ_JS)

    def _send(self, msg: str, payload: dict) -> None:
        self._page.evaluate(_SEND_JS, [_PREFIX + msg, payload])

    def dispatch(self, action: Action) -> None:
        if action.kind == C.A_EXPLORE:
            self._send("dragon_realm_start_explore_c2s", {})
        elif action.kind == C.A_CHOICE:
            self._send("dragon_realm_event_choice_c2s",
                       {"choice": action.choice, "event_uid": action.uid})
        elif action.kind == C.A_ENTER_CENG:
            self._send("dragon_realm_enter_ceng_c2s", {"ceng": action.ceng})
        elif action.kind == C.A_PROVIDE_HELP:
            self._send("dragon_realm_provide_help_c2s",
                       {"help_target": action.role_id, "event_id": action.event_id})
        elif action.kind == C.A_RECEIVE_HELP:
            self._send("dragon_realm_receive_help_event_c2s", {"event_id": action.event_id})
            self._send("dragon_realm_help_event_list_c2s", {})
        # A_WAIT / A_STOP: no RPC
```

- [ ] **Step 2: 語法檢查**

Run: `python -m py_compile dragon_realm/client.py`
Expected: 無輸出（成功）

- [ ] **Step 3: Commit**

```bash
git add dragon_realm/client.py
git commit -m "feat(dragon_realm): H5 RPC client bridge (named send + s2c flatten)"
```

---

## Task 6: Service loop

> 協調 read→decide→act→wait，含 wall-clock 預算、dead-loop 偵測、pause_guard、錯誤截圖。以 fake client 單測迴圈行為。

**Files:**
- Create: `dragon_realm/service.py`
- Test: `tests/test_dragon_realm_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dragon_realm_service.py
from dragon_realm.service import run_loop, LoopLimits
from dragon_realm.planner import Action, Prefs
from dragon_realm.state import DragonState
from dragon_realm import constants as C


class FakeClient:
    """回放預設 state 序列；記錄 dispatch。"""
    def __init__(self, states):
        self._states = list(states)
        self._i = 0
        self.dispatched = []

    def read_state(self):
        s = self._states[min(self._i, len(self._states) - 1)]
        return s

    def dispatch(self, action):
        self.dispatched.append(action)
        self._i += 1   # 每次動作推進到下一個 state


def _s(**over):
    base = dict(activity_open=True, ceng=1, hp=30, server_time=0, help_hp=0,
                event_id=0, event_type=0, event_uid=0, is_challenge=False,
                is_ask_help=False, back_kill_time=0, event_list=(), help_events=(), bag={})
    base.update(over)
    return DragonState(**base)


class Cfg:
    tier_two_require = (1518, 1)
    tier_three_require = (1519, 1)
    stamina_tier = (5, 8, 10)
    back_kill_cooldown = 600
    chest_key = {}
    stamina_item_gtid = 0


PREFS = Prefs(my_role_id=1)


def test_loop_stops_on_out_of_stamina():
    client = FakeClient([_s(hp=4)])
    report = run_loop(client, Cfg, PREFS, LoopLimits())
    assert report.stop_reason == "out_of_stamina"
    assert report.actions == 0  # 直接停，無 dispatch


def test_loop_runs_then_stops_on_tier3_gate():
    # 先探索一次(層1無事件)，下個 state 已在層2且達 tier3 → stop
    client = FakeClient([_s(ceng=1, hp=30), _s(ceng=2, bag={1519: 1})])
    report = run_loop(client, Cfg, PREFS, LoopLimits())
    assert report.stop_reason == "reached_tier_three_gate"
    assert client.dispatched[0].kind == C.A_EXPLORE


def test_loop_aborts_on_deadloop():
    # 卡在「已求助、冷卻未到」→ 永遠 WAIT，dead-loop 偵測中止
    stuck = _s(event_id=5001, event_type=C.PVE, event_uid=9,
               is_challenge=True, is_ask_help=True, back_kill_time=10**9, server_time=0)
    client = FakeClient([stuck])
    report = run_loop(client, Cfg, PREFS, LoopLimits(max_wait_iters=3, sleep_fn=lambda s: None))
    assert report.stop_reason == "deadloop"


def test_loop_aborts_on_budget():
    client = FakeClient([_s(ceng=2, hp=30, bag={})])  # 永遠探索，不會自然停
    report = run_loop(client, Cfg, PREFS,
                      LoopLimits(max_actions=5, sleep_fn=lambda s: None))
    assert report.stop_reason == "budget_exhausted"
    assert report.actions == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dragon_realm_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dragon_realm.service'`

- [ ] **Step 3: Write minimal implementation**

```python
# dragon_realm/service.py
"""龍骸聖域迴圈協調。

read_state → decide → dispatch → 等下一個更新。終止：planner STOP、
wall-clock/action 預算、dead-loop（連續 WAIT 或重複動作無進展）。

``run_loop`` 為純邏輯（吃任意 client 介面 + 注入 sleep），可單測。
``run(ip, d)`` 是 runtime 入口：建 DragonClient + pause_guard + 錯誤截圖。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from dragon_realm import constants as C
from dragon_realm.planner import Action, Prefs, decide
from dragon_realm.state import DragonState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoopLimits:
    max_actions: int = 200          # 動作預算（含探索）
    max_wait_iters: int = 30        # 連續 WAIT 上限 → dead-loop
    wait_sleep_sec: float = 3.0     # WAIT 時的輪詢間隔
    sleep_fn: Callable[[float], None] = None  # type: ignore


@dataclass
class LoopReport:
    stop_reason: str = ""
    actions: int = 0
    waits: int = 0


def run_loop(client, config, prefs: Prefs, limits: LoopLimits) -> LoopReport:
    sleep = limits.sleep_fn or (lambda s: __import__("time").sleep(s))
    report = LoopReport()
    consecutive_wait = 0
    while True:
        state = client.read_state()
        action = decide(state, config, prefs)

        if action.kind == C.A_STOP:
            report.stop_reason = action.reason
            return report

        if action.kind == C.A_WAIT:
            consecutive_wait += 1
            report.waits += 1
            if consecutive_wait >= limits.max_wait_iters:
                report.stop_reason = "deadloop"
                return report
            sleep(limits.wait_sleep_sec)
            continue

        consecutive_wait = 0
        client.dispatch(action)
        report.actions += 1
        if report.actions >= limits.max_actions:
            report.stop_reason = "budget_exhausted"
            return report


def run(ip: str, d) -> LoopReport:
    """Runtime 入口（H5 only）。adb 後端直接回 aborted。"""
    page = getattr(d, "_page", None)
    if page is None:
        logger.info("[dragon_realm] %s — H5 only (no _page), skip", ip)
        return LoopReport(stop_reason="not_h5")

    from dragon_realm.client import DragonClient
    import config_manager
    from utils import pause_guard
    from utils.screenshot_helpers import save_error_screenshot

    dev = config_manager.get_device_config(ip)
    my_role_id = int(dev.get("dragon_realm_role_id", 0))
    prefs = Prefs(my_role_id=my_role_id,
                  assist_teammates=bool(dev.get("dragon_realm_assist", True)),
                  auto_open_box=bool(dev.get("dragon_realm_open_box", True)))

    client = DragonClient(page, my_role_id)
    token = pause_guard.bind(ip)
    try:
        if not client.install():
            logger.warning("[dragon_realm] %s — netManager 不可用，略過", ip)
            return LoopReport(stop_reason="no_netmanager")
        config = _load_config(client)
        return run_loop(_StateAdapter(client, my_role_id), config, prefs, LoopLimits())
    except Exception:
        logger.exception("[dragon_realm] %s — 迴圈異常", ip)
        save_error_screenshot(d, ip, "dragon_realm")
        return LoopReport(stop_reason="error")
    finally:
        pause_guard.unbind(ip, token)


class _StateAdapter:
    """把 DragonClient.read_raw() 包成 run_loop 期望的 read_state()/dispatch()。"""
    def __init__(self, client, my_role_id: int):
        self._client = client
        self._role = my_role_id

    def read_state(self) -> DragonState:
        return DragonState.from_raw(self._client.read_raw().get("raw") or {"activity_open": True}, self._role)

    def dispatch(self, action: Action) -> None:
        self._client.dispatch(action)


def _load_config(client):
    """讀一次 config KV（靜態）。實作對照 DRAGON_REALM_SCHEMA.md 的 accessor。"""
    from dragon_realm.config_loader import load_dragon_config
    return load_dragon_config(client)
```

> 註：`dragon_realm/config_loader.py`（`load_dragon_config(client)` 讀 cocos config 表回傳具 `tier_two_require/tier_three_require/stamina_tier/back_kill_cooldown/chest_key/stamina_item_gtid` 屬性的物件）於本任務一併建立，accessor 對照 Task 1 schema；值結構已知（見 `config_kv.json`）。

- [ ] **Step 4: 建立 config_loader（最小可動，值來自 client.read 的 config）**

```python
# dragon_realm/config_loader.py
"""讀龍骸聖域 config KV（靜態）。鍵與 accessor 見 DRAGON_REALM_SCHEMA.md。"""
from __future__ import annotations

from dataclasses import dataclass

# config KV index（ActivityLhsyKey，已逆向確認）
KV_ENTER_TIER_TWO_REQUIRE = 7
KV_ENTER_TIER_THREE_REQUIRE = 8
KV_CHEST_KEY = 15
KV_BACK_KILL = 16
KV_STAMINA_TIER = 17
KV_STAMINA_ITEM = 26

_READ_CONFIG_JS = r"""
() => {
  // 對照 schema：configDragon_map_kv.getDataByKey(idx).info（字串，{} 換 [] 後 JSON.parse）
  const get = (idx) => {
    try { return window.__dr_getKV(idx); } catch(_) { return null; }
  };
  return {
    tier2: get(7), tier3: get(8), chest: get(15),
    back_kill: get(16), stamina_tier: get(17), stamina_item: get(26),
  };
}
"""


@dataclass(frozen=True)
class DragonConfig:
    tier_two_require: tuple
    tier_three_require: tuple
    stamina_tier: tuple
    back_kill_cooldown: int
    chest_key: dict
    stamina_item_gtid: int


def load_dragon_config(client) -> DragonConfig:
    raw = client._page.evaluate(_READ_CONFIG_JS)  # type: ignore[attr-defined]
    t2 = raw.get("tier2") or [0, 1]
    t3 = raw.get("tier3") or [0, 1]
    chest = {}
    for row in (raw.get("chest") or []):
        # row 形狀見 schema：[.., key_gtid, event_id]
        if len(row) >= 3:
            chest[int(row[2])] = int(row[1])
    return DragonConfig(
        tier_two_require=(int(t2[0]), int(t2[1])),
        tier_three_require=(int(t3[0]), int(t3[1])),
        stamina_tier=tuple(int(x) for x in (raw.get("stamina_tier") or [5, 8, 10])),
        back_kill_cooldown=int(raw.get("back_kill") or 600),
        chest_key=chest,
        stamina_item_gtid=int(raw.get("stamina_item") or 0),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_dragon_realm_service.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: 語法檢查 + commit**

Run: `python -m py_compile dragon_realm/service.py dragon_realm/config_loader.py`

```bash
git add dragon_realm/service.py dragon_realm/config_loader.py tests/test_dragon_realm_service.py
git commit -m "feat(dragon_realm): service loop (budget/deadloop/pause_guard) + config loader"
```

---

## Task 7: Scheduler + daily_pipeline wiring

> 接進每日任務，flag 閘控 + 每日冷卻（一天跑一次，對齊體力每日重置）。最小改動 `daily_pipeline`。

**Files:**
- Create: `game_actions/dragon_realm_scheduler.py`
- Modify: `game_actions/daily_pipeline.py`（import + 在尾段加掛呼叫）
- Test: `tests/test_dragon_realm_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dragon_realm_scheduler.py
from unittest import mock
from game_actions import dragon_realm_scheduler as sch


def test_skips_when_flag_off():
    cfg = {"global": {"dragon_realm_enabled": False}}
    with mock.patch("dragon_realm.service.run") as run, \
         mock.patch("config_manager.load_config", return_value=cfg):
        sch.run_dragon_realm_if_due("emulator-5560", object())
        run.assert_not_called()


def test_runs_when_flag_on_and_due():
    cfg = {"global": {"dragon_realm_enabled": True}}
    with mock.patch("dragon_realm.service.run") as run, \
         mock.patch("config_manager.load_config", return_value=cfg), \
         mock.patch.object(sch, "_is_due", return_value=True), \
         mock.patch.object(sch, "_mark_done"):
        sch.run_dragon_realm_if_due("emulator-5560", object())
        run.assert_called_once()


def test_skips_when_not_due():
    cfg = {"global": {"dragon_realm_enabled": True}}
    with mock.patch("dragon_realm.service.run") as run, \
         mock.patch("config_manager.load_config", return_value=cfg), \
         mock.patch.object(sch, "_is_due", return_value=False):
        sch.run_dragon_realm_if_due("emulator-5560", object())
        run.assert_not_called()


def test_open_window_gate_before_10am():
    import datetime
    assert sch._within_open_window(datetime.datetime(2026, 6, 4, 9, 59)) is False
    assert sch._within_open_window(datetime.datetime(2026, 6, 4, 10, 0)) is True
    assert sch._within_open_window(datetime.datetime(2026, 6, 4, 23, 0)) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dragon_realm_scheduler.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'game_actions.dragon_realm_scheduler'`

- [ ] **Step 3: Write minimal implementation**

```python
# game_actions/dragon_realm_scheduler.py
"""龍骸聖域排程：flag 閘控 + 每日一次冷卻。在 daily_pipeline 尾段呼叫。"""
from __future__ import annotations

import datetime

import config_manager
from dragon_realm import use_dragon_realm
from json_manager import is_record_expired, time_recording
from utils.logging_utils import logger

_RECORD_KEY = "dragon_realm_last_run"
_COOLDOWN_HOURS = 20   # 體力每日重置；20h 確保每日一次且不重入
_OPEN_HOUR = 10        # 活動每天 10:00 才開（同 sea 的時間閘）


def _within_open_window(now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now()
    return now.hour >= _OPEN_HOUR


def _is_due(ip: str) -> bool:
    if not _within_open_window():
        return False
    return is_record_expired(ip, _RECORD_KEY, _COOLDOWN_HOURS)


def _mark_done(ip: str) -> None:
    time_recording(ip, _RECORD_KEY)


def run_dragon_realm_if_due(ip: str, d) -> None:
    config = config_manager.load_config()
    if not use_dragon_realm(ip, config):
        return
    if not _is_due(ip):
        return
    import dragon_realm.service as service
    logger.info("[dragon_realm] %s — 開始龍骸聖域", ip)
    report = service.run(ip, d)
    logger.info("[dragon_realm] %s — 結束：%s（actions=%s waits=%s）",
                ip, report.stop_reason, report.actions, report.waits)
    if report.stop_reason in ("reached_tier_three_gate", "out_of_stamina"):
        _mark_done(ip)
```

> 註：`is_record_expired(ip, key, hours)` / `time_recording(ip, key)` 的確切簽章對照 `json_manager.py` 既有用法（與 sea/lamp 冷卻同源）；若簽章不同，於本步調整呼叫。

- [ ] **Step 4: Run scheduler test to verify it passes**

Run: `python -m pytest tests/test_dragon_realm_scheduler.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Wire into daily_pipeline**

在 `game_actions/daily_pipeline.py` 的 import 區（其他 scheduler import 附近）加：

```python
from game_actions.dragon_realm_scheduler import run_dragon_realm_if_due
```

在 `run(ctx)` 的任務序列尾段（航海 `_sea_dispatch` 之後、device-specific cleanup 之前）加一段，對齊既有 task 的 try/log 包法：

```python
    # 龍骸聖域（flag 預設 off；H5 only，adb 會自行 abort）
    try:
        run_dragon_realm_if_due(ip, d)
    except Exception:
        logger.exception("[%s] 龍骸聖域 任務異常", ip)
```

> 確切插入點：找 `run(ctx)` 內呼叫 `_sea_dispatch(...)` 的位置，插在其後、`emulator-5558` / `fc65396d` cleanup 分支之前。`ip` / `d` 變數沿用 pipeline 內既有名稱。

- [ ] **Step 6: 語法檢查 + 跑相關測試**

Run: `python -m py_compile game_actions/dragon_realm_scheduler.py game_actions/daily_pipeline.py`
Run: `python -m pytest tests/test_dragon_realm_scheduler.py tests/test_daily_pipeline.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add game_actions/dragon_realm_scheduler.py game_actions/daily_pipeline.py tests/test_dragon_realm_scheduler.py
git commit -m "feat(dragon_realm): scheduler + daily_pipeline wiring (flag-gated, daily cooldown)"
```

---

## Task 8: Live verification (H5, manual-hold)

> 觸發 dual-backend-task-dev skill。在 manual-hold 獨佔的 H5 裝置上端到端驗證。**不挑正在跑的裝置**（先看 main.log）。

**Files:**
- Modify: `bot_config.json`（暫時為該測試裝置開 `dragon_realm_enabled: true` + `dragon_realm_role_id`）
- Modify: `docs/protocol/DRAGON_REALM_SCHEMA.md`（補 live 觀察）

- [ ] **Step 1:** manual-hold 取得一台 H5 裝置獨佔；確認在主頁面、龍骸聖域活動開啟、已在隊伍、體力 30/30。
- [ ] **Step 2:** 在 `bot_config.json` 對該裝置設 `dragon_realm_enabled: true` 與 `dragon_realm_role_id`（從 `RoleDataCache.GetRoleId()` 取，或 schema 文件記法）。
- [ ] **Step 3:** 手動觸發 `service.run(ip, d)`（或等 pipeline 跑到）。觀察 log：探索 → 事件決策 → 進第2層 → 第2層探索 → tier3 達標停。
- [ ] **Step 4:** 驗證三項覆寫：陷阱出現時只送 `choice(3)`（log 無 `choice(1)` 掙扎）；體力不足時 `out_of_stamina` 停且未用道具；達 tier3 條件時 `reached_tier_three_gate` 停且未進第3層。**特別確認**：挑戰中(IsChallenge)的陷阱/怪物，rekill-list 路徑不會搶先送出 `choice(1)`（即 `event_list` 排除 active 事件，或 active 事件 back_kill_time 未過冷卻）。若實測發現會搶先，需在 planner rekill 迴圈加「排除 active event_uid」守衛並補測試。
- [ ] **Step 5:** 驗證協助/領獎：隊友事件出現時送 `provide_help`；有可領時送 `receive_help`。
- [ ] **Step 6:** 把 live 觀察（實際欄位值、邊界）補進 `DRAGON_REALM_SCHEMA.md`；還原 `bot_config.json` 測試裝置 flag（或維持，視使用者意願）。
- [ ] **Step 7: Commit**

```bash
git add docs/protocol/DRAGON_REALM_SCHEMA.md
git commit -m "docs(dragon_realm): live verification notes + schema confirmations"
```

---

## Self-Review notes

- **Spec coverage**：①探索/三型別決策 → Task4；②陷阱只求助 → Task4 `test_trap_challenge_always_asks_help_never_struggles`；③進2層、第2層探到 tier3 即停 → Task4 `test_layer1_with_key_enters_layer2` + `test_layer2_reaching_tier3_gate_stops_not_enter`；④體力不足即停不用道具 → Task4 `test_out_of_stamina_stops_without_using_item`；⑤協助+領獎 → Task4 assist/receive 測試；⑥H5 RPC 橋接 → Task5；⑦排程 flag off → Task0/Task7；⑧錯誤處理 → Task6 `run` try/except + 截圖。全部對應。
- **Live 未知收斂**：僅 Task1 依賴 live；Task3/5/6 的 protocol 鍵路徑明確指向 `DRAGON_REALM_SCHEMA.md`，非 placeholder。
- **Type 一致性**：`Action`/`DragonState`/`DragonConfig`/`Prefs`/`LoopLimits`/`LoopReport` 欄位在 Task4/6 定義並於 service/scheduler 一致引用；`decide(state, config, prefs)` 簽章一致。
- **已知待實作期校準（非 placeholder，有明確對照來源）**：`json_manager` 冷卻函式簽章、`pause_guard`/`save_error_screenshot` 確切 import、config KV accessor（`window.__dr_getKV` 需在 client.install 時一併注入或改走 schema 確認的途徑）、s2c 欄位攤平鍵名。
