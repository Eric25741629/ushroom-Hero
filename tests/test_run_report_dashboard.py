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


def test_dashboard_renders_report_and_read_only_endpoint():
    from pathlib import Path

    template = Path("templates/dashboard.html").read_text(encoding="utf-8-sig")
    routes = Path("control_panel/routes_status.py").read_text(encoding="utf-8-sig")
    assert 'id="report-${ip}"' in template
    assert "task_report" in template
    assert '@bp.route("/api/task_report/<ip>"' in routes

