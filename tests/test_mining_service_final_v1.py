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


def test_run_main_exception_emits_exception_round_and_one_summary(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_run_dependencies(monkeypatch, recorder, logger)

    class _Device:
        def screenshot(self, **_kwargs):
            raise RuntimeError("screenshot failed")

    service.run(_Device(), "exception-device", object())

    payloads = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
    events = [payload["event"] for payload in payloads]
    assert events.count("exception") == 1
    assert events.count("round") == 1
    assert events.count("session_summary") == 1
    round_event = next(payload for payload in payloads if payload["event"] == "round")
    assert round_event["status"] == "exception"
    assert round_event["error"] == "RuntimeError: screenshot failed"
    summary = next(payload for payload in payloads if payload["event"] == "session_summary")
    assert summary["session_id"] == round_event["session_id"]
    assert summary["rounds"] == 1


def test_run_session_start_exception_emits_one_summary(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_run_dependencies(monkeypatch, recorder, logger)
    monkeypatch.setattr(
        service,
        "check_pickaxe_count",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("ocr unavailable")),
    )

    service.run(object(), "preflight-device", object())

    payloads = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
    assert [p["event"] for p in payloads].count("session_start") == 1
    assert [p["event"] for p in payloads].count("exception") == 1
    assert [p["event"] for p in payloads].count("session_summary") == 1
    summary = next(p for p in payloads if p["event"] == "session_summary")
    exception = next(p for p in payloads if p["event"] == "exception")
    assert summary["session_id"] == exception["session_id"]
    assert summary["device_id"] == "preflight-device"
    assert summary["rounds"] == 0


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
    # Standalone JSON lines mirror the WS telemetry emitter; existing textual
    # MiningTelemetry lines remain separate and append-only.
    payloads = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
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
    assert summary["shadow_successes"] == 0
    assert summary["shadow_failures"] == 0
    assert summary["shadow_skipped"] == 0
    assert summary["shadow_not_attempted"] == 1
    assert summary["shadow_sample_rate"] == 0.0
    round_payloads = _telemetry_json_lines(logger, "round")
    assert round_payloads, logger.info_calls
    round_event = round_payloads[0]
    assert round_event["screenshots_per_round"] == 2
    assert round_event["round_screenshot_calls"] == 2
    assert round_event["round_shadow_not_attempted"] == 1
    assert round_event["round_shadow_calls"] == 0
    assert round_event["round_shadow_successes"] == 0
    assert round_event["round_shadow_failures"] == 0
    assert round_event["round_shadow_skipped"] == 0
    assert summary["screenshots_per_round_avg"] == 2.0
    assert summary["session_id"] == round_event["session_id"]
    assert summary["device_id"] == round_event["device_id"]


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
    assert summary["shadow_successes"] == 0
    assert summary["shadow_failures"] == 1
    assert summary["shadow_skipped"] == 0
    assert summary["shadow_successes"] + summary["shadow_failures"] + summary["shadow_skipped"] == summary["shadow_calls"]
    assert summary["shadow_elapsed_ms"] >= 0
    assert summary["shadow_sample_rate"] == 1.0


@pytest.mark.parametrize("shadow", ["", "final_v1"])
def test_pickaxe_empty_ocr_closes_fifth_round_and_shadow_accounting(monkeypatch, shadow):
    """A fifth-round OCR zero must still emit a complete round event."""
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder, shadow=shadow)
    pickaxe_reads = iter([10, 0])  # session preflight, then round five OCR
    monkeypatch.setattr(
        service,
        "check_pickaxe_count",
        lambda *_a, **_k: next(pickaxe_reads),
    )
    monkeypatch.setattr(
        service,
        "execute_plan_steps",
        lambda *_a, **_k: service.ExecutionResult(shovels_used=1, steps_completed=1),
    )
    monkeypatch.setattr(service, "_identical_board_exceeded", lambda _a, _b, count: (count, False))
    if shadow:
        # Keep the test focused on lifecycle accounting: no shadow result means
        # every configured dispatch is represented as a skipped result.
        monkeypatch.setattr(service, "_compute_shadow_plan", lambda *_a, **_k: None)
    clf = types.SimpleNamespace(classify_board=lambda *_a, **_k: (_board(), []))

    service.run(_ScreenshotDevice(), "pickaxe-empty", clf)

    rounds = _telemetry_json_lines(logger, "round")
    summary = _telemetry_json_lines(logger, "session_summary")[0]
    assert len(rounds) == summary["rounds"] == 5
    assert rounds[-1]["status"] == "pickaxe_empty"
    if shadow:
        assert summary["shadow_calls"] == 5
        assert summary["shadow_skipped"] == 5
        assert summary["shadow_not_attempted"] == 0
        assert rounds[-1]["round_shadow_calls"] == 1
        assert rounds[-1]["round_shadow_skipped"] == 1
    else:
        assert summary["shadow_calls"] == 0
        assert summary["shadow_skipped"] == 0
        assert summary["shadow_not_attempted"] == 5
        assert rounds[-1]["round_shadow_calls"] == 0
        assert rounds[-1]["round_shadow_not_attempted"] == 1


