"""human_played devices must be excluded from the online-check checker pool.

A device a human plays directly must never be auto-logged-in as a checker
(異地登入 would kick the human). The "*" checker wildcard must skip them.
"""
from __future__ import annotations

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
