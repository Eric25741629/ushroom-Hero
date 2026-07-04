"""每日自動領郵件附件 — once-daily gate for the ws_token runner.

Pure-WS, modelled on ws_token.runner._run_rogue (date gate via ws_state). Runs
the in-game 一鍵領取全部附件 (mail_claim_c2s {mail_id:0, type:0}) at most once per
calendar day; repeated hourly wakes on the same day skip without sending. The
date marker is written ONLY on a successful claim reply, so a transient failure
(0x0201 / timeout) retries on the next wake.

Capacity ("武魂滿 / 神器附魔寶石滿") is best-effort only: the client has NO hard
cap (artifact_gem_max_num=500 is a dead config; 武魂 has none). So when a
configured threshold is hit we LOG a warning but STILL claim — missing earned
mail is worse than a no-op claim, and the server is the authoritative end on
overflow. See docs/protocol/MAIL_PROTO_SCHEMA.md.

This module owns ONLY the gate + capacity advisory; the WS calls live in
ws_token.mail. The runner wires it as the ``mail`` step.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ws_token import mail
from ws_token import state as ws_state

logger = logging.getLogger(__name__)

STATE_KEY = "mail"


def _today(now: Optional[datetime]) -> tuple[datetime, str]:
    now = datetime.now() if now is None else now
    return now, now.strftime("%Y-%m-%d")


def _capacity_advisory(client, *, gem_threshold: Optional[int],
                       skill_threshold: Optional[int]) -> dict:
    """Read gem / 武魂 counts and warn when a configured threshold is hit.

    Returns ``{gem_count, gem_full, skill_count, skill_full}`` (values are None
    when not checked or the read timed out). NEVER blocks the claim — see module
    docstring. A failing read is swallowed (advisory only).
    """
    out: dict = {"gem_count": None, "gem_full": None,
                 "skill_count": None, "skill_full": None}
    if gem_threshold:
        try:
            out["gem_count"] = mail.read_gem_count(client, timeout=6.0)
            if out["gem_count"] is not None:
                out["gem_full"] = out["gem_count"] >= gem_threshold
                if out["gem_full"]:
                    logger.warning(
                        "ws_token mail: 神器附魔寶石 count=%d >= threshold=%d "
                        "(advisory; client has no hard cap — claiming anyway)",
                        out["gem_count"], gem_threshold)
        except Exception:  # noqa: BLE001 — advisory read must not block claim
            logger.debug("ws_token mail: gem count read failed", exc_info=True)
    if skill_threshold:
        try:
            out["skill_count"] = mail.read_skill_count(client, timeout=6.0)
            if out["skill_count"] is not None:
                out["skill_full"] = out["skill_count"] >= skill_threshold
                if out["skill_full"]:
                    logger.warning(
                        "ws_token mail: 武魂 count=%d >= threshold=%d "
                        "(advisory; client has no cap — claiming anyway)",
                        out["skill_count"], skill_threshold)
        except Exception:  # noqa: BLE001
            logger.debug("ws_token mail: skill count read failed", exc_info=True)
    return out


def run_mail_claim_if_due(client, *, device: str,
                          gem_threshold: Optional[int] = None,
                          skill_threshold: Optional[int] = None,
                          state_dir=None, now: Optional[datetime] = None) -> dict:
    """每日一次領取全部郵件附件 (once-daily gated, free).

    Skips (without sending) when already claimed today. On a due wake it reads an
    optional capacity advisory (gem / 武魂 thresholds; never blocks), then sends
    the claim-all. Persists ``ws_state/<device>.json`` ``{"mail":{"last_date"}}``
    only on a successful reply.

    Returns ``{claimed_run, reason?}`` when skipped, else
    ``{claimed_run, success, claimed_count, goods, capacity}``.
    """
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    now, today = _today(now)
    st = ws_state.load_state(device, **kw)
    if (st.get(STATE_KEY) or {}).get("last_date") == today:
        return {"claimed_run": False, "reason": f"already claimed {today}"}

    capacity = _capacity_advisory(client, gem_threshold=gem_threshold,
                                  skill_threshold=skill_threshold)
    res = mail.claim_all_attachments(client)
    if res.success:
        st[STATE_KEY] = {"last_date": today, "last_ts": now.timestamp()}
        ws_state.save_state(device, st, **kw)
    return {
        "claimed_run": True,
        "success": res.success,
        "claimed_count": len(res.claimed_ids),
        "goods": res.goods_list,
        "error_code": res.error_code,
        "capacity": capacity,
    }