def test_executor_telemetry_proxies_count_real_calls_and_delegate(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder)
    device = _ScreenshotDevice()
    device.marker = "device-marker"
    clf = types.SimpleNamespace(
        marker="classifier-marker",
        classify_board=lambda *_a, **_k: (_board(), []),
    )
    observed = {}

    def fake_executor(execution_device, execution_clf, *_args, **_kwargs):
        observed["device_marker"] = execution_device.marker
        observed["classifier_marker"] = execution_clf.marker
        execution_device.screenshot(format="opencv")
        execution_clf.classify_board(object())
        return service.ExecutionResult(shovels_used=10, steps_completed=1)

    monkeypatch.setattr(service, "execute_plan_steps", fake_executor)
    service.run(device, "proxy-device", clf)

    summary = _telemetry_json_lines(logger, "session_summary")[0]
    assert observed == {
        "device_marker": "device-marker",
        "classifier_marker": "classifier-marker",
    }
    assert summary["screenshot_calls"] == 2
    assert summary["classify_calls"] == 2


def test_telemetry_common_schema_and_canonical_aggregates(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder)
    clf = types.SimpleNamespace(classify_board=lambda *_a, **_k: (_board(), []))

    service.run(_ScreenshotDevice(), "schema-device", clf)
    events = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
    assert events
    for event in events:
        assert event["schema"] == "mining_telemetry_v1"
        assert isinstance(event["session_id"], str)
        assert isinstance(event["device_id"], str)
        assert isinstance(event["planner"], str) or event["planner"] is None
        assert isinstance(event["shadow_planner"], str) or event["shadow_planner"] is None

    round_event = next(event for event in events if event["event"] == "round")
    for key in (
        "round_screenshot_calls", "round_classify_calls", "round_overlay_ocr_calls",
        "round_shadow_calls", "round_shadow_successes", "round_shadow_failures",
        "round_shadow_skipped", "round_shadow_not_attempted",
    ):
        assert isinstance(round_event[key], int)
    assert isinstance(round_event["round_shadow_elapsed_ms"], (int, float))
    assert isinstance(round_event["round_shadow_sample_rate"], (int, float))

    summary = next(event for event in events if event["event"] == "session_summary")
    for key in (
        "screenshots_total", "screenshots_per_round_avg", "screenshot_calls_avg",
        "classify_calls_avg", "overlay_ocr_calls_avg", "shadow_calls_avg",
        "shadow_successes_avg", "shadow_failures_avg", "shadow_skipped_avg",
        "shadow_not_attempted_avg", "shadow_elapsed_ms_avg",
    ):
        assert key in summary
    assert summary["screenshots_total"] == summary["screenshot_calls"]
    assert summary["screenshots_per_round"] == summary["screenshots_per_round_avg"]


@pytest.mark.parametrize("shadow, expected_key", [("final_v1", "shadow_skipped"), ("", "shadow_not_attempted")])
def test_primary_exception_accounts_shadow_and_emits_active_round(monkeypatch, shadow, expected_key):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder, shadow=shadow)
    monkeypatch.setattr(
        service,
        "read_ws_below_rows",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        service,
        "_dispatch_planner",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("planner boom")),
    )
    clf = types.SimpleNamespace(classify_board=lambda *_a, **_k: (_board(), []))

    service.run(_ScreenshotDevice(), "primary-exception", clf)
    events = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
    round_event = next(event for event in events if event["event"] == "round")
    summary = next(event for event in events if event["event"] == "session_summary")
    assert round_event["status"] == "exception"
    assert round_event["round_shadow_calls"] == (1 if shadow else 0)
    assert round_event["round_shadow_skipped"] == (1 if shadow else 0)
    assert round_event["round_shadow_not_attempted"] == (0 if shadow else 1)
    assert summary["shadow_successes"] + summary["shadow_failures"] + summary["shadow_skipped"] == summary["shadow_calls"]
    assert summary[expected_key] == 1


def test_active_round_force_sleep_is_re_raised_and_stopped_reason_is_force_sleep(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_telemetry_run(monkeypatch, logger, recorder, shadow="final_v1")
    monkeypatch.setattr(
        service,
        "_dispatch_planner",
        lambda *_a, **_k: (_ for _ in ()).throw(service.ForceSleepRequested("stop")),
    )
    clf = types.SimpleNamespace(classify_board=lambda *_a, **_k: (_board(), []))
    with pytest.raises(service.ForceSleepRequested):
        service.run(_ScreenshotDevice(), "force-round", clf)

    events = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
    assert sum(event["event"] == "exception" for event in events) == 1
    assert sum(event["event"] == "round" for event in events) == 1
    summary = next(event for event in events if event["event"] == "session_summary")
    assert summary["stopped_reason"] == "force_sleep"
    assert summary["shadow_calls"] == 1
    assert summary["shadow_skipped"] == 1
    assert len(recorder.end_calls) == 1


def test_config_get_exception_emits_one_summary(monkeypatch):
    recorder = _FakeMapRecorder()
    logger = _TelemetryLogger()
    _patch_run_dependencies(monkeypatch, recorder, logger)
    monkeypatch.setattr(
        service.config_manager,
        "get_device_config",
        lambda _ip: (_ for _ in ()).throw(RuntimeError("config boom")),
    )

    service.run(object(), "config-exception", object())
    events = [json.loads(line) for line in logger.info_calls if line.startswith("{")]
    assert [event["event"] for event in events].count("session_summary") == 1
    assert [event["event"] for event in events].count("exception") == 1
    assert not recorder.end_calls
