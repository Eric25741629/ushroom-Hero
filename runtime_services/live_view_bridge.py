"""Remote live-view bridge: stream a headless Chrome's screen to a browser client
over CDP and forward the client's pointer/keyboard input back.

Used by the control panel's ``/ws/live_view/<ip>`` WebSocket route so a user can
see and manually operate a web_h5 device's game on a headless server (no display
required). The bot's Playwright object is thread-affine and cannot be touched from
the Flask thread, so we attach to Chrome over the already-exposed
``--remote-debugging-port`` (config ``web_debug_port``) as an independent raw-CDP
client. This neither requires nor disturbs Playwright's own pipe connection.

Data flow (one instance per viewer / WebSocket connection)::

    client <-- frame (base64 jpeg + meta) --  reader thread  <-- CDP screencastFrame
    client --- pointer/key event JSON ------> main loop      --> CDP Input.dispatch*

CDP is Chromium-specific. ``Page.startScreencast`` works in headless mode and uses
per-frame ``screencastFrameAck`` backpressure (we ack as soon as we forward).
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

try:  # websocket-client (sync). Imported lazily-safe so module import never hard-fails.
    import websocket  # type: ignore
except Exception:  # pragma: no cover - import guard
    websocket = None  # type: ignore


# ---------------------------------------------------------------------------
# CDP target discovery
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # nosec - localhost only
        return json.loads(resp.read().decode("utf-8", "replace"))


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def find_game_page_target(
    debug_port: int,
    url_host: str,
    *,
    timeout_sec: float = 12.0,
    poll_interval: float = 0.5,
) -> Optional[str]:
    """Return the ``webSocketDebuggerUrl`` of the game page target.

    Picks a ``type == "page"`` target whose URL host matches ``url_host``. Falls
    back to the first page target if none match (e.g. the game is mid-redirect).
    Retries until ``timeout_sec`` because the CDP endpoint may not be up yet right
    after the device thread (re)launches the browser.
    """
    list_url = f"http://127.0.0.1:{int(debug_port)}/json/list"
    target_host = (url_host or "").lower()
    deadline = time.time() + timeout_sec
    while True:
        pages: List[Dict[str, Any]] = []
        try:
            entries = _http_get_json(list_url)
            if isinstance(entries, list):
                pages = [e for e in entries if isinstance(e, dict) and e.get("type") == "page"]
        except Exception:
            pages = []

        if pages:
            matched = None
            if target_host:
                for e in pages:
                    if target_host in _host_of(str(e.get("url", ""))):
                        matched = e
                        break
            chosen = matched or pages[0]
            ws_url = chosen.get("webSocketDebuggerUrl")
            if ws_url:
                return str(ws_url)

        if time.time() >= deadline:
            return None
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Live-view session
# ---------------------------------------------------------------------------

class LiveViewSession:
    """Bridges one browser WebSocket (``client_ws``) to one Chrome CDP socket.

    ``client_ws`` is a flask-sock / simple-websocket ``Server`` object exposing
    ``send(str)`` / ``receive(timeout)`` / ``close()``.
    """

    def __init__(
        self,
        client_ws: Any,
        debug_port: int,
        url_host: str = "",
        *,
        viewport_width: int = 540,
        viewport_height: int = 960,
        jpeg_quality: int = 60,
        idle_timeout_sec: float = 3600.0,
        logger: Any = None,
    ) -> None:
        self.client_ws = client_ws
        self.debug_port = int(debug_port)
        self.url_host = url_host or ""
        self.viewport_width = int(viewport_width)
        self.viewport_height = int(viewport_height)
        self.jpeg_quality = int(jpeg_quality)
        # Idle auto-disconnect: if no client INPUT (mouse/key) arrives within this
        # many seconds, the session disconnects itself so the device does not stay
        # paused indefinitely. <= 0 disables. Stamped in _dispatch_input, enforced
        # by the 1 Hz tick in _read_client_loop.
        self.idle_timeout_sec = float(idle_timeout_sec)
        self._last_input_ts = time.monotonic()
        self.logger = logger

        self._cdp: Any = None
        self._cdp_send_lock = threading.Lock()
        # Serializes ALL writes to the client WS. The writer thread sends binary
        # frames while _read_client_loop/run may send control JSON (ready/error/
        # closed) concurrently; simple_websocket.send is not internally locked, so
        # without this two threads can interleave bytes and corrupt WS framing.
        self._client_send_lock = threading.Lock()
        self._msg_id = 0
        self._stop = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._writer: Optional[threading.Thread] = None
        # Single-slot outbox: latest unsent JPEG frame (raw bytes), dropped if a
        # newer one arrives before the writer drains it. Eliminates the
        # backpressure chain where a slow client WS receive blocks the reader
        # from acking Chrome. Frames go as binary WS messages (no base64, no
        # JSON wrap); control messages (ready/error) still use _client_send_json.
        self._outbox_lock = threading.Lock()
        self._outbox_cond = threading.Condition(self._outbox_lock)
        self._outbox_frame: Optional[bytes] = None
        # Last screencast metadata, used to map normalized client coords -> CSS px.
        self._dev_w = float(self.viewport_width)
        self._dev_h = float(self.viewport_height)

    # -- logging helpers ----------------------------------------------------
    def _log(self, level: str, msg: str) -> None:
        if self.logger is not None:
            getattr(self.logger, level, self.logger.info)(msg)

    # -- CDP plumbing -------------------------------------------------------
    def _cdp_send(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        with self._cdp_send_lock:
            self._msg_id += 1
            payload = {"id": self._msg_id, "method": method, "params": params or {}}
            self._cdp.send(json.dumps(payload))

    def _client_send_json(self, obj: Dict[str, Any]) -> bool:
        try:
            with self._client_send_lock:
                self.client_ws.send(json.dumps(obj))
            return True
        except Exception:
            self._stop.set()
            return False

    # -- public entry -------------------------------------------------------
    def run(self) -> None:
        if websocket is None:
            self._client_send_json({"type": "error", "message": "websocket-client not installed"})
            return

        ws_url = find_game_page_target(self.debug_port, self.url_host)
        if not ws_url:
            self._log("warning", f"[live_view] no CDP page target on port {self.debug_port}")
            self._client_send_json(
                {"type": "error", "message": f"no browser page on debug port {self.debug_port}"}
            )
            return

        try:
            # suppress_origin omits the Origin header. Without it, modern Chrome
            # (111+) rejects the DevTools WS upgrade with HTTP 403 unless the
            # browser was launched with --remote-allow-origins. Omitting Origin is
            # accepted and works against an already-running browser (no relaunch).
            self._cdp = websocket.create_connection(ws_url, timeout=5, suppress_origin=True)
            self._cdp.settimeout(1.0)
        except Exception as exc:
            self._log("warning", f"[live_view] CDP connect failed: {exc}")
            self._client_send_json({"type": "error", "message": f"CDP connect failed: {exc}"})
            return

        try:
            self._cdp_send("Page.enable")
            self._cdp_send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": self.jpeg_quality,
                    "maxWidth": self.viewport_width,
                    "maxHeight": self.viewport_height,
                    "everyNthFrame": 1,
                },
            )
            self._client_send_json({"type": "ready", "width": self.viewport_width, "height": self.viewport_height})

            self._reader = threading.Thread(target=self._read_cdp_loop, name="live_view_cdp_reader", daemon=True)
            self._reader.start()
            self._writer = threading.Thread(target=self._write_client_loop, name="live_view_client_writer", daemon=True)
            self._writer.start()

            self._read_client_loop()
        finally:
            self._teardown()

    # -- CDP -> client ------------------------------------------------------
    def _read_cdp_loop(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self._cdp.recv()
            except Exception as exc:
                # Timeout just lets us re-check the stop flag; anything else ends it.
                if websocket is not None and isinstance(exc, getattr(websocket, "WebSocketTimeoutException", ())):
                    continue
                if not self._stop.is_set():
                    self._log("info", f"[live_view] CDP recv ended: {exc}")
                self._stop.set()
                break
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("method") != "Page.screencastFrame":
                continue
            params = msg.get("params") or {}
            session_id = params.get("sessionId")
            meta = params.get("metadata") or {}
            try:
                self._dev_w = float(meta.get("deviceWidth") or self._dev_w) or self.viewport_width
                self._dev_h = float(meta.get("deviceHeight") or self._dev_h) or self.viewport_height
            except Exception:
                pass
            # Ack first so Chrome keeps streaming; then enqueue (drop-old).
            try:
                self._cdp_send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:
                self._stop.set()
                break
            # Decode base64 -> raw JPEG bytes once on the server. Saves the
            # client a data: URL decode (its own base64 -> Blob path); saves
            # the wire ~33% bandwidth; eliminates the JSON wrap. Per-frame
            # `meta` (deviceWidth/Height) is already stored on self via
            # self._dev_w / self._dev_h above and used for input coord mapping,
            # so it doesn't need to travel to the client.
            b64 = params.get("data") or ""
            if not b64:
                continue
            try:
                jpeg_bytes = base64.b64decode(b64, validate=False)
            except Exception:
                continue
            with self._outbox_cond:
                # Replace any unsent frame: client only ever sees the latest.
                self._outbox_frame = jpeg_bytes
                self._outbox_cond.notify()

    # -- outbox -> client ---------------------------------------------------
    def _write_client_loop(self) -> None:
        """Drain the single-slot outbox to the client WebSocket.

        Always sends the latest pending frame. If the client is slow to drain
        (TCP backpressure on ws.send blocks here), the reader thread keeps
        replacing _outbox_frame with newer frames; we wake up and send only
        the most-recent one. Stale frames are silently dropped -- exactly the
        behaviour we want for low click-to-response latency.
        """
        while not self._stop.is_set():
            with self._outbox_cond:
                while self._outbox_frame is None and not self._stop.is_set():
                    self._outbox_cond.wait(timeout=1.0)
                frame = self._outbox_frame
                self._outbox_frame = None
            if frame is None:
                continue
            # Binary WS send: flask-sock / simple-websocket treats bytes as a
            # binary opcode frame. Client distinguishes by typeof event.data.
            try:
                with self._client_send_lock:
                    self.client_ws.send(frame)
            except Exception:
                # Client gone or send failure -> stop. The reader will also see
                # this via subsequent _client_send_json failures for ready/error.
                self._stop.set()
                break

    # -- client -> CDP ------------------------------------------------------
    def _read_client_loop(self) -> None:
        while not self._stop.is_set():
            # Idle auto-disconnect. receive() below already ticks ~1 Hz even when
            # fully idle (1 s timeout), so this check fires promptly. We only set
            # the stop flag + notify the client here; the actual CDP teardown and
            # the route's set_pause(False) resume happen in run()'s finally ->
            # _teardown, off any CDP lock. (See _client_send_json: no CDP lock.)
            if self.idle_timeout_sec > 0 and (
                time.monotonic() - self._last_input_ts > self.idle_timeout_sec
            ):
                mins = int(self.idle_timeout_sec // 60)
                self._log("info", f"[live_view] idle > {mins} min, auto-disconnecting")
                self._client_send_json(
                    {"type": "closed", "reason": "idle_timeout", "message": f"閒置超過 {mins} 分鐘，已自動中斷"}
                )
                self._stop.set()
                break
            try:
                raw = self.client_ws.receive(timeout=1.0)
            except Exception:
                break
            if raw is None:
                # timeout (keep looping) vs closed: probe stop flag and continue.
                if self._stop.is_set():
                    break
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            try:
                self._dispatch_input(msg)
            except Exception as exc:
                self._log("info", f"[live_view] input dispatch failed: {exc}")
                self._stop.set()
                break

    def _norm_to_css(self, nx: float, ny: float) -> Tuple[float, float]:
        nx = min(max(float(nx), 0.0), 1.0)
        ny = min(max(float(ny), 0.0), 1.0)
        return nx * self._dev_w, ny * self._dev_h

    def _dispatch_input(self, msg: Dict[str, Any]) -> None:
        # Any client->server message counts as user activity (resets idle timer).
        # Outgoing screencast frames never pass through here, so frame traffic
        # alone will not keep an unattended session alive.
        self._last_input_ts = time.monotonic()
        kind = msg.get("type")
        if kind == "mouse":
            action = msg.get("action")  # "down" | "up" | "move"
            x, y = self._norm_to_css(msg.get("nx", 0), msg.get("ny", 0))
            event_type = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}.get(action)
            if not event_type:
                return
            pressed = action == "down"
            self._cdp_send(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "none" if action == "move" else "left",
                    "buttons": 1 if pressed else 0,
                    "clickCount": 0 if action == "move" else 1,
                },
            )
        elif kind == "key":
            action = msg.get("action")  # "down" | "up"
            event_type = {"down": "keyDown", "up": "keyUp"}.get(action)
            if not event_type:
                return
            params: Dict[str, Any] = {
                "type": event_type,
                "key": msg.get("key", ""),
                "code": msg.get("code", ""),
            }
            text = msg.get("text")
            if action == "down" and text:
                params["text"] = text
            self._cdp_send("Input.dispatchKeyEvent", params)

    # -- public stop --------------------------------------------------------
    def stop(self) -> None:
        """Signal session to shut down from any thread. Idempotent."""
        self._stop.set()
        with self._outbox_cond:
            self._outbox_cond.notify_all()
        # Close the client WS from here so receive() unblocks immediately
        # instead of waiting up to 1 s for the timeout to fire.
        try:
            self.client_ws.close()
        except Exception:
            pass

    # -- teardown -----------------------------------------------------------
    def _teardown(self) -> None:
        self._stop.set()
        # Wake the writer so it can observe the stop flag and exit promptly.
        with self._outbox_cond:
            self._outbox_cond.notify_all()
        if self._cdp is not None:
            try:
                self._cdp_send("Page.stopScreencast")
            except Exception:
                pass
            try:
                self._cdp.close()
            except Exception:
                pass
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=2.0)
        if self._writer is not None and self._writer.is_alive():
            self._writer.join(timeout=2.0)
        try:
            self.client_ws.close()
        except Exception:
            pass
