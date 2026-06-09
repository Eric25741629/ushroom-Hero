"""Byte-parity + round-trip tests for ws_token.codec.

The one-shot PoC tools/_login_poc.py is LIVE-verified against the real game
server (role_login code=0, then 337 pets decoded). So the strongest correctness
proof for the new codec is *byte-identical* behavior to that PoC across many
inputs -- provable offline, no live socket needed.
"""
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import _login_poc as poc  # noqa: E402  golden reference (LIVE-verified)

from ws_token import codec  # noqa: E402  module under test


# (cmd, body) cases spanning login/heartbeat/task command ids and body sizes.
CMDS = [257, 260, 16898, 0x2605, 0x0C01, 0, 65535]
BODIES = [
    b"",
    b"\x01",
    bytes(range(127)),          # exercises 1-byte protobuf-ish varint boundary
    bytes(range(256)) * 3,      # long body > 255
    b"\xff" * 1000,
]


def test_key_256_matches_poc():
    assert codec.KEY_256 == poc.KEY_256


@pytest.mark.parametrize("cmd", CMDS)
@pytest.mark.parametrize("body", BODIES)
def test_xor_body_matches_poc(cmd, body):
    assert bytes(codec.xor_body(bytearray(body), cmd)) == bytes(
        poc._xor(bytearray(body), cmd)
    )


@pytest.mark.parametrize("cmd", CMDS)
@pytest.mark.parametrize("body", BODIES)
def test_xor_body_is_involutive(cmd, body):
    once = codec.xor_body(bytearray(body), cmd)
    twice = codec.xor_body(bytearray(once), cmd)
    assert bytes(twice) == body


@pytest.mark.parametrize("v", [0, 1, 0x7F, 0x80, 300, 65535, 1 << 31, 1780924117])
def test_pb_varint_matches_poc(v):
    assert codec.pb_varint(v) == poc._vint(v)


@pytest.mark.parametrize("fid", [1, 2, 10, 15])
@pytest.mark.parametrize("s", ["", "h5", "540 X 960", "U2FsdGVkX1+abc/=="])
def test_pb_str_matches_poc(fid, s):
    assert codec.pb_str(fid, s) == poc._f_str(fid, s)


@pytest.mark.parametrize("fid", [1, 5, 11, 12])
@pytest.mark.parametrize("v", [0, 1, 89555436834913, 1780924117])
def test_pb_uint_matches_poc(fid, v):
    assert codec.pb_uint(fid, v) == poc._f_var(fid, v)


def test_pb_msg_matches_poc():
    sub = poc._f_str(1, "h5") + poc._f_var(2, 7)
    assert codec.pb_msg(10, sub) == poc._f_msg(10, sub)


@pytest.mark.parametrize("cmd", CMDS)
@pytest.mark.parametrize("body", BODIES)
@pytest.mark.parametrize("send_id", [1, 255, 256, 65535])
def test_gen_packet_matches_poc(cmd, body, send_id):
    assert codec.gen_packet(cmd, body, send_id) == poc.gen_packet(
        cmd, body, send_id
    )


def test_gen_packet_framing_layout():
    # [int32 totalLen][int16 sendID][int16 cmd][encBody]; len excludes itself.
    pkt = codec.gen_packet(257, b"abc", 7)
    (total,) = struct.unpack_from(">i", pkt, 0)
    assert total == len(pkt) - 4 == len(b"abc") + 4
    assert struct.unpack_from(">H", pkt, 4)[0] == 7      # sendID
    assert struct.unpack_from(">H", pkt, 6)[0] == 257    # cmd


def _recv_frame(cmd: int, body: bytes, flag: int = 0) -> bytes:
    """Build a server->client frame: [int32 len][int16 cmd][u8 flag][XOR body].

    Uses the PoC xor so the input itself is golden; both drains must recover it.
    """
    payload = zlib.compress(body) if flag == 1 else body
    enc = poc._xor(bytearray(payload), cmd)
    inner = struct.pack(">H", cmd) + bytes([flag]) + bytes(enc)
    return struct.pack(">i", len(inner)) + inner


def test_drain_packets_matches_poc_multi():
    buf_a = bytearray(_recv_frame(257, b"\x08\x00") + _recv_frame(16898, b""))
    buf_b = bytearray(bytes(buf_a))
    assert codec.drain_packets(buf_a) == poc.drain_packets(buf_b)
    assert len(buf_a) == 0  # fully consumed


def test_drain_packets_roundtrip_and_decompress():
    body = b"\x08\x00\x12\x05hello" * 40  # compressible
    buf = bytearray(_recv_frame(0x0C01, body, flag=1))
    out = codec.drain_packets(buf)
    assert out == [(0x0C01, body)]


def test_drain_packets_partial_keeps_leftover():
    frame = _recv_frame(257, b"\x08\x00")
    buf = bytearray(frame[:-2])  # last 2 bytes not arrived yet
    assert codec.drain_packets(buf) == []
    assert len(buf) == len(frame) - 2  # nothing consumed; waits for the rest


@pytest.mark.parametrize(
    "fields",
    [
        b"",
        poc._f_var(1, 0),
        poc._f_str(2, "abc") + poc._f_var(1, 300),
        poc._f_msg(10, poc._f_str(1, "h5")) + poc._f_var(11, 89555436834913),
    ],
)
def test_walk_matches_poc(fields):
    assert codec.walk(fields) == poc._walk(fields)


def test_walk_dict_picks_last_field_value():
    body = poc._f_var(1, 0) + poc._f_var(2, 89555436834913) + poc._f_var(4, 123)
    d = codec.walk_dict(body)
    assert d[1] == 0 and d[2] == 89555436834913 and d[4] == 123
