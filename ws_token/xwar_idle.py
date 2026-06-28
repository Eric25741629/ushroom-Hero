"""跨服戰 放置獎勵 (cross_war idle/掛機 reward) one-shot claim over pure WS.

The 跨服戰 (cross-server war, ActivityType=33) runs biweekly (Sat 10:00 → Sun
22:00). Its 放置獎勵 popup (the bottom-left chest on CrosswarMapSceneView) accrues
gold + a second currency by combat-power bracket, capped at 8h; over-cap is
discarded, so claiming no less often than every 8h avoids waste (this bot claims
every 4h, ``MIN_INTERVAL_S``, leaving headroom).

LIVE-decoded 2026-06-28 on 5560 (s1467, CDP 9225), cross-checked against the
client source (docs/game_client_sources/...index.966f5.js, ``CrossWarControl``).
Module 45 ``cross_war`` (cmd = 45*256 + sub):

  cross_war_get_idle_reward  0x2d04 (11524)  c2s {}  ->  s2c { new_last_time#1 }

The claim is server-authoritative with an EMPTY body — the in-game 領取 button
fires nothing but this frame, and the reply only carries the reset timestamp
(accrual is computed server-side from the stored last_time). No scene-enter /
map-load cmd is required on the connection. A REJECTION (event closed / not
joined) replies on 0x0201; a fully DORMANT event may send NO frame at all (the
``call_for`` times out) — both are treated as a benign skip, like rogue.

Open-window gate uses the generic Activity list (server-authoritative, no
hardcoded biweekly anchor → no drift):

  act_list  0x180c (6156)  c2s {}  ->  s2c { activities#1: repeated p_activity }
  p_activity { id#1, type#2, round#3, state#5, start_time#6, end_time#7, ... }

The cross-war row is ``type==33``; it is open iff ``state==2`` (ActivityState
Null=0/Preview=1/Open=2/EndShow=3).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ws_token import codec
from ws_token import state as ws_state
from ws_token.client import WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

# --- cmd ids (c2s/s2c share the same id; FAILURES reply on 0x0201) -----------
CMD_CLAIM = 0x2d04        # cross_war.cross_war_get_idle_reward (module 45; empty body)
CMD_ACT_LIST = 0x180c     # act.act_list (module 24; empty body) -> activity windows
CMD_ERROR = 0x0201        # error.error_info_s2c {error_code#1}

ACT_TYPE_CROSS_WAR = 33   # ActivityType.CrossWar
STATE_OPEN = 2            # ActivityState.Open (Null=0/Preview=1/Open=2/EndShow=3)

# Cadence: claim at most once per 4h (the accrual cap is 8h, so a 4h cadence keeps
# headroom and never overflows). Short probe timeout so a dormant event that sends
# no frame degrades fast instead of blocking the run.
MIN_INTERVAL_S: float = 4 * 3600
PROBE_TIMEOUT_S: float = 6.0

_LEDGER_KEY = "xwar_idle"

# Error codes (configErrorInfo, shared across modules) for nicer logs.
_ERR_REASONS = {90: "冷卻時間未到", 159: "次數不足", 173: "活動已結束"}


@dataclass(frozen=True)
class CrossWarWindow:
    """The cross-war row of act_list, or found=False if absent."""

    found: bool
    state: int = 0
    start_ts: int = 0
    end_ts: int = 0
    round: int = 0

    @property
    def is_open(self) -> bool:
        """True iff the cross-war activity is present and in the Open state."""
        return self.found and self.state == STATE_OPEN


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of cross_war_get_idle_reward (or its rejection)."""

    ok: bool
    new_last_time: int = 0
    error_code: Optional[int] = None
    response_cmd: int = 0


# --- parsers -----------------------------------------------------------------

def parse_act_list(body: bytes) -> CrossWarWindow:
    """act_list_s2c {activities#1: p_activity[]} -> the cross-war (type 33) window."""
    for fnum, val in codec.walk(bytes(body)):
        if fnum != 1 or not isinstance(val, (bytes, bytearray)):
            continue
        d = codec.walk_dict(bytes(val))
        if _as_int(d.get(2)) == ACT_TYPE_CROSS_WAR:
            return CrossWarWindow(
                found=True,
                state=_as_int(d.get(5)),
                start_ts=_as_int(d.get(6)),
                end_ts=_as_int(d.get(7)),
                round=_as_int(d.get(3)),
            )
    return CrossWarWindow(found=False)


