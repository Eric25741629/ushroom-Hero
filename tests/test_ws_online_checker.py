"""Tests for the pure-WS online-check backend (S0-wire).

Two layers under test:

  1. ``ws_token.online_guard.friend_presence`` — returns ``Optional[bool]`` so a
     target that is simply *not in the friend list* is reported as ``None``
     (undetermined) rather than conflated with "offline" (the existing
     ``is_role_online`` returns ``False`` for both, which is unsafe for the
     protection gate — we must never 放行 on an unknown result).
  2. ``runtime_services.ws_online_checker.check_via_ws`` — builds a WSGameClient
     from the checker's own creds, connects, asks ``friend_presence`` and (when a
     guild_id is supplied and friend lookup is undetermined) falls back to the
     guild member list. Any error / undetermined → ``None``. The client is always
     closed.

`check_via_ws` is what the master-only ``runtime_services.online_check_service``
calls to answer cross-device online-check out of the wake loop.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token import online_guard  # noqa: E402

TARGET = 89616640123660
GUILD_ID = 1001


# --- fakes -----------------------------------------------------------------

def _friend(pid, name, last_ts=None):
    out = codec.pb_uint(1, pid) + codec.pb_str(2, name)
    if last_ts is not None:
        out += codec.pb_uint(7, last_ts)
    return out


def _friend_list_body(entries):
    body = codec.pb_uint(1, 0) + codec.pb_uint(2, 1)
    for e in entries:
        body += codec.pb_msg(5, e)
    return body


def _member(role_id, name, is_online):
    return (codec.pb_uint(1, role_id) + codec.pb_str(2, name)
            + codec.pb_uint(5, 1 if is_online else 0))


def _members_body(members):
    body = b""
    for m in members:
        body += codec.pb_msg(2, m)
    return body


class FakeClient:
    """Records connect/close and answers .call(cmd, body) from a scripted map."""

    def __init__(self, responses, *, raise_on=None):
        self._responses = responses          # {cmd: bytes}
        self._raise_on = raise_on or set()    # {cmd} -> raise on call
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True
        return {"role_id": 1, "serv_time": 0}

    def call(self, cmd, body, timeout=None):
        if cmd in self._raise_on:
            raise RuntimeError(f"boom on {cmd}")
        return self._responses.get(cmd, b"")

    def close(self):
        self.closed = True


# --- friend_presence: Optional[bool] semantics -----------------------------

def test_friend_presence_online_when_ts_zero():
    body = _friend_list_body([_friend(TARGET, "p", 0)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    assert online_guard.friend_presence(client, TARGET) is True


def test_friend_presence_offline_when_ts_old():
    body = _friend_list_body([_friend(TARGET, "p", 1000)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    assert online_guard.friend_presence(
        client, TARGET, threshold_sec=60, now=lambda: 100000) is False


def test_friend_presence_online_within_threshold():
    body = _friend_list_body([_friend(TARGET, "p", 99990)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    assert online_guard.friend_presence(
        client, TARGET, threshold_sec=60, now=lambda: 100000) is True


def test_friend_presence_none_when_not_in_list():
    body = _friend_list_body([_friend(111, "other", 0)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    assert online_guard.friend_presence(client, TARGET) is None


def test_friend_presence_none_when_no_ts():
    body = _friend_list_body([_friend(TARGET, "p", None)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    # In the list but server gave no ts → cannot tell → undetermined.
    assert online_guard.friend_presence(client, TARGET) is None


def test_is_role_online_still_false_for_not_in_list():
    # The original bool API stays backward-compatible (None -> False).
    body = _friend_list_body([_friend(111, "other", 0)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    assert online_guard.is_role_online(client, TARGET) is False


# --- check_via_ws ----------------------------------------------------------

def _patch_checker(monkeypatch, client):
    from runtime_services import ws_online_checker as mod
    monkeypatch.setattr(mod, "_load_creds", lambda: (lambda ip: object()))
    monkeypatch.setattr(mod, "_make_client", lambda creds, **kw: client)
    return mod


def test_check_via_ws_returns_friend_presence(monkeypatch):
    body = _friend_list_body([_friend(TARGET, "p", 0)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    mod = _patch_checker(monkeypatch, client)
    assert mod.check_via_ws("emulator-5554", TARGET, _Logger()) is True
    assert client.connected and client.closed


def test_check_via_ws_offline(monkeypatch):
    body = _friend_list_body([_friend(TARGET, "p", 1000)])
    client = FakeClient({online_guard.CMD_FRIEND_LIST: body})
    mod = _patch_checker(monkeypatch, client)
    assert mod.check_via_ws("emulator-5554", TARGET, _Logger(),
                            threshold_sec=60, now=lambda: 100000) is False
    assert client.closed


def test_check_via_ws_guild_fallback_when_friend_undetermined(monkeypatch):
    friends = _friend_list_body([_friend(111, "other", 0)])  # target absent
    members = _members_body([_member(TARGET, "p", True)])
    client = FakeClient({
        online_guard.CMD_FRIEND_LIST: friends,
        online_guard.CMD_GUILD_MEMBERS_INFO: members,
    })
    mod = _patch_checker(monkeypatch, client)
    assert mod.check_via_ws("emulator-5554", TARGET, _Logger(),
                            guild_id=GUILD_ID) is True


def test_check_via_ws_none_when_unknown_everywhere(monkeypatch):
    friends = _friend_list_body([_friend(111, "other", 0)])
    members = _members_body([_member(222, "x", True)])
    client = FakeClient({
        online_guard.CMD_FRIEND_LIST: friends,
        online_guard.CMD_GUILD_MEMBERS_INFO: members,
    })
    mod = _patch_checker(monkeypatch, client)
    assert mod.check_via_ws("emulator-5554", TARGET, _Logger(),
                            guild_id=GUILD_ID) is None


def test_check_via_ws_none_and_closes_on_error(monkeypatch):
    client = FakeClient({}, raise_on={online_guard.CMD_FRIEND_LIST})
    mod = _patch_checker(monkeypatch, client)
    assert mod.check_via_ws("emulator-5554", TARGET, _Logger()) is None
    assert client.closed  # must always close even on error


class _Logger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass
