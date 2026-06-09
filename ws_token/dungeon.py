"""Dungeon tasks over a logged-in WSGameClient — 深淵之門 + 萬神試煉 (週副本).

One pure-WS module over a single dungeon.* protocol; the two features differ only
by ``type`` (the same list / battle / sweep cmds carry the type discriminator):

  - 深淵之門 (Abyss Gate) — daily Coin dungeon:  type 2   (TYPE_ABYSS)
  - 萬神試煉 (Pantheon Trial) — weekly Seven dungeon: type 23 (TYPE_SEVEN_TRIAL)

Authoritative field numbers / cmd ids (verify against the exported schemas):
  docs/protocol/DUNGEON_PROTO_SCHEMA.json
    dungeon_list          3585 0x0E01  c2s {} -> s2c {dungeon_list#1:p_dungeon[]}
    dungeon_battle_start  3591 0x0E07  c2s {type#1, level#2}
                          -> s2c {code#1, type#2, dungeon_id#3, battle_checkout#4,
                                  random_seed#5:uint64, roles#6, rank_list#8}
    dungeon_battle_result 3592 0x0E08  c2s {type#1, dungeon_id#2, result#3,
                                  manual_operators#4, operators#5:p_battle_operator[],
                                  args#6:p_key_value[]}
                          -> s2c {code#1:int32, type#2, dungeon_id#3, result#4,
                                  reward_list#5:p_reward[], ext#6}
    dungeon_sweep         3596 0x0E0C  c2s {type#1, dungeon_id#2, sweep_num#3}
                          -> s2c {dungeon_id#1, reward_list#2:p_reward[]}  (NO code field)
  docs/protocol/TYPE_PROTO_SCHEMA.json
    p_dungeon {type#1, max_level#2, cur_level#3, day_times#4, ext#5, sext#6}
    p_reward  {gtid#1:int32, num#2:int64}

⚠ ANTI-CHEAT — 戰鬥勝負是客戶端算的，且有 checkCheat：server 可用 random_seed +
operators 回放驗算，並可能強制判敗。送 result=0 (client-reported win) 不保證成功。
  - 優先走 run_sweep：直接拿 reward，繞過戰鬥與 anti-cheat。
  - run_battle 仍提供，但送 result=0 / manual_operators=0 / operators=[] 這條
    # live-confirm: server 是否接受 client-reported result=0 (anti-cheat 風險)
    未經 live 驗證，可能被拒（回 0x0201 或 battle_result code!=0）。

門票 / 次數:
  - 週副本 type=23 (萬神試煉) 用 gtid 1081 (SEVEN_TICKET_GTID)，每場 1 張。
  - 深淵 type=2 (深淵之門) 門票 gtid 待 live 確認 (# live-confirm)。
  - 次數用完 / 門票不足 → battle_start.code != 0 或 sweep 回 0x0201 error。

LIVE 2026-06-09 (5554 + CDP 解碼 configErrorInfo):
  - run_sweep(type=2, dungeon_id∈{150,1,0}, num=1) 一律回 0x0201 **code=173 =「活動已結束」**
    → 深淵/萬神 的「掃蕩」(3596) 目前不開放(這條多半是限時掃蕩活動;日常 深淵/萬神 要走
    BATTLE 3591/3592,有 anti-cheat 風險)。run_sweep 已優雅處理(SweepResult success=False
    error_code=173,不 crash)。
  - day_times(156/748…)經 CDP chapterDataCache 確認 = **累計總數,非今日剩餘**。
    深淵(type2)真正每日上限 = 2(chapterDataCache.getLimit(2) -> [2, 2, [1003,1]]),
    **門票 gtid = 1003**(解掉先前的 # live-confirm)。
  - error 碼字義(configErrorInfo,權威):90=冷卻時間未到 / 159=次數不足 / 173=活動已結束。
  # live-confirm: 「掃蕩活動」幾時開 + 開時 sweep 的 dungeon_id 真值(抓客戶端封包);
  #   日常自動化 深淵/萬神 大概率得走 battle 路徑(anti-cheat)。
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_LIST = 3585            # 0x0E01 dungeon.dungeon_list_c2s/s2c (empty request body)
CMD_BATTLE_START = 3591    # 0x0E07 dungeon.dungeon_battle_start_c2s/s2c
CMD_BATTLE_RESULT = 3592   # 0x0E08 dungeon.dungeon_battle_result_c2s/s2c
CMD_SWEEP = 3596           # 0x0E0C dungeon.dungeon_sweep_c2s/s2c
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}

# Dungeon type discriminators (same protocol, only the type differs).
TYPE_ABYSS = 2             # 深淵之門 (daily Coin dungeon); daily limit 2, 門票 gtid 1003
TYPE_SEVEN_TRIAL = 23      # 萬神試煉 (weekly Seven dungeon, Mon-Sat)
ABYSS_TICKET_GTID = 1003   # 深淵之門 門票 gtid (CDP getLimit(2) -> [..,[1003,1]])
SEVEN_TICKET_GTID = 1081   # 萬神試煉 門票 gtid (每場 1 張)


@dataclass(frozen=True)
class Dungeon:
    """One p_dungeon {type#1, max_level#2, cur_level#3, day_times#4}."""

    type: int               # 2=深淵之門, 23=萬神試煉
    max_level: int          # highest level ever cleared
    cur_level: int          # current / next level
    day_times: int          # p_dungeon#4 — SEMANTIC UNCONFIRMED (see note below)
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def has_attempts(self) -> bool:
        """⚠ UNRELIABLE. Live (小寶/5554/5556/5560 2026-06-09) day_times returns
        large values (150~748), i.e. it looks like a CUMULATIVE total, NOT
        "remaining today" — so ``day_times > 0`` does not mean a sweep/battle is
        available. The real "remaining today" field is not yet identified.
        # live-confirm: find the true remaining-attempts field before gating on this."""
        return self.day_times > 0


@dataclass(frozen=True)
class BattleResult:
    """Outcome of a battle path (battle_start -> battle_result) or its failure."""

    type: int
    success: bool                       # True iff battle_result_s2c code == 0
    dungeon_id: int = 0
    random_seed: int = 0
    rewards: dict[int, int] = field(default_factory=dict)  # {gtid: num}
    response_cmd: int = 0
    fields: dict = field(compare=False, default_factory=dict)
    error_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class SweepResult:
    """dungeon_sweep_s2c {dungeon_id#1, reward_list#2} — or its 0x0201 failure."""

    type: int
    success: bool                       # True iff a sweep_s2c (not error) arrived
    dungeon_id: int = 0
    rewards: dict[int, int] = field(default_factory=dict)  # {gtid: num}
    response_cmd: int = 0
    fields: dict = field(compare=False, default_factory=dict)
    error_code: int | None = None
    error: str | None = None


# --- body builders ----------------------------------------------------------

def build_battle_start_body(type_: int, level: int) -> bytes:
    """dungeon_battle_start_c2s {type#1:uint32, level#2:uint32} — type FIRST."""
    return codec.pb_uint(1, type_) + codec.pb_uint(2, level)


def build_sweep_body(type_: int, dungeon_id: int, sweep_num: int) -> bytes:
    """dungeon_sweep_c2s {type#1, dungeon_id#2, sweep_num#3} — in that order."""
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, dungeon_id)
            + codec.pb_uint(3, sweep_num))