def parse_claim(cmd: int, body: bytes) -> ClaimResult:
    """cross_war_get_idle_reward_s2c {new_last_time#1}; 0x0201 = rejection."""
    if cmd == CMD_CLAIM:
        return ClaimResult(ok=True,
                           new_last_time=_as_int(codec.walk_dict(bytes(body)).get(1)),
                           response_cmd=cmd)
    if cmd == CMD_ERROR:
        return ClaimResult(ok=False,
                           error_code=_as_int(codec.walk_dict(bytes(body)).get(1)),
                           response_cmd=cmd)
    return ClaimResult(ok=False, response_cmd=cmd)


# --- reads / mutates ---------------------------------------------------------

def read_window(client: WSGameClient, *, timeout: Optional[float] = None) -> CrossWarWindow:
    """Read act_list (empty body) and return the cross-war open-window state."""
    w = parse_act_list(client.call(CMD_ACT_LIST, b"", timeout=timeout))
    logger.info("ws_token xwar_idle: window found=%s state=%s start=%s end=%s",
                w.found, w.state, w.start_ts, w.end_ts)
    return w


def claim_idle(client: WSGameClient, *, timeout: Optional[float] = None) -> ClaimResult:
    """Claim the cross-war idle reward: send 0x2d04 (empty body).

    Replies on 0x2d04 (success, new_last_time) or 0x0201 (rejection).
    """
    cmd, body = client.call_for(
        CMD_CLAIM, b"", expect_cmds=(CMD_CLAIM, CMD_ERROR), timeout=timeout)
    r = parse_claim(cmd, body)
    if r.ok:
        logger.info("ws_token xwar_idle: claimed new_last_time=%s", r.new_last_time)
    else:
        logger.warning("ws_token xwar_idle: claim failed cmd=0x%04x code=%s %s",
                       cmd, r.error_code, _ERR_REASONS.get(r.error_code or -1, ""))
    return r


# --- cadence gate (4h interval + act_list open-window) -----------------------

def claim_if_due(
    client: WSGameClient, *, device: str, state_dir=None, now=None,
    min_interval_s: float = MIN_INTERVAL_S, timeout: float = PROBE_TIMEOUT_S,
) -> dict:
    """Claim the cross-war idle reward when due, else skip.

    Gate (all server-authoritative — no hardcoded biweekly date):
      1. throttle: skip if < ``min_interval_s`` since the last attempt (no socket
         traffic at all — keeps off-window chatter to one act_list read per 4h);
      2. window: read act_list (0x180c); skip unless cross-war is Open (state==2);
      3. claim: send 0x2d04; persist ``last_success_ts``/``last_new_time`` on ok.

    ``last_attempt_ts`` advances on every due check (success, rejection, or
    no-response) so the 4h throttle holds even while the event is closed. A
    timeout (dormant event sends no frame) is a benign skip. Returns a summary
    dict for the runner's RunReport.
    """
    from datetime import datetime
    now = datetime.now() if now is None else now
    kw = {"state_dir": state_dir} if state_dir is not None else {}

    st = ws_state.load_state(device, **kw)
    rec = dict(st.get(_LEDGER_KEY) or {})
    elapsed = now.timestamp() - _as_float(rec.get("last_attempt_ts"))
    if elapsed < min_interval_s:
        return {"claimed_run": False, "reason": "within interval",
                "next_in_s": round(min_interval_s - elapsed)}

    rec["last_attempt_ts"] = now.timestamp()

    def _persist() -> None:
        st[_LEDGER_KEY] = rec
        ws_state.save_state(device, st, **kw)

    try:
        window = read_window(client, timeout=timeout)
    except WSTimeoutError:
        logger.info("ws_token xwar_idle: %s act_list 無回應，跳過", device)
        _persist()
        return {"claimed_run": False, "reason": "act_list no response"}

    if not window.is_open:
        _persist()
        return {"claimed_run": False,
                "reason": f"event not open (state={window.state})",
                "state": window.state}

    try:
        r = claim_idle(client, timeout=timeout)
    except WSTimeoutError:
        logger.info("ws_token xwar_idle: %s claim 無回應（事件休眠），跳過", device)
        _persist()
        return {"claimed_run": False, "reason": "claim no response"}

    if r.ok:
        rec["last_success_ts"] = now.timestamp()
        rec["last_new_time"] = r.new_last_time
    _persist()
    return {"claimed_run": True, "ok": r.ok, "new_last_time": r.new_last_time,
            "error_code": r.error_code}


# --- helpers -----------------------------------------------------------------

def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0


def _as_float(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0
