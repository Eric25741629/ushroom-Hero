"""星據搶佔 protobuf 編解碼，不連真實伺服器。"""
import pytest

from ws_token import codec, star_seize


def _slot(pos=1, owner=88, free_end=100, mount_id=999):
    defender = codec.pb_uint(1, 1)
    return (
        codec.pb_uint(1, pos)
        + codec.pb_uint(2, owner)
        + codec.pb_msg(4, defender)
        + codec.pb_msg(4, defender)
        + codec.pb_uint(5, 0)
        + codec.pb_uint(6, free_end)
        + codec.pb_uint(8, mount_id)
    )


def test_parse_state_preserves_slots_cooldowns_and_attackable():
    body = (
        codec.pb_msg(1, _slot(pos=2, owner=88, free_end=100))
        + codec.pb_msg(1, _slot(pos=1, owner=1467, free_end=0))
        + codec.pb_uint(4, 300)
        + codec.pb_uint(5, 250)
    )
    result = star_seize.parse_state(
        body, my_server=1467, server_time=200
    )
    assert [slot["pos"] for slot in result["slots"]] == [1, 2]
    assert result["slots"][0]["attackable"] is False
    assert result["slots"][1]["attackable"] is True
    assert result["slots"][1]["defQ"] == 2
    assert result["attack_cd_end_time"] == 250
    assert result["my_attack_cd_remaining"] == 50


def test_parse_opponent_decodes_utf8_and_attributes():
    attrs = codec.pb_uint(1, 7) + codec.pb_uint(2, 1234)
    defender = (
        codec.pb_uint(2, 88)
        + codec.pb_str(3, "守方甲")
        + codec.pb_uint(4, 2)
        + codec.pb_msg(6, attrs)
    )
    result = star_seize.parse_opponent(
        codec.pb_msg(3, defender), pos=3
    )
    assert result == {
        "pos": 3,
        "defenders": [{
            "name": "守方甲",
            "server": 88,
            "queue_index": 2,
            "attrs_kv": [{"k": 7, "v": 1234}],
        }],
    }


class FakeClient:
    def __init__(self, reply_cmd, reply):
        self.reply_cmd = reply_cmd
        self.reply = reply
        self.calls = []

    def call_for(self, cmd, body, *, expect_cmds, timeout):
        self.calls.append((cmd, body, expect_cmds, timeout))
        return self.reply_cmd, self.reply


def test_join_builds_pos_and_queue_type_and_parses_reply():
    reply = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 4)
        + codec.pb_uint(3, 2)
        + codec.pb_uint(4, 9)
    )
    client = FakeClient(star_seize.CMD_SERVER_CAR_JOIN, reply)
    result = star_seize.join(client, 4, 2)
    sent = codec.walk_dict(client.calls[0][1])
    assert sent == {1: 4, 2: 2}
    assert result == {
        "ok": True, "code": 0, "pos": 4, "queue_type": 2, "queue_index": 9
    }


def test_error_reply_raises_rpc_error():
    client = FakeClient(
        star_seize.CMD_ERROR, codec.pb_uint(1, 173)
    )
    with pytest.raises(star_seize.StarSeizeRPCError) as exc:
        star_seize.read_state(client)
    assert exc.value.code == 173