def build_battle_result_body(
    type_: int,
    dungeon_id: int,
    *,
    result: int = 0,
    manual_operators: int = 0,
    operators: Iterable[bytes] | None = None,
    args: Iterable[Sequence[int]] | None = None,
) -> bytes:
    """dungeon_battle_result_c2s {type#1, dungeon_id#2, result#3, manual_operators#4,
    operators#5:p_battle_operator[], args#6:p_key_value[]}.

    Defaults report a *client-side win with no replayed operators*
    (result=0, manual_operators=0, operators=[]).
    # live-confirm: server 是否接受 client-reported result=0 (anti-cheat 風險).

    ``operators`` are pre-encoded p_battle_operator sub-messages (this builder does
    NOT synthesise a believable replay); ``args`` are (k, v) pairs encoded as
    p_key_value. Both are omitted from the wire when empty.
    """
    out = (codec.pb_uint(1, type_) + codec.pb_uint(2, dungeon_id)
           + codec.pb_uint(3, result) + codec.pb_uint(4, manual_operators))
    for op in operators or ():
        out += codec.pb_msg(5, bytes(op))
    for k, v in args or ():
        out += codec.pb_msg(6, codec.pb_uint(1, k) + codec.pb_uint(2, v))
    return out


# --- s2c parsers ------------------------------------------------------------

