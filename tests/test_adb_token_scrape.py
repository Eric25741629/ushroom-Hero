"""Tests for utils/adb_token_scrape.refresh_from_adb_device (Task B).

Passive logcat scrape: every adb wake should best-effort mint/refresh
auth_state/_auth_capture_<ip>.json from the SDK's plaintext login chain,
WITHOUT restarting the app or doing a WS verify. Never raises.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from utils import adb_token_scrape
from ws_token.creds import Creds

DEVICE = "emulator-5554"

# --- Realistic logcat fixtures, constructed to match the regexes in
# tools/adb_token_login.py (SCENE_LINE / AUTH_URL_RE / AUTH_REPLY_RE). ---

_SERVERLIST = (
    '{"list":[{"ip":"wss://gw.example.com/","cp":1,"cp2":2}],'
    '"gateway":{"ip":"wss://gw.example.com/"}}'
)
_AUTH_URL = "/oversea/login_auth?game_id=fngid-abc&did=dev-xyz&plat=2002"
_AUTH_REPLY = (
    '{"code":0,"uid":27353216,"uname":"小寶","role_id":1001,'
    '"time":1700000000,"is_white_ip":0,"ip":"1.2.3.4","token":"TICKET-AAAA",'
    '"p_key":"PKEY-BBBB","server_list":[{"game_server":"gs-1234567890"}]}'
)


def _fake_logcat(serverlist=_SERVERLIST, auth_url=_AUTH_URL, auth_reply=_AUTH_REPLY):
    parts = ["I/Cocos: some unrelated noise line"]
    if serverlist is not None:
        parts.append(f"I/Cocos: [JS] server-list get:{serverlist} done")
    if auth_url is not None:
        parts.append(f"I/Cocos: [JS] requesting https://host{auth_url} now")
    if auth_reply is not None:
        parts.append(f"I/Cocos: [JS] auth reply {auth_reply} end")
    return "\n".join(parts) + "\n"


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def _patch_run(monkeypatch, stdout=None, exc=None):
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(cmd)
        if exc is not None:
            raise exc
        return _FakeCompleted(stdout)

    monkeypatch.setattr(adb_token_scrape.subprocess, "run", fake_run)
    return calls


def test_happy_path_writes_capture(tmp_path, monkeypatch):
    _patch_run(monkeypatch, stdout=_fake_logcat())

    ok = adb_token_scrape.refresh_from_adb_device(
        object(), DEVICE, auth_dir=tmp_path, timeout_sec=5.0
    )

    assert ok is True
    path = tmp_path / f"_auth_capture_{DEVICE}.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    creds = data["creds"]
    for key in ("uid", "uname", "plat", "loginGameId", "roleId", "pKey", "loginTicket"):
        assert creds.get(key) not in (None, ""), key
    assert creds["_source"] == "adb_logcat_passive"
    # consumer must accept it
    parsed = Creds.from_dict(creds)
    assert parsed.login_ticket == "TICKET-AAAA"
    assert parsed.uid == "27353216"


def test_warm_start_patterns_absent_returns_false(tmp_path, monkeypatch):
    # No login chain in buffer (app merely resumed).
    _patch_run(monkeypatch, stdout="I/Cocos: nothing interesting here\n")

    ok = adb_token_scrape.refresh_from_adb_device(
        object(), DEVICE, auth_dir=tmp_path, timeout_sec=1.0
    )

    assert ok is False
    assert not (tmp_path / f"_auth_capture_{DEVICE}.json").exists()


def test_adb_subprocess_error_returns_false(tmp_path, monkeypatch):
    _patch_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="adb", timeout=5))

    ok = adb_token_scrape.refresh_from_adb_device(
        object(), DEVICE, auth_dir=tmp_path, timeout_sec=1.0
    )

    assert ok is False
    assert not (tmp_path / f"_auth_capture_{DEVICE}.json").exists()


def test_build_creds_failure_returns_false(tmp_path, monkeypatch):
    # auth reply present but code != 0 -> build_creds raises -> swallowed.
    bad_reply = _AUTH_REPLY.replace('"code":0', '"code":7')
    _patch_run(monkeypatch, stdout=_fake_logcat(auth_reply=bad_reply))

    ok = adb_token_scrape.refresh_from_adb_device(
        object(), DEVICE, auth_dir=tmp_path, timeout_sec=1.0
    )

    assert ok is False
    assert not (tmp_path / f"_auth_capture_{DEVICE}.json").exists()


def test_existing_capture_overwritten_with_fresh_ticket(tmp_path, monkeypatch):
    path = tmp_path / f"_auth_capture_{DEVICE}.json"
    path.write_text(
        json.dumps({"creds": {"loginTicket": "OLD-TICKET", "uid": "1"}}),
        encoding="utf-8",
    )
    _patch_run(monkeypatch, stdout=_fake_logcat())

    ok = adb_token_scrape.refresh_from_adb_device(
        object(), DEVICE, auth_dir=tmp_path, timeout_sec=5.0
    )

    assert ok is True
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    assert data["creds"]["loginTicket"] == "TICKET-AAAA"
