"""Tests for ws_token.creds — loading ADB-scraped login credentials."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token.creds import Creds, load_creds  # noqa: E402


# Mirrors the real auth_state/_auth_capture_emulator-5554.json shape.
SAMPLE = {
    "uid": "27353216",
    "uname": "900501469830@google",
    "plat": "2002",
    "loginGameId": "1694508815979110",
    "loginSceneId": 0,
    "roleId": 89555436834913,
    "isWhiteIp": 0,
    "loginTime": 1780924117,
    "pKey": "11906045",
    "loginTicket": "435048074945b36fbff4d61c9d0b4220",
    "ip": "114.38.146.150",
    "device_id": "a90ea022-aab1-4481-b595-2e729148d573",
    "device_name": "emulator-5554",
    "game_server": "U2FsdGVkX18QyhekNrOLY+Z7ADOb6BA0ZE8iX+lfKco=",
    "gateway": "wss://sgw-mix-tw-xxjzz.acenetgame.com",
    "_ws_url": "wss://sgw-mix-tw-xxjzz.acenetgame.com?token=U2FsdGVkX18QyhekNrOLY+Z7ADOb6BA0ZE8iX+lfKco=",
}


def test_from_dict_maps_login_fields():
    c = Creds.from_dict(SAMPLE)
    assert c.uid == "27353216"
    assert c.uname == "900501469830@google"
    assert c.plat == "2002"
    assert c.login_game_id == "1694508815979110"
    assert c.role_id == 89555436834913
    assert c.is_white_ip == 0
    assert c.login_time == 1780924117
    assert c.p_key == "11906045"
    assert c.login_ticket == "435048074945b36fbff4d61c9d0b4220"
    assert c.login_scene_id == 0
    assert c.device_id == "a90ea022-aab1-4481-b595-2e729148d573"
    assert c.ws_url == SAMPLE["_ws_url"]


def test_from_dict_is_frozen():
    c = Creds.from_dict(SAMPLE)
    with pytest.raises(Exception):
        c.uid = "999"  # type: ignore[misc]


def test_from_dict_derives_ws_url_when_missing():
    d = {k: v for k, v in SAMPLE.items() if k != "_ws_url"}
    c = Creds.from_dict(d)
    assert c.ws_url == f"{SAMPLE['gateway']}?token={SAMPLE['game_server']}"


def test_from_dict_missing_required_field_raises():
    d = {k: v for k, v in SAMPLE.items() if k != "loginTicket"}
    with pytest.raises(ValueError, match="loginTicket"):
        Creds.from_dict(d)


def test_load_creds_reads_capture_file(tmp_path: Path):
    auth_dir = tmp_path / "auth_state"
    auth_dir.mkdir()
    (auth_dir / "_auth_capture_emu-x.json").write_text(
        json.dumps({"creds": SAMPLE}), encoding="utf-8"
    )
    c = load_creds("emu-x", auth_dir=auth_dir)
    assert c.uid == "27353216"
    assert c.ws_url == SAMPLE["_ws_url"]


def test_load_creds_tolerates_utf8_bom(tmp_path: Path):
    auth_dir = tmp_path / "auth_state"
    auth_dir.mkdir()
    (auth_dir / "_auth_capture_bom.json").write_text(
        json.dumps({"creds": SAMPLE}), encoding="utf-8-sig"  # writes a BOM
    )
    c = load_creds("bom", auth_dir=auth_dir)
    assert c.uid == "27353216"  # no stray ﻿ on the first key


def test_load_creds_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_creds("nope", auth_dir=tmp_path / "auth_state")


def test_refresh_creds_subprocess_has_timeout(tmp_path: Path, monkeypatch):
    """wake loop 會呼叫 refresh_creds；外層 subprocess 必須有 timeout 護欄。"""
    from ws_token import creds as creds_mod

    auth_dir = tmp_path / "auth_state"
    auth_dir.mkdir()
    (auth_dir / "_auth_capture_emu-t.json").write_text(
        json.dumps({"creds": SAMPLE}), encoding="utf-8"
    )
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr(creds_mod.subprocess, "run", fake_run)
    creds_mod.refresh_creds("emu-t", auth_dir=auth_dir)
    assert captured.get("timeout") is not None and captured["timeout"] > 0
