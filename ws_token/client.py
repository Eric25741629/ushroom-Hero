"""WSGameClient — a reusable, logged-in game WebSocket connection.

Packages the LIVE-verified one-shot flow from tools/_login_poc.py into a class:
connect (active byte + role_login) -> background reader + heartbeat -> send/recv
request-response by cmd id -> reconnect / close. This is the shared foundation
the ws_token task layers (carpark, mining) sit on; see
docs/WS_TOKEN_BACKEND_PLAN.md Step 1.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from ws_token import codec
from ws_token.creds import Creds
from ws_token.transport import Transport, WebsocketTransport

logger = logging.getLogger(__name__)

CMD_ROLE_LOGIN = 257       # login.role_login_c2s / _s2c (same id both directions)
CMD_HEARTBEAT = 260        # login.heart_beat_c2s {svr_time uint32 id=1}
ACTIVE_NEW = b"\x00"       # SocketClient active message: fresh connect
ACTIVE_RECONNECT = b"\x01"

_MAX_SEND_ID = 65535
_DEFAULT_HEARTBEAT_S = 5.0
_DEFAULT_LOGIN_TIMEOUT_S = 20.0
_DEFAULT_CALL_TIMEOUT_S = 15.0
_JOIN_TIMEOUT_S = 2.0

TransportFactory = Callable[[str], Transport]
PushHandler = Callable[[int, bytes], None]


class WSError(Exception):
    """Base error for the ws_token client."""


class WSLoginError(WSError):
    """role_login did not return code==0 (or never arrived)."""


class WSTimeoutError(WSError):
    """A request did not get its matching response in time."""


def build_role_login(creds: Creds, time_val: int) -> bytes:
    """Build the role_login_c2s (cmd 257) body. Byte-identical to the PoC.

    ``time_val`` is the protobuf `time` field; the server does not validate it
    (AUTH_HANDSHAKE_SPEC §7), so any value works with a still-valid ticket.
    """
    mi = (
        codec.pb_str(1, "h5")
        + codec.pb_str(2, creds.device_name or "")
        + codec.pb_str(3, creds.device_id or "")
        + codec.pb_str(4, "9.0.2.12596")
        + codec.pb_str(5, "0")
        + codec.pb_str(6, "0")
        + codec.pb_str(7, "540 X 960")
        + codec.pb_str(8, "wifi")
        + codec.pb_str(10, creds.ip or "")
    )
    return (
        codec.pb_str(1, creds.uid)
        + codec.pb_str(2, creds.uname)
        + codec.pb_str(3, creds.plat)
        + codec.pb_str(4, creds.login_game_id)
        + codec.pb_uint(5, creds.is_white_ip)
        + codec.pb_uint(6, time_val)
        + codec.pb_str(7, creds.p_key)
        + codec.pb_str(8, creds.login_ticket)
        + codec.pb_uint(9, creds.login_scene_id)
        + codec.pb_msg(10, mi)
        + codec.pb_uint(11, creds.role_id)
        + codec.pb_uint(12, 1)
    )


def build_heartbeat(svr_time: int) -> bytes:
    """Build the heart_beat_c2s (cmd 260) body: field 1 = svr_time (uint32)."""
    return codec.pb_uint(1, svr_time)


class _Waiter:
    """A pending response slot, fulfilled by the first of several reply cmds.

    Registered under each expected cmd; whichever arrives first delivers
    ``(cmd, body)`` and the waiter is unregistered from all its cmds.
    """

    __slots__ = ("queue", "cmds", "done")

    def __init__(self, cmds) -> None:
        self.queue: queue.Queue = queue.Queue(maxsize=1)
        self.cmds: tuple[int, ...] = tuple(cmds)
        self.done: bool = False


class WSGameClient:
    """A logged-in connection to the game gateway.

    Thread model: one daemon reader thread drains frames and routes each to the
    waiting :meth:`call`, plus one daemon heartbeat thread (optional). Sends are
    serialized under a lock and carry an auto-incrementing sendID. Responses are
    correlated by cmd id (the recv framing carries no sendID).
    """

    def __init__(
        self,
        creds: Creds,
        *,
        transport_factory: TransportFactory = WebsocketTransport,
        heartbeat_enabled: bool = True,
        heartbeat_interval: float = _DEFAULT_HEARTBEAT_S,
        login_timeout: float = _DEFAULT_LOGIN_TIMEOUT_S,
        call_timeout: float = _DEFAULT_CALL_TIMEOUT_S,
        time_val: Optional[int] = None,
        push_handler: Optional[PushHandler] = None,
    ) -> None:
        self._creds = creds
        self._factory = transport_factory
        self._heartbeat_enabled = heartbeat_enabled
        self._heartbeat_interval = heartbeat_interval
        self._login_timeout = login_timeout
        self._call_timeout = call_timeout
        self._time_val = time_val
        self._push_handler = push_handler

        self._transport: Optional[Transport] = None
        self._reader: Optional[threading.Thread] = None
        self._heartbeat: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._waiters_lock = threading.Lock()
        self._waiters: dict[int, list[_Waiter]] = {}
        self._send_id = 1
        self._serv_time = 0
        self._connected = False

    # --- lifecycle ---------------------------------------------------------

    def connect(self) -> dict:
        """Open the socket, log in, and start the background threads.

        Returns the parsed login fields ``{code, role_id, serv_time}``.
        Raises :class:`WSLoginError` if login fails or never replies.
        """
        self._stop.clear()
        self._transport = self._factory(self._creds.ws_url)
        self._start_reader()

        waiter = self._register_waiter((CMD_ROLE_LOGIN,))
        time_val = self._time_val
        if time_val is None:
            time_val = self._creds.login_time or int(time.time())
        self._transport.send(ACTIVE_NEW)
        self._send_framed(CMD_ROLE_LOGIN, build_role_login(self._creds, time_val))

        try:
            _cmd, body = waiter.queue.get(timeout=self._login_timeout)
        except queue.Empty:
            self.close()
            raise WSLoginError("timed out waiting for role_login_s2c")

        fields = codec.walk_dict(body)
        code = fields.get(1)
        if code != 0:
            self.close()
            raise WSLoginError(f"role_login failed: code={code}")

        self._serv_time = int(fields.get(4) or self._creds.login_time or 0)
        self._connected = True
        if self._heartbeat_enabled:
            self._start_heartbeat()
        logger.info("ws_token login ok role_id=%s serv_time=%s",
                    fields.get(2), fields.get(4))
        return {"code": code, "role_id": fields.get(2), "serv_time": fields.get(4)}

    def reconnect(self) -> dict:
        """Tear down and log in again, reusing the same (reusable) ticket."""
        self.close()
        return self.connect()

    def close(self) -> None:
        """Stop threads and close the transport. Safe to call repeatedly."""
        self._stop.set()
        self._connected = False
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
        current = threading.current_thread()
        for th in (self._reader, self._heartbeat):
            if th is not None and th.is_alive() and th is not current:
                th.join(timeout=_JOIN_TIMEOUT_S)

    def is_running(self) -> bool:
        return self._connected and not self._stop.is_set()

    def set_push_handler(self, handler: Optional[PushHandler]) -> None:
        """Install (or clear with None) the callback for unmatched server frames.

        Server-push cmds (e.g. equip_change 0x0504, item-delta 0x0402) that no
        ``call``/``call_for`` is waiting on are delivered here as (cmd, body).
        """
        self._push_handler = handler

    def __enter__(self) -> "WSGameClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- request / response ------------------------------------------------

    def call(
        self,
        cmd: int,
        body: bytes = b"",
        *,
        expect_cmd: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> bytes:
        """Send ``cmd``/``body`` and return the matching response body.

        Responses are matched by cmd id; ``expect_cmd`` overrides the reply cmd
        for asymmetric pairs. Raises :class:`WSTimeoutError` on no response.
        """
        expect = cmd if expect_cmd is None else expect_cmd
        _reply_cmd, reply = self.call_for(cmd, body, expect_cmds=(expect,),
                                          timeout=timeout)
        return reply

    def call_for(
        self,
        cmd: int,
        body: bytes = b"",
        *,
        expect_cmds,
        timeout: Optional[float] = None,
    ) -> tuple[int, bytes]:
        """Send ``cmd``/``body`` and return the first reply among ``expect_cmds``.

        Use when a request can reply on one of several cmds (e.g. red-packet grab
        -> success 0x2603 OR error 0x0201). Returns ``(reply_cmd, body)``.
        Raises :class:`WSTimeoutError` if none arrive in time.
        """
        waiter = self._register_waiter(tuple(expect_cmds))
        self._send_framed(cmd, body)
        try:
            return waiter.queue.get(
                timeout=timeout if timeout is not None else self._call_timeout)
        except queue.Empty:
            self._drop_waiter(waiter)
            raise WSTimeoutError(
                f"no response for cmd={cmd} (expected one of {tuple(expect_cmds)})"
            )

    # --- internals ---------------------------------------------------------

    def _next_send_id(self) -> int:
        sid = self._send_id
        self._send_id = sid + 1 if sid < _MAX_SEND_ID else 1
        return sid

    def _send_framed(self, cmd: int, body: bytes) -> None:
        with self._send_lock:
            sid = self._next_send_id()
            assert self._transport is not None
            self._transport.send(codec.gen_packet(cmd, body, sid))

    def _register_waiter(self, cmds) -> _Waiter:
        w = _Waiter(cmds)
        with self._waiters_lock:
            for c in w.cmds:
                self._waiters.setdefault(c, []).append(w)
        return w

    def _drop_waiter(self, w: _Waiter) -> None:
        with self._waiters_lock:
            for c in w.cmds:
                lst = self._waiters.get(c)
                if lst and w in lst:
                    lst.remove(w)

    def _route(self, cmd: int, body: bytes) -> None:
        if cmd == CMD_HEARTBEAT:
            st = codec.walk_dict(body).get(1)
            if st:
                self._serv_time = int(st)
            return
        waiter = None
        with self._waiters_lock:
            lst = self._waiters.get(cmd)
            while lst:
                cand = lst.pop(0)
                if not cand.done:
                    waiter = cand
                    break
            if waiter is not None:
                waiter.done = True
                for c in waiter.cmds:  # unregister from its other reply cmds
                    if c == cmd:
                        continue
                    other = self._waiters.get(c)
                    if other and waiter in other:
                        other.remove(waiter)
        if waiter is not None:
            waiter.queue.put((cmd, body))
            return
        if self._push_handler is not None:
            try:
                self._push_handler(cmd, body)
            except Exception:
                logger.exception("ws_token push_handler failed for cmd=%s", cmd)
        else:
            logger.debug("ws_token unhandled push cmd=%s len=%d", cmd, len(body))

    def _start_reader(self) -> None:
        self._reader = threading.Thread(
            target=self._reader_loop, name="ws_token-reader", daemon=True
        )
        self._reader.start()

    def _reader_loop(self) -> None:
        buf = bytearray()
        while not self._stop.is_set():
            try:
                data = self._transport.recv()  # type: ignore[union-attr]
            except Exception:
                break
            if not data:
                break
            buf += data
            for cmd, body in codec.drain_packets(buf):
                self._route(cmd, body)

    def _start_heartbeat(self) -> None:
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="ws_token-heartbeat", daemon=True
        )
        self._heartbeat.start()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_interval):
            try:
                self._send_framed(CMD_HEARTBEAT, build_heartbeat(self._serv_time))
            except Exception:
                break
