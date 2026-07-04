"""郵件/信箱 (mail) task over a logged-in WSGameClient — list + claim attachments.

Pure WS, LIVE-verified (emulator-5554 2026-06-14; see docs/protocol/MAIL_PROTO_SCHEMA.md).
mail module = 21; c2s and s2c share the same cmd id; failures reply on 0x0201.

  mail_list  5377 (0x1501)  c2s { mail_id#1:uint64 }   ⚠ mail_id REQUIRED (空 body 回空)
                            s2c { mail_list#1:p_mail[] }
  mail_claim 5380 (0x1504)  c2s { mail_id#1:uint64, type#2:uint32 }  mail_id=0 = 一鍵全領
                            s2c { claim_list#1:uint64[], goods_list#2:p_reward[] }
                                 (無可領時 server 回空 body，不報錯 — idempotent)

  p_mail { id#1:uint64, cfg_id#2, ?#3, title#5, content#6, send_time#7, exp_time#8,
           is_read#9, is_attach#10, goods_list#11:p_reward[] }
           is_attach==1 → 有未領附件。

容量「滿」偵測（武魂 / 神器附魔寶石）：client 端 **無硬 cap**（artifact_gem_max_num=500 是
dead config，帳號實際 2515 顆未被擋；武魂走 upgrade/compose 消耗、無上限概念）。所以這裡只
提供「讀現量 + best-effort 門檻檢查」：

  artifact_gem_info 13569 (0x3501)  c2s {}  s2c { gem_list#2:p_gem[] }  → gem 數 = len(f2)
  skill_list         2049 (0x801)   c2s {}  s2c { active#1:[], owned#2:p_skill_item[] }
                                            → 武魂道具種數 = len(f2)

真正的溢位防護仍靠 server（claim 無可領回空、不報錯）；門檻命中只 log 警告、不阻擋領取。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

# --- cmd ids (mail module 21; c2s and s2c share the id; failures on 0x0201) --
CMD_MAIL_LIST = 5377       # 0x1501 mail.mail_list_c2s/s2c
CMD_MAIL_CLAIM = 5380      # 0x1504 mail.mail_claim_c2s/s2c (mail_id=0 = claim all)
CMD_MAIL_READ = 5379       # 0x1503 mail.mail_read_c2s/s2c
CMD_MAIL_DELETE = 5381     # 0x1505 mail.mail_delete_c2s/s2c
CMD_ERROR = 0x0201         # error.error_info_s2c {error_code#1}

# capacity reads (best-effort "full" detection; client has no hard cap)
CMD_GEM_INFO = 0x3501      # 13569 artifact_gem.artifact_gem_info_s2c
CMD_SKILL_LIST = 0x0801    # 2049 skill.skill_list_s2c

MAIL_ID_ALL = 0            # mail_id=0 = 全部 (list-all / claim-all / read-all)
CLAIM_TYPE_DEFAULT = 0     # type 一律 0 (遊戲內 btnGetAll = reqGetMailGood(0,0))

# 神器附魔寶石: artifact_gem_max_num=500 是 client dead config（從不讀、非硬牆）；
# 只作 best-effort 門檻參考值。武魂無任何 cap config。
DEFAULT_GEM_CAP = 500


@dataclass(frozen=True)
class Mail:
    """One p_mail entry from mail_list_s2c (field 1, repeated)."""

    mail_id: int                     # id #1
    cfg_id: int                      # #2 -> configMail key
    send_time: int                   # #7 unix seconds
    exp_time: int                    # #8 unix seconds
    is_read: bool                    # #9
    is_attach: bool                  # #10 — True = has unclaimed attachment
    goods_list: list[tuple[int, int]] = field(default_factory=list)  # #11 (item_id, num)
    raw: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class ClaimResult:
    """Result of a mail_claim (single or claim-all)."""

    success: bool                    # True iff reply arrived on CMD_MAIL_CLAIM
    response_cmd: int
    claimed_ids: list[int] = field(default_factory=list)   # claim_list #1
    goods_list: list[tuple[int, int]] = field(default_factory=list)  # goods_list #2
    error_code: Optional[int] = None
    error: Optional[str] = None


# --- parsers ----------------------------------------------------------------

def _rewards(body: bytes, fnum: int) -> list[tuple[int, int]]:
    """Decode every repeated p_reward at ``fnum`` into (item_id, num) pairs."""
    out: list[tuple[int, int]] = []
    for fn, v in codec.walk(body):
        if fn == fnum and isinstance(v, (bytes, bytearray)):
            d = codec.walk_dict(bytes(v))
            out.append((_as_int(d.get(1)), _as_int(d.get(2))))
    return out


def _parse_mail(entry: bytes) -> Mail:
    d = codec.walk_dict(entry)
    return Mail(
        mail_id=_as_int(d.get(1)),
        cfg_id=_as_int(d.get(2)),
        send_time=_as_int(d.get(7)),
        exp_time=_as_int(d.get(8)),
        is_read=bool(_as_int(d.get(9))),
        is_attach=bool(_as_int(d.get(10))),
        goods_list=_rewards(entry, 11),
        raw=d,
    )


def parse_mail_list(body: bytes) -> list[Mail]:
    """mail_list_s2c: every mail is field 1 (repeated p_mail)."""
    return [_parse_mail(bytes(v)) for fnum, v in codec.walk(body)
            if fnum == 1 and isinstance(v, (bytes, bytearray))]


def parse_claim_result(cmd: int, body: bytes) -> ClaimResult:
    """mail_claim_s2c {claim_list#1:uint64[], goods_list#2:p_reward[]} or 0x0201."""
    if cmd == CMD_MAIL_CLAIM:
        claimed = [int(v) for fn, v in codec.walk(body)
                   if fn == 1 and isinstance(v, int)]
        goods = _rewards(body, 2)
        return ClaimResult(True, cmd, claimed_ids=claimed, goods_list=goods)
    if cmd == CMD_ERROR:
        ec = codec.walk_dict(body).get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return ClaimResult(False, cmd, error_code=ec, error=f"server error code={ec}")
    return ClaimResult(False, cmd, error=f"unexpected response cmd 0x{cmd:04x}")


# --- body builders ----------------------------------------------------------

def build_list_body(mail_id: int = MAIL_ID_ALL) -> bytes:
    """mail_list_c2s {mail_id#1}. ⚠ mail_id is REQUIRED — an empty body returns empty."""
    return codec.pb_uint(1, mail_id)


def build_claim_body(mail_id: int = MAIL_ID_ALL,
                     type_: int = CLAIM_TYPE_DEFAULT) -> bytes:
    """mail_claim_c2s {mail_id#1, type#2}. mail_id=0 = 一鍵領取全部."""
    return codec.pb_uint(1, mail_id) + codec.pb_uint(2, type_)


# --- reads ------------------------------------------------------------------

def list_mail(client: WSGameClient, *, mail_id: int = MAIL_ID_ALL,
              timeout: Optional[float] = None) -> list[Mail]:
    """Send mail_list_c2s {mail_id} and parse the mail list."""
    body = client.call(CMD_MAIL_LIST, build_list_body(mail_id), timeout=timeout)
    return parse_mail_list(body)


def claimable_mail(mails: list[Mail]) -> list[Mail]:
    """Subset with an unclaimed attachment (is_attach == True)."""
    return [m for m in mails if m.is_attach]


# --- mutates (call_for with 0x0201; never plain call) -----------------------

def claim_mail(client: WSGameClient, mail_id: int, *,
               type_: int = CLAIM_TYPE_DEFAULT,
               timeout: Optional[float] = None) -> ClaimResult:
    """Claim ONE mail's attachment (mail_id != 0). Reply on 5380 OR 0x0201."""
    cmd, body = client.call_for(
        CMD_MAIL_CLAIM, build_claim_body(mail_id, type_),
        expect_cmds=(CMD_MAIL_CLAIM, CMD_ERROR), timeout=timeout)
    res = parse_claim_result(cmd, body)
    if res.success:
        logger.info("ws_token mail: claimed mail_id=%s claimed_ids=%s goods=%s",
                    mail_id, res.claimed_ids, res.goods_list)
    else:
        logger.info("ws_token mail: claim mail_id=%s rejected cmd=0x%04x code=%s",
                    mail_id, cmd, res.error_code)
    return res


def claim_all_attachments(client: WSGameClient, *,
                          type_: int = CLAIM_TYPE_DEFAULT,
                          timeout: Optional[float] = None) -> ClaimResult:
    """一鍵領取全部附件: mail_claim_c2s {mail_id:0, type:0}.

    This is exactly what the in-game 一鍵領取 button sends. The server claims
    every mail with an unclaimed attachment in one shot and returns
    {claim_list, goods_list}; when nothing is claimable it returns an EMPTY body
    on cmd 5380 (no 0x0201, idempotent — live-verified). So this is safe to run
    unconditionally on every wake.
    """
    cmd, body = client.call_for(
        CMD_MAIL_CLAIM, build_claim_body(MAIL_ID_ALL, type_),
        expect_cmds=(CMD_MAIL_CLAIM, CMD_ERROR), timeout=timeout)
    res = parse_claim_result(cmd, body)
    if res.success:
        logger.info("ws_token mail: claim-all ok claimed=%d goods=%s",
                    len(res.claimed_ids), res.goods_list)
    else:
        logger.info("ws_token mail: claim-all rejected cmd=0x%04x code=%s",
                    cmd, res.error_code)
    return res


# --- capacity helpers (best-effort "full" detection; client has no hard cap) -

def read_gem_count(client: WSGameClient, *,
                   timeout: Optional[float] = None) -> Optional[int]:
    """神器附魔寶石 current count = len(artifact_gem_info_s2c.gem_list (field 2)).

    Returns None if the read times out (the artifact_gem reply can be large; a
    dormant module just times out — never crash the mail task on it).
    """
    try:
        body = client.call(CMD_GEM_INFO, b"", timeout=timeout)
    except WSTimeoutError:
        logger.info("ws_token mail: gem info (0x3501) no reply — gem count unknown")
        return None
    return sum(1 for fn, v in codec.walk(body)
               if fn == 2 and isinstance(v, (bytes, bytearray)))


def read_skill_count(client: WSGameClient, *,
                     timeout: Optional[float] = None) -> Optional[int]:
    """武魂 current count = number of owned skill-item kinds (skill_list field 2).

    skill_list_s2c f1 = active skills, f2 = owned skill-items {item_id#1, count#2}.
    Returns None on timeout.
    """
    try:
        body = client.call(CMD_SKILL_LIST, b"", timeout=timeout)
    except WSTimeoutError:
        logger.info("ws_token mail: skill list (0x801) no reply — skill count unknown")
        return None
    return sum(1 for fn, v in codec.walk(body)
               if fn == 2 and isinstance(v, (bytes, bytearray)))


def is_gem_full(client: WSGameClient, *, threshold: int = DEFAULT_GEM_CAP,
                timeout: Optional[float] = None) -> Optional[bool]:
    """Best-effort: True iff gem count >= ``threshold``.

    NOTE: client has no hard gem cap (artifact_gem_max_num=500 is a dead config —
    real accounts hold far more). This is an advisory threshold check only;
    returns None when the count is unknown (timeout).
    """
    count = read_gem_count(client, timeout=timeout)
    if count is None:
        return None
    full = count >= threshold
    logger.info("ws_token mail: gem count=%d threshold=%d full=%s", count, threshold, full)
    return full


def is_skill_full(client: WSGameClient, *, threshold: int,
                  timeout: Optional[float] = None) -> Optional[bool]:
    """Best-effort: True iff 武魂 owned-item kinds >= ``threshold``.

    NOTE: 武魂 has NO cap config in the client (skill items are consumed by
    upgrade/compose). ``threshold`` is caller-supplied advisory only; returns
    None when the count is unknown (timeout).
    """
    count = read_skill_count(client, timeout=timeout)
    if count is None:
        return None
    full = count >= threshold
    logger.info("ws_token mail: skill count=%d threshold=%d full=%s",
                count, threshold, full)
    return full


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0
