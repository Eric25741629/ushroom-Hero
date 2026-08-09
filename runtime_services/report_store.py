"""Small in-process store for the most recent task reports.

Reports are diagnostic UI data only.  The store deliberately keeps no durable
state and trims task payloads to JSON-safe values so a malformed action result
cannot break the dashboard status endpoint.
"""
from __future__ import annotations

import copy
import threading
import time
from collections.abc import Mapping
from typing import Any

_LOCK = threading.RLock()
_REPORTS: dict[str, dict[str, Any]] = {}


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(v) for v in value]
    outcome = getattr(value, "outcome", None)
    if outcome is not None:
        return {
            "outcome": getattr(outcome, "value", str(outcome)),
            "detail": _safe(getattr(value, "detail", None)),
        }
    return str(value)


def publish(device: str, report: Any, *, source: str = "client") -> None:
    """Publish a bounded diagnostic snapshot for one device."""
    if not device or report is None:
        return
    tasks = getattr(report, "tasks", {}) or {}
    errors = getattr(report, "errors", {}) or {}
    snapshot = {
        "source": source,
        "updated_at": time.time(),
        "device": device,
        "login_ok": bool(getattr(report, "login_ok", True)),
        "aborted": bool(getattr(report, "aborted", False)),
        "kicked": bool(getattr(report, "kicked", False)),
        "tasks": _safe(tasks),
        "errors": _safe(errors),
    }
    with _LOCK:
        _REPORTS[str(device)] = snapshot


def get(device: str) -> dict[str, Any] | None:
    with _LOCK:
        value = _REPORTS.get(str(device))
        return copy.deepcopy(value) if value is not None else None


def get_all() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return copy.deepcopy(_REPORTS)


def clear() -> None:
    with _LOCK:
        _REPORTS.clear()

