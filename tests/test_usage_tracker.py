from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import threading
from datetime import datetime, timedelta

import pytest

from utils.action_tracker import ActionTraceRecorder
from utils import usage_tracker


_SUMMARY_SPEC = importlib.util.spec_from_file_location(
    "summarize_usage_tracking",
    Path(__file__).parents[1] / "tools" / "summarize_usage_tracking.py",
)
assert _SUMMARY_SPEC and _SUMMARY_SPEC.loader
_SUMMARY_MODULE = importlib.util.module_from_spec(_SUMMARY_SPEC)
_SUMMARY_SPEC.loader.exec_module(_SUMMARY_MODULE)
summarize_events = _SUMMARY_MODULE.summarize_events


def test_action_tracker_accepts_explicit_caller(tmp_path):
    recorder = ActionTraceRecorder(base_dir=str(tmp_path))
    caller = {
        "file": "feature.py",
        "line": 42,
        "function": "run_feature",
        "module": "feature",
    }

    recorder.log(
        device_id="device-1",
        event_type="ocr_request",
        source="test",
        caller=caller,
    )

    event_file = next((tmp_path / "device-1").glob("events_*.jsonl"))
    event = json.loads(event_file.read_text(encoding="utf-8").strip())
    assert event["caller"] == caller


def test_record_usage_infers_bot_thread_device_and_external_caller(monkeypatch):
    captured = {}

    class FakeRecorder:
        def log(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(usage_tracker, "_RECORDER", FakeRecorder())
    original_name = threading.current_thread().name
    threading.current_thread().name = "Bot-emulator-5554"
    try:
        usage_tracker.record_usage(
            event_type="ocr_request",
            component="remote_ocr",
            payload={"endpoint": "/analyze_skill"},
        )
    finally:
        threading.current_thread().name = original_name

    assert captured["device_id"] == "emulator-5554"
    assert captured["event_type"] == "ocr_request"
    assert captured["caller"]["function"] == "test_record_usage_infers_bot_thread_device_and_external_caller"
    assert captured["payload"]["component"] == "remote_ocr"
    assert captured["payload"]["call_chain"]


def test_classifier_decorator_tracks_success_and_failure(monkeypatch):
    events = []
    monkeypatch.setattr(usage_tracker, "record_usage", lambda **kwargs: events.append(kwargs))

    @usage_tracker.trace_classifier("test_classifier")
    def classify(value):
        if value < 0:
            raise ValueError("bad")
        return value + 1

    assert classify(1) == 2
    with pytest.raises(ValueError):
        classify(-1)

    assert [event["status"] for event in events] == ["success", "error"]
    assert all(event["event_type"] == "classifier_inference" for event in events)
    assert all(event["component"] == "test_classifier" for event in events)
    assert events[1]["payload"]["error_type"] == "ValueError"


def test_model_load_decorator_uses_distinct_event_type(monkeypatch):
    events = []
    monkeypatch.setattr(usage_tracker, "record_usage", lambda **kwargs: events.append(kwargs))

    @usage_tracker.trace_model_load("test_model")
    def load_model():
        return object()

    load_model()

    assert events[0]["event_type"] == "classifier_model_load"
    assert events[0]["component"] == "test_model"


def test_summarize_events_groups_by_real_caller():
    now = datetime.now()
    base = {
        "timestamp": now.isoformat(),
        "device_id": "device-1",
        "event_type": "ocr_request",
        "caller": {"file": "feature.py", "line": 9, "function": "scan"},
        "device_context": {"task": "雪國"},
    }
    events = [
        {
            **base,
            "payload": {
                "component": "remote_ocr",
                "status": "success",
                "elapsed_ms": 100,
            },
        },
        {
            **base,
            "payload": {
                "component": "remote_ocr",
                "status": "error",
                "elapsed_ms": 300,
            },
        },
    ]

    rows = summarize_events(events, since=now - timedelta(minutes=1))

    assert len(rows) == 1
    assert rows[0]["calls"] == 2
    assert rows[0]["success"] == 1
    assert rows[0]["errors"] == 1
    assert rows[0]["avg_ms"] == 200.0
    assert rows[0]["tasks"] == "雪國"
