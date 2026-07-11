"""CNN mining_service final_v1 dispatch, v1 fallback, blocked-first-step,
and shadow exception isolation."""
import logging

import miner.mining_service as service

_LOGGER = logging.getLogger("test_mining_service_final_v1")


def _board():
    out = [["unreachable_dirt"] * 6 for _ in range(7)]
    out[0][2] = "empty"
    out[1][2] = "dirt"
    return out


def test_dispatches_final_v1_with_blocked_actions_as_invalid_first_targets(monkeypatch):
    captured = {}
    monkeypatch.setattr(service, "plan_final_v1", lambda *args, **kwargs: captured.update(kwargs) or {
        "steps": [{"type": "dig", "target": (2, 3)}], "score_breakdown": {"total": 4},
    })
    blocked = {("dig", None, (1, 2), (1, 2), "dig")}
    plan, title = service._dispatch_planner(
        _board(), 20, {"bomb": 1, "drill": 1}, blocked, "final_v1", _LOGGER,
    )
    assert "Final V1" in title
    assert ("dig", "pickaxe", 1, 2) not in captured["valid_targets"]
    assert plan["steps"][0]["target"] == (2, 3)
    assert plan["planner_source"] == "planner"


def test_empty_final_v1_plan_uses_existing_v1_fallback(monkeypatch):
    monkeypatch.setattr(service, "plan_final_v1", lambda *args, **kwargs: {"steps": []})
    monkeypatch.setattr(service, "find_tool_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "plan_smart",
                        lambda *args, **kwargs: {"ok": True, "steps": [{"type": "dig"}]})
    plan, title = service._dispatch_planner(_board(), 20, {}, set(), "final_v1", _LOGGER)
    assert plan["planner_source"] == "v1_fallback"
    assert plan["steps"]


def test_shadow_exception_does_not_change_primary_plan(monkeypatch):
    primary = {"steps": [{"type": "dig", "target": (1, 2)}]}
    monkeypatch.setattr(service, "plan_final_v1",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    shadow = service._compute_shadow_plan(_board(), 20, {}, set(), "final_v1", _LOGGER)
    assert primary["steps"][0]["target"] == (1, 2)
    assert shadow["ok"] is False
    assert "boom" in shadow["error"]


def test_shadow_disabled_returns_none_and_enabled_computes(monkeypatch):
    assert service._compute_shadow_plan(_board(), 20, {}, set(), "", _LOGGER) is None
    monkeypatch.setattr(service, "plan_final_v1", lambda *args, **kwargs: {
        "steps": [{"type": "dig", "target": (1, 2)}],
        "score_breakdown": {"total": 1.0}, "budget_hit": False,
    })
    shadow = service._compute_shadow_plan(_board(), 20, {}, set(), "final_v1", _LOGGER)
    assert shadow["ok"] is True
    assert shadow["planner"] == "final_v1"
    assert shadow["first_step"] == {"type": "dig", "target": (1, 2)}


def test_known_board_reaches_final_v1_but_valid_targets_stay_visible(monkeypatch):
    captured = {}

    def fake_plan(board, **kwargs):
        captured["rows"] = len(board)
        captured.update(kwargs)
        return {"steps": [{"type": "dig", "target": (1, 2)}], "score_breakdown": {"total": 1}}

    monkeypatch.setattr(service, "plan_final_v1", fake_plan)
    board = _board()
    known = [list(r) for r in board] + [["unreachable_dirt"] * 6 for _ in range(14)]
    plan, _ = service._dispatch_planner(
        board, 20, {}, set(), "final_v1", _LOGGER, known_board=known,
    )
    assert captured["rows"] == 21
    assert captured["valid_targets"]
    assert all(key[2] < 7 for key in captured["valid_targets"])
    assert plan["steps"]


def test_shadow_uses_known_board_when_available(monkeypatch):
    captured = {}

    def fake_plan(board, **kwargs):
        captured["rows"] = len(board)
        return {"steps": [{"type": "dig", "target": (1, 2)}],
                "score_breakdown": {"total": 1.0}, "budget_hit": False}

    monkeypatch.setattr(service, "plan_final_v1", fake_plan)
    board = _board()
    known = [list(r) for r in board] + [["unreachable_dirt"] * 6 for _ in range(3)]
    shadow = service._compute_shadow_plan(board, 20, {}, set(), "final_v1", _LOGGER,
                                          known_board=known)
    assert shadow["ok"] is True
    assert captured["rows"] == 10
