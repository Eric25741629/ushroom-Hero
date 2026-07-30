from __future__ import annotations

import numpy as np

import cnn_model
import img_tools
import new_cnn.cnn_model as new_cnn_model
from miner.models.classifier import ClassifierCNN, load_cnn_model as load_mining_model_v1
from miner.v2.classifier import BoardClassifierV2, load_board_model as load_mining_model_v2


class _Response:
    status_code = 200

    @staticmethod
    def json():
        return {"success": True, "ocr_results": [{"text": "主頁面"}]}


def test_remote_ocr_records_selected_server_and_result_count(monkeypatch):
    events = []
    monkeypatch.setattr(img_tools, "record_usage", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(img_tools, "encode_image", lambda _img: "encoded")
    monkeypatch.setattr(img_tools, "_build_ocr_server_priority", lambda explicit_url=None: ["http://ocr"])
    monkeypatch.setattr(img_tools, "_ensure_ocr_probe_thread", lambda: None)
    monkeypatch.setattr(img_tools, "_filter_servers_by_circuit", lambda servers: servers)
    monkeypatch.setattr(img_tools, "_mark_server_recovered", lambda _server: None)
    monkeypatch.setattr(img_tools.requests, "post", lambda *_args, **_kwargs: _Response())

    result = img_tools.analyze_skill_via_http(np.zeros((2, 2, 3), dtype=np.uint8))

    assert result["success"] is True
    assert len(events) == 1
    assert events[0]["event_type"] == "ocr_request"
    assert events[0]["status"] == "success"
    assert events[0]["payload"]["server"] == "http://ocr"
    assert events[0]["payload"]["endpoint"] == "/analyze_skill"
    assert events[0]["payload"]["result_count"] == 1
    assert events[0]["skip_files"] == {"img_tools.py"}


def test_all_runtime_classifier_entrypoints_have_usage_tracking():
    assert cnn_model.load_cnn_model._usage_tracking_component == "legacy_stage_cnn_root"
    assert cnn_model.predict_image._usage_tracking_component == "legacy_stage_cnn_root"
    assert new_cnn_model.load_cnn_model._usage_tracking_component == "legacy_stage_cnn"
    assert new_cnn_model.predict_image._usage_tracking_component == "legacy_stage_cnn"
    assert load_mining_model_v1._usage_tracking_component == "mining_board_cnn_v1"
    assert ClassifierCNN.classify_board._usage_tracking_component == "mining_board_cnn_v1"
    assert load_mining_model_v2._usage_tracking_component == "mining_board_cnn_v2"
    assert BoardClassifierV2.classify_board._usage_tracking_component == "mining_board_cnn_v2"
