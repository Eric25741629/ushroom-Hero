"""Unit tests for utils.protobuf_walk — the shared protobuf wire walker."""
from __future__ import annotations

import pytest

from utils.protobuf_walk import (
    read_varint,
    walk_fields,
    walk_fields_lenient,
)


# ──────────────────────────────────────────────────────────────────────
# read_varint
# ──────────────────────────────────────────────────────────────────────


def test_read_varint_single_byte():
    val, off = read_varint(bytes([0x2a]), 0)
    assert val == 42
    assert off == 1


def test_read_varint_multi_byte():
    # 89616640123043 = a3 c1 80 80 98 b0 14 (from a live capture)
    data = bytes([0xa3, 0xc1, 0x80, 0x80, 0x98, 0xb0, 0x14])
    val, off = read_varint(data, 0)
    assert val == 89616640123043
    assert off == len(data)


def test_read_varint_respects_offset():
    # second varint starts at offset 1
    data = bytes([0x08, 0x2a])
    val, off = read_varint(data, 1)
    assert val == 42
    assert off == 2


def test_read_varint_truncated_raises():
    with pytest.raises(ValueError):
        # high bit set but no continuation byte
        read_varint(bytes([0x80]), 0)


def test_read_varint_overflow_raises():
    # 10 bytes all with continuation bit → shift reaches 63 → overflow
    with pytest.raises(ValueError):
        read_varint(bytes([0x80] * 10 + [0x01]), 0)


# ──────────────────────────────────────────────────────────────────────
# walk_fields (strict)
# ──────────────────────────────────────────────────────────────────────


def test_walk_fields_varint():
    out = walk_fields(bytes([0x08, 0x2a]))
    assert out == [(1, 0, 42)]


def test_walk_fields_len_delim():
    out = walk_fields(bytes([0x2a, 0x03, ord("a"), ord("b"), ord("c")]))
    assert out == [(5, 2, b"abc")]


def test_walk_fields_fixed64():
    body = bytes([0x09]) + (0x0102030405060708).to_bytes(8, "little")
    out = walk_fields(body)
    assert out == [(1, 1, 0x0102030405060708)]


def test_walk_fields_fixed32():
    body = bytes([0x0d]) + (0x01020304).to_bytes(4, "little")
    out = walk_fields(body)
    assert out == [(1, 5, 0x01020304)]


def test_walk_fields_multiple():
    # field 1 varint=42, field 5 len-delim "abc"
    body = bytes([0x08, 0x2a, 0x2a, 0x03, ord("a"), ord("b"), ord("c")])
    out = walk_fields(body)
    assert out == [(1, 0, 42), (5, 2, b"abc")]


def test_walk_fields_strict_raises_on_unknown_wire():
    # wire type 3 (group start, unsupported) on field 1 → 0x0b
    with pytest.raises(ValueError):
        walk_fields(bytes([0x0b]))


def test_walk_fields_empty():
    assert walk_fields(b"") == []


# ──────────────────────────────────────────────────────────────────────
# walk_fields_lenient (tolerant)
# ──────────────────────────────────────────────────────────────────────


def test_lenient_matches_strict_on_well_formed():
    body = bytes([0x08, 0x2a, 0x2a, 0x03, ord("a"), ord("b"), ord("c")])
    assert walk_fields_lenient(body) == walk_fields(body)


def test_lenient_stops_on_truncated_len_delim():
    # tag claims 10 bytes of len-delim but only 3 follow → stop, no entry
    out = walk_fields_lenient(bytes([0x2a, 0x0a, 0x61, 0x62, 0x63]))
    assert out == []


def test_lenient_stops_on_unknown_wire_returning_prior():
    # field 1 varint=42 parses, then wire 3 (unknown) terminates the walk
    out = walk_fields_lenient(bytes([0x08, 0x2a, 0x0b]))
    assert out == [(1, 0, 42)]


def test_lenient_stops_on_truncated_fixed64():
    out = walk_fields_lenient(bytes([0x09, 0x01, 0x02]))
    assert out == []


def test_lenient_stops_on_truncated_fixed32():
    out = walk_fields_lenient(bytes([0x0d, 0x01]))
    assert out == []


def test_lenient_empty():
    assert walk_fields_lenient(b"") == []


# ──────────────────────────────────────────────────────────────────────
# Cross-check: the live redbag entry decodes identically under both
# (it is well-formed, so strict and lenient must agree)
# ──────────────────────────────────────────────────────────────────────


_LIVE_ENTRY = bytes([
    0x08, 0xa3, 0xc1, 0x80, 0x80, 0x98, 0xb0, 0x14,
    0x10, 0xf8, 0xe0, 0x06,
    0x18, 0x02,
    0x20, 0xda, 0xa0, 0x80, 0x80, 0xd8, 0xae, 0x14,
    0x2a, 0x0f, 0xe5, 0x85, 0xa8, 0xe5, 0xae, 0xb6, 0xe7, 0xa6, 0xae,
    0xe6, 0x9c, 0x8d, 0xe5, 0xba, 0x97,
    0x30, 0xd7, 0xad, 0xb7, 0xd0, 0x06,
])


def test_live_entry_strict_and_lenient_agree():
    assert walk_fields(_LIVE_ENTRY) == walk_fields_lenient(_LIVE_ENTRY)


def test_live_entry_field_values():
    fields = dict((f, v) for f, _w, v in walk_fields(_LIVE_ENTRY))
    assert fields[1] == 89616640123043       # bag_id
    assert fields[2] == 110712               # cfg_id
    assert fields[3] == 2                     # state
    assert fields[4] == 89565100511322        # sender_id
    assert fields[5].decode("utf-8") == "全家禮服店"
    assert fields[6] == 1779291863           # timestamp
