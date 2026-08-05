"""CNN mining_service final_v1 dispatch, v1 fallback, blocked-first-step,
and shadow exception isolation."""
import logging
import json
import sys
import types

import pytest


# Keep planner dispatch tests importable in the lightweight test environment.
if "uiautomator2" not in sys.modules:
    sys.modules["uiautomator2"] = types.SimpleNamespace(Device=object)

import miner.mining_service as service

_LOGGER = logging.getLogger("test_mining_service_final_v1")


class _FakeMapRecorder:
    def __init__(self):
        self.end_calls = []
        self.round_calls = []

    def start(self, **_kwargs):
        pass

    def round(self, **kwargs):
        self.round_calls.append(kwargs)

    def end(self, **kwargs):
        self.end_calls.append(kwargs)


class _FakeRLRecorder:
    def __init__(self):
        self.flush_calls = 0

    def flush(self):
        self.flush_calls += 1

    def summary(self):
        return {"total": 0, "log_path": "test"}


class _FakeLogger:
    def __init__(self):
        self.exception_calls = []

    def info(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def exception(self, *args, **kwargs):
        self.exception_calls.append((args, kwargs))


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
    assert title == "V1 規劃 (final_v1 fallback)"
    assert plan["planner_name"] == "v1"
    assert plan["planner_source"] == "final_v1_fallback"
    assert plan["steps"]


def test_failed_final_v1_plan_attributes_fallback_to_v1(monkeypatch):
    monkeypatch.setattr(
        service,
        "plan_final_v1",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("planner down")),
    )
    monkeypatch.setattr(service, "find_tool_candidate", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "plan_smart",
        lambda *args, **kwargs: {"ok": True, "steps": [{"type": "dig"}]},
    )
    plan, _title = service._dispatch_planner(_board(), 20, {}, set(), "final_v1", _LOGGER)
    assert plan["planner_name"] == "v1"
    assert plan["planner_source"] == "final_v1_fallback"


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


def _patch_run_dependencies(monkeypatch, recorder, logger):
    monkeypatch.setattr(service, "setup_miner_logger", lambda _ip: logger)
    monkeypatch.setattr(
        service.config_manager,
        "get_device_config",
        lambda _ip: {"backend": "adb", "mining_planner_version": "v1"},
    )
    monkeypatch.setattr(service, "check_pickaxe_count", lambda *_a, **_kw: 10)
    monkeypatch.setattr(
        service,
        "MiningMapRecorder",
        types.SimpleNamespace(for_device=lambda *_a, **_kw: recorder),
    )


def test_run_unexpected_exception_still_finishes_recorders(monkeypatch):
    recorder = _FakeMapRecorder()
    rl = _FakeRLRecorder()
    logger = _FakeLogger()
    _patch_run_dependencies(monkeypatch, recorder, logger)

    class _Device:
        def screenshot(self, **_kwargs):
            raise RuntimeError("screenshot failed")

    result = service.run(_Device(), "test-device", object(), rl_recorder=rl)

    assert result is None
    assert len(recorder.end_calls) == 1
    assert recorder.end_calls[0]["totals"]["fatal_error"]["type"] == "RuntimeError"
    assert rl.flush_calls == 1
    assert logger.exception_calls


def test_run_force_sleep_is_re_raised_after_recorder_cleanup(monkeypatch):
    recorder = _FakeMapRecorder()
    rl = _FakeRLRecorder()
    logger = _FakeLogger()
    _patch_run_dependencies(monkeypatch, recorder, logger)
    monkeypatch.setattr(
        service,
        "_check_force_sleep",
        lambda _ip: (_ for _ in ()).throw(service.ForceSleepRequested("stop")),
    )

    with pytest.raises(service.ForceSleepRequested):
        service.run(object(), "test-device", object(), rl_recorder=rl)

    assert len(recorder.end_calls) == 1
    assert recorder.end_calls[0]["totals"] is None
    assert rl.flush_calls == 1


