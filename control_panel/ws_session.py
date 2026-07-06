"""Dashboard 持久純 WS 連線的生命週期管理 + Flask 端點。

讓 dashboard 不開瀏覽器、用既有的可重用 ticket 直接建立並維持一條純 WS 連線
（每個 device 一條）。前端視窗開著時持續 ping 保活；偵測異地登入(cmd 259)被踢；
閒置(>90s 無 ping)或主動關窗時自動回收連線。

**佔用登記 + bot pause 已外包給 `runtime_services.session_registry`**（Phase 2）：
本模組只管 WSGameClient 連線生命週期（建立 / sweeper / ping / 回收）；
「哪台裝置帳號此刻被誰佔用」「借用時暫停 bot loop」由 registry 統一負責，
消除舊版散落此處的 `had_existing → 重連失敗還原 pause` 易錯邏輯與 `is_active` 盲區。

# ponytail: 全機暫停 + 90s sweeper；若要更細粒度再說
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from flask import Blueprint, jsonify

from control_panel.shared.auth import _fly_pet_auth
from runtime_services import session_registry as registry
from runtime_services.session_registry import Channel, Owner
from ws_token.client import WSGameClient
from ws_token.creds import load_creds

logger = logging.getLogger(__name__)

# --- 常數 -------------------------------------------------------------------

_IDLE_TIMEOUT_SEC = 90.0   # 超過此秒數沒收到 ping 視為閒置 → sweeper 回收
_SWEEP_INTERVAL_SEC = 15.0  # sweeper 掃描週期


@dataclass
class _Session:
    """單一 device 的持久 WS 連線狀態。owner/role_id 供回收時向 registry 釋放。"""

    client: WSGameClient
    last_seen: float
    owner: Owner
    role_id: Optional[int] = None
    kicked: bool = False
    kick_reason: Optional[int] = None


# 模組級狀態：以 `_lock` 保護 `_sessions` 的所有讀寫。
_lock = threading.Lock()
_sessions: dict[str, _Session] = {}

# sweeper thread 只啟動一次（模組 import 時），用旗標 + 鎖避免重複啟動。
_sweeper_started = False
_sweeper_lock = threading.Lock()


# --- 連線管理 API -----------------------------------------------------------

def ensure(device: str, *, owner: Owner = Owner.TOOL,
           preempt: bool = False, check_wake: bool = False) -> dict:
    """確保 ``device`` 有一條 live 純 WS 連線；沒有就建立。

    佔用權先向 registry `acquire`（原子判定 + 借用型自動 pause bot loop），**成功才連線**
    （先拿權再連，避免連了才發現要退）。

    ``check_wake=True``（借用型呼叫者如坐騎追蹤用）：acquire 對空閒裝置額外做即將喚醒/空窗
    保守閘門，判定不安全（即將自我喚醒 / OFFLINE 空窗 / 無 state row）→ 回
    ``{"status": "skip"}``，呼叫端換下一台候選。判定在 registry 鎖內與佔用登記原子完成
    （消 TOCTOU）。

    回傳：
      - ``{"status": "ok", ...}``        已連線 / 新建成功
      - ``{"status": "conflict", ...}``  帳號被別的 owner 佔用且不允許搶佔（不建立連線）
      - ``{"status": "skip", ...}``      裝置即將自我喚醒 / 空窗，暫不借用（不建立連線）
      - ``{"status": "error", ...}``     無 creds / 連線失敗 / 受保護帳號
    """
    _ensure_sweeper()

    with _lock:
        existing = _sessions.get(device)
        if existing is not None and existing.client.is_running():
            existing.last_seen = time.time()
            # 續租（冪等）避免 lease 過期被 sweeper/搶佔誤收。
            registry.acquire(device, existing.owner, Channel.WS,
                             label="工具", role_id=existing.role_id)
            return {"status": "ok", "connected": True, "kicked": False}

        # 舊 session 已死/被踢：關連線並釋放它的 lease（恢復 bot loop）。
        if existing is not None:
            _close_session_locked(device, existing)
            registry.release(device, existing.owner)

        # 先讀 creds（拿 role_id 做保護比對；無 creds 直接回錯，不佔 lease）。
        try:
            creds = load_creds(device)
        except FileNotFoundError:
            # 無 captured creds 時 load_creds 的訊息含絕對路徑 + 登入指令；對前端只回通用提示。
            logger.warning("ws_session ensure 無 creds device=%s", device)
            return {"status": "error",
                    "message": "此裝置尚未連線（連線詳情見伺服器日誌）"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("ws_session ensure 讀 creds 失敗 device=%s: %s", device, exc)
            return {"status": "error", "message": str(exc)}

        role_id = int(getattr(creds, "role_id", 0)) or None

        # 先取得佔用權（借用型 owner 成功即 pause bot loop）。
        result = registry.acquire(device, owner, Channel.WS, label="工具",
                                  role_id=role_id, preempt=preempt,
                                  check_wake=check_wake)
        if not result.ok:
            if result.reason == "protected":
                logger.warning("ws_session ensure 拒絕受保護帳號 device=%s", device)
                return {"status": "error", "message": "此帳號受保護，不可由工具連線"}
            if result.reason == "about_to_wake":
                # 借用型 check_wake 閘門:即將自我喚醒 / OFFLINE 空窗 → 呼叫端換下一台候選。
                logger.info("ws_session ensure 裝置即將喚醒/空窗,暫不借用 device=%s", device)
                return {"status": "skip",
                        "message": "此裝置即將自我喚醒，暫不借用"}
            conflict = result.conflict
            owner_val = conflict.owner.value if conflict else "其他"
            label = conflict.label if conflict else ""
            logger.info("ws_session ensure 佔用衝突 device=%s 現任=%s", device, owner_val)
            return {
                "status": "conflict",
                "message": f"此帳號目前在線中（{owner_val}{'：' + label if label else ''}）",
                "conflict": {"owner": owner_val, "label": label},
            }

        # 拿到權才連線。連線失敗要釋放 lease（registry 會恢復 bot loop）。
        client: Optional[WSGameClient] = None
        try:
            client = WSGameClient(creds)
            client.set_kick_handler(_make_kick_handler(device))
            client.connect()
        except Exception as exc:  # noqa: BLE001 - 對前端統一回報錯誤訊息
            logger.warning("ws_session ensure 連線失敗 device=%s: %s", device, exc)
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            registry.release(device, owner)
            return {"status": "error", "message": str(exc)}

        _sessions[device] = _Session(client=client, last_seen=time.time(),
                                     owner=owner, role_id=role_id)
        logger.info("ws_session 已建立持久連線 device=%s owner=%s", device, owner.value)
        return {"status": "ok", "connected": True, "kicked": False}


def ping(device: str) -> dict:
    """前端保活心跳：更新 last_seen 並回報目前連線/被踢狀態。

    若 session 已被踢或不再 running，這裡**不**回收（交給 sweeper / disconnect 統一
    釋放 lease），但保留 kicked 狀態讓前端讀得到。
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
    """主動關閉 ``device`` 的持久連線並釋放 registry lease。重複呼叫安全。"""
    with _lock:
        session = _sessions.pop(device, None)
        owner = session.owner if session is not None else Owner.TOOL
        if session is not None:
            try:
                session.client.close()
            except Exception:
                logger.exception("ws_session disconnect close 失敗 device=%s", device)
        # 釋放 lease（registry 恢復 bot loop）。無 session 時以預設 TOOL owner
        # 冪等釋放（release 對 owner 不符者無動作）。
        registry.release(device, owner)
    return {"status": "ok"}