def _parse_dungeon(entry: bytes) -> Dungeon:
    d = codec.walk_dict(entry)
    return Dungeon(
        type=_as_int(d.get(1)),
        max_level=_as_int(d.get(2)),
        cur_level=_as_int(d.get(3)),
        day_times=_as_int(d.get(4)),
        raw=d,
    )


def parse_dungeon_list(body: bytes) -> list[Dungeon]:
    """dungeon_list_s2c: every dungeon is dungeon_list (field 1, repeated)."""
    return [_parse_dungeon(bytes(v)) for fnum, v in codec.walk(body)
            if fnum == 1 and isinstance(v, (bytes, bytearray))]


def _collect_rewards(body: bytes, reward_field: int) -> dict[int, int]:
    """Decode every p_reward {gtid#1, num#2} on ``reward_field`` into {gtid: num}."""
    rewards: dict[int, int] = {}
    for fnum, v in codec.walk(body):
        if fnum == reward_field and isinstance(v, (bytes, bytearray)):
            r = codec.walk_dict(bytes(v))
            rewards[_as_int(r.get(1))] = _as_int(r.get(2))
    return rewards


def parse_battle_start(cmd: int, body: bytes) -> BattleResult:
    """dungeon_battle_start_s2c {code#1, type#2, dungeon_id#3, random_seed#5}.

    Returns a BattleResult carrying the dungeon_id + random_seed needed to send a
    battle_result; success here only means the battle was *opened* (code == 0).
    """
    f = codec.walk_dict(body)
    if cmd == CMD_BATTLE_START:
        code = _as_int(f.get(1))
        if code == 0:
            return BattleResult(
                type=_as_int(f.get(2)), success=True,
                dungeon_id=_as_int(f.get(3)), random_seed=_as_int(f.get(5)),
                response_cmd=cmd, fields=f)
        return BattleResult(type=_as_int(f.get(2)), success=False,
                            response_cmd=cmd, fields=f, error_code=code,
                            error=f"battle_start code={code}")
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        return BattleResult(type=0, success=False, response_cmd=cmd, fields=f,
                            error_code=ec, error=f"server error code={ec}")
    return BattleResult(type=0, success=False, response_cmd=cmd, fields=f,
                        error=f"unexpected response cmd 0x{cmd:04x}")


def parse_battle_result(cmd: int, body: bytes) -> BattleResult:
    """dungeon_battle_result_s2c {code#1, type#2, dungeon_id#3, reward_list#5}.

    code != 0 means the server rejected the reported result (anti-cheat /
    checkCheat forced loss, or invalid replay) — treated as failure, no rewards.
    """
    f = codec.walk_dict(body)
    if cmd == CMD_BATTLE_RESULT:
        code = _as_int(f.get(1))
        if code == 0:
            return BattleResult(
                type=_as_int(f.get(2)), success=True,
                dungeon_id=_as_int(f.get(3)),
                rewards=_collect_rewards(body, 5),
                response_cmd=cmd, fields=f)
        return BattleResult(type=_as_int(f.get(2)), success=False,
                            dungeon_id=_as_int(f.get(3)),
                            response_cmd=cmd, fields=f, error_code=code,
                            error=f"battle_result code={code} (anti-cheat?)")
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        return BattleResult(type=0, success=False, response_cmd=cmd, fields=f,
                            error_code=ec, error=f"server error code={ec}")
    return BattleResult(type=0, success=False, response_cmd=cmd, fields=f,
                        error=f"unexpected response cmd 0x{cmd:04x}")


def parse_sweep_result(cmd: int, body: bytes) -> SweepResult:
    """dungeon_sweep_s2c {dungeon_id#1, reward_list#2} — there is NO code field,
    so any sweep_s2c reply is success; a 0x0201 error (門票不足 / over-sweep) fails."""
    f = codec.walk_dict(body)
    if cmd == CMD_SWEEP:
        return SweepResult(
            type=0, success=True, dungeon_id=_as_int(f.get(1)),
            rewards=_collect_rewards(body, 2), response_cmd=cmd, fields=f)
    if cmd == CMD_ERROR:
        ec = _as_opt_int(f.get(1))
        return SweepResult(type=0, success=False, response_cmd=cmd, fields=f,
                           error_code=ec, error=f"server error code={ec}")
    return SweepResult(type=0, success=False, response_cmd=cmd, fields=f,
                       error=f"unexpected response cmd 0x{cmd:04x}")


