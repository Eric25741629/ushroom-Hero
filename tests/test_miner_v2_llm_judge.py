from miner.v2.llm_judge import (
    build_chat_payload,
    build_native_load_url,
    build_models_url,
    build_snapshot_payload,
    extract_json_object_from_text,
    normalize_suspect_cells,
    parse_json_response,
)
from miner.v2.types import BoardSnapshot, ScreenCheckResult


def _make_snapshot() -> BoardSnapshot:
    return BoardSnapshot(
        board=[["empty", "dirt"], ["reachable_pit", "rock"]],
        confidences=[[0.9, 0.7], [0.8, 0.4]],
        captured_at="2026-04-09T23:30:00",
        image_shape=(100, 200, 3),
        grid_config={"H": 2, "W": 2, "x0": 0, "y0": 0, "x1": 20, "y1": 20},
        screen_check=ScreenCheckResult(passed=True, matched_points=7),
    )


def test_build_models_url_from_chat_completions_endpoint():
    url = build_models_url("http://127.0.0.1:1234/v1/chat/completions")
    assert url == "http://127.0.0.1:1234/v1/models"


def test_build_native_load_url_from_chat_completions_endpoint():
    url = build_native_load_url("http://127.0.0.1:1234/v1/chat/completions")
    assert url == "http://127.0.0.1:1234/api/v1/models/load"


def test_extract_json_object_from_wrapped_text():
    parsed = extract_json_object_from_text('answer: {"judgment":"valid_board","confidence":0.9}')
    assert parsed is not None
    assert parsed["judgment"] == "valid_board"


def test_parse_json_response_accepts_markdown_block():
    parsed = parse_json_response(
        '```json\n{"judgment":"need_retry","next_action":"retry_screenshot","confidence":0.6,"reason":"x","suspect_cells":[]}\n```'
    )
    assert parsed["next_action"] == "retry_screenshot"


def test_build_snapshot_payload_contains_expected_fields():
    payload = build_snapshot_payload(_make_snapshot(), device_id="emulator-5560")
    assert payload["device_id"] == "emulator-5560"
    assert payload["avg_confidence"] == 0.7
    assert payload["screen_check"]["matched_points"] == 7
    assert "board_visual" in payload


def test_build_chat_payload_text_only_uses_string_content():
    payload = build_chat_payload(model="test-model", snapshot=_make_snapshot(), device_id="dev1")
    assert payload["model"] == "test-model"
    assert isinstance(payload["messages"][1]["content"], str)


def test_normalize_suspect_cells_filters_invalid_items():
    cells = normalize_suspect_cells(
        [
            {"row": 1, "col": 2, "reason": "low_confidence"},
            {"row": "bad", "col": 3, "reason": "skip"},
            "x",
        ]
    )
    assert cells == [{"row": 1, "col": 2, "reason": "low_confidence"}]
