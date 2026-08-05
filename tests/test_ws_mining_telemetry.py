"""Runtime telemetry contracts for the pure-WS supervised mining loop."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import mining_supervised  # noqa: E402


def _board(*, baseline=162391, actives=(), blocks=()):
    return SimpleNamespace(
        baseline=baseline,
        actives=list(actives),
        blocks=list(blocks),
        holes=[],
        area=1,
        area_info={},
        max_num=0,
        next_time=0,
    )


def _block(block_id, x=1, y=162391, *, count=1):
    return SimpleNamespace(
        block_id=block_id, x=x, y=y, config_id=201, count=count, is_reward=0,
    )


def _events(caplog):
    events = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except (TypeError, ValueError):
            continue
        if payload.get("schema") == "ws_mining_telemetry_v1":
            events.append(payload)
    return events


def test_ws_telemetry_round_and_session_have_explicit_zero_vision_counters(
    monkeypatch, caplog,
):
    board = _board(actives=[16239104], blocks=[_block(16239104)])
    after = _board()
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {mining_supervised.mining.GOODS_PICKAXE: 1}

    monkeypatch.setattr(mining_supervised.mining, "read_board", lambda *a, **k: board)
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "plan",
        lambda *a, **k: {"ws_steps": [{"type": "dig", "block_id": 16239104}]},
    )
    monkeypatch.setattr(
        mining_supervised,
        "execute_plan_step",
        lambda *a, **k: {
            "confirmed": True, "goods_id": mining_supervised.mining.GOODS_PICKAXE,
            "block_id": 16239104, "hits": 1,
            "confirmation": "target_changed", "after_board": after,
        },
    )

    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        result = mining_supervised.mine_until_pickaxe_empty(object(), tracker)

    assert result["stopped_reason"] == "pickaxe_empty"
    events = _events(caplog)
    assert [event["event"] for event in events] == [
        "session_start", "round", "session_summary",
    ]
    round_event, summary = events[1], events[2]
    for event in (round_event, summary):
        assert event["screenshot_calls"] == 0
        assert event["classify_calls"] == 0
        assert event["overlay_ocr_calls"] == 0
        assert event["overlay_source"] == "not_in_ws_module"
    assert round_event["screenshots_per_round"] == 0
    assert summary["rounds"] == 1
    assert summary["shadow_calls"] == 0
    assert summary["shadow_elapsed_ms"] == 0.0
    assert summary["shadow_sample_rate"] == 0.0
    assert summary["shadow_skipped"] == 1


def test_ws_telemetry_shadow_success_and_failure_are_json_serializable(
    monkeypatch, caplog,
):
    board = _board(actives=[16239104], blocks=[_block(16239104)])
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {mining_supervised.mining.GOODS_PICKAXE: 2}
    calls = {"n": 0}

    monkeypatch.setattr(mining_supervised.mining, "read_board", lambda *a, **k: board)

    def fake_plan(*args, **kwargs):
        calls["n"] += 1
        return {
            "ws_steps": [{"type": "dig", "block_id": 16239104}],
            "shadow": {
                "ok": calls["n"] == 1,
                "elapsed_ms": 2.5 if calls["n"] == 1 else 3.25,
                # Tuples and nested fields must survive JSON conversion.
                "first_step": ("dig", 0, 0),
            },
        }

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fake_plan)
    monkeypatch.setattr(
        mining_supervised,
        "execute_plan_step",
        lambda *a, **k: {
            "confirmed": False, "goods_id": mining_supervised.mining.GOODS_PICKAXE,
            "block_id": 16239104, "hits": 1,
            "confirmation": "unchanged", "after_board": board,
        },
    )

    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        result = mining_supervised.mine_until_pickaxe_empty(
            object(), tracker, max_steps=2, shadow_planner_version="final_v1",
        )

    assert result["stopped_reason"] == "unconfirmed"
    events = _events(caplog)
    round_event = next(event for event in events if event["event"] == "round")
    summary = next(event for event in events if event["event"] == "session_summary")
    assert round_event["shadow_calls"] == 1
    assert round_event["shadow_elapsed_ms"] == pytest.approx(2.5)
    assert summary["shadow_calls"] == 1
    assert summary["shadow_elapsed_ms"] == pytest.approx(2.5)
    assert summary["shadow_sample_rate"] == pytest.approx(1.0)
    assert summary["shadow_failures"] == 0


def test_ws_telemetry_counts_shadow_failure_and_missing_result(monkeypatch, caplog):
    board = _board(actives=[16239104], blocks=[_block(16239104)])
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {mining_supervised.mining.GOODS_PICKAXE: 1}
    monkeypatch.setattr(mining_supervised.mining, "read_board", lambda *a, **k: board)
    monkeypatch.setattr(
        mining_supervised.mining_adapter,
        "plan",
        lambda *a, **k: {
            "ws_steps": [],
            "shadow": {"ok": False, "elapsed_ms": 1.75, "error": "boom"},
        },
    )

    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        result = mining_supervised.mine_until_pickaxe_empty(
            object(), tracker, shadow_planner_version="final_v1",
        )

    assert result["stopped_reason"] == "no_steps"
    summary = next(event for event in _events(caplog) if event["event"] == "session_summary")
    assert summary["shadow_calls"] == 1
    assert summary["shadow_elapsed_ms"] == pytest.approx(1.75)
    assert summary["shadow_failures"] == 1
    assert summary["shadow_skipped"] == 0


def test_ws_telemetry_emits_exception_and_does_not_count_unstarted_shadow(
    monkeypatch, caplog,
):
    board = _board()
    tracker = mining_supervised.mining.InventoryTracker()
    tracker.counts = {mining_supervised.mining.GOODS_PICKAXE: 1}
    monkeypatch.setattr(mining_supervised.mining, "read_board", lambda *a, **k: board)

    def fail_plan(*args, **kwargs):
        raise RuntimeError("planner boom")

    monkeypatch.setattr(mining_supervised.mining_adapter, "plan", fail_plan)
    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        with pytest.raises(RuntimeError, match="planner boom"):
            mining_supervised.mine_until_pickaxe_empty(
                object(), tracker, shadow_planner_version="final_v1",
            )

    events = _events(caplog)
    summary = next(event for event in events if event["event"] == "session_summary")
    assert summary["stopped_reason"] == "exception"
    assert summary["rounds"] == 1
    assert summary["shadow_calls"] == 0
    assert summary["shadow_elapsed_ms"] == 0.0
    assert any(event["event"] == "round" and event["status"] == "exception"
               for event in events)


def test_ws_telemetry_counters_reset_between_sessions(caplog):
    with caplog.at_level("INFO", logger=mining_supervised.logger.name):
        first = mining_supervised._WSTelemetry(None, "v1", "", mining_supervised.logger)
        first.begin_round(0)
        first.finish_round({}, status="no_steps")
        first.finish("no_steps")

        second = mining_supervised._WSTelemetry(None, "v1", "", mining_supervised.logger)
        second.finish("inventory_unknown")

    events = _events(caplog)
    summaries = [event for event in events if event["event"] == "session_summary"]
    assert len(summaries) >= 2
    assert summaries[-2]["rounds"] == 1
    assert summaries[-1]["rounds"] == 0
    assert summaries[-2]["session_id"] != summaries[-1]["session_id"]