# --- orchestrators ----------------------------------------------------------

def list_dungeons(client: WSGameClient, *,
                  timeout: float | None = None) -> list[Dungeon]:
    """Read every dungeon (all types) from dungeon_list (0x0E01)."""
    return parse_dungeon_list(client.call(CMD_LIST, b"", timeout=timeout))


def run_sweep(client: WSGameClient, *, type: int, dungeon_id: int,
              sweep_num: int, timeout: float | None = None) -> SweepResult:
    """掃蕩 (preferred): send dungeon_sweep {type, dungeon_id, sweep_num} and take
    the reward directly, bypassing the client-side battle + anti-cheat entirely.

    Reply is sweep_s2c (rewards) or 0x0201 (門票不足 / over-sweep / 次數用完).
    """
    cmd, body = client.call_for(
        CMD_SWEEP, build_sweep_body(type, dungeon_id, sweep_num),
        expect_cmds=(CMD_SWEEP, CMD_ERROR), timeout=timeout)
    result = parse_sweep_result(cmd, body)
    if result.success:
        logger.info("ws_token dungeon: sweep type=%d dungeon_id=%d num=%d -> rewards=%s",
                    type, dungeon_id, sweep_num, result.rewards)
    else:
        logger.warning("ws_token dungeon: sweep type=%d dungeon_id=%d failed code=%s",
                       type, dungeon_id, result.error_code)
    return SweepResult(
        type=type, success=result.success, dungeon_id=result.dungeon_id,
        rewards=result.rewards, response_cmd=result.response_cmd,
        fields=result.fields, error_code=result.error_code, error=result.error)


def run_battle(client: WSGameClient, *, type: int, level: int,
               timeout: float | None = None) -> BattleResult:
    """戰鬥路徑 (NOT live-verified): battle_start -> report result=0 -> rewards.

    Flow: send battle_start {type, level}; if the server opens it (code==0) we get
    a dungeon_id + random_seed, then send a *client-reported win*
    battle_result {type, dungeon_id, result=0, manual_operators=0, operators=[]}.

    # live-confirm: server 是否接受 client-reported result=0 (anti-cheat 風險).
    The server may replay random_seed + operators (here empty) and force a loss
    (battle_result code != 0) or reject the frame (0x0201). Prefer run_sweep.
    """
    start = parse_battle_start(*client.call_for(
        CMD_BATTLE_START, build_battle_start_body(type, level),
        expect_cmds=(CMD_BATTLE_START, CMD_ERROR), timeout=timeout))
    if not start.success:
        logger.warning("ws_token dungeon: battle_start type=%d level=%d failed code=%s",
                       type, level, start.error_code)
        return start

    cmd, body = client.call_for(
        CMD_BATTLE_RESULT,
        build_battle_result_body(type, start.dungeon_id, result=0,
                                 manual_operators=0, operators=[]),
        expect_cmds=(CMD_BATTLE_RESULT, CMD_ERROR), timeout=timeout)
    outcome = parse_battle_result(cmd, body)
    if outcome.success:
        logger.info("ws_token dungeon: battle type=%d level=%d dungeon_id=%d -> rewards=%s",
                    type, level, outcome.dungeon_id, outcome.rewards)
    else:
        logger.warning(
            "ws_token dungeon: battle_result type=%d dungeon_id=%d rejected code=%s "
            "(anti-cheat? client-reported result=0 未驗證)",
            type, start.dungeon_id, outcome.error_code)
    # carry through the started dungeon_id/seed even on rejection for diagnostics
    return BattleResult(
        type=type, success=outcome.success,
        dungeon_id=outcome.dungeon_id or start.dungeon_id,
        random_seed=start.random_seed, rewards=outcome.rewards,
        response_cmd=outcome.response_cmd, fields=outcome.fields,
        error_code=outcome.error_code, error=outcome.error)


# --- helpers ----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _as_opt_int(v) -> int | None:
    return int(v) if isinstance(v, int) else None
