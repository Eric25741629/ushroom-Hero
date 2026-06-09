"""Shared scripted fakes for ws_token client/task tests (no real socket).

A ``FakeTransport`` is driven by a ``responder(cmd, body) -> list[frames]`` that
replies to framed packets the client sends, so login sequencing, heartbeat, and
request/response correlation are all deterministic.
"""
from __future__ import annotations

import queue
import struct

from ws_token import codec
from ws_token.creds import Creds

CMD_LOGIN = 257
CMD_HEARTBEAT = 260
CMD_PETS = 16898

# Mirrors the real auth_state/_auth_capture_<dev>.json creds shape.
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
    "_ws_url": "wss://gw.example?token=abc",
}
CREDS = Creds.from_dict(SAMPLE)


def s2c(cmd: int, body: bytes, flag: int = 0) -> bytes:
    """Build a server->client frame the client's reader can decode."""
    enc = codec.xor_body(bytearray(body), cmd)
    inner = struct.pack(">H", cmd) + bytes([flag]) + bytes(enc)
    return struct.pack(">i", len(inner)) + inner


def login_ok(code: int = 0, role_id: int = 89555436834913,
             serv_time: int = 1780924200) -> bytes:
    return s2c(CMD_LOGIN, codec.pb_uint(1, code) + codec.pb_uint(2, role_id)
               + codec.pb_uint(4, serv_time))


class FakeTransport:
    """Scripted transport. ``responder(cmd, body) -> list[frames]`` replies."""

    def __init__(self, responder):
        self.url = None
        self.sent: list[bytes] = []
        self.closed = False
        self._inbox: queue.Queue = queue.Queue()
        self._responder = responder

    def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        if len(data) < 8:  # active byte / non-framed control
            return
        cmd = struct.unpack_from(">H", data, 6)[0]
        body = bytes(codec.xor_body(bytearray(data[8:]), cmd))
        for frame in self._responder(cmd, body):
            self._inbox.put(frame)

    def recv(self) -> bytes:
        item = self._inbox.get()
        if item is None:
            raise ConnectionError("transport closed")
        return item

    def close(self) -> None:
        self.closed = True
        self._inbox.put(None)

    # test helpers ----------------------------------------------------------
    def framed_sent(self) -> list[tuple[int, int, bytes]]:
        """[(send_id, cmd, decoded_body)] for every framed packet sent."""
        out = []
        for p in self.sent:
            if len(p) < 8:
                continue
            sid = struct.unpack_from(">H", p, 4)[0]
            cmd = struct.unpack_from(">H", p, 6)[0]
            body = bytes(codec.xor_body(bytearray(p[8:]), cmd))
            out.append((sid, cmd, body))
        return out

    def sent_cmds(self) -> list[int]:
        return [cmd for _sid, cmd, _b in self.framed_sent()]


def factory_for(fake: FakeTransport):
    def _factory(url: str):
        fake.url = url
        return fake
    return _factory


def login_responder(extra=None):
    """A responder that answers role_login with code=0; ``extra`` maps other
    cmd -> callable(body) -> list[frames]."""
    extra = extra or {}

    def _r(cmd: int, body: bytes):
        if cmd == CMD_LOGIN:
            return [login_ok()]
        if cmd in extra:
            return extra[cmd](body)
        return []
    return _r
