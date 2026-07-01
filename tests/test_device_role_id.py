"""config_manager.get_device_role_id — single source of truth for a device's
account roleId, shared by the dashboard badge and every start-gate.

Precedence: explicit online_check_target_pid (creds-less shared accounts) >
captured creds.role_id > None.
"""
from __future__ import annotations

import json

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


def test_falls_back_to_lenient_role_id_when_creds_incomplete(monkeypatch):
    # A page-seeded web capture lacks uname/plat → load_creds raises, but the
    # lenient load_role_id still yields the roleId for the monitor.
    monkeypatch.setattr(config_manager, "get_device_config", lambda d: {})

    def _raise(d):
        raise ValueError("creds missing required field(s): uname, plat")

    monkeypatch.setattr(creds_mod, "load_creds", _raise)
    monkeypatch.setattr(creds_mod, "load_role_id", lambda d, **k: 777)

    assert config_manager.get_device_role_id("dev") == 777


def test_load_role_id_reads_partial_capture(tmp_path):
    (tmp_path / "_auth_capture_web-001.json").write_text(
        json.dumps({"creds": {"roleId": 555, "loginTicket": "t"}}),
        encoding="utf-8")
    assert creds_mod.load_role_id("web-001", auth_dir=tmp_path) == 555


def test_load_role_id_missing_file_is_none(tmp_path):
    assert creds_mod.load_role_id("nope", auth_dir=tmp_path) is None
