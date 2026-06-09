"""Tests for ws_token.online_guard — pure-WS "is the real player online" check.

Two independent lookups against a logged-in WSGameClient:

  1. Friend list (primary, cmd 0x0F02): repeated friend entries at field 5;
     per-entry sf1=player_id, sf2=name, sf7=last_login_ts. Online iff
     last_login_ts == 0 (server presence sentinel) or (now - ts) < threshold.
     Logic mirrors utils/web_game_api.is_player_online (ported field/sf numbers
     from web_game_api.parse_friend_list / _build_friend_list_request_body).
  2. Guild members (fallback, cmd 7440): member_list at field 2; each member is
     p_simple_guild_member { role_id#1, role_name#2, is_online#5, offline_time#6 }.

Time-dependent cases use fixed far-past / near-now timestamps (or an injected
`now`) to stay deterministic; the ts==0 and not-in-list cases never touch the
wall clock.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.online_guard import (  # noqa: E402
    CMD_FRIEND_LIST,
    CMD_GUILD_MEMBERS_INFO,
    FriendEntry,
    GuildMember,
    build_friend_list_body,
    build_members_body,
    is_role_online,
    is_role_online_in_guild,
    parse_friend_list,
    parse_members,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)

# A target role id used across the friend-list tests.
TARGET = 89616640123660


# --- friend entry / member synthesizers ------------------------------------

def _friend(pid, name, last_ts=None):
    out = codec.pb_uint(1, pid) + codec.pb_str(2, name)
    if last_ts is not None:
        out += codec.pb_uint(7, last_ts)
    return out


def _friend_list_body(entries, *, result=0, tab=1, total=None):
    body = codec.pb_uint(1, result) + codec.pb_uint(2, tab)
    if total is None:
        total = len(entries)
    body += codec.pb_uint(4, total)
    body += b"".join(codec.pb_msg(5, e) for e in entries)
    return body


def _member(role_id, name, is_online):
    return (codec.pb_uint(1, role_id) + codec.pb_str(2, name)
            + codec.pb_uint(5, is_online))


def _members_body(guild_id, members):
    return (codec.pb_uint(1, guild_id)
            + b"".join(codec.pb_msg(2, m) for m in members))


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


# --- build_friend_list_body: 0x0F02 / tab=1 wire ---------------------------

def test_build_friend_list_body_default_tab1_wire():
    # {field1: 1, field2: tab=1} -> 08 01 10 01, byte-identical to
    # web_game_api._build_friend_list_request_body(tab=1).
    assert build_friend_list_body() == bytes.fromhex("08011001")


def test_build_friend_list_body_honors_tab():
    assert build_friend_list_body(tab=2) == bytes.fromhex("08011002")
    assert codec.walk_dict(build_friend_list_body(tab=3)) == {1: 1, 2: 3}


# --- parse_friend_list: field 5 entries, sf1/sf2/sf7 -----------------------

def test_parse_friend_list_decodes_entries():
    body = _friend_list_body([
        _friend(TARGET, "在線玩家", last_ts=0),       # online sentinel
        _friend(111, "離線玩家", last_ts=1700000000),  # old offline ts
    ])
    friends = parse_friend_list(body)
    assert len(friends) == 2
    assert friends[0] == FriendEntry(player_id=TARGET, name="在線玩家",
                                     last_login_ts=0)
    assert friends[1] == FriendEntry(player_id=111, name="離線玩家",
                                     last_login_ts=1700000000)


def test_parse_friend_list_skips_entries_without_pid_or_name():
    only_name = codec.pb_str(2, "無id")          # sf1 missing
    only_pid = codec.pb_uint(1, 222)             # sf2 missing
    body = _friend_list_body([only_name, only_pid,
                              _friend(333, "好友", last_ts=0)])
    friends = parse_friend_list(body)
    assert [f.player_id for f in friends] == [333]


def test_parse_friend_list_empty():
    assert parse_friend_list(b"") == []


def test_parse_friend_list_missing_ts_is_none():
    body = _friend_list_body([_friend(TARGET, "no_ts")])  # no sf7
    friends = parse_friend_list(body)
    assert friends[0].last_login_ts is None


# --- is_role_online: presence logic (ts==0 / old / absent / recent) --------

def test_is_role_online_ts_zero_is_online():
    # No wall-clock dependency: ts==0 is the server presence sentinel.
    body = _friend_list_body([_friend(TARGET, "online", last_ts=0)])
    c, _ = _client({CMD_FRIEND_LIST: lambda _b: [s2c(CMD_FRIEND_LIST, body)]})
    try:
        assert is_role_online(c, TARGET) is True
    finally:
        c.close()


def test_is_role_online_old_ts_is_offline():
    body = _friend_list_body([_friend(TARGET, "stale", last_ts=1_000_000)])
    c, _ = _client({CMD_FRIEND_LIST: lambda _b: [s2c(CMD_FRIEND_LIST, body)]})
    try:
        assert is_role_online(c, TARGET, threshold_sec=60) is False
    finally:
        c.close()


def test_is_role_online_target_not_in_list_is_false():
    body = _friend_list_body([_friend(111, "someone_else", last_ts=0)])
    c, _ = _client({CMD_FRIEND_LIST: lambda _b: [s2c(CMD_FRIEND_LIST, body)]})
    try:
        assert is_role_online(c, TARGET) is False
    finally:
        c.close()


def test_is_role_online_recent_ts_within_threshold_is_online():
    # Inject `now` so threshold logic is deterministic (no flaky wall clock).
    fixed_now = 2_000_000_000
    body = _friend_list_body([_friend(TARGET, "recent", last_ts=fixed_now - 10)])
    c, _ = _client({CMD_FRIEND_LIST: lambda _b: [s2c(CMD_FRIEND_LIST, body)]})
    try:
        assert is_role_online(c, TARGET, threshold_sec=60,
                              now=lambda: fixed_now) is True
        # Same ts but tighter threshold -> offline.
        assert is_role_online(c, TARGET, threshold_sec=5,
                              now=lambda: fixed_now) is False
    finally:
        c.close()


def test_is_role_online_missing_ts_is_false():
    body = _friend_list_body([_friend(TARGET, "no_ts")])  # sf7 absent
    c, _ = _client({CMD_FRIEND_LIST: lambda _b: [s2c(CMD_FRIEND_LIST, body)]})
    try:
        assert is_role_online(c, TARGET) is False
    finally:
        c.close()


def test_is_role_online_sends_friend_list_request():
    body = _friend_list_body([_friend(TARGET, "x", last_ts=0)])
    c, fake = _client({CMD_FRIEND_LIST: lambda _b: [s2c(CMD_FRIEND_LIST, body)]})
    try:
        is_role_online(c, TARGET)
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_FRIEND_LIST]
        assert len(sent) == 1
        assert codec.walk_dict(sent[0]) == {1: 1, 2: 1}  # tab=1 request
    finally:
        c.close()


# --- guild members fallback ------------------------------------------------

def test_build_members_body_carries_guild_id():
    assert codec.walk_dict(build_members_body(7788)) == {1: 7788}


def test_parse_members_decodes_is_online():
    body = _members_body(7788, [
        _member(TARGET, "會長", 1),
        _member(222, "副會長", 0),
    ])
    members = parse_members(body)
    assert members == [
        GuildMember(role_id=TARGET, role_name="會長", is_online=True),
        GuildMember(role_id=222, role_name="副會長", is_online=False),
    ]


def test_parse_members_empty():
    assert parse_members(b"") == []


def test_is_role_online_in_guild_by_role_id():
    body = _members_body(7788, [_member(TARGET, "會長", 1),
                                _member(222, "離線", 0)])
    c, _ = _client(
        {CMD_GUILD_MEMBERS_INFO: lambda _b: [s2c(CMD_GUILD_MEMBERS_INFO, body)]})
    try:
        assert is_role_online_in_guild(c, 7788, target_role_id=TARGET) is True
        assert is_role_online_in_guild(c, 7788, target_role_id=222) is False
    finally:
        c.close()


def test_is_role_online_in_guild_by_name():
    body = _members_body(7788, [_member(TARGET, "在線會長", 1),
                                _member(222, "離線會員", 0)])
    c, _ = _client(
        {CMD_GUILD_MEMBERS_INFO: lambda _b: [s2c(CMD_GUILD_MEMBERS_INFO, body)]})
    try:
        assert is_role_online_in_guild(c, 7788, target_name="在線會長") is True
        assert is_role_online_in_guild(c, 7788, target_name="離線會員") is False
    finally:
        c.close()


def test_is_role_online_in_guild_not_found_is_none():
    body = _members_body(7788, [_member(222, "別人", 1)])
    c, _ = _client(
        {CMD_GUILD_MEMBERS_INFO: lambda _b: [s2c(CMD_GUILD_MEMBERS_INFO, body)]})
    try:
        assert is_role_online_in_guild(c, 7788, target_role_id=TARGET) is None
        assert is_role_online_in_guild(c, 7788, target_name="不存在") is None
    finally:
        c.close()


def test_is_role_online_in_guild_requires_a_target():
    body = _members_body(7788, [_member(TARGET, "會長", 1)])
    c, _ = _client(
        {CMD_GUILD_MEMBERS_INFO: lambda _b: [s2c(CMD_GUILD_MEMBERS_INFO, body)]})
    try:
        # Neither target_role_id nor target_name -> cannot resolve -> None.
        assert is_role_online_in_guild(c, 7788) is None
    finally:
        c.close()


def test_is_role_online_in_guild_sends_guild_id():
    body = _members_body(7788, [_member(TARGET, "會長", 1)])
    c, fake = _client(
        {CMD_GUILD_MEMBERS_INFO: lambda _b: [s2c(CMD_GUILD_MEMBERS_INFO, body)]})
    try:
        is_role_online_in_guild(c, 7788, target_role_id=TARGET)
        sent = [b for _sid, cmd, b in fake.framed_sent()
                if cmd == CMD_GUILD_MEMBERS_INFO]
        assert codec.walk_dict(sent[0]) == {1: 7788}
    finally:
        c.close()
