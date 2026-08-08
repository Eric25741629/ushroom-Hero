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
from enum import Enum
from typing import Callable, Optional

from ws_token import codec
from ws_token.creds import Creds
from ws_token.transport import Transport, WebsocketTransport

logger = logging.getLogger(__name__)

CMD_ROLE_LOGIN = 257       # login.role_login_c2s / _s2c (same id both directions)
CMD_HEARTBEAT = 260        # login.heart_beat_c2s {svr_time uint32 id=1}
CMD_KICKED = 259           # login.kick_s2c (0x103): server push when this account
                           # is logged in elsewhere (異地登入). Body is {1: reason};
                           # reason 20 = 異地登入. The server closes the socket
                           # right after, so we treat this frame as "kicked".
# ``is_kicked`` 同時涵蓋 server 明確踢人與 socket 非預期斷線；上層必須用
# reason 分流，避免一般網路抖動被誤判成異地登入。
# Stable reason names shared by the runner and hybrid runtime.  The detailed
# close metadata below keeps the raw cmd-259 payload and transport exception.
KICK_REASON_EXPLICIT = "explicit_login_conflict"
KICK_REASON_TRANSPORT_DROP = "transport_drop"
ACTIVE_NEW = b"\x00"       # SocketClient active message: fresh connect
ACTIVE_RECONNECT = b"\x01"


def normalize_kick_reason(reason: object) -> Optional[str]:
    """將新舊 client/report 的 kick reason 統一成穩定字串。

    舊 adapter 可能回傳 callable、Enum 或 cmd=259 的原始 reason code；
    這裡只做診斷欄位正規化，不把一般斷線誤提升成異地登入。
    """
    if callable(reason):
        try:
            reason = reason()
        except Exception:  # noqa: BLE001 — 診斷欄位失敗只能視為未知
            return None
    value = getattr(reason, "value", reason)
    if value in (None, ""):
        return None
    if value == 20:
        return KICK_REASON_EXPLICIT
    return str(value)

_MAX_SEND_ID = 65535
_DEFAULT_HEARTBEAT_S = 5.0
_DEFAULT_LOGIN_TIMEOUT_S = 20.0
_DEFAULT_CALL_TIMEOUT_S = 15.0
_JOIN_TIMEOUT_S = 2.0

TransportFactory = Callable[[str], Transport]
PushHandler = Callable[[int, bytes], None]
KickHandler = Callable[[], None]


class WSCloseReason(str, Enum):
    """WS 連線結束的可判斷原因。"""

    EXPLICIT_LOGIN_CONFLICT = "explicit_login_conflict"
    TRANSPORT_DROP = "transport_drop"
    INTENTIONAL_CLOSE = "intentional_close"
    SESSION_HANDOFF = "session_handoff"


