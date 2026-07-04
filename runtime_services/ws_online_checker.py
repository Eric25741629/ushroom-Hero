"""Pure-WS backend for cross-device online-check (S0-wire).

A *checker* device normally answers "is the protected human player online?" by
running a protocol-only check through its live web_h5 (Playwright) session
(``web_session_service._run_checker_protocol_only``). That path needs a browser
session on the checker. This module lets a checker answer the same question with
nothing but its own login ticket: it builds a one-shot :class:`WSGameClient` from
the checker's captured creds, connects, and reads the target's presence off the
friend list (falling back to the guild member list).

:func:`check_via_ws` returns:
  - ``True``  — the target is online (do NOT run; protect the human).
  - ``False`` — the target is confirmed offline (safe to run).
  - ``None``  — undetermined (target not visible to this checker, connect/call
                failed, …). The caller MUST treat ``None`` as "let the requester
                retry / another checker claim it" and never 放行 on it.

The client is always closed (``finally``). Heavy WS deps are imported lazily so
importing this module stays cheap for unit tests; tests monkeypatch the
``_load_creds`` / ``_make_client`` hooks below.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from ws_token import online_guard

logger = logging.getLogger(__name__)

NowFn = Callable[[], float]


def _load_creds():
    """Lazy import of ws_token.creds.load_creds (keeps imports light)."""
    from ws_token.creds import load_creds
    return load_creds


def _make_client(creds, **kwargs):
    """Construct the WSGameClient. Indirected so tests can inject a fake."""
    from ws_token.client import WSGameClient
    return WSGameClient(creds, **kwargs)


def check_via_ws(
    checker_ip: str,
    target_pid: int,
    logger_obj: Any = logger,
    *,
    guild_id: Optional[int] = None,
    threshold_sec: int = 60,
    timeout: Optional[float] = 10.0,
    now: NowFn = time.time,
) -> Optional[bool]:
    """Resolve ``target_pid``'s online state over a one-shot WS login.

    Builds a client from ``checker_ip``'s creds, connects, and asks
    :func:`online_guard.friend_presence`. When the friend lookup is undetermined
    (``None``) and a ``guild_id`` is supplied, falls back to
    :func:`online_guard.is_role_online_in_guild`. Any exception →
    ``None`` (never放行). The client is always closed.
    """
    try:
        load_creds = _load_creds()
        creds = load_creds(checker_ip)
    except Exception as exc:  # noqa: BLE001
        logger_obj.warning(
            f"[{checker_ip}] ws online-check: 載入 creds 失敗: {exc}；視為未定 (None)")
        return None

    client = _make_client(creds)
    try:
        client.connect()
        presence = online_guard.friend_presence(
            client, int(target_pid), threshold_sec=int(threshold_sec),
            timeout=timeout, now=now)
        if presence is not None:
            return presence
        if guild_id:
            return online_guard.is_role_online_in_guild(
                client, int(guild_id), target_role_id=int(target_pid),
                timeout=timeout)
        logger_obj.info(
            f"[{checker_ip}] ws online-check: target {target_pid} 不在好友列表"
            f"且未設 guild_id，結果未定 (None)")
        return None
    except Exception as exc:  # noqa: BLE001 — never放行 on error
        logger_obj.warning(
            f"[{checker_ip}] ws online-check 例外: {exc}；視為未定 (None)")
        return None
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 — close must never mask the result
            logger_obj.debug(f"[{checker_ip}] ws online-check close raised",
                             exc_info=True)