def get_client(device: str) -> Optional[WSGameClient]:
    """供 routes_inventory 等讀倉庫共用 live client。

    僅在連線仍 running 時回傳 client（否則 None），並順手更新 last_seen 保活。
    """
    with _lock:
        session = _sessions.get(device)
        if session is None or not session.client.is_running():
            return None
        session.last_seen = time.time()
        return session.client


def is_active(device: str) -> bool:
    """``device`` 帳號此刻是否被任何 owner 佔用（無 keep-alive 副作用）。

    改讀 registry（Phase 2）：涵蓋**全 owner**——不只 dashboard 工具與坐騎追蹤建立的
    純 WS，還包含 wake-cycle 的 SCHEDULER、在線監控等（後續 phase 接線後）。這正是
    喚醒/借用互斥閘門需要的：舊版只看 `_sessions` 有盲區（好友清單 presence 也看不到
    純 WS session），改讀 registry 消除盲區。刻意不更新任何 last_seen。
    """
    return registry.peek(device) is not None


# --- 內部 helper（多數需在持有 `_lock` 時呼叫） -----------------------------

def _make_kick_handler(device):
    """產生綁定 ``device`` 的 kick closure。

    被踢(cmd 259)時於 reader thread 觸發一次：標記該 device 的 session 為 kicked。
    lease 的回收交給 sweeper（偵測 not is_running() → close + release）。
    kick_reason 目前無法取得（WSGameClient 未對外公開被踢 reason），一律填 None。
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
    """關閉一條 session 的 client 並從 `_sessions` 移除（需持有 `_lock`）。

    僅負責 client 與 `_sessions`；registry lease 的釋放由呼叫端負責。
    """
    try:
        session.client.close()
    except Exception:
        logger.exception("ws_session 關閉舊連線失敗 device=%s", device)
    _sessions.pop(device, None)


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
    """每 15 秒掃 `_sessions`，回收閒置(>90s)或已死的連線。

    對每個 session 的清理都包 try/except，單一錯誤不可弄垮整條 thread。
    """
    while True:
        time.sleep(_SWEEP_INTERVAL_SEC)
        try:
            _sweep_once()
        except Exception:
            logger.exception("ws_session sweeper 迴圈異常 (已吞掉，繼續)")


def _sweep_once() -> None:
    """掃描一輪：找出該回收的 device，逐一關連線並釋放 lease。"""
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
            owner = session.owner
            try:
                _close_session_locked(device, session)
                registry.release(device, owner)
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
    """建立 / 復用 ``ip`` 的持久 WS 連線。error→502、conflict→409。"""
    result = ensure(ip)
    status = result.get("status")
    if status == "error":
        return jsonify(result), 502
    if status == "conflict":
        return jsonify(result), 409
    return jsonify(result), 200


@bp.route("/api/ws_session/<ip>/ping", methods=["POST"])
@_fly_pet_auth
def ping_endpoint(ip: str):
    """前端保活心跳。"""
    return jsonify(ping(ip))


@bp.route("/api/ws_session/<ip>/disconnect", methods=["POST"])
@_fly_pet_auth
def disconnect_endpoint(ip: str):
    """主動關閉 ``ip`` 的持久連線並釋放 lease。"""
    return jsonify(disconnect(ip))
