"""config_manager.get_device_role_id — single source of truth for a device's
account roleId, shared by the dashboard badge and every start-gate.

Precedence: explicit online_check_target_pid (creds-less shared accounts) >
captured creds.role_id > None.
"""
from __future__ import annotations

import config_manager
from ws_token import creds as creds_mod


def _fake_creds(role_id):
    return type("C", (), {"role_id": role_id})()


def test_target_pid_takes_precedence_over_creds(monkeypatch):
    monkeypatch.setattr(config_manager, "get_device_config",
                        lambda d: {"online_check_target_pid": 123})
    monkeypatch.setattr(creds_mod, "load_creds", lambda d: _fake_creds(999))

    assert config_manager.get_device_role_id("dev") == 123


def test_falls_back_to_creds_when_no_target_pid(monkeypatch):
    monkeypatch.setattr(config_manager, "get_device_config", lambda d: {})
    monkeypatch.setattr(creds_mod, "load_creds", lambda d: _fake_creds(456))

    assert config_manager.get_device_role_id("dev") == 456


def test_none_when_neither_target_nor_creds(monkeypatch):
    monkeypatch.setattr(config_manager, "get_device_config", lambda d: {})

    def _raise(d):
        raise FileNotFoundError

    monkeypatch.setattr(creds_mod, "load_creds", _raise)

    assert config_manager.get_device_role_id("dev") is None


def test_string_target_pid_is_coerced_to_int(monkeypatch):
    monkeypatch.setattr(config_manager, "get_device_config",
                        lambda d: {"online_check_target_pid": "89565100511322"})
    monkeypatch.setattr(creds_mod, "load_creds", lambda d: _fake_creds(1))

    assert config_manager.get_device_role_id("dev") == 89565100511322
