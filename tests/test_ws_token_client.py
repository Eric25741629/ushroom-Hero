"""Tests for ws_token.client.WSGameClient.

Driven against a scripted in-process fake transport (tests/fakes/ws_fakes.py),
so login handshake sequencing, heartbeat scheduling, and request/response
correlation are all deterministic. Wire-correctness of the login body is pinned
by byte-parity against the LIVE-verified PoC.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import _login_poc as poc  # noqa: E402
from ws_token import codec  # noqa: E402
from ws_token.client import (  # noqa: E402
    WSGameClient,
    WSLoginError,
    WSTimeoutError,
    build_heartbeat,
    build_role_login,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CMD_HEARTBEAT,
    CMD_LOGIN,
    CMD_PETS,
    CREDS,
    SAMPLE,
    FakeTransport,
    factory_for,
    login_ok,
    login_responder,
    s2c,
)


# --- pure builders ----------------------------------------------------------

@pytest.mark.parametrize("time_val", [0, 1780924117, 1780924200])
def test_build_role_login_byte_parity_with_poc(time_val):
    # PoC build_role_login is LIVE-verified; ours must be byte-identical.
    assert build_role_login(CREDS, time_val) == poc.build_role_login(SAMPLE, time_val)


def test_build_heartbeat_is_svr_time_field_1():
    body = build_heartbeat(1780924200)
    assert codec.walk_dict(body) == {1: 1780924200}


# --- connect / login --------------------------------------------------------

def test_connect_sends_active_byte_then_login_and_returns_fields():
    fake = FakeTransport(login_responder())
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    info = client.connect()
    try:
        assert fake.url == CREDS.ws_url
        assert fake.sent[0] == b"\x00"            # active byte (fresh connect)
        sid, cmd, _body = fake.framed_sent()[0]
        assert (sid, cmd) == (1, CMD_LOGIN)        # first framed send = role_login
        assert info["code"] == 0
        assert info["role_id"] == 89555436834913
        assert info["serv_time"] == 1780924200
    finally:
        client.close()


def test_connect_failure_raises_login_error():
    fake = FakeTransport(lambda cmd, body: [login_ok(code=7)] if cmd == CMD_LOGIN else [])
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    with pytest.raises(WSLoginError, match="7"):
        client.connect()
    assert fake.closed  # failed login tears down the transport


# --- request / response correlation ----------------------------------------

def test_call_returns_matching_response_body():
    pets_body = poc._f_msg(1, poc._f_str(7, "Fluffy"))
    fake = FakeTransport(login_responder({CMD_PETS: lambda _b: [s2c(CMD_PETS, pets_body)]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        assert client.call(CMD_PETS, b"") == pets_body
    finally:
        client.close()


def test_call_expect_cmd_override_routes_to_different_reply_cmd():
    CMD_GRAB, CMD_ERR = 0x2603, 0x0201
    err_body = codec.pb_uint(1, 2)  # error_code=2 (already collected)
    fake = FakeTransport(login_responder({CMD_GRAB: lambda _b: [s2c(CMD_ERR, err_body)]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        out = client.call(CMD_GRAB, codec.pb_uint(1, 123), expect_cmd=CMD_ERR)
        assert codec.walk_dict(out) == {1: 2}
    finally:
        client.close()


def test_call_times_out_when_no_reply():
    fake = FakeTransport(login_responder())  # 16898 gets no response
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        with pytest.raises(WSTimeoutError):
            client.call(CMD_PETS, b"", timeout=0.2)
    finally:
        client.close()


# --- send id ----------------------------------------------------------------

def test_send_id_increments_per_framed_packet():
    pets = lambda _b: [s2c(CMD_PETS, b"\x08\x00")]  # noqa: E731
    fake = FakeTransport(login_responder({CMD_PETS: pets}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        client.call(CMD_PETS, b"")
        client.call(CMD_PETS, b"")
        sids = [sid for sid, _c, _b in fake.framed_sent()]
        assert sids == [1, 2, 3]  # login, call, call
    finally:
        client.close()


def test_send_id_wraps_at_65535():
    fake = FakeTransport(login_responder())
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client._send_id = 65535
    assert client._next_send_id() == 65535
    assert client._next_send_id() == 1
    assert client._next_send_id() == 2


# --- heartbeat --------------------------------------------------------------

def test_heartbeat_emitted_on_interval_with_current_svr_time():
    def hb(_body):
        return [s2c(CMD_HEARTBEAT, codec.pb_uint(1, 1780924250))]

    fake = FakeTransport(login_responder({CMD_HEARTBEAT: hb}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=True, heartbeat_interval=0.05)
    client.connect()
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and CMD_HEARTBEAT not in fake.sent_cmds():
            time.sleep(0.02)
        hbs = [b for sid, c, b in fake.framed_sent() if c == CMD_HEARTBEAT]
        assert hbs, "no heartbeat packet was sent"
        assert codec.walk_dict(hbs[0]) == {1: 1780924200}  # serv_time from login
    finally:
        client.close()


# --- shutdown ---------------------------------------------------------------

def test_close_stops_threads_and_closes_transport():
    fake = FakeTransport(login_responder())
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=True, heartbeat_interval=0.05)
    client.connect()
    client.close()
    assert fake.closed
    time.sleep(0.1)
    assert not client.is_running()


def test_context_manager_closes_on_exit():
    fake = FakeTransport(login_responder())
    with WSGameClient(CREDS, transport_factory=factory_for(fake),
                      heartbeat_enabled=False) as client:
        client.connect()
        assert client.is_running()
    assert fake.closed


# --- call_for: wait for one of several reply cmds (grab -> 0x2603 | 0x0201) --

CMD_GRAB, CMD_ERR = 0x2603, 0x0201


def test_call_for_returns_success_cmd():
    fake = FakeTransport(login_responder(
        {CMD_GRAB: lambda _b: [s2c(CMD_GRAB, codec.pb_uint(1, 999))]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        cmd, body = client.call_for(CMD_GRAB, b"", expect_cmds=(CMD_GRAB, CMD_ERR))
        assert cmd == CMD_GRAB
        assert codec.walk_dict(body) == {1: 999}
    finally:
        client.close()


def test_call_for_returns_error_cmd():
    fake = FakeTransport(login_responder(
        {CMD_GRAB: lambda _b: [s2c(CMD_ERR, codec.pb_uint(1, 2))]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        cmd, body = client.call_for(CMD_GRAB, b"", expect_cmds=(CMD_GRAB, CMD_ERR))
        assert cmd == CMD_ERR
        assert codec.walk_dict(body) == {1: 2}
    finally:
        client.close()


def test_call_for_extra_reply_on_other_cmd_is_harmless():
    # grab replies with the success cmd AND a stray error cmd; the waiter must
    # take the first and unregister from the other so routing stays healthy.
    def grab(_b):
        return [s2c(CMD_GRAB, codec.pb_uint(1, 1)), s2c(CMD_ERR, codec.pb_uint(1, 2))]
    fake = FakeTransport(login_responder(
        {CMD_GRAB: grab, CMD_PETS: lambda _b: [s2c(CMD_PETS, b"\x08\x00")]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        cmd, body = client.call_for(CMD_GRAB, b"", expect_cmds=(CMD_GRAB, CMD_ERR))
        assert cmd == CMD_GRAB and codec.walk_dict(body) == {1: 1}
        time.sleep(0.05)  # let the stray CMD_ERR frame get routed (and dropped)
        assert client.call(CMD_PETS, b"") == b"\x08\x00"  # routing still works
    finally:
        client.close()


def test_call_for_times_out_when_no_reply():
    fake = FakeTransport(login_responder())  # CMD_GRAB gets no response
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.connect()
    try:
        with pytest.raises(WSTimeoutError):
            client.call_for(CMD_GRAB, b"", expect_cmds=(CMD_GRAB, CMD_ERR),
                            timeout=0.2)
    finally:
        client.close()


# --- push handler (server-initiated frames with no waiter) ------------------

def test_set_push_handler_receives_unmatched_frames():
    CMD_PUSH = 0x0504
    got = []
    # a call to CMD_PETS also emits an unsolicited CMD_PUSH frame
    fake = FakeTransport(login_responder(
        {CMD_PETS: lambda _b: [s2c(CMD_PUSH, b"\x08\x01"), s2c(CMD_PETS, b"\x08\x00")]}))
    client = WSGameClient(CREDS, transport_factory=factory_for(fake),
                          heartbeat_enabled=False)
    client.set_push_handler(lambda cmd, body: got.append((cmd, body)))
    client.connect()
    try:
        assert client.call(CMD_PETS, b"") == b"\x08\x00"
        time.sleep(0.05)  # let the push frame route
        assert got == [(CMD_PUSH, b"\x08\x01")]
    finally:
        client.close()
