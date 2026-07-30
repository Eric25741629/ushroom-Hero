from __future__ import annotations

import functools
import inspect
import os
import threading
import time
from typing import Any, Callable, Optional, TypeVar

from utils.action_tracker import ActionTraceRecorder


_F = TypeVar("_F", bound=Callable[..., Any])
_RECORDER = ActionTraceRecorder()
_DEFAULT_SKIP_FILES = {"usage_tracker.py", "action_tracker.py"}


def _string_device_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.startswith("<"):
        return ""
    return text


def _device_from_object(value: Any) -> str:
    for attr in ("_ip", "ip", "device_id", "devices_serial", "serial"):
        try:
            device_id = _string_device_id(getattr(value, attr, None))
        except Exception:
            device_id = ""
        if device_id:
            return device_id
    return ""


def _capture_usage_context(skip_files: Optional[set[str]] = None) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """找出真正的功能呼叫點，並盡量從執行緒或區域變數推回裝置 ID。"""
    skip = {name.lower() for name in _DEFAULT_SKIP_FILES}
    skip.update(name.lower() for name in (skip_files or set()))
    caller: dict[str, Any] = {"file": "", "line": 0, "function": "", "module": ""}
    call_chain: list[dict[str, Any]] = []
    device_id = ""

    thread_name = threading.current_thread().name
    if thread_name.startswith("Bot-"):
        device_id = thread_name[4:]

    frame = inspect.currentframe()
    if frame is not None:
        frame = frame.f_back
    try:
        while frame is not None:
            path = os.path.abspath(frame.f_code.co_filename)
            filename = os.path.basename(path)
            if filename.lower() not in skip:
                entry = {
                    "file": path,
                    "line": int(frame.f_lineno),
                    "function": frame.f_code.co_name,
                    "module": frame.f_globals.get("__name__", ""),
                }
                if not caller["file"]:
                    caller = entry
                if len(call_chain) < 8:
                    call_chain.append(entry)

            if not device_id:
                for key in ("ip", "device_id", "serial", "devices_serial"):
                    device_id = _string_device_id(frame.f_locals.get(key))
                    if device_id:
                        break
            if not device_id:
                for key in ("self", "d", "device"):
                    device_id = _device_from_object(frame.f_locals.get(key))
                    if device_id:
                        break
            frame = frame.f_back
    finally:
        # 不保留 frame reference，避免長時間程序形成 reference cycle。
        del frame

    return caller, device_id or "unknown", call_chain


def record_usage(
    *,
    event_type: str,
    component: str,
    payload: Optional[dict[str, Any]] = None,
    status: str = "success",
    elapsed_ms: Optional[float] = None,
    skip_files: Optional[set[str]] = None,
) -> None:
    """將 OCR／分類器實際使用事件寫入既有 action trace JSONL。"""
    try:
        caller, device_id, call_chain = _capture_usage_context(skip_files=skip_files)
        event_payload = dict(payload or {})
        event_payload["component"] = str(component)
        event_payload["status"] = str(status)
        if elapsed_ms is not None:
            event_payload["elapsed_ms"] = round(float(elapsed_ms), 3)
        event_payload["call_chain"] = call_chain
        _RECORDER.log(
            device_id=device_id,
            event_type=event_type,
            source="utils.usage_tracker",
            meaning=f"{component} {status}",
            actor="python",
            payload=event_payload,
            caller=caller,
        )
    except Exception:
        # 追蹤失敗不能影響遊戲自動化主流程。
        return


def _trace_call(event_type: str, component: str) -> Callable[[_F], _F]:
    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                record_usage(
                    event_type=event_type,
                    component=component,
                    status="error",
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    payload={"error_type": type(exc).__name__},
                )
                raise
            record_usage(
                event_type=event_type,
                component=component,
                status="success",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
            return result

        setattr(wrapper, "_usage_tracking_component", component)
        return wrapper  # type: ignore[return-value]

    return decorator


def trace_classifier(component: str) -> Callable[[_F], _F]:
    """裝飾 PyTorch 分類器推論入口，記錄成功、失敗、耗時與呼叫來源。"""
    return _trace_call("classifier_inference", component)


def trace_model_load(component: str) -> Callable[[_F], _F]:
    """裝飾 PyTorch 模型載入入口，區分「載入占資源」與「實際推論」事件。"""
    return _trace_call("classifier_model_load", component)
