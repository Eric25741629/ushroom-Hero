"""Recorder + trace schema tests."""
import json
from pathlib import Path

from task_sandbox.trace.recorder import Recorder
from task_sandbox.trace.schema import TraceEvent, EventKind  # noqa: F401


def test_schema_imports():
    """Schema module is importable."""
    pass


def test_recorder_writes_event_to_jsonl(tmp_path: Path):
    rec = Recorder(tmp_path)
    rec.event("click", x=100, y=200)
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["kind"] == "click"
    assert obj["args"] == {"x": 100, "y": 200}
    assert obj["seq"] == 0
    assert obj["ok"] is True
    assert "ts" in obj


def test_recorder_seq_increments(tmp_path: Path):
    rec = Recorder(tmp_path)
    rec.event("click", x=1, y=2)
    rec.event("click", x=3, y=4)
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == [0, 1]


def test_recorder_assertion_failure_triggers_screenshot(tmp_path: Path):
    """Assertion with ok=False must capture a screenshot."""
    rec = Recorder(tmp_path)

    class FakePngDevice:
        def screenshot(self, format: str = "opencv"):
            import numpy as np
            return np.zeros((10, 10, 3), dtype=np.uint8)

    rec.bind_device(FakePngDevice())
    rec.assertion("on_main_page", ok=False, detail="actual=lamp_page")
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[0])
    assert obj["kind"] == "assertion"
    assert obj["ok"] is False
    assert obj["screenshot_path"] is not None
    assert (tmp_path / obj["screenshot_path"]).exists()


def test_recorder_span_emits_start_and_end(tmp_path: Path):
    rec = Recorder(tmp_path)
    with rec.span("navigate"):
        rec.event("click", x=1, y=2)
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == ["span_start", "click", "span_end"]


def test_recorder_stage_check_screenshots_on_change(tmp_path: Path):
    rec = Recorder(tmp_path)

    class FakePngDevice:
        def screenshot(self, format: str = "opencv"):
            import numpy as np
            return np.zeros((10, 10, 3), dtype=np.uint8)

    rec.bind_device(FakePngDevice())
    rec.stage_check(before="main_page", after="lamp_page")
    rec.stage_check(before="lamp_page", after="lamp_page")
    rec.close()

    lines = (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    objs = [json.loads(line) for line in lines]
    assert objs[0]["screenshot_path"] is not None
    assert objs[1].get("screenshot_path") is None
