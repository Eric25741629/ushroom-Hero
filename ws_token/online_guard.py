"""Online-presence guard over a logged-in WSGameClient — pure WS, no OCR.

Used before running a WS task on some account: confirm the *real* human player
you want to protect is not currently online, so the bot does not stomp a live
session. Two independent lookups (caller supplies a connected WSGameClient):

  1. Friend list (primary, cmd 0x0F02). Ported field/sf numbers from
     utils/web_game_api.parse_friend_list and _build_friend_list_request_body:
       request body  = {field1: 1, field2: tab}  -> 08 01 10 <tab>
       response body = repeated friend entries at field 5 (wire 2)
       per entry     : sf1 = player_id, sf2 = name, sf7 = last_login_ts
     Presence logic mirrors web_game_api.is_player_online exactly:
       last_login_ts == 0          -> online (server presence sentinel)
       (now - last_login_ts) < thr -> online (active within threshold)
       missing ts / not in list    -> offline
  2. Guild members (fallback, cmd 7440 = guild.guild_members_info_c2s).
     request : {guild_id#1:int64}
     response : guild_members_info_s2c { member_list#2 : p_simple_guild_member[] }
     p_simple_guild_member { role_id#1, role_name#2, is_online#5, offline_time#6 }
     (TYPE_PROTO_SCHEMA.json / GUILD_PROTO_SCHEMA.json)

Reuses ws_token.codec (pb_* builders, walk/walk_dict) and ws_token.client; no
new wire machinery. `now` is injectable so threshold tests stay deterministic.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

# --- friend list (primary) -------------------------------------------------
CMD_FRIEND_LIST = 0x0F02         # friend.* list request/response (same cmd)
_FRIEND_TAB_DEFAULT = 1          # tab 1 = 好友
_DEFAULT_THRESHOLD_SEC = 60
_FRIEND_ENTRY_FIELD = 5          # repeated friend entry field number
_SF_PLAYER_ID = 1
_SF_NAME = 2
_SF_LAST_LOGIN_TS = 7

# --- guild members (fallback) ----------------------------------------------
CMD_GUILD_MEMBERS_INFO = 7440    # guild.guild_members_info_c2s/_s2c
_GUILD_ID_FIELD = 1              # guild_members_info_c2s.guild_id
_MEMBER_LIST_FIELD = 2           # guild_members_info_s2c.member_list (repeated)
_MEMBER_ROLE_ID = 1              # p_simple_guild_member.role_id
_MEMBER_ROLE_NAME = 2            # p_simple_guild_member.role_name
_MEMBER_IS_ONLINE = 5           # p_simple_guild_member.is_online

NowFn = Callable[[], float]


@dataclass(frozen=True)
class FriendEntry:
    """One friend from the 0x0F02 response (only the presence-relevant fields)."""

    player_id: int
    name: str
    last_login_ts: Optional[int]


@dataclass(frozen=True)
class GuildMember:
    """One member from guild_members_info_s2c (p_simple_guild_member)."""

    role_id: int
    role_name: str
    is_online: bool


# --- small coercion helpers (mirrors ws_token.redpack) ---------------------

def _as_int(v: object) -> Optional[int]:
    return int(v) if isinstance(v, int) else None


def _as_str(v: object) -> str:
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8")
        except UnicodeDecodeError:
            return bytes(v).decode("utf-8", "replace")
    return "" if v is None else str(v)


# ── friend list (primary) ──────────────────────────────────────────────────

def build_friend_list_body(tab: int = _FRIEND_TAB_DEFAULT) -> bytes:
    """Friend-list request: {field1: 1, field2: tab}.

    Byte-identical to web_game_api._build_friend_list_request_body (08 01 10 tab).
    """
    return codec.pb_uint(1, 1) + codec.pb_uint(2, tab)


def parse_friend_list(body: bytes) -> List[FriendEntry]:
    """Decode friend entries from a 0x0F02 response body.

    Entries live at field 5 (wire 2). Per entry: sf1=player_id, sf2=name,
    sf7=last_login_ts. Entries without both a player_id and a name are skipped
    (matches web_game_api.parse_friend_list).
    """
    if not body:
        return []
    friends: List[FriendEntry] = []
    for fnum, val in codec.walk(body):
        if fnum != _FRIEND_ENTRY_FIELD or not isinstance(val, (bytes, bytearray)):
            continue
        sub = codec.walk_dict(bytes(val))
        pid = _as_int(sub.get(_SF_PLAYER_ID))
        raw_name = sub.get(_SF_NAME)
        if pid is None or not isinstance(raw_name, (bytes, bytearray)):
            continue
        friends.append(
            FriendEntry(
                player_id=pid,
                name=_as_str(raw_name),
                last_login_ts=_as_int(sub.get(_SF_LAST_LOGIN_TS)),
            )
        )
    return friends


def is_role_online(
    client: WSGameClient,
    target_role_id: int,
    *,
    threshold_sec: int = _DEFAULT_THRESHOLD_SEC,
    timeout: Optional[float] = None,
    now: NowFn = time.time,
) -> bool:
    """True if ``target_role_id`` is currently online per the friend list.

    Sends the friend list (tab=1), finds the entry whose player_id matches, then
    applies the same presence rule as web_game_api.is_player_online:
      last_login_ts == 0           -> online
      (now - last_login_ts) < thr  -> online
      no ts / not in list          -> offline (False)

    ``now`` is injectable so threshold behavior is testable without the wall
    clock. Returns False on any timeout/parse miss (fail-safe: do not assume the
    protected player is offline when we cannot tell — see note below).
    """
    body = client.call(CMD_FRIEND_LIST, build_friend_list_body(), timeout=timeout)
    for friend in parse_friend_list(body):
        if friend.player_id != target_role_id:
            continue
        ts = friend.last_login_ts
        if ts is None:
            return False
        if ts == 0:
            return True
        return (int(now()) - ts) < int(threshold_sec)
    return False


# ── guild members (fallback) ────────────────────────────────────────────────

def build_members_body(guild_id: int) -> bytes:
    """guild_members_info_c2s request: {guild_id#1:int64}."""
    return codec.pb_uint(_GUILD_ID_FIELD, guild_id)


def parse_members(body: bytes) -> List[GuildMember]:
    """Decode member_list (field 2) from a guild_members_info_s2c body.

    Each member is p_simple_guild_member: role_id#1, role_name#2, is_online#5.
    Members without a role_id are skipped.
    """
    if not body:
        return []
    members: List[GuildMember] = []
    for fnum, val in codec.walk(body):
        if fnum != _MEMBER_LIST_FIELD or not isinstance(val, (bytes, bytearray)):
            continue
        sub = codec.walk_dict(bytes(val))
        role_id = _as_int(sub.get(_MEMBER_ROLE_ID))
        if role_id is None:
            continue
        members.append(
            GuildMember(
                role_id=role_id,
                role_name=_as_str(sub.get(_MEMBER_ROLE_NAME)),
                is_online=bool(_as_int(sub.get(_MEMBER_IS_ONLINE)) or 0),
            )
        )
    return members


def is_role_online_in_guild(
    client: WSGameClient,
    guild_id: int,
    target_role_id: Optional[int] = None,
    target_name: Optional[str] = None,
    *,
    timeout: Optional[float] = None,
) -> Optional[bool]:
    """Online state of a guild member, matched by role_id or role_name.

    Returns:
      True/False — the matched member's is_online flag.
      None       — no target given, member not found, or no member_list.
    role_id match takes precedence over name match when both are supplied.
    """
    if target_role_id is None and target_name is None:
        logger.warning("ws_token online_guard: is_role_online_in_guild called "
                       "without target_role_id or target_name")
        return None
    body = client.call(CMD_GUILD_MEMBERS_INFO, build_members_body(guild_id),
                       timeout=timeout)
    for member in parse_members(body):
        if target_role_id is not None and member.role_id == target_role_id:
            return member.is_online
        if target_name is not None and member.role_name == target_name:
            return member.is_online
    return None
