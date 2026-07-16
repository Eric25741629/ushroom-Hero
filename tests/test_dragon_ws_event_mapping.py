"""龍骸聖域 pure-WS 事件映射回歸鎖（fix/dragon-ws-event-mapping）。

以 LIVE 實抓資料為權威來源：
  - _info s2c 真實佈局（來自客戶端解碼 ground truth dcache_5554.json）：
        field 1 = team_id
        field 2 = ceng
        field 3 = hp
        field 4 = next_hp_time
        field 5 = help_hp
        field 6 = help_counter
        field 7 = event_id
        field 8 = event_data   (repeated {k#1, v#2})
        field 9 = ext          (repeated {k#1, v#2})
        field 10 = event_uid
    修正前 bug：把 field 8 當 event_uid、field 9 當 event_data，導致 ext 的
    {k:1,v:0} 被誤讀成 K_PVE_HP → 寶箱被當成小怪送 CHOICE_ADVANCE → server 忽略
    → deadloop（「站在事件前不動」）。
  - event_choice c2s 黃金樣本（frame 8，寶箱 event_id=11 時實抓「繼續探索」）：
        cmd 0x4F12  body = 08021000 = {choice#1: 2(CHOICE_DETOUR), event_uid#2: 0}
        server 回 0x4F13 {1:1,3:1} 成功。
"""
from __future__ import annotations

import pytest

from ws_token import codec
from ws_token import dragon_realm as dr


# --- helpers: build synthetic wire bodies from the ground-truth field layout --

def _kv(k: int, v: int) -> bytes:
    """一個 {k#1, v#2} 子訊息 body。"""
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def encode_info(*, team_id=89608050180626, ceng=1, hp=28, next_hp_time=0,
                help_hp=0, help_counter=0, event_id=0,
                event_data=None, ext=None, event_uid=0) -> bytes:
    """依 LIVE ground-truth 佈局編一個 dragon_realm_info_s2c body。"""
    parts = [
        codec.pb_uint(1, team_id),
        codec.pb_uint(2, ceng),
        codec.pb_uint(3, hp),
        codec.pb_uint(4, next_hp_time),
        codec.pb_uint(5, help_hp),
        codec.pb_uint(6, help_counter),
        codec.pb_uint(7, event_id),
    ]
    for k, v in (event_data or {}).items():
        parts.append(codec.pb_msg(8, _kv(k, v)))
    for k, v in (ext or {}).items():
        parts.append(codec.pb_msg(9, _kv(k, v)))
    parts.append(codec.pb_uint(10, event_uid))
    return b"".join(parts)


class FakeClient:
    """最小 WSGameClient 替身：call(CMD_INFO) 回固定 info；記錄 send。"""

    def __init__(self, info_body: bytes):
        self._info_body = info_body
        self.sends: list[tuple[int, bytes]] = []

    def call(self, cmd: int, body: bytes) -> bytes:  # noqa: ARG002
        assert cmd == dr.CMD_INFO
        return self._info_body

    def send(self, cmd: int, body: bytes) -> None:
        self.sends.append((cmd, bytes(body)))


class FakeTracker:
    def __init__(self, keys: int = 0):
        self.counts = {dr.KEY_ITEM: keys}

    def seed_from_query(self, client):  # noqa: ARG002
        return None


# --- BUG 1: _read_info 欄位映射 ---------------------------------------------

def test_read_info_maps_event_uid_to_field_10():
    """event_uid 來自 field 10（不是 field 8）。"""
    body = encode_info(ceng=1, hp=28, event_id=11,
                       event_uid=20011000002, ext={1: 0})
    info = dr._read_info(FakeClient(body))
    assert info["euid"] == 20011000002


def test_read_info_basic_scalar_fields():
    body = encode_info(ceng=1, hp=28, event_id=11,
                       event_uid=20011000002, ext={1: 0})
    info = dr._read_info(FakeClient(body))
    assert (info["ceng"], info["hp"], info["eid"]) == (1, 28, 11)


def test_read_info_event_data_is_field_8_not_ext():
    """ground-truth 寶箱：event_data 空、ext=[{k:1,v:0}]。

    ext 的 k=1 絕不可洩漏進 event_data，否則 _infer_type 會把寶箱誤判成 monster。
    這是 live deadloop 的直接根因鎖。
    """
    body = encode_info(ceng=1, hp=28, event_id=11,
                       event_uid=20011000002, event_data={}, ext={1: 0})
    info = dr._read_info(FakeClient(body))
    assert info["ed"] == {}
    assert dr._infer_type(info["ed"]) == "other"