def is_explicit_login_conflict(reason: object) -> bool:
    """判斷 reason 是否確實來自 server 的 cmd 259 異地登入 push。"""
    value = reason.value if isinstance(reason, WSCloseReason) else reason
    return value == WSCloseReason.EXPLICIT_LOGIN_CONFLICT.value


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
        on_kick: Optional[KickHandler] = None,
    ) -> None:
        self._creds = creds
        self._factory = transport_factory
        self._heartbeat_enabled = heartbeat_enabled
        self._heartbeat_interval = heartbeat_interval
        self._login_timeout = login_timeout
        self._call_timeout = call_timeout
        self._time_val = time_val
        self._push_handler = push_handler
        self._on_kick = on_kick

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
        self._kicked = False
        self._close_reason: Optional[WSCloseReason] = None
        self._close_detail: Optional[str] = None
        self._kick_reason: Optional[int] = None
        self._connection_started_at: Optional[float] = None
        self._closed_at: Optional[float] = None

    # --- lifecycle ---------------------------------------------------------

    def connect(self) -> dict:
        """Open the socket, log in, and start the background threads.

        Returns the parsed login fields ``{code, role_id, serv_time}``.
        Raises :class:`WSLoginError` if login fails or never replies.
        """
        self._stop.clear()
        self._kicked = False
        self._close_reason = None
        self._close_detail = None
        self._kick_reason = None
        self._connection_started_at = time.time()
        self._closed_at = None
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
        # role_login_s2c {code#1, role_id#2, server_id#3, serv_time#4, ...}
        # (AUTH_HANDSHAKE_SPEC §4) — server_id == 本服 id (e.g. 小寶 1467),
        # consumed by carpark 同服抱團 ranking.
        return {"code": code, "role_id": fields.get(2),
                "server_id": fields.get(3), "serv_time": fields.get(4)}

    def reconnect(self) -> dict:
        """Tear down and log in again, reusing the same (reusable) ticket."""
        self.close()
        return self.connect()

    def close(
        self,
        reason: WSCloseReason | str = WSCloseReason.INTENTIONAL_CLOSE,
    ) -> None:
        """Stop threads and close the transport. Safe to call repeatedly."""
        self._record_close(reason)
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
        """True while logged-in, not stopped, and not kicked.

        Kicking (異地登入 cmd 259, or a non-stop reader exit) flips the session
        dead even though ``_connected`` is still set, so callers polling this no
        longer keep using a connection the server has already torn down.
        """
        return self._connected and not self._stop.is_set() and not self._kicked

    def is_kicked(self) -> bool:
        """True iff this connection was kicked or dropped unexpectedly.

        Set when a kick push (cmd 259) arrives, or when the reader thread exits
        on a closed/erroring socket WITHOUT a deliberate ``close()`` — i.e. the
        server hung up on us. A clean ``close()`` does NOT set this.
        """
        return self._kicked

    @property
    def close_reason(self) -> Optional[str]:
        """Return the first classified close reason as a stable string value."""
        return self._close_reason.value if self._close_reason is not None else None

    @property
    def close_detail(self) -> Optional[str]:
        """Return protocol/transport detail captured with ``close_reason``."""
        return self._close_detail

    @property
    def kick_reason(self) -> Optional[int]:
        """Return the raw cmd-259 reason code, when one was received."""
        return self._kick_reason

    @property
    def connection_started_at(self) -> Optional[float]:
        """Wall-clock timestamp for the current connection attempt."""
        return self._connection_started_at

    @property
    def closed_at(self) -> Optional[float]:
        """Wall-clock timestamp at which the first close reason was recorded."""
        return self._closed_at

    def get_kick_reason(self) -> Optional[str]:
        """Return the stable reason for :meth:`is_kicked`, if any.

        ``explicit_cmd_259`` means the server sent the login conflict push;
        ``transport_drop`` means the reader ended without that push. Keeping
        this separate from the legacy boolean lets callers choose recovery
        without treating every broken socket as an account conflict.
        """
        if self.close_reason == KICK_REASON_EXPLICIT:
            return KICK_REASON_EXPLICIT
        if self.close_reason == KICK_REASON_TRANSPORT_DROP:
            return KICK_REASON_TRANSPORT_DROP
        return None

    def set_push_handler(self, handler: Optional[PushHandler]) -> None:
        """Install (or clear with None) the callback for unmatched server frames.

        Server-push cmds (e.g. equip_change 0x0504, item-delta 0x0402) that no
        ``call``/``call_for`` is waiting on are delivered here as (cmd, body).
        """
        self._push_handler = handler

    def set_kick_handler(self, handler: Optional[KickHandler]) -> None:
        """Install (or clear with None) the callback fired when we are kicked.

        Invoked once from the reader thread when a kick (cmd 259) is detected.
        It takes no arguments; use :meth:`is_kicked` to read the flag afterwards.
        """
        self._on_kick = handler

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

    def send(self, cmd: int, body: bytes = b"") -> None:
        """Send a framed packet without waiting for a response.

        Some live mutations are confirmed by a later read/push instead of a
        same-cmd reply. Callers using this method must do their own confirmation.
        """
        self._send_framed(cmd, body)

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

    def _record_close(
        self,
        reason: WSCloseReason | str,
        detail: Optional[str] = None,
    ) -> None:
        """Keep the first close cause; later socket cleanup must not hide it."""
        if not isinstance(reason, WSCloseReason):
            try:
                reason = WSCloseReason(str(reason))
            except ValueError:
                reason = WSCloseReason.INTENTIONAL_CLOSE
        if (self._close_reason is not None
                and reason != WSCloseReason.EXPLICIT_LOGIN_CONFLICT):
            return
        self._close_reason = reason
        self._close_detail = detail
        self._closed_at = time.time()
        logger.info("ws_token close reason=%s detail=%s", reason.value, detail or "")

    def _mark_kicked(
        self,
        *,
        fire_callback: bool,
        reason: WSCloseReason,
        detail: Optional[str] = None,
        kick_reason: Optional[int] = None,
    ) -> None:
        """Flip the kicked flag, retain its reason, and optionally fire ``on_kick``.

        ``fire_callback`` is True only for the explicit kick push (cmd 259) — a
        bare socket drop sets the flag silently (no callback) so a deliberate
        reconnect isn't reported as a kick to the dashboard.
        """
        already = self._kicked
        self._kicked = True
        if kick_reason is not None:
            self._kick_reason = kick_reason
        self._record_close(reason, detail)
        if fire_callback and not already and self._on_kick is not None:
            try:
                self._on_kick()
            except Exception:
                logger.exception("ws_token on_kick handler failed")

    def _route(self, cmd: int, body: bytes) -> None:
        if cmd == CMD_KICKED:
            raw_reason = codec.walk_dict(body).get(1)
            logger.warning(
                "ws_token 異地登入被踢 (cmd=259, reason=%s) — 連線即將被伺服器關閉",
                raw_reason,
            )
            try:
                kick_reason = int(raw_reason)
            except (TypeError, ValueError):
                kick_reason = None
            self._mark_kicked(
                fire_callback=True,
                reason=WSCloseReason.EXPLICIT_LOGIN_CONFLICT,
                detail=f"cmd=259 reason={raw_reason}",
                kick_reason=kick_reason,
            )
            return
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
        recv_error: Optional[Exception] = None
        while not self._stop.is_set():
            try:
                data = self._transport.recv()  # type: ignore[union-attr]
                if not data:
                    break
                buf += data
                for cmd, body in codec.drain_packets(buf):
                    self._route(cmd, body)
            except Exception as exc:
                recv_error = exc
                break
        # Reader exited. If we did NOT ask it to stop, the socket was closed by
        # the server (or errored) — treat that as a kick/interruption too. A
        # deliberate close() sets _stop first, so this stays quiet for shutdown.
        if not self._stop.is_set():
            if recv_error is None:
                detail = "recv returned EOF"
            else:
                detail = (f"recv error {type(recv_error).__name__}: "
                          f"{recv_error}")
            self._mark_kicked(
                fire_callback=False,
                reason=WSCloseReason.TRANSPORT_DROP,
                detail=detail,
            )

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
