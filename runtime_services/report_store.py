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


_KNOWN_OUTCOMES = {
    "COMPLETED", "SKIPPED", "RETRYABLE_FAILURE", "PERMANENT_FAILURE",
    "INTERRUPTED", "UNKNOWN",
}


def normalize_task_payload(
    value: Any, *, source: str = "client", task_id: str | None = None
) -> dict[str, Any]:
    """把既有 action payload 轉成 dashboard 可判定的統一 outcome。

    WS runner 的 ``RunReport`` 以「結果在 ``tasks``、例外在 ``errors``」作為
    成功邊界；不少舊 WS executor 成功時只回傳摘要 dict，甚至回傳空 dict，
    沒有另外塞 ``success=True``。僅在 WS 報告中套用這個既有契約，避免把
    client 端仍無法判定的 legacy payload 誤報成完成。
    """
    safe = _safe(value)
    if isinstance(safe, Mapping):
        payload = {str(k): v for k, v in safe.items()}
        outcome = str(payload.get("outcome", "")).upper()
        if outcome in _KNOWN_OUTCOMES:
            payload["outcome"] = outcome
            return payload

        status = str(payload.get("status", "")).lower()
        if payload.get("skipped") or status in {"skipped", "skip"}:
            detail = payload.get("skipped") or payload.get("detail") or status
            return {"outcome": "SKIPPED", "detail": str(detail)}
        if payload.get("error") or status in {"error", "failed", "failure"}:
            detail = payload.get("error") or payload.get("detail") or status
            return {"outcome": "PERMANENT_FAILURE", "detail": str(detail)}
        if payload.get("success") is False or payload.get("ok") is False:
            # pay_mall 每日免費禮包已領／已過期時，伺服器會用 173 拒絕；
            # 這是冪等的正常跳過，不是本輪執行故障。
            if task_id == "pay_mall" and payload.get("error_code") == 173:
                return {
                    "outcome": "SKIPPED",
                    "detail": "already claimed or expired (code=173)",
                    "error_code": 173,
                }
            detail = payload.get("detail") or payload.get("reason") or "執行失敗"
            if payload.get("error_code") not in (None, 0):
                detail = f"{detail} (code={payload['error_code']})"
            return {"outcome": "PERMANENT_FAILURE", "detail": str(detail)}
        if payload.get("success") is True or payload.get("ok") is True \
                or status in {"ok", "completed", "success"}:
            return {"outcome": "COMPLETED",
                    "detail": str(payload.get("detail") or "完成")}
        if not payload:
            if source == "ws":
                return {"outcome": "COMPLETED", "detail": "完成"}
            return {"outcome": "UNKNOWN", "detail": "無結果"}
        if source == "ws":
            # WS runner 已把未拋例外的結果放進 tasks；保留原摘要供 API 除錯，
            # dashboard 顯示穩定的人類可讀完成狀態。
            return {"outcome": "COMPLETED", "detail": "完成", "raw": payload}
        return {"outcome": "UNKNOWN", "detail": "無法判定", "raw": payload}

    if safe is False:
        return {"outcome": "PERMANENT_FAILURE", "detail": "執行失敗"}
    if safe is None:
        return {"outcome": "UNKNOWN", "detail": "無結果"}
    return {"outcome": "UNKNOWN", "detail": str(safe)}


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
        "tasks": {
            str(task_id): normalize_task_payload(
                payload, source=source, task_id=str(task_id)
            )
            for task_id, payload in tasks.items()
        } if isinstance(tasks, Mapping) else {},
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

