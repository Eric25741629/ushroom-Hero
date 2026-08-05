"""七日登入獎勵純 WS 協議與 runner/page wiring 測試。"""
from __future__ import annotations

import pytest

from ws_token import seven_login


def test_build_reward_body_uses_day_field_one():
    assert seven_login.build_reward_body(7) == b"\x08\x07"


def test_build_reward_body_rejects_days_outside_seven_day_event():
    with pytest.raises(ValueError):
        seven_login.build_reward_body(0)
    assert seven_login.build_reward_body(900) == b"\x08\x84\x07"


def test_parse_info_supports_repeated_and_packed_get_day():
    # day=7, get_day=1 (unpacked), get_day=[2, 7] (packed), if_day_first=1
    body = b"\x08\x07\x10\x01\x12\x02\x02\x07\x18\x01"
    assert seven_login.parse_info(body) == {
        "day": 7,
        "get_day": [1, 2, 7],
        "if_day_first": 1,
    }


def test_next_claimable_day_is_first_unclaimed_unlocked_day():
    assert seven_login.next_claimable_day({"day": 7, "get_day": [1, 2, 3, 4, 5, 6]}) == 7
    assert seven_login.next_claimable_day({"day": 903, "get_day": list(range(1, 904))}) is None
    assert seven_login.next_claimable_day({"day": 900, "get_day": list(range(1, 897))}) == 897
    assert seven_login.next_claimable_day({"day": 3, "get_day": [1]}) == 2


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def call_for(self, cmd, body, *, expect_cmds):
        self.calls.append((cmd, body, tuple(expect_cmds)))
        return self.replies.pop(0)


def test_apply_via_client_queries_then_claims_one_day():
    client = _FakeClient([
        (seven_login.CMD_INFO, b"\x08\x07\x10\x01\x10\x02\x10\x03\x10\x04\x10\x05\x10\x06"),
        (seven_login.CMD_REWARD, b""),
    ])
    result = seven_login.apply_via_client(client, device="5554")
    assert result["ok"] is True
    assert result["claimed"] == 7
    assert [call[0] for call in client.calls] == [seven_login.CMD_INFO, seven_login.CMD_REWARD]
    assert client.calls[1][1] == b"\x08\x07"
    assert client.calls[1][2] == (seven_login.CMD_REWARD, seven_login.CMD_ERROR)


def test_apply_via_client_treats_error_reply_as_benign_skip():
    client = _FakeClient([
        (seven_login.CMD_INFO, b"\x08\x07"),
        (seven_login.CMD_ERROR, b"\x08\x09"),
    ])
    result = seven_login.apply_via_client(client)
    assert result["skipped"] == "server_reject"
    assert result["error_code"] == 9


def test_apply_via_client_does_not_claim_when_no_day_is_available():
    client = _FakeClient([(seven_login.CMD_INFO, b"\x08\x07" + b"\x10\x01\x10\x02\x10\x03\x10\x04\x10\x05\x10\x06\x10\x07")])
    result = seven_login.apply_via_client(client)
    assert result["skipped"] == "not_claimable"
    assert len(client.calls) == 1


def test_apply_via_page_accepts_raw_page_transport(monkeypatch):
    class FakeAPI:
        def __init__(self, page):
            assert page == "page"

        def call_raw_for(self, cmd, body, *, expect_cmds):
            if cmd == seven_login.CMD_INFO:
                return seven_login.CMD_INFO, b"\x08\x01"
            return seven_login.CMD_REWARD, b""

    import utils.web_game_api
    monkeypatch.setattr(utils.web_game_api, "WebGameAPI", FakeAPI)
    result = seven_login.apply_via_page("page")
    assert result == {"ok": True, "claimed": 1, "day": 1, "claimed_days": ()}
