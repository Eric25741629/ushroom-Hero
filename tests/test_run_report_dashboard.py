"""Wave 6 W14: RunReport dashboard snapshot contract."""

from dataclasses import dataclass


@dataclass
class _Outcome:
    value: str = "SKIPPED"


@dataclass
class _Result:
    outcome: _Outcome
    detail: str


class _Report:
    tasks = {"lamp": _Result(_Outcome(), "WS 已完成，跳過")}
    errors = {}
    login_ok = True
    aborted = False
    kicked = False


def test_report_store_publishes_json_safe_task_result():
    from runtime_services import report_store

    report_store.clear()
    report_store.publish("report-device", _Report(), source="client")
    snapshot = report_store.get("report-device")

    assert snapshot["source"] == "client"
    assert snapshot["tasks"]["lamp"] == {
        "outcome": "SKIPPED", "detail": "WS 已完成，跳過"
    }
    # Callers receive a copy, not the mutable store entry.
    snapshot["tasks"]["lamp"]["detail"] = "changed"
    assert report_store.get("report-device")["tasks"]["lamp"]["detail"] == "WS 已完成，跳過"
    report_store.clear()


def test_report_store_normalizes_real_task_result_outcome_to_uppercase():
    from game_actions.task_registry import TaskOutcome, TaskResult
    from runtime_services import report_store

    result = TaskResult(TaskOutcome.SKIPPED, detail="x")
    assert report_store.normalize_task_payload(result) == {
        "outcome": "SKIPPED", "detail": "x"
    }

    class _Report:
        tasks = {"lamp": result}
        errors = {}

    report_store.clear()
    report_store.publish("enum-device", _Report())
    assert report_store.get("enum-device")["tasks"]["lamp"]["outcome"] == "SKIPPED"
    report_store.clear()


def test_report_store_normalizes_legacy_skip_error_and_unknown_payloads():
    from runtime_services import report_store

    class _LegacyReport:
        tasks = {
            "no_page": {"skipped": "no_page"},
            "disabled": {"status": "skipped", "detail": "flag_off"},
            "network": {"error": "連線失敗"},
            "empty": {},
            "success": {"ok": True},
        }
        errors = {}

    report_store.clear()
    report_store.publish("legacy-device", _LegacyReport())
    tasks = report_store.get("legacy-device")["tasks"]
    assert tasks["no_page"]["outcome"] == "SKIPPED"
    assert tasks["disabled"]["outcome"] == "SKIPPED"
    assert tasks["network"]["outcome"] == "PERMANENT_FAILURE"
    assert tasks["empty"]["outcome"] == "UNKNOWN"
    assert tasks["success"]["outcome"] == "COMPLETED"
    report_store.clear()


def test_dashboard_renders_report_and_read_only_endpoint():
    from pathlib import Path

    template = Path("templates/dashboard.html").read_text(encoding="utf-8-sig")
    routes = Path("control_panel/routes_status.py").read_text(encoding="utf-8-sig")
    assert 'id="report-${ip}"' in template
    assert "task_report" in template
    assert "UNKNOWN" in template
    assert '@bp.route("/api/task_report/<ip>"' in routes