def test_read_info_monster_event_data_parsed_from_field_8():
    """小怪：event_data 帶 K_PVE_HP → _infer_type=monster。"""
    body = encode_info(ceng=1, hp=28, event_id=5,
                       event_uid=123, event_data={dr.K_PVE_HP: 500})
    info = dr._read_info(FakeClient(body))
    assert info["ed"].get(dr.K_PVE_HP) == 500
    assert dr._infer_type(info["ed"]) == "monster"


# --- BUG 2: event_choice 編碼對齊黃金樣本 ------------------------------------

def test_choice_body_box_matches_golden_frame():
    """寶箱「繼續探索」body 必須位元相同於 frame 8 = 08021000。"""
    assert dr._choice_body(dr.CHOICE_DETOUR) == bytes.fromhex("08021000")


def test_choice_body_decodes_to_choice_and_zero_uid():
    fields = codec.walk(dr._choice_body(dr.CHOICE_DETOUR))
    assert fields == [(1, 2), (2, 0)]


def test_choice_body_never_leaks_nonzero_event_uid():
    """當前事件 choice 永遠 event_uid=0（避免『多送/錯送 event_uid』）。"""
    for choice in (dr.CHOICE_ADVANCE, dr.CHOICE_DETOUR, dr.CHOICE_ASK_HELP):
        assert codec.walk(dr._choice_body(choice)) == [(1, choice), (2, 0)]


def test_golden_frame8_roundtrip_through_codec():
    """_choice_body + framing/XOR 位元重現實抓的『繼續探索』封包 frame 8。"""
    frame8 = bytes.fromhex("0000000806c74f120796ed55")
    built = codec.gen_packet(dr.CMD_CHOICE,
                             dr._choice_body(dr.CHOICE_DETOUR), send_id=0x06c7)
    assert built == frame8


# --- BUG 3: 寶箱不開箱，一律送 CHOICE_DETOUR（繼續探索） ---------------------

def test_box_event_sends_detour_not_advance():
    body = encode_info(ceng=1, hp=10, event_id=11,
                       event_uid=20011000002, event_data={}, ext={1: 0})
    client = FakeClient(body)
    dr.run(client, FakeTracker(keys=0), max_actions=1, pace=0.0, max_stuck=99)
    assert client.sends == [(dr.CMD_CHOICE, bytes.fromhex("08021000"))]


def test_monster_event_sends_advance():
    body = encode_info(ceng=1, hp=10, event_id=5,
                       event_uid=123, event_data={dr.K_PVE_HP: 500})
    client = FakeClient(body)
    dr.run(client, FakeTracker(keys=0), max_actions=1, pace=0.0, max_stuck=99)
    assert client.sends == [(dr.CMD_CHOICE,
                             codec.pb_uint(1, dr.CHOICE_ADVANCE) + codec.pb_uint(2, 0))]


def test_trap_challenge_sends_ask_help():
    body = encode_info(ceng=1, hp=10, event_id=7, event_uid=9,
                       event_data={dr.K_TRAP_TIME: 100, dr.K_IS_CHALLENGE: 1})
    client = FakeClient(body)
    dr.run(client, FakeTracker(keys=0), max_actions=1, pace=0.0, max_stuck=99)
    assert client.sends == [(dr.CMD_CHOICE,
                             codec.pb_uint(1, dr.CHOICE_ASK_HELP) + codec.pb_uint(2, 0))]


# --- BUG 4: reached_tier_three_gate 需 server 權威 ceng==2 -------------------

def test_gate_returns_only_when_server_says_ceng_two():
    body = encode_info(ceng=2, hp=28, event_id=0)
    reason = dr.run(FakeClient(body), FakeTracker(keys=dr.TIER3_KEYS),
                    max_actions=5, pace=0.0, max_stuck=99)
    assert reason == "reached_tier_three_gate"


def test_ceng_one_with_enough_keys_never_declares_gate():
    """ceng==1 即使鑰匙足夠也絕不回 reached_tier_three_gate（先進 2 樓）。"""
    body = encode_info(ceng=1, hp=28, event_id=0)  # server 卡在 ceng=1
    client = FakeClient(body)
    reason = dr.run(client, FakeTracker(keys=dr.TIER3_KEYS),
                    max_actions=50, pace=0.0, max_stuck=3)
    assert reason != "reached_tier_three_gate"
    assert reason == "deadloop"
    # 應該是嘗試進 2 樓（enter_ceng）而非宣告到門前
    assert client.sends and client.sends[0][0] == dr.CMD_ENTER_CENG


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
