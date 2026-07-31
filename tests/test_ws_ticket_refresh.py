"""utils.ws_ticket_refresh — 從 Playwright page 回寫 _auth_capture JSON。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import ws_ticket_refresh as wtr  # noqa: E402
from ws_token.creds import load_creds  # noqa: E402


class _FakePage:
    def __init__(self, result):
        self._result = result

    def evaluate(self, _js):
        return self._result


class _SequencePage:
    def __init__(self, *results):
        self._results = list(results)
        self.calls = 0

    def evaluate(self, _js):
        self.calls += 1
        if self._results:
            result = self._results.pop(0)
        else:
            result = None
        return result


class _FakeDevice:
    def __init__(self, page):
        self._page = page


_FRESH = {
    "uid": "u1", "uname": "name", "plat": "android",
    "loginGameId": "g1", "roleId": 42, "pKey": "newkey",
    "loginTicket": "newticket", "loginSceneId": 1, "isWhiteIp": 0,
    "loginTime": 1770000000, "gateway": "wss://x", "game_server": "abc",
    "_ws_url": "wss://x/?token=abc",
}


def test_capture_script_reads_login_cache_singleton_fields():
    assert "const L = IS(mod.LoginDataCache);" in wtr._CAPTURE_JS
    for field in ("uname", "plat", "gateway", "game_server", "loginTicket"):
        assert f"{field}:" in wtr._CAPTURE_JS


def _seed_capture(auth_dir, extra=None):
    creds = {"uid": "u1", "uname": "name", "plat": "android",
             "loginGameId": "g1", "roleId": 42, "pKey": "oldkey",
             "loginTicket": "oldticket", "loginSceneId": 1, "isWhiteIp": 0,
             "loginTime": 1760000000, "_ws_url": "wss://x/?token=old"}
    creds.update(extra or {})
    (auth_dir / "_auth_capture_dev1.json").write_text(
        json.dumps({"creds": creds, "_source": "adb_logcat"}), encoding="utf-8")


def test_refresh_updates_ticket_preserves_uname_plat(tmp_path):
    _seed_capture(tmp_path)
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(_FRESH)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is True
    data = json.loads((tmp_path / "_auth_capture_dev1.json")
                      .read_text(encoding="utf-8-sig"))
    creds = data["creds"]
    assert creds["loginTicket"] == "newticket"
    assert creds["pKey"] == "newkey"
    assert creds["loginTime"] == 1770000000
    assert creds["_ws_url"] == "wss://x/?token=abc"
    assert creds["uname"] == "name" and creds["plat"] == "android"  # 保留
    assert data["_source"] == "playwright_refresh"
    assert data["_partial"] is False


def test_refresh_no_capture_file_seeds_complete_capture(tmp_path):
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(_FRESH)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is True
    data = json.loads((tmp_path / "_auth_capture_dev1.json")
                      .read_text(encoding="utf-8-sig"))
    assert data["creds"]["roleId"] == 42
    assert data["creds"]["loginTicket"] == "newticket"
    assert data["_source"] == "playwright_seed"
    assert data["_partial"] is False
    parsed = load_creds("dev1", auth_dir=tmp_path)
    assert parsed.uname == "name"
    assert parsed.plat == "android"


def test_refresh_no_capture_file_keeps_partial_seed(tmp_path):
    partial = dict(_FRESH, uname="", plat="")
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(partial)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is True
    data = json.loads((tmp_path / "_auth_capture_dev1.json")
                      .read_text(encoding="utf-8-sig"))
    assert data["_source"] == "playwright_seed"
    assert data["_partial"] is True
    with pytest.raises(ValueError, match="uname.*plat|uname|plat"):
        load_creds("dev1", auth_dir=tmp_path)


def test_seed_without_role_id_is_noop(tmp_path):
    bad = dict(_FRESH, roleId=0)
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(bad)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is False
    assert not (tmp_path / "_auth_capture_dev1.json").exists()


def test_refresh_no_page_is_noop(tmp_path):
    _seed_capture(tmp_path)
    ok = wtr.refresh_from_device(_FakeDevice(None), "dev1", auth_dir=tmp_path)
    assert ok is False


def test_refresh_page_eval_error_is_noop(tmp_path):
    _seed_capture(tmp_path)

    class _Boom:
        def evaluate(self, _js):
            raise RuntimeError("page closed")

    ok = wtr.refresh_from_device(_FakeDevice(_Boom()), "dev1", auth_dir=tmp_path)
    assert ok is False


def test_refresh_invalid_existing_capture_is_noop(tmp_path):
    path = tmp_path / "_auth_capture_dev1.json"
    original = "not-json"
    path.write_text(original, encoding="utf-8")
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(_FRESH)), "dev1",
                                 auth_dir=tmp_path)
    assert ok is False
    assert path.read_text(encoding="utf-8") == original


def test_refresh_missing_ticket_in_result_is_noop(tmp_path):
    _seed_capture(tmp_path)
    bad = dict(_FRESH, loginTicket="")
    ok = wtr.refresh_from_device(_FakeDevice(_FakePage(bad)), "dev1",
                                 auth_dir=tmp_path, ticket_attempts=1,
                                 retry_delay_sec=0)
    assert ok is False


def test_refresh_retries_until_login_ticket_is_populated(tmp_path):
    _seed_capture(tmp_path)
    empty = dict(_FRESH, loginTicket="")
    page = _SequencePage(empty, _FRESH)

    ok = wtr.refresh_from_device(
        _FakeDevice(page),
        "dev1",
        auth_dir=tmp_path,
        ticket_attempts=2,
        retry_delay_sec=0,
    )

    assert ok is True
    assert page.calls == 2
    data = json.loads((tmp_path / "_auth_capture_dev1.json")
                      .read_text(encoding="utf-8-sig"))
    assert data["creds"]["loginTicket"] == "newticket"
