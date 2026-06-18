"""Dashboard 持久純 WS 連線的 registry + Flask 端點。

讓 dashboard 不開瀏覽器、用既有的可重用 ticket 直接建立並維持一條純 WS 連線
（每個 device 一條）。前端視窗開著時持續 ping 保活；偵測異地登入(cmd 259)被踢；
閒置(>90s 無 ping)或主動關窗時自動回收連線並恢復該機的 bot loop。

接線（blueprint 註冊、前端輪詢、routes_inventory 共用 client）由外層之後處理；
本模組只提供 registry 與三個 Flask 端點。

# ponytail: 全機暫停 + 90s sweeper；若要更細粒度再說
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from flask import Blueprint, jsonify

import bot_state
from control_panel.shared.auth import _fly_pet_auth
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

logger = logging.getLogger(__name__)

# --- 常數 -------------------------------------------------------------------

_IDLE_TIMEOUT_SEC = 90.0   # 超過此秒數沒收到 ping 視為閒置 → sweeper 回收
_SWEEP_INTERVAL_SEC = 15.0  # sweeper 掃描週期


@dataclass
class _Session:
    """單一 device 的持久 WS 連線狀態。"""

    client: WSGameClient
    last_seen: float
    kicked: bool = False
    kick_reason: Optional[int] = None


# 模組級狀態：以 `_lock` 保護 `_sessions` 的所有讀寫。
_lock = threading.Lock()
_sessions: dict[str, _Session] = {}

# sweeper thread 只啟動一次（模組 import 時），用旗標 + 鎖避免重複啟動。
_sweeper_started = False
_sweeper_lock = threading.Lock()


# --- registry API -----------------------------------------------------------

def ensure(device: str) -> dict:
    """確保 ``device`` 有一條 live 純 WS 連線；沒有就建立。

    - 已有且仍 running：更新 last_seen，直接回報已連線。
    - 無 / 已死 / 被踢：清掉舊的（先記住是否曾暫停 bot，避免重複 pause），
      重新登入；連線成功才暫停該機 bot loop 並存入 registry。
    - 例外：回 error，並確保沒有殘留半開連線或未配對的 pause。
    """
    _ensure_sweeper()

    with _lock:
        existing = _sessions.get(device)
        if existing is not None and existing.client.is_running():
            existing.last_seen = time.time()
            return {"status": "ok", "connected": True, "kicked": False}

        # 舊 session 已死/被踢：先記住「之前是否已暫停過 bot」，再清掉。
        # 若之前已暫停（existing 存在代表 ensure 成功過 → 必定已 pause），
        # 等下重連成功會再 pause 一次，這裡先不 unpause，交給重連路徑接手；
        # 但若重連失敗，需把這個既有暫停還回去（見下方 except）。
        had_existing = existing is not None
        if existing is not None:
            _close_session_locked(device, existing)

        try:
            creds = load_creds(device)
            client = WSGameClient(creds)
            client.set_kick_handler(_make_kick_handler(device))
            client.connect()
        except Exception as exc:  # noqa: BLE001 - 對前端統一回報錯誤訊息
            logger.warning("ws_session ensure 失敗 device=%s: %s", device, exc)
            # 確保沒有殘留半開連線。
            try:
                client.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            # 若先前已有一條（已暫停 bot）的 session 被我們清掉，這次重連又失敗，
            # 必須把 bot loop 還原，避免裝置永久卡在暫停。
            if had_existing:
                _safe_set_pause(device, False)
            return {"status": "error", "message": str(exc)}

        # 連線成功才暫停 bot loop，並登錄 session。
        _safe_set_pause(device, True)
        _sessions[device] = _Session(client=client, last_seen=time.time())
        logger.info("ws_session 已建立持久連線 device=%s", device)
        return {"status": "ok", "connected": True, "kicked": False}


def ping(device: str) -> dict:
    """前端保活心跳：更新 last_seen 並回報目前連線/被踢狀態。

    若 session 已被踢或不再 running，這裡**不**恢復 bot loop（交給 sweeper /
    disconnect 統一回收），但保留 kicked 狀態讓前端讀得到。
    """
    with _lock:
        session = _sessions.get(device)
        if session is None:
            return {"alive": False, "kicked": False, "kick_reason": None}
        session.last_seen = time.time()
        return {
            "alive": session.client.is_running(),
            "kicked": session.kicked,
            "kick_reason": session.kick_reason,
        }


def disconnect(device: str) -> dict:
    """主動關閉 ``device`` 的持久連線並恢復 bot loop。重複呼叫安全。"""
    with _lock:
        session = _sessions.pop(device, None)
        if session is not None:
            try:
                session.client.close()
            except Exception:
                logger.exception("ws_session disconnect close 失敗 device=%s", device)
        # 不論 registry 中是否還有 session，都確保 bot loop 已恢復（冪等）。
        _safe_set_pause(device, False)
    return {"status": "ok"}


def get_client(device: str) -> Optional[WSGameClient]:
    """供 routes_inventory 讀倉庫共用 live client。

    僅在連線仍 running 時回傳 client（否則 None），並順手更新 last_seen 保活。
    """
    with _lock:
        session = _sessions.get(device)
        if session is None or not session.client.is_running():
            return None
        session.last_seen = time.time()
        return session.client


# --- 內部 helper（多數需在持有 `_lock` 時呼叫） -----------------------------

def _make_kick_handler(device):
    """產生綁定 ``device`` 的 kick closure。

    被踢(cmd 259)時於 reader thread 觸發一次：標記該 device 的 session 為 kicked。
    kick_reason 目前無法取得：WSGameClient 不對外公開被踢 reason（cmd 259 body 的
    reason 只在 client 內部 log，callback 無參數、也無 getter），故一律填 None。
    """

    def _on_kick() -> None:
        with _lock:
            session = _sessions.get(device)
            if session is not None:
                session.kicked = True
                session.kick_reason = None  # WSGameClient 未公開 reason
        logger.warning("ws_session device=%s 異地登入被踢 (cmd 259)", device)

    return _on_kick


def _close_session_locked(device: str, session: _Session) -> None:
    """關閉一條 session 的 client 並從 registry 移除（需持有 `_lock`）。

    僅負責 client 與 registry；bot loop 的恢復由呼叫端依情境決定。
    """
    try:
        session.client.close()
    except Exception:
        logger.exception("ws_session 關閉舊連線失敗 device=%s", device)
    _sessions.pop(device, None)


def _safe_set_pause(device: str, paused: bool) -> None:
    """包一層 try/except 的 bot_state.set_pause，避免影響連線生命週期。"""
    try:
        bot_state.set_pause(device, paused)
    except Exception:
        logger.exception("ws_session set_pause(%s, %s) 失敗", device, paused)


# --- sweeper thread ---------------------------------------------------------

def _ensure_sweeper() -> None:
    """確保 sweeper daemon thread 只啟動一次。"""
    global _sweeper_started
    if _sweeper_started:
        return
    with _sweeper_lock:
        if _sweeper_started:
            return
        thread = threading.Thread(
            target=_sweep_loop, name="ws_session-sweeper", daemon=True
        )
        thread.start()
        _sweeper_started = True
        logger.info("ws_session sweeper 已啟動 (每 %.0fs 掃描)", _SWEEP_INTERVAL_SEC)


def _sweep_loop() -> None:
    """每 15 秒掃 registry，回收閒置(>90s)或已死的連線。

    對每個 session 的清理都包 try/except，單一錯誤不可弄垮整條 thread。
    """
    while True:
        time.sleep(_SWEEP_INTERVAL_SEC)
        try:
            _sweep_once()
        except Exception:
            logger.exception("ws_session sweeper 迴圈異常 (已吞掉，繼續)")


def _sweep_once() -> None:
    """掃描一輪：找出該回收的 device，逐一回收。"""
    now = time.time()
    with _lock:
        stale = [
            device
            for device, session in _sessions.items()
            if (now - session.last_seen) > _IDLE_TIMEOUT_SEC
            or not session.client.is_running()
        ]
        for device in stale:
            session = _sessions.get(device)
            if session is None:
                continue
            try:
                _close_session_locked(device, session)
                _safe_set_pause(device, False)
                logger.info("ws_session sweeper 已回收閒置/失效連線 device=%s", device)
            except Exception:
                logger.exception("ws_session sweeper 回收 device=%s 失敗", device)


# import 時即啟動 sweeper（端點被呼叫前就開始保活回收）。
_ensure_sweeper()


# --- Flask blueprint --------------------------------------------------------

bp = Blueprint("ws_session", __name__)


@bp.route("/api/ws_session/<ip>/connect", methods=["POST"])
@_fly_pet_auth
def connect_endpoint(ip: str):
    """建立 / 復用 ``ip`` 的持久 WS 連線。error 時回 502。"""
    result = ensure(ip)
    status_code = 502 if result.get("status") == "error" else 200
    return jsonify(result), status_code


@bp.route("/api/ws_session/<ip>/ping", methods=["POST"])
@_fly_pet_auth
def ping_endpoint(ip: str):
    """前端保活心跳。"""
    return jsonify(ping(ip))


@bp.route("/api/ws_session/<ip>/disconnect", methods=["POST"])
@_fly_pet_auth
def disconnect_endpoint(ip: str):
    """主動關閉 ``ip`` 的持久連線並恢復 bot loop。"""
    return jsonify(disconnect(ip))
