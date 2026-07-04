"""Minimal protobuf wire-format walker — single source of truth.

A few modules need to peek at raw protobuf bodies coming off the game's
WebSocket without a generated schema (`web_game_api`, `equipment_cache`,
`redpack_detector`). They each used to hand-roll the same varint reader +
field-walking loop. This module is that shared core.

Two walk variants exist because the existing callers diverged on how they
treat malformed input:

- :func:`walk_fields` is *strict*: an unknown wire type raises ``ValueError``.
  This mirrors the original ``utils.web_game_api._walk_pb`` (which trusts the
  game's well-formed RPC responses and wants a loud failure otherwise).
- :func:`walk_fields_lenient` is *tolerant*: it stops cleanly on unknown wire
  types or truncated length-delimited fields, returning whatever it parsed so
  far. This mirrors the original ``utils.redpack_detector._walk_pb`` (which
  parses best-effort frames captured off a ring buffer).

Both return the same tuple shape ``(field_number, wire_type, value)`` where
``value`` is ``int`` for wire 0/1/5 and ``bytes`` for wire 2.

Wire types: 0=varint, 1=fixed64, 2=len-delimited, 5=fixed32.
"""
from __future__ import annotations

import struct
from typing import Any, List, Tuple

__all__ = [
    "read_varint",
    "walk_fields",
    "walk_fields_lenient",
]

PbField = Tuple[int, int, Any]


def read_varint(buf: bytes, off: int) -> Tuple[int, int]:
    """Read a base-128 varint at ``off``; return ``(value, new_off)``.

    Raises ``ValueError`` on truncation (buffer ends mid-varint) or overflow
    (more than 64 bits of payload).
    """
    val = 0
    shift = 0
    while off < len(buf):
        b = buf[off]
        off += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, off
        shift += 7
        if shift >= 64:
            raise ValueError("varint too long")
    raise ValueError("varint truncated")


def walk_fields(data: bytes) -> List[PbField]:
    """Strict walk: yield ``(field, wire, value)`` tuples.

    An unsupported wire type raises ``ValueError``. Length-delimited fields are
    sliced directly (a length running past the buffer yields a short ``bytes``,
    matching Python slice semantics).
    """
    out: List[PbField] = []
    off = 0
    n = len(data)
    while off < n:
        tag, off = read_varint(data, off)
        field = tag >> 3
        wire = tag & 7
        if wire == 0:
            v, off = read_varint(data, off)
            out.append((field, 0, v))
        elif wire == 1:
            v = struct.unpack_from("<Q", data, off)[0]
            off += 8
            out.append((field, 1, v))
        elif wire == 2:
            length, off = read_varint(data, off)
            out.append((field, 2, data[off:off + length]))
            off += length
        elif wire == 5:
            v = struct.unpack_from("<I", data, off)[0]
            off += 4
            out.append((field, 5, v))
        else:
            raise ValueError(f"unsupported wire type {wire} at offset {off}")
    return out


def walk_fields_lenient(data: bytes) -> List[PbField]:
    """Tolerant walk: yield ``(field, wire, value)`` tuples, stop on trouble.

    Returns whatever was parsed before the first problem instead of raising:
    a truncated tag/varint, a fixed field running past the end, a
    length-delimited field whose payload would overrun the buffer, or an
    unknown wire type all terminate the walk cleanly.
    """
    out: List[PbField] = []
    off = 0
    n = len(data)
    while off < n:
        try:
            tag, off = read_varint(data, off)
        except ValueError:
            break
        field = tag >> 3
        wire = tag & 7
        try:
            if wire == 0:
                val, off = read_varint(data, off)
                out.append((field, 0, val))
            elif wire == 1:
                if off + 8 > n:
                    break
                out.append((field, 1, int.from_bytes(data[off:off + 8], "little")))
                off += 8
            elif wire == 2:
                length, off = read_varint(data, off)
                if off + length > n:
                    break
                out.append((field, 2, data[off:off + length]))
                off += length
            elif wire == 5:
                if off + 4 > n:
                    break
                out.append((field, 5, int.from_bytes(data[off:off + 4], "little")))
                off += 4
            else:
                break
        except ValueError:
            break
    return out
