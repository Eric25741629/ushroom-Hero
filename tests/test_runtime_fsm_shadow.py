"""Live shadow adapter contract tests (no ADB/Playwright)."""

from pathlib import Path

from runtime_services.runtime_fsm import RuntimeEvent, RuntimePhase
from runtime_services.runtime_fsm_shadow import RuntimeShadowRegistry


def test_registry_tracks_observed_context_and_mismatches():
    messages = []

    class _Logger:
        def warning(self, *args):
            messages.append(args)

    registry = RuntimeShadowRegistry(_Logger())
    first = registry.observe("shadow-dev", RuntimeEvent.WAKE_DUE,
                             RuntimePhase.WS_PHASE)
    assert first.accepted
    second = registry.observe("shadow-dev", RuntimeEvent.WS_COMPLETED,
                              RuntimePhase.CLIENT_TASKS)
    assert second.to_phase is RuntimePhase.WAKING_CLIENT

    snapshot = registry.snapshot("shadow-dev")
    assert snapshot["phase"] == RuntimePhase.CLIENT_TASKS.value
    assert snapshot["event_count"] == 2
    assert snapshot["mismatch_count"] == 1
    assert snapshot["last_mismatch"]["event"] == RuntimeEvent.WS_COMPLETED.value
    assert messages


def test_shadow_observer_is_wired_into_live_main_lifecycle():
    source = Path("new_main_v2.py").read_text(encoding="utf-8-sig")
    for event in ("WAKE_DUE", "WS_COMPLETED", "CLIENT_READY",
                  "TASKS_COMPLETED", "FORCE_SLEEP"):
        assert f'"{event}"' in source
    assert "observe_runtime_event" in source


def test_dashboard_exposes_shadow_snapshot_for_review():
    routes = Path("control_panel/routes_status.py").read_text(encoding="utf-8-sig")
    assert '@bp.route("/api/runtime_shadow/<ip>"' in routes
    assert "runtime_shadow" in routes