class _TelemetryLogger(_FakeLogger):
    def __init__(self):
        super().__init__()
        self.info_calls = []

    def info(self, message, *args, **kwargs):
        if args:
            message = message % args
        self.info_calls.append(str(message))


class _ScreenshotDevice:
    def __init__(self):
        self.screenshot_calls = 0

    def screenshot(self, **_kwargs):
        self.screenshot_calls += 1
        return object()


def _patch_telemetry_run(monkeypatch, logger, recorder, *, shadow="", overlay=False):
    _patch_run_dependencies(monkeypatch, recorder, logger)
    monkeypatch.setattr(
        service.config_manager,
        "get_device_config",
        lambda _ip: {
            "backend": "adb",
            "mining_planner_version": "v1",
            "mining_shadow_planner_version": shadow,
        },
    )
    monkeypatch.setattr(service, "read_ws_prop_counts", lambda _d: None)
    monkeypatch.setattr(service, "check_drill_num", lambda *_a, **_k: 0)
    monkeypatch.setattr(service, "check_boom_num", lambda *_a, **_k: 0)
    monkeypatch.setattr(service, "_log_inventory_validation", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_log_board_validation", lambda *_a, **_k: None)
    monkeypatch.setattr(service, "_check_force_sleep", lambda _ip: None)
    monkeypatch.setattr(
        service,
        "_dismiss_mining_overlay_if_needed",
        lambda *_a, **_k: overlay,
    )
    monkeypatch.setattr(
        service,
        "_dispatch_planner",
        lambda *_a, **_k: (
            {"ok": True, "steps": [{"type": "dig", "target": (1, 2), "pos": (1, 2)}]},
            "test",
        ),
    )
    monkeypatch.setattr(
        service,
        "execute_plan_steps",
        lambda *_a, **_k: service.ExecutionResult(shovels_used=10, steps_completed=1),
    )


def _telemetry_json_lines(logger, event):
    prefix = "[MiningTelemetryJSON] "
    payloads = [json.loads(line[len(prefix):]) for line in logger.info_calls if line.startswith(prefix)]
    return [payload for payload in payloads if payload.get("event") == event]


def test_run_telemetry_counts_round_overlay_and_session_screenshots(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder, overlay=True)
    clf = types.SimpleNamespace(
        classify_board=lambda *_a, **_k: (_board(), []),
    )
    device = _ScreenshotDevice()

    service.run(device, "telemetry-device", clf)

    summary = _telemetry_json_lines(logger, "session_summary")[0]
    assert summary["rounds"] == 1
    assert summary["overlay_ocr_calls"] == 1
    assert summary["classify_calls"] == 1
    # One shared frame + one frame after the detected overlay; execution is
    # monkeypatched here, so no executor screenshots are added.
    assert summary["screenshot_calls"] == 2
    assert summary["shadow_calls"] == 0
    assert summary["shadow_sample_rate"] == 0.0
    round_payloads = _telemetry_json_lines(logger, "round")
    assert round_payloads, logger.info_calls
    round_event = round_payloads[0]
    assert round_event["screenshots_per_round"] == 2


def test_run_telemetry_records_shadow_exception_and_elapsed_time(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder, shadow="final_v1")
    monkeypatch.setattr(
        service,
        "plan_final_v1",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("shadow boom")),
    )
    clf = types.SimpleNamespace(
        classify_board=lambda *_a, **_k: (_board(), []),
    )

    service.run(_ScreenshotDevice(), "telemetry-shadow", clf)

    summary = _telemetry_json_lines(logger, "session_summary")[0]
    assert summary["shadow_calls"] == 1
    assert summary["shadow_failures"] == 1
    assert summary["shadow_elapsed_ms"] >= 0
    assert summary["shadow_sample_rate"] == 1.0
    assert summary["shadow_skipped"] == 0
