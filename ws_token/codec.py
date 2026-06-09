"""Wire codec for the game WebSocket protocol: framing + XOR + protobuf.

Pure functions, no I/O. Behavior is byte-identical to the LIVE-verified PoC
``tools/_login_poc.py`` (enforced by tests/test_ws_token_codec.py), so the parts
that talk to the real server are proven without needing a live socket.

Protocol (docs/protocol/AUTH_HANDSHAKE_SPEC.md):
- §2 framing, big-endian. Send: [int32 len][int16 sendID][int16 cmd][body].
  Recv: [int32 len][int16 cmd][u8 flag][body]; flag==1 -> zlib-compressed body.
  ``len`` counts every byte after the length field itself.
- §3 encryption: XOR the body only (header is plaintext) with the static 256-byte
  KEY_256; keystream starts at ``cmd % 256`` and advances +1 (mod 256) per byte.
"""
from __future__ import annotations

import struct
import zlib

# Static 256-byte XOR key, lifted verbatim from the client (IOHandler `s`).
KEY_256: list[int] = [
    153, 234, 171, 122, 153, 37, 54, 178, 41, 143, 55, 117, 108, 2, 144, 122,
    103, 79, 15, 148, 253, 85, 47, 52, 9, 227, 214, 212, 84, 65, 207, 5,
    7, 13, 14, 252, 144, 156, 100, 171, 224, 228, 203, 149, 76, 184, 103, 203,
    19, 101, 153, 173, 165, 19, 69, 154, 1, 240, 209, 164, 106, 118, 6, 157,
    239, 63, 246, 239, 221, 68, 81, 194, 149, 53, 25, 35, 43, 61, 235, 197,
    86, 70, 116, 6, 150, 244, 237, 81, 252, 85, 153, 107, 4, 30, 147, 86,
    7, 220, 152, 169, 158, 183, 214, 193, 240, 242, 51, 14, 204, 137, 81, 139,
    102, 158, 158, 203, 141, 17, 97, 90, 221, 81, 226, 85, 146, 57, 198, 233,
    204, 36, 84, 131, 71, 224, 52, 233, 29, 174, 213, 163, 211, 25, 222, 189,
    45, 20, 134, 25, 36, 228, 86, 163, 170, 148, 140, 19, 47, 150, 12, 176,
    20, 144, 97, 115, 12, 124, 208, 59, 225, 102, 232, 64, 81, 190, 17, 98,
    254, 14, 108, 231, 105, 199, 12, 56, 148, 242, 123, 24, 26, 82, 193, 199,
    154, 87, 211, 92, 63, 147, 90, 224, 164, 243, 216, 137, 19, 118, 7, 31,
    106, 244, 41, 113, 160, 17, 117, 247, 126, 26, 200, 86, 45, 115, 199, 58,
    133, 235, 184, 217, 245, 247, 9, 198, 200, 34, 71, 174, 175, 125, 77, 129,
    35, 234, 7, 143, 112, 142, 138, 121, 100, 149, 203, 142, 137, 116, 243, 225,
]


def xor_body(buf: bytearray, cmd: int) -> bytearray:
    """XOR-encrypt/decrypt ``buf`` in place for ``cmd``; returns the same buffer.

    Symmetric: applying it twice with the same cmd restores the original bytes.
    """
    h = cmd % 256
    for i in range(len(buf)):
        buf[i] ^= KEY_256[h]
        h = (h + 1) % 256
    return buf


# --- protobuf wire encoders (just enough to build c2s bodies) ---------------

def pb_varint(v: int) -> bytes:
    """Base-128 varint encoding of a non-negative integer."""
    out = bytearray()
    while v > 0x7F:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v & 0x7F)
    return bytes(out)


def pb_str(fid: int, s: str) -> bytes:
    """Length-delimited (wire type 2) UTF-8 string field."""
    b = s.encode("utf-8")
    return pb_varint(fid << 3 | 2) + pb_varint(len(b)) + b


def pb_uint(fid: int, v: int) -> bytes:
    """Varint (wire type 0) integer field."""
    return pb_varint(fid << 3 | 0) + pb_varint(int(v))


def pb_msg(fid: int, sub: bytes) -> bytes:
    """Length-delimited (wire type 2) embedded-message field."""
    return pb_varint(fid << 3 | 2) + pb_varint(len(sub)) + sub


# --- framing ----------------------------------------------------------------

def gen_packet(cmd: int, body: bytes, send_id: int = 1) -> bytes:
    """Frame a client->server packet: [int32 len][int16 sendID][int16 cmd][XOR body]."""
    enc = xor_body(bytearray(body), cmd)
    out = bytearray()
    out += struct.pack(">i", len(enc) + 4)  # len = body + sendID(2) + cmd(2)
    out += struct.pack(">H", send_id)
    out += struct.pack(">H", cmd)
    out += enc
    return bytes(out)


def drain_packets(buf: bytearray) -> list[tuple[int, bytes]]:
    """Pull every complete server->client packet out of a running buffer.

    Consumes the parsed bytes from ``buf`` in place and leaves any trailing
    partial packet for the next read. Returns ``[(cmd, decoded_body), ...]``.
    """
    out: list[tuple[int, bytes]] = []
    off = 0
    while off + 4 <= len(buf):
        (length,) = struct.unpack_from(">i", buf, off)
        if length <= 0:
            off += 4
            continue
        if off + 4 + length > len(buf):
            break  # packet not fully arrived yet
        pkt = buf[off + 4:off + 4 + length]
        off += 4 + length
        cmd = struct.unpack_from(">H", pkt, 0)[0]
        flag = pkt[2]
        body = xor_body(bytearray(pkt[3:]), cmd)
        if flag == 1:
            try:
                body = bytearray(zlib.decompress(bytes(body)))
            except zlib.error:
                pass
        out.append((cmd, bytes(body)))
    del buf[:off]
    return out


# --- protobuf wire decoder --------------------------------------------------

def _read_varint(data: bytes, off: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, off
        shift += 7


def walk(data: bytes) -> list[tuple[int, object]]:
    """Decode a protobuf body into ``[(field_number, value), ...]``.

    Values: varint -> int, len-delimited -> bytes, fixed64 -> int, fixed32 -> int.
    Repeated fields appear as multiple entries with the same field number.
    """
    off = 0
    out: list[tuple[int, object]] = []
    while off < len(data):
        tag, off = _read_varint(data, off)
        fn, wt = tag >> 3, tag & 7
        if wt == 0:
            v, off = _read_varint(data, off)
            out.append((fn, v))
        elif wt == 2:
            ln, off = _read_varint(data, off)
            out.append((fn, data[off:off + ln]))
            off += ln
        elif wt == 1:
            out.append((fn, struct.unpack_from("<Q", data, off)[0]))
            off += 8
        elif wt == 5:
            out.append((fn, struct.unpack_from("<I", data, off)[0]))
            off += 4
        else:
            break
    return out


def walk_dict(data: bytes) -> dict[int, object]:
    """``walk`` collapsed to a dict (last value wins for repeated fields)."""
    return dict(walk(data))
