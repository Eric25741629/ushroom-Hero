"""Offline tests for supervised WS mining execution.

No real socket is opened here. The supervised layer is the explicit bridge from
planner ``ws_steps`` to ``home_mine_use_goods`` calls; live execution remains
single-step and opt-in.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import mining_supervised  # noqa: E402
from ws_token.mining import GOODS_BOMB, GOODS_DRILL, GOODS_PICKAXE  # noqa: E402


def _board(*, baseline=162391, actives=(), blocks=(), holes=()):
    return SimpleNamespace(
        baseline=baseline,
        actives=list(actives),
        blocks=list(blocks),
        holes=list(holes),
        area=1,
        area_info={},
        max_num=0,
        next_time=0,
    )


def _block(block_id, x, y, config_id=201, count=1, is_reward=0):
    return SimpleNamespace(
        block_id=block_id,
        x=x,
        y=y,
        config_id=config_id,
        count=count,
        is_reward=is_reward,
    )


def test_step_goods_id_maps_dig_to_pickaxe():
    step = {"type": "dig", "block_id": 16239104}

    assert mining_supervised.step_goods_id(step) == GOODS_PICKAXE


def test_step_goods_id_blocks_bomb_by_default():
    step = {"type": "use", "item": "bomb", "block_id": 16239104}

    with pytest.raises(mining_supervised.LiveMiningBlocked):
        mining_supervised.step_goods_id(step)


def test_step_goods_id_allows_bomb_when_explicitly_enabled():
    step = {"type": "use", "item": "bomb", "block_id": 16239104}

    assert mining_supervised.step_goods_id(step, allow_bomb=True) == GOODS_BOMB


def test_step_goods_id_blocks_drill_by_default():
    step = {"type": "use", "item": "drill", "block_id": 16239104}

    with pytest.raises(mining_supervised.LiveMiningBlocked):
        mining_supervised.step_goods_id(step)


def test_step_goods_id_allows_drill_when_explicitly_enabled():
    step = {"type": "use", "item": "drill", "block_id": 16239104}

    assert mining_supervised.step_goods_id(step, allow_drill=True) == GOODS_DRILL


def test_execute_plan_step_sends_ws_dig_and_confirms_by_refresh(monkeypatch):
    sent = []
    before = _board(actives=[16239104])
    after = _board(baseline=162392, actives=[16239204])

    def fake_send_dig(client, goods_id, block_id):
        sent.append((client, goods_id, block_id))

    monkeypatch.setattr(mining_supervised.mining, "send_dig", fake_send_dig)
    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: after,
    )

    client = object()
    result = mining_supervised.execute_plan_step(
        client,
        {"type": "dig", "block_id": 16239104},
        before_board=before,
        timeout=3.5,
    )

    assert sent == [(client, GOODS_PICKAXE, 16239104)]
    assert result["goods_id"] == GOODS_PICKAXE
    assert result["block_id"] == 16239104
    assert result["confirmed"] is True
    assert result["confirmation"] == "baseline_changed"
    assert result["raw_reply"] is None
    assert result["after_board"] is after


def test_execute_dig_step_sends_one_pickaxe_then_requires_replan(monkeypatch):
    sent = []
    before = _board(actives=[16239104])
    after = _board(baseline=162392, actives=[16239204])

    def fake_send_dig(client, goods_id, block_id):
        sent.append((goods_id, block_id))

    monkeypatch.setattr(mining_supervised.mining, "send_dig", fake_send_dig)
    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: after,
    )

    result = mining_supervised.execute_plan_step(
        object(),
        {"type": "dig", "block_id": 16239104, "step_cost": 2.0},
        before_board=before,
    )

    assert sent == [(GOODS_PICKAXE, 16239104)]
    assert result["hits"] == 1
    assert result["planned_hits"] == 2
    assert result["confirmation"] == "baseline_changed"


def test_execute_plan_step_reports_unconfirmed_when_refresh_has_no_change(monkeypatch):
    sent = []
    before = _board(actives=[16239104])
    after = _board(actives=[16239104])

    monkeypatch.setattr(
        mining_supervised.mining,
        "send_dig",
        lambda client, goods_id, block_id: sent.append((goods_id, block_id)),
    )
    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: after,
    )

    result = mining_supervised.execute_plan_step(
        object(),
        {"type": "dig", "block_id": 16239104},
        before_board=before,
        refresh_timeout=0,
    )

    assert sent == [(GOODS_PICKAXE, 16239104)]
    assert result["confirmed"] is False
    assert result["confirmation"] == "unconfirmed_no_board_change"


def test_execute_plan_step_polls_refresh_until_board_changes(monkeypatch):
    sent = []
    before = _board(actives=[16239104])
    stale = _board(actives=[16239104])
    after = _board(baseline=162392, actives=[16239204])
    reads = iter([stale, after])

    monkeypatch.setattr(
        mining_supervised.mining,
        "send_dig",
        lambda client, goods_id, block_id: sent.append((goods_id, block_id)),
    )
    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: next(reads),
    )

    result = mining_supervised.execute_plan_step(
        object(),
        {"type": "dig", "block_id": 16239104},
        before_board=before,
        refresh_timeout=1.0,
        refresh_interval=0,
    )

    assert sent == [(GOODS_PICKAXE, 16239104)]
    assert result["confirmed"] is True
    assert result["confirmation"] == "baseline_changed"
    assert result["refresh_attempts"] == 2
    assert result["after_board"] is after


def test_execute_plan_step_refresh_failed_uses_inventory_delta(monkeypatch):
    """短暫 0x0c01 失敗但 0x0402 已扣鎬時，仍算本次挖掘成功。"""
    before = _board(actives=[16239104])
    reads = iter([
        {"pickaxe": 8, "drill": 1, "bomb": 1},
        {"pickaxe": 7, "drill": 1, "bomb": 1},
    ])

    monkeypatch.setattr(mining_supervised.mining, "send_dig", lambda *a: None)

    def fail_refresh(*_args, **_kwargs):
        raise OSError(10038, "socket operation on nonsocket")

    monkeypatch.setattr(mining_supervised.mining, "read_board", fail_refresh)
    item = mining_supervised.execute_plan_step(
        object(), {"type": "dig", "block_id": 16239104},
        before_board=before,
        before_inventory=next(reads),
        inventory_reader=lambda: next(reads),
        refresh_timeout=0,
    )

    assert item["confirmed"] is True
    assert item["confirmation"] == "inventory_changed"
    assert item["error"].startswith("OSError:")


def test_execute_plan_step_refresh_failed_without_delta_is_retryable(monkeypatch):
    """無盤面、無庫存下降只回報 refresh_failed，不宣稱成功。"""
    before = _board(actives=[16239104])
    monkeypatch.setattr(mining_supervised.mining, "send_dig", lambda *a: None)

    def fail_refresh(*_args, **_kwargs):
        raise OSError(10038, "socket operation on nonsocket")

    monkeypatch.setattr(mining_supervised.mining, "read_board", fail_refresh)
    item = mining_supervised.execute_plan_step(
        object(), {"type": "dig", "block_id": 16239104},
        before_board=before,
        before_inventory={"pickaxe": 8, "drill": 1, "bomb": 1},
        inventory_reader=lambda: {"pickaxe": 8, "drill": 1, "bomb": 1},
        refresh_timeout=0,
    )

    assert item["confirmed"] is False
    assert item["confirmation"] == "refresh_failed"


def test_plan_current_board_dry_run_never_digs(monkeypatch):
    board = object()
    plan = {
        "message": "ok",
        "ws_steps": [{"type": "dig", "block_id": 16239104}],
    }

    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: board,
    )
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "plan",
        lambda got_board, inventory, max_depth=None, terrain=None: plan,
    )

    def forbidden_dig(*args, **kwargs):
        raise AssertionError("dry run must not dig")

    monkeypatch.setattr(mining_supervised.mining, "dig", forbidden_dig)

    result = mining_supervised.plan_current_board(
        object(),
        {"pickaxe": 1, "drill": 0, "bomb": 0},
        execute=False,
    )

    assert result["board"] is board
    assert result["plan"] is plan
    assert result["candidate_steps"] == plan["ws_steps"]
    assert result["executed"] == []


def test_plan_current_board_replans_between_executed_steps(monkeypatch):
    board1 = _board(actives=[16239104])
    board2 = _board(actives=[16239204])
    board3 = _board(actives=[16239304])
    step1 = {"type": "dig", "block_id": 16239104}
    step2 = {"type": "dig", "block_id": 16239204}
    planned_boards = []

    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: board1,
    )

    def fake_plan(board, inventory, max_depth=None, terrain=None):
        planned_boards.append(board)
        step = step1 if board is board1 else step2
        return {"message": "ok", "ws_steps": [step]}

    after_by_step = {16239104: board2, 16239204: board3}

    def fake_execute_step(client, step, **kwargs):
        after = after_by_step[step["block_id"]]
        return {
            "step": dict(step),
            "goods_id": GOODS_PICKAXE,
            "block_id": step["block_id"],
            "hits": 1,
            "confirmed": True,
            "confirmation": "confirmed_by_board_change",
            "after_board": after,
        }

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fake_plan)
    monkeypatch.setattr(mining_supervised, "execute_plan_step", fake_execute_step)

    result = mining_supervised.plan_current_board(
        object(),
        {"pickaxe": 2, "drill": 0, "bomb": 0},
        execute=True,
        max_steps=2,
    )

    assert planned_boards == [board1, board2]
    assert result["candidate_steps"] == [step1, step2]
    assert [item["block_id"] for item in result["executed"]] == [16239104, 16239204]


def test_mine_until_pickaxe_empty_uses_tracker_inventory_and_items(monkeypatch):
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {
        GOODS_PICKAXE: 2,
        GOODS_DRILL: 1,
        GOODS_BOMB: 1,
    }
    board1 = _board(actives=[16239104])
    board2 = _board(actives=[16239204])
    board3 = _board(actives=[16239304])
    plans = []
    sent_steps = []

    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: board1,
    )

    def fake_plan(board, inventory, max_depth=None, terrain=None):
        plans.append(dict(inventory))
        if board is board1:
            return {"message": "use drill", "ws_steps": [
                {"type": "use", "item": "drill", "block_id": 16239104}
            ]}
        if board is board2:
            return {"message": "dig", "ws_steps": [
                {"type": "dig", "block_id": 16239204}
            ]}
        return {"message": "dig", "ws_steps": [
            {"type": "dig", "block_id": 16239304}
        ]}

    after_by_block = {
        16239104: board2,
        16239204: board3,
        16239304: _board(actives=[]),
    }

    def fake_execute_step(client, step, **kwargs):
        sent_steps.append(dict(step))
        return {
            "step": dict(step),
            "goods_id": mining_supervised.step_goods_id(
                step,
                allow_bomb=kwargs.get("allow_bomb", False),
                allow_drill=kwargs.get("allow_drill", False),
            ),
            "block_id": step["block_id"],
            "hits": 1,
            "confirmed": True,
            "confirmation": "confirmed_by_board_change",
            "after_board": after_by_block[step["block_id"]],
        }

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fake_plan)
    monkeypatch.setattr(mining_supervised, "execute_plan_step", fake_execute_step)

    result = mining_supervised.mine_until_pickaxe_empty(
        object(),
        tracker,
        allow_drill=True,
        allow_bomb=True,
        max_steps=5,
    )

    assert [step.get("item", "pickaxe") for step in sent_steps] == [
        "drill", "pickaxe", "pickaxe",
    ]
    assert plans == [
        {"pickaxe": 2, "drill": 1, "bomb": 1},
        {"pickaxe": 2, "drill": 0, "bomb": 1},
        {"pickaxe": 1, "drill": 0, "bomb": 1},
    ]
    assert result["stopped_reason"] == "pickaxe_empty"
    assert result["final_inventory"]["pickaxe"] == 0


def test_mine_until_pickaxe_empty_seeds_when_count_unknown_then_adopts_push(monkeypatch):
    """No login snapshot -> seed so the planner runs, then adopt the real count
    from the consume push (tracker) after the first dig and stop at 0."""
    tracker = mining_supervised.mining.InventoryTracker()  # empty: no has_item(4001)
    board1 = _board(actives=[16239104])
    board2 = _board(actives=[16239204])
    seen_inventories = []

    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: board1)

    def fake_plan(board, inventory, max_depth=None, terrain=None):
        seen_inventories.append(dict(inventory))
        bid = 16239104 if board is board1 else 16239204
        return {"message": "dig", "ws_steps": [{"type": "dig", "block_id": bid}]}

    after = {16239104: board2, 16239204: _board(actives=[])}

    def fake_execute(client, step, **kwargs):
        # simulate the consume push landing in the tracker: real count 1 left after
        # the first dig (adopted in place of the seed), then 0 after the second.
        tracker.counts[GOODS_PICKAXE] = 1 if step["block_id"] == 16239104 else 0
        return {"goods_id": GOODS_PICKAXE, "block_id": step["block_id"], "hits": 1,
                "confirmed": True, "confirmation": "confirmed_by_board_change",
                "after_board": after[step["block_id"]]}

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fake_plan)
    monkeypatch.setattr(mining_supervised, "execute_plan_step", fake_execute)

    result = mining_supervised.mine_until_pickaxe_empty(object(), tracker, max_steps=10)

    assert "skipped" not in result
    assert seen_inventories[0]["pickaxe"] == 999          # seeded for the first plan
    assert result["stopped_reason"] == "pickaxe_empty"     # adopted real count, hit 0
    assert len(result["executed"]) == 2


def test_select_dig_step_skips_non_active_and_collected_pits():
    # planner proposes a non-active pit, then an already-collected pit (count 0),
    # then a valid active pit -> the valid one is chosen.
    board = _board(
        actives=[12206204],
        blocks=[
            _block(12206202, 2, 122062, config_id=401, count=1),  # not in actives
            _block(12206203, 3, 122062, config_id=401, count=0),  # collected
            _block(12206204, 4, 122062, config_id=401, count=1),  # valid
        ],
    )
    steps = [
        {"type": "dig", "block_id": 12206202},
        {"type": "dig", "block_id": 12206203},
        {"type": "dig", "block_id": 12206204},
    ]
    chosen = mining_supervised._select_dig_step(board, steps)
    assert chosen["block_id"] == 12206204


def test_select_dig_step_falls_back_to_frontier_when_all_steps_stale():
    # every planner step targets an already-collected pit; fall back to the
    # deepest diggable frontier cell (implicit dirt: active, no block entry).
    board = _board(
        actives=[12206602, 12206604],  # implicit frontier cells (no block entries)
        blocks=[_block(12206204, 4, 122062, config_id=401, count=0)],  # collected
    )
    steps = [{"type": "dig", "block_id": 12206204}]
    chosen = mining_supervised._select_dig_step(board, steps)
    assert chosen["block_id"] in (12206602, 12206604)
    assert chosen["type"] == "dig"


def test_select_dig_step_skips_dug_count0_block_picks_undug():
    # LIVE CDP dig 2026-06-20 (小寶): a count==0 block (incl. stone 202) is
    # ALREADY-DUG air — digging is a no-op (0x0c03 no reply, board unchanged), so
    # it is NOT a valid dig target. A fresh stone actually carries count>0.
    dug = _board(actives=[9901], blocks=[_block(9901, 1, 99, config_id=202, count=0)])
    assert mining_supervised._select_dig_step(dug, [{"type": "dig", "block_id": 9901}]) is None
    live = _board(actives=[9901], blocks=[_block(9901, 1, 99, config_id=202, count=1)])
    chosen = mining_supervised._select_dig_step(live, [{"type": "dig", "block_id": 9901}])
    assert chosen["block_id"] == 9901


def test_select_dig_step_honors_exclude():
    # an excluded (already-rejected) block is skipped even though it is diggable.
    board = _board(actives=[9901, 9902],
                   blocks=[_block(9901, 1, 99, config_id=201, count=1),
                           _block(9902, 2, 99, config_id=201, count=1)])
    steps = [{"type": "dig", "block_id": 9901}, {"type": "dig", "block_id": 9902}]
    chosen = mining_supervised._select_dig_step(board, steps, exclude={9901})
    assert chosen["block_id"] == 9902


def test_mine_retries_next_candidate_after_unconfirmed(monkeypatch):
    """單一被伺服器拒挖的目標不該中止整輪挖礦：黑名單後改試下一個可達格。

    regression: hold_floor 盤面挑到被上方石頭擋住的 frontier 格 → unconfirmed →
    之前第一步就 break、整輪 digs=1 挖 0；修正後應跳過該格、挖到第二個候選。
    """
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 5}
    board0 = _board(actives=[9901, 9902],
                    blocks=[_block(9901, 1, 99, config_id=201, count=1),
                            _block(9902, 2, 99, config_id=201, count=1)])
    board1 = _board(actives=[])  # 一次 confirmed dig 後此盤無可挖目標
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: board0)

    def fake_plan(b, inv, max_depth=None, terrain=None):
        if b is board0:
            return {"message": "dig", "hold_floor": False, "grid": None,
                    "ws_steps": [{"type": "dig", "block_id": 9901, "row": 0, "col": 0},
                                 {"type": "dig", "block_id": 9902, "row": 0, "col": 1}]}
        return {"message": "done", "hold_floor": False, "grid": None, "ws_steps": []}

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fake_plan)

    def fake_exec(c, step, **kw):
        confirmed = step["block_id"] == 9902  # 9901 被拒、9902 成功
        return {"step": dict(step), "goods_id": GOODS_PICKAXE,
                "block_id": step["block_id"], "hits": 1, "confirmed": confirmed,
                "confirmation": ("confirmed_by_board_change" if confirmed
                                 else "unconfirmed_no_board_change"),
                "after_board": board1 if confirmed else board0}

    monkeypatch.setattr(mining_supervised, "execute_plan_step", fake_exec)

    result = mining_supervised.mine_until_pickaxe_empty(object(), tracker, max_steps=5)

    confirmed = [it for it in result["executed"] if it.get("confirmed")]
    assert len(confirmed) == 1 and confirmed[0]["block_id"] == 9902
    assert "skipped" not in result          # 有挖到 → 不標 skipped、不退回 ADB
    assert result["stopped_reason"] == "no_steps"


def test_select_dig_step_empty_plan_returns_none():
    board = _board(actives=[9901], blocks=[_block(9901, 1, 99, config_id=201, count=1)])
    assert mining_supervised._select_dig_step(board, []) is None


def test_mine_until_pickaxe_empty_should_abort_raises(monkeypatch):
    """should_abort true at the step-loop top -> WSRunAborted before any plan /
    execute, so the in-progress mining task is left pending for the resume."""
    from ws_token.abort import WSRunAborted

    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 5}
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: _board(actives=[16239104]))

    def fail_plan(*a, **k):
        raise AssertionError("plan must not run after abort")

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fail_plan)

    with pytest.raises(WSRunAborted):
        mining_supervised.mine_until_pickaxe_empty(
            object(), tracker, max_steps=5, should_abort=lambda: True)


def test_mine_until_pickaxe_empty_logs_board_projection_and_dropped_blocks(
    monkeypatch,
    caplog,
):
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 1}
    board = _board(
        baseline=100,
        actives=[9501],
        blocks=[
            _block(9501, 1, 95, config_id=201),
            _block(9301, 1, 93, config_id=202),
            _block(9507, 7, 95, config_id=401),
        ],
        holes=[SimpleNamespace(config_id=7001, last_num=1, max_num=3, hole_num=2)],
    )

    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: board,
    )
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "plan",
        lambda board, inventory, max_depth=None, terrain=None: {"message": "no step", "ws_steps": []},
    )

    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        result = mining_supervised.mine_until_pickaxe_empty(object(), tracker)

    text = caplog.text
    assert result["stopped_reason"] == "no_steps"
    assert "ws_mining board" in text
    assert "baseline=100" in text
    assert "actives=1" in text
    assert "blocks=3" in text
    assert "holes=1" in text
    assert "dropped_blocks=2" in text
    assert "outside_viewport" in text
    assert "outside_cols" in text
    assert "grid=" in text


def test_mine_marks_skipped_when_no_confirmed_dig(monkeypatch):
    """死結（第一步 unconfirmed、0 confirmed dig）→ 回傳帶 "skipped" sentinel。

    判定用 confirmed_digs==0，不是 executed==[]：unconfirmed step 也會 append 進
    executed（confirmed 檢查之前），死結時 executed 長度為 1。用 "skipped" sentinel
    讓 ws_phase 不把「挖礦/Oracle」記為完成、保留 ADB 後備。
    """
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 2}
    board = _board(actives=[9901],
                   blocks=[_block(9901, 1, 99, config_id=201, count=1)])
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: board)
    monkeypatch.setattr(
        mining_supervised.mining_adapter, "plan",
        lambda b, inv, max_depth=None, terrain=None: {
            "message": "dig", "hold_floor": False, "grid": None,
            "ws_steps": [{"type": "dig", "block_id": 9901, "row": 0, "col": 0}]})
    monkeypatch.setattr(
        mining_supervised, "execute_plan_step",
        lambda c, step, **kw: {
            "step": dict(step), "goods_id": GOODS_PICKAXE,
            "block_id": step["block_id"], "hits": 1, "confirmed": False,
            "confirmation": "unconfirmed_no_board_change", "after_board": board})

    result = mining_supervised.mine_until_pickaxe_empty(object(), tracker, max_steps=3)

    assert result["stopped_reason"] == "unconfirmed"
    assert "skipped" in result
    assert len(result["executed"]) == 1  # unconfirmed step 仍被記錄


def test_mine_no_skipped_when_pickaxe_empty(monkeypatch):
    """有 confirmed dig 挖到鎬子用完 → 不加 "skipped"（視為完成，skip ADB）。"""
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 1}
    board1 = _board(actives=[9901],
                    blocks=[_block(9901, 1, 99, config_id=201, count=1)])
    board2 = _board(actives=[])
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: board1)
    monkeypatch.setattr(
        mining_supervised.mining_adapter, "plan",
        lambda b, inv, max_depth=None, terrain=None: {
            "message": "dig", "hold_floor": False, "grid": None,
            "ws_steps": [{"type": "dig", "block_id": 9901, "row": 0, "col": 0}]})
    monkeypatch.setattr(
        mining_supervised, "execute_plan_step",
        lambda c, step, **kw: {
            "step": dict(step), "goods_id": GOODS_PICKAXE,
            "block_id": step["block_id"], "hits": 1, "confirmed": True,
            "confirmation": "confirmed_by_board_change", "after_board": board2})

    result = mining_supervised.mine_until_pickaxe_empty(object(), tracker, max_steps=3)

    assert result["stopped_reason"] == "pickaxe_empty"
    assert "skipped" not in result


def test_final_v1_skips_when_pickaxe_was_never_seeded(monkeypatch):
    tracker = mining_supervised.mining.InventoryTracker()
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda *args, **kwargs: _board())
    result = mining_supervised.mine_until_pickaxe_empty(
        object(), tracker, planner_version="final_v1", max_steps=1,
    )
    assert result["stopped_reason"] == "inventory_unknown"
    assert result["executed"] == []


def test_authoritative_tracker_replaces_local_consume_and_gain_after_each_step(monkeypatch):
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts.update({4001: 4, 4002: 2, 4003: 1})
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda *args, **kwargs: _board(actives=[10101]))
    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", lambda *args, **kwargs: {
        "ws_steps": [{"type": "dig", "block_id": 10101}], "hold_floor": False, "grid": [],
    })

    def execute(*args, **kwargs):
        tracker.counts.update({4001: 3, 4002: 3, 4003: 1})
        return {"confirmed": True, "goods_id": 4001, "block_id": 10101,
                "hits": 1, "confirmation": "confirmed_by_board_change",
                "after_board": _board()}

    monkeypatch.setattr(mining_supervised, "execute_plan_step", execute)
    result = mining_supervised.mine_until_pickaxe_empty(
        object(), tracker, planner_version="final_v1", max_steps=1)
    assert result["final_inventory"] == {"pickaxe": 3, "drill": 3, "bomb": 1}


def test_inventory_change_can_confirm_even_when_target_snapshot_is_delayed(monkeypatch):
    before = {"pickaxe": 4, "drill": 1, "bomb": 1}
    reads = iter([before, {"pickaxe": 3, "drill": 1, "bomb": 1}])
    monkeypatch.setattr(mining_supervised.mining, "send_dig",
                        lambda client, goods_id, block_id: None)
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: _board())
    item = mining_supervised.execute_plan_step(
        object(), {"type": "dig", "block_id": 10101}, before_board=_board(),
        before_inventory=before, inventory_reader=lambda: next(reads),
        refresh_timeout=5.0, refresh_interval=0,
    )
    assert item["confirmed"] is True
    assert item["confirmation"] == "inventory_changed"


def test_unchanged_target_footprint_baseline_and_inventory_is_not_success(monkeypatch):
    monkeypatch.setattr(mining_supervised.mining, "send_dig",
                        lambda client, goods_id, block_id: None)
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: _board())
    item = mining_supervised.execute_plan_step(
        object(), {"type": "dig", "block_id": 10101}, before_board=_board(),
        before_inventory={"pickaxe": 4, "drill": 1, "bomb": 1},
        inventory_reader=lambda: {"pickaxe": 4, "drill": 1, "bomb": 1},
        refresh_timeout=0,
    )
    assert item["confirmed"] is False
    assert item["confirmation"] == "unchanged"


def test_board_confirmation_distinguishes_target_and_baseline(monkeypatch):
    before = _board(actives=[10101], blocks=[_block(10101, 1, 101, count=1)])
    target_dug = _board(actives=[10101], blocks=[_block(10101, 1, 101, count=0)])
    scrolled = _board(baseline=162392, actives=[10101],
                      blocks=[_block(10101, 1, 101, count=1)])
    step = {"type": "dig", "block_id": 10101}
    assert mining_supervised._board_confirmation(before, target_dug, step) == "target_changed"
    assert mining_supervised._board_confirmation(before, scrolled, step) == "baseline_changed"
    assert mining_supervised._board_confirmation(before, _board(actives=[10101],
                                                                blocks=[_block(10101, 1, 101, count=1)]),
                                                 step) is None


def test_unrelated_board_change_does_not_confirm_dig():
    target = _block(10101, 1, 101, count=1)
    unrelated_before = _block(10102, 2, 101, count=1)
    unrelated_after = _block(10102, 2, 101, count=0)
    before = _board(actives=[10101], blocks=[target, unrelated_before])
    after = _board(actives=[10101], blocks=[target, unrelated_after])

    assert mining_supervised._board_confirmation(
        before, after, {"type": "dig", "block_id": 10101}
    ) is None


def test_unrelated_board_change_does_not_confirm_drill():
    target = _block(10101, 1, 101, count=0)
    unrelated_before = _block(10106, 6, 101, count=1)
    unrelated_after = _block(10106, 6, 101, count=0)
    before = _board(actives=[10101], blocks=[target, unrelated_before])
    after = _board(actives=[10101], blocks=[target, unrelated_after])

    assert mining_supervised._board_confirmation(
        before, after, {"type": "use", "item": "drill", "block_id": 10101}
    ) is None


def test_select_dig_step_returns_none_when_pickaxe_is_zero():
    board = _board(actives=[10101], blocks=[_block(10101, 1, 101, count=1)])

    chosen = mining_supervised._select_dig_step(
        board,
        [{"type": "dig", "block_id": 10101}],
        inventory={"pickaxe": 0, "drill": 1, "bomb": 1},
    )

    assert chosen is None


def test_select_dig_step_does_not_use_below_pit_steer_when_pickaxe_is_zero(monkeypatch):
    board = _board(actives=[10101], blocks=[
        _block(10101, 1, 101, count=1),
        _block(10801, 1, 108, config_id=401, count=1, is_reward=1),
    ])
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "map_pits",
        lambda _board: [{"row": 8, "col": 0}],
    )
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "prop_step_for_pit",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "pit_directed_next",
        lambda *a, **kw: 10101,
    )

    chosen = mining_supervised._select_dig_step(
        board,
        [{"type": "dig", "block_id": 10101}],
        inventory={"pickaxe": 0, "drill": 0, "bomb": 0},
        allow_drill=True,
        allow_bomb=True,
    )

    assert chosen is None


def test_runner_forwards_planner_settings(monkeypatch):
    from ws_token import runner as runner_mod

    captured = {}

    def fake_mine(client, tracker, **kwargs):
        captured.update(kwargs)
        return {"executed": []}

    monkeypatch.setattr(runner_mod.mining_supervised,
                        "mine_until_pickaxe_empty", fake_mine)
    tracker = mining_supervised.mining.InventoryTracker()
    runner_mod._run_mining(object(), tracker, mining_config={
        "enabled": True, "planner_version": "FINAL_V1",
        "shadow_planner_version": "final_v1",
    })
    assert captured["planner_version"] == "final_v1"
    assert captured["shadow_planner_version"] == "final_v1"


def test_mine_until_pickaxe_empty_logs_executed_step(monkeypatch, caplog):
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 1}
    board1 = _board(actives=[16239104])
    board2 = _board(actives=[])

    monkeypatch.setattr(
        mining_supervised.mining,
        "read_board",
        lambda client, timeout=None: board1,
    )
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "plan",
        lambda board, inventory, max_depth=None, terrain=None: {
            "message": "dig",
            "ws_steps": [{"type": "dig", "block_id": 16239104}],
        },
    )
    monkeypatch.setattr(
        mining_supervised,
        "execute_plan_step",
        lambda client, step, **kwargs: {
            "step": dict(step),
            "goods_id": GOODS_PICKAXE,
            "block_id": step["block_id"],
            "hits": 1,
            "confirmed": True,
            "confirmation": "confirmed_by_board_change",
            "after_board": board2,
            "refresh_attempts": 2,
        },
    )

    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        result = mining_supervised.mine_until_pickaxe_empty(object(), tracker)

    text = caplog.text
    assert result["stopped_reason"] == "pickaxe_empty"
    assert "ws_mining execute" in text
    assert "goods_id=4001" in text
    assert "block_id=16239104" in text
    assert "confirmation=confirmed_by_board_change" in text
    assert "refresh_attempts=2" in text


def test_final_v1_session_marks_collected_pit_as_dug_pit_next_round(monkeypatch):
    """跨兩輪同一 session：第一輪某格是活躍礦坑（count>0），第二輪已採集（count==0）。
    第二輪 planner 收到的重建盤面該格應為 "dug_pit"（身分經 side table 跨輪保存），
    而第一輪仍是活躍礦坑標籤。驗證 mine_until_pickaxe_empty 有把 DugPitTracker 接進
    plan()（final_v1 分支），否則第二輪只會看到 WS 投影的 "empty"。"""
    top = mining_supervised.mining_adapter.viewport_top_depth(162391)  # 162386
    pit_id = (top + 2) * 100 + 3      # depth top+2, x=3 → grid (row2, col2)
    frontier = (top + 6) * 100 + 1    # 可挖前緣，供每輪產生一個 dig 步

    live_pit = _block(pit_id, 3, top + 2, config_id=mining_supervised.mining.TERRAIN_PIT,
                      count=1, is_reward=1)
    dug_pit = _block(pit_id, 3, top + 2, config_id=mining_supervised.mining.TERRAIN_PIT,
                     count=0, is_reward=1)
    board1 = _board(actives=[frontier], blocks=[live_pit])
    board2 = _board(actives=[frontier], blocks=[dug_pit])

    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {GOODS_PICKAXE: 5}  # has_item(4001) → 走 final_v1 authoritative 路徑

    captured_boards = []

    def fake_named(name, projected, inventory):
        # 記錄「planner 實際收到的重建盤面」（session.annotate 已在此之前套用）。
        captured_boards.append([row[:] for row in projected["board"]])
        return {"steps": [{"type": "dig", "pos": (6, 0), "step_cost": 1}]}

    monkeypatch.setattr(mining_supervised.mining_adapter, "_run_named_planner", fake_named)
    monkeypatch.setattr(mining_supervised.mining, "read_board",
                        lambda client, timeout=None: board1)

    def fake_execute(client, step, **kwargs):
        return {
            "step": dict(step),
            "goods_id": GOODS_PICKAXE,
            "block_id": step["block_id"],
            "hits": 1,
            "confirmed": True,
            "confirmation": "confirmed_by_board_change",
            "after_board": board2,  # 第一輪後盤面推進到已採集的 board2
        }

    monkeypatch.setattr(mining_supervised, "execute_plan_step", fake_execute)

    mining_supervised.mine_until_pickaxe_empty(
        object(), tracker, max_steps=2, planner_version="final_v1",
    )

    assert len(captured_boards) == 2
    assert captured_boards[0][2][2] == "reachable_pit"  # 第一輪：活躍礦坑
    assert captured_boards[1][2][2] == "dug_pit"        # 第二輪：身分跨輪保存
