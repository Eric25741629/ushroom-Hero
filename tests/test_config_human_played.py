"""Online-check checker selection guardrails.

A device a human plays directly must never be auto-logged-in as a checker
(異地登入 would kick the human). The "*" checker wildcard must skip them.
"""
from __future__ import annotations

import json
from pathlib import Path

import config_manager as cm


def _stub(monkeypatch, devices, global_cfg):
    monkeypatch.setattr(cm, "load_config", lambda: {"devices": devices})
    monkeypatch.setattr(cm, "get_global_config", lambda: global_cfg)


def test_wildcard_excludes_human_played(monkeypatch):
    devices = {
        "emulator-5554": {},
        "emulator-5556": {},
        "phone": {"human_played": True},
        "emulator-5558": {"online_check_target_pid": 123},
    }
    _stub(monkeypatch, devices, {"online_check_checkers": ["*"]})

    checkers = cm.get_online_check_checkers()

    assert "phone" not in checkers
    assert "emulator-5558" not in checkers  # has target_pid (pre-existing rule)
    assert "emulator-5554" in checkers
    assert "emulator-5556" in checkers


def test_get_human_played_devices(monkeypatch):
    devices = {"a": {"human_played": True}, "b": {}, "c": {"human_played": False}}
    monkeypatch.setattr(cm, "load_config", lambda: {"devices": devices})

    assert cm.get_human_played_devices() == ["a"]


def test_project_uses_explicit_online_check_checker_allowlist():
    config_path = Path(cm.__file__).with_name("bot_config.json")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))

    assert config["global"]["online_check_checkers"] == [
        "emulator-5554",
        "emulator-5556",
        "emulator-5560",
        "7fe98fc6",
    ]
