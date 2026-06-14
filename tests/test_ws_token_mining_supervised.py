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
    after = _board(actives=[16239204])

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
    assert result["confirmation"] == "confirmed_by_board_change"
    assert result["raw_reply"] is None
    assert result["after_board"] is after


def test_execute_dig_step_sends_one_pickaxe_then_requires_replan(monkeypatch):
    sent = []
    before = _board(actives=[16239104])
    after = _board(actives=[16239204])

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
    assert result["confirmation"] == "confirmed_by_board_change"


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
    after = _board(actives=[16239204])
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
    assert result["confirmation"] == "confirmed_by_board_change"
    assert result["refresh_attempts"] == 2
    assert result["after_board"] is after


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
        lambda got_board, inventory, max_depth=None: plan,
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

    def fake_plan(board, inventory, max_depth=None):
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

    def fake_plan(board, inventory, max_depth=None):
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


def test_mine_until_pickaxe_empty_skips_when_pickaxe_count_unknown():
    tracker = mining_supervised.mining.InventoryTracker()

    result = mining_supervised.mine_until_pickaxe_empty(object(), tracker)

    assert result["skipped"] == "inventory snapshot missing"
    assert result["executed"] == []


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
        lambda board, inventory, max_depth=None: {"message": "no step", "ws_steps": []},
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
        lambda board, inventory, max_depth=None: {
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
