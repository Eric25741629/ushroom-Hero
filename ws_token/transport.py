"""Transport seam for WSGameClient.

A tiny interface around a binary WebSocket so the client logic can be unit-tested
with a scripted fake (no real socket). The production implementation wraps
websocket-client, the same library the LIVE-verified PoC uses.
"""
from __future__ import annotations

from typing import Protocol


class Transport(Protocol):
    """Binary, message-framed bidirectional channel."""

    def send(self, data: bytes) -> None: ...
    def recv(self) -> bytes: ...
    def close(self) -> None: ...


class WebsocketTransport:
    """Real transport over websocket-client (binary frames)."""

    def __init__(self, url: str, *, timeout: float = 20.0) -> None:
        import websocket  # lazy: unit tests inject a fake and never import this

        self._ws = websocket.create_connection(
            url, timeout=timeout, suppress_origin=True
        )

    def send(self, data: bytes) -> None:
        self._ws.send_binary(data)

    def recv(self) -> bytes:
        data = self._ws.recv()
        if isinstance(data, str):
            data = data.encode("latin1")
        return data

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass
