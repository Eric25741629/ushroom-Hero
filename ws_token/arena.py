# -*- coding: utf-8 -*-
"""競技場 pure WS：info / combat / result（勝負由 BattleMainServer 算出後回報）。

cmd（docs/protocol/ARENA_PROTO_SCHEMA.json）::
  5121 arena_info
  5123 arena_combat  c2s {eid#1} → s2c {code,eid,vid,seed,atk_data,def_data}
  5124 arena_result  c2s {vid#1, wid#2} → s2c {is_win, my_score, ...}
  5126 arena_refresh
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_INFO = 5121
CMD_COMBAT = 5123
CMD_RESULT = 5124
CMD_REFRESH = 5126
CMD_ERROR = 0x0201


@dataclass(frozen=True)
class ArenaEnemy:
    id: int
    name: str = ""
    lev: int = 0
    power: int = 0
    score: int = 0
    rank: int = 0


@dataclass(frozen=True)
class ArenaInfo:
    success: bool
    my_score: int = 0
    my_rank: int = 0
    enemies: tuple[ArenaEnemy, ...] = ()
    buy_times: int = 0
    error: str | None = None
    raw: bytes = field(default=b"", repr=False, compare=False)


@dataclass(frozen=True)
class ArenaCombat:
    success: bool
    code: int = 0
    eid: int = 0
    vid: int = 0
    seed: int = 0
    body: bytes = field(default=b"", repr=False, compare=False)
    error: str | None = None


@dataclass(frozen=True)
class ArenaResult:
    success: bool
    is_win: int | None = None
    my_score: int | None = None
    my_rank: int | None = None
    my_score_change: int | None = None
    e_name: str = ""
    error: str | None = None
    fields: dict = field(default_factory=dict, compare=False)


def build_combat_c2s(eid: int) -> bytes:
    return codec.pb_uint(1, int(eid))


def build_result_c2s(vid: int, wid: int) -> bytes:
    return codec.pb_uint(1, int(vid)) + codec.pb_uint(2, int(wid))


def _parse_enemy(blob: bytes) -> ArenaEnemy | None:
    d = codec.walk_dict(blob)
    eid = d.get(1)
    if not isinstance(eid, int):
        return None
    name = d.get(6)
    return ArenaEnemy(
        id=int(eid),
        lev=int(d.get(2) or 0),
        rank=int(d.get(3) or 0),
        score=int(d.get(4) or 0),
        power=int(d.get(5) or 0),
        name=name if isinstance(name, str) else "",
    )


def parse_info(body: bytes) -> ArenaInfo:
    d = codec.walk_dict(body)
    enemies: list[ArenaEnemy] = []
    for fnum, v in codec.walk(body):
        if fnum == 5 and isinstance(v, (bytes, bytearray)):
            e = _parse_enemy(bytes(v))
            if e:
                enemies.append(e)
    return ArenaInfo(
        success=True,
        my_score=int(d.get(3) or 0),
        my_rank=int(d.get(4) or 0),
        enemies=tuple(enemies),
        buy_times=int(d.get(6) or 0),
        raw=body,
    )


def parse_combat(cmd: int, body: bytes) -> ArenaCombat:
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return ArenaCombat(success=False, error=f"server error {ec}", body=body)
    if cmd != CMD_COMBAT:
        return ArenaCombat(success=False, error=f"unexpected cmd 0x{cmd:04x}", body=body)
    d = codec.walk_dict(body)
    code = int(d.get(1) or 0)
    if code not in (0,):
        return ArenaCombat(success=False, code=code, error=f"combat code={code}", body=body)
    return ArenaCombat(
        success=True,
        code=code,
        eid=int(d.get(2) or 0),
        vid=int(d.get(3) or 0),
        seed=int(d.get(4) or 0),
        body=body,
    )


def parse_result(cmd: int, body: bytes) -> ArenaResult:
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        return ArenaResult(success=False, error=f"server error {ec}")
    if cmd != CMD_RESULT:
        return ArenaResult(success=False, error=f"unexpected cmd 0x{cmd:04x}")
    d = codec.walk_dict(body)
    name = d.get(5)
    return ArenaResult(
        success=True,
        is_win=int(d[1]) if 1 in d and isinstance(d[1], int) else None,
        my_score=int(d[2]) if 2 in d and isinstance(d[2], int) else None,
        my_rank=int(d[3]) if 3 in d and isinstance(d[3], int) else None,
        my_score_change=int(d[4]) if 4 in d and isinstance(d[4], int) else None,
        e_name=name if isinstance(name, str) else "",
        fields=d,
    )


def fetch_info(client: WSGameClient, *, timeout: float | None = None) -> ArenaInfo:
    body = client.call(CMD_INFO, b"", timeout=timeout)
    return parse_info(body)


def start_combat(
    client: WSGameClient, eid: int, *, timeout: float | None = None
) -> ArenaCombat:
    cmd, body = client.call_for(
        CMD_COMBAT,
        build_combat_c2s(eid),
        expect_cmds=(CMD_COMBAT, CMD_ERROR),
        timeout=timeout,
    )
    return parse_combat(cmd, body)


def report_result(
    client: WSGameClient, vid: int, wid: int, *, timeout: float | None = None
) -> ArenaResult:
    cmd, body = client.call_for(
        CMD_RESULT,
        build_result_c2s(vid, wid),
        expect_cmds=(CMD_RESULT, CMD_ERROR),
        timeout=timeout,
    )
    return parse_result(cmd, body)


def pick_weakest(enemies: tuple[ArenaEnemy, ...] | list[ArenaEnemy]) -> Optional[ArenaEnemy]:
    if not enemies:
        return None
    return min(enemies, key=lambda e: (e.power or 0, e.score or 0))
