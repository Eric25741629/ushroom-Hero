"""Kick-detection tests for ws_token.client.WSGameClient.

異地登入 (logging the same account in elsewhere) makes the server:
  1. push a kick frame ``cmd=259 (0x103)`` with body ``{1: <reason>}`` (20 = 異地登入),
  2. then close the socket.

The client must (a) detect the kick push, set ``is_kicked()``, fire the optional
``on_kick`` callback ONCE, and NOT leak the frame to the push_handler; and (b)
also flag a kick when the reader thread exits because the transport was closed by
the *server* (not by our own ``close()``). A deliberate ``close()`` must stay
quiet (no kick).
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import CMD_KICKED, WSGameClient  # noqa: E402
from tests.fakes.ws_fakes import (  # noqa: E402
    CMD_PETS,
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


def _kick_frame(reason: int = 20) -> bytes:
    """Build the LIVE-observed kick push: cmd 259, body {1: reason} (08 14)."""
    return s2c(CMD_KICKED, codec.pb_uint(1, reason))


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# --- kick push (cmd 259) ----------------------------------------------------

def test_kick_push_sets_is_kicked_and_fires_callback_once():
    """A cmd-259 frame flips is_kicked() and fires on_kick exactly once."""
    fired = []
    # A call to CMD_PETS also triggers the server's kick push.
    fake = FakeTransport(login_responder(
        {CMD_PETS: lambda _b: [_kick_frame(20), _kick_frame(20)]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False,
                          on_kick=lambda: fired.append(1))
    client.connect()
    try:
        client.call(CMD_PETS, b"", timeout=0.2)  # no reply -> times out, that's fine
    except Exception:
        pass
    try:
        assert _wait_until(client.is_kicked)
        # callback fired exactly once even though two kick frames arrived
        assert _wait_until(lambda: len(fired) == 1)
        assert fired == [1]
    finally:
        client.close()


def test_kick_push_not_delivered_to_push_handler():
    """The kick frame must be consumed by kick handling, NOT leaked as a push."""
    pushed = []
    fake = FakeTransport(login_responder(
        {CMD_PETS: lambda _b: [_kick_frame(20)]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.set_push_handler(lambda cmd, body: pushed.append((cmd, body)))
    client.connect()
    try:
        try:
            client.call(CMD_PETS, b"", timeout=0.2)
        except Exception:
            pass
        assert _wait_until(client.is_kicked)
        time.sleep(0.05)  # give any (wrong) push routing a chance to happen
        assert pushed == []  # cmd 259 never reached the push handler
    finally:
        client.close()


def test_set_kick_handler_installs_callback_after_construction():
    fired = []
    fake = FakeTransport(login_responder(
        {CMD_PETS: lambda _b: [_kick_frame(20)]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.set_kick_handler(lambda: fired.append(1))
    client.connect()
    try:
        try:
            client.call(CMD_PETS, b"", timeout=0.2)
        except Exception:
            pass
        assert _wait_until(lambda: fired == [1])
    finally:
        client.close()


def test_is_running_false_after_kick():
    """is_running() must reflect the kick even though _connected is still set."""
    fake = FakeTransport(login_responder(
        {CMD_PETS: lambda _b: [_kick_frame(20)]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        assert client.is_running() is True  # healthy right after login
        try:
            client.call(CMD_PETS, b"", timeout=0.2)
        except Exception:
            pass
        assert _wait_until(client.is_kicked)
        assert client.is_running() is False
    finally:
        client.close()


# --- reader exit on server-side socket close --------------------------------

def test_reader_exit_on_server_close_marks_kicked():
    """If the transport closes while we did NOT request stop, that's a kick."""
    fake = FakeTransport(login_responder())
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        # Simulate the server hanging up: close the transport from underneath
        # the client WITHOUT going through client.close() (so _stop stays clear).
        fake.close()  # reader's recv() now raises -> reader exits
        assert _wait_until(client.is_kicked)
    finally:
        client.close()


def test_reader_exit_on_server_close_fires_no_callback():
    """A bare socket drop sets the flag but does NOT fire on_kick (silent)."""
    fired = []
    fake = FakeTransport(login_responder())
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False, on_kick=lambda: fired.append(1))
    client.connect()
    try:
        fake.close()
        assert _wait_until(client.is_kicked)
        time.sleep(0.05)
        assert fired == []  # only the explicit cmd-259 push fires the callback
    finally:
        client.close()


def test_deliberate_close_does_not_mark_kicked():
    """A normal close() (we set _stop first) must NOT look like a kick."""
    fake = FakeTransport(login_responder())
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    client.close()
    time.sleep(0.1)
    assert client.is_kicked() is False
