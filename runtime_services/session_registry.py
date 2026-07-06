"""帳號佔用 Session Registry —— 單一真相。

同一遊戲帳號同時只能有一條 live session(異地登入互踢,WS cmd 259)。所有會用某台
裝置帳號上線的消費者(腳本排程、坐騎追蹤、在線監控、dashboard 工具)都先向此 registry
`acquire` 登記 owner、用完 `release`。一台裝置同一時刻只有一個 Lease(單一 owner)。

設計依據:docs/superpowers/specs/2026-07-06-account-session-registry-design.md(§1)。

Phase 1:純加法,只提供 registry API + 單元測試,不接任何現有呼叫端。

鎖規則:`_lock`(RLock)全程持鎖;鎖內只做 dict / Event / set_pause 這類非阻塞操作,
不在鎖內開 socket 或登入(登入由呼叫端拿到 ok=True 後自行在鎖外進行)。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# --- 資料模型 ---------------------------------------------------------------

class Channel(Enum):
    WS = "ws"    # 純 WS 連線(wake-cycle WS 階段 / 工具 / 追蹤 / 監控)
    H5 = "h5"    # Playwright 瀏覽器
    ADB = "adb"  # adb/u2 前景 app


class Owner(Enum):
    SCHEDULER = "scheduler"           # 腳本排程(bot 主迴圈)—— 免確認白名單
    MOUNT_TRACKER = "mount_tracker"   # 坐騎追蹤 —— 免確認白名單
    ONLINE_MONITOR = "online_monitor"  # 在線監控常駐 detector —— 免確認白名單
    ONLINE_CHECK = "online_check"     # 在線監控一次性慢路徑 —— 免確認白名單
    TOOL = "tool"                     # dashboard 工具分頁 —— 需手動登入 + 在線確認


# 優先權序位(數字越大越優先)。preempt=True 且嚴格高於現任才可搶佔。
_PRIORITY: dict[Owner, int] = {
    Owner.SCHEDULER: 100,
    Owner.ONLINE_MONITOR: 40,
    Owner.ONLINE_CHECK: 40,
    Owner.MOUNT_TRACKER: 30,
    Owner.TOOL: 20,
}

# 借用型 owner 若 check_wake=True,取用一台『閒置』裝置前的即將喚醒/空窗保守閘門用:
# 距離下次喚醒少於此秒數 → 讓位給裝置自身 bot loop,不借用。
_HANDOFF_LEAD_SEC = 120
# 視為『明確休眠中』(可安全登入、不會踢掉 live session)的 task 字串。
_IDLE_TASKS = ("休眠中", "啟動後休眠")


@dataclass
class Lease:
    device: str
    owner: Owner
    channel: Channel
    label: str
    acquired_at: float
    role_id: Optional[int]
    # 被更高優先權搶佔時由 registry `set()`;借用型服務在每次 WS 呼叫前 poll,
    # 一旦 set 就收線讓位(取代靠 bot_state 猜的舊 still_idle 判定)。
    preempted: threading.Event = field(default_factory=threading.Event)


@dataclass
class AcquireResult:
    ok: bool
    lease: Optional[Lease] = None       # ok=True 時為當前(新登記 / 續租)的 lease
    conflict: Optional[Lease] = None    # ok=False 且被別的 owner 佔用時的現任 lease
    reason: Optional[str] = None        # ok=False 的原因,例:"protected"


# --- 模組級狀態 -------------------------------------------------------------

_lock = threading.RLock()
_leases: dict[str, Lease] = {}


def _is_borrowing(owner: Owner) -> bool:
    """借用型 owner(非 SCHEDULER):acquire 成功要 pause bot loop,釋放要恢復。"""
    return owner is not Owner.SCHEDULER


# --- 核心 API ---------------------------------------------------------------

def acquire(device: str, owner: Owner, channel: Channel, label: str = "", *,
            role_id: Optional[int] = None,
            preempt: bool = False,
            check_wake: bool = False) -> AcquireResult:
    """嘗試取得 ``device`` 帳號的佔用權。全程持 `_lock`(判定→登記原子化,消 TOCTOU)。

    - 同 owner 再次 acquire = 續租(更新 channel/label/role_id/acquired_at)。
    - 不同 owner:未 preempt 或優先權不夠 → 回 conflict(現任不變)。
    - preempt=True 且嚴格高於現任 → 觸發現任 `preempted`,改寫 lease。
    - human_played 保護帳號(role_id 命中 protected,或裝置被標 human_played)→
      任何 owner 都回 ``reason="protected"``。
    - ``check_wake=True`` 且借用型 owner 取用一台**空閒**裝置時,額外做即將喚醒/空窗
      保守閘門(讀 bot_state next_wake_at):即將自我喚醒(<120s)或缺 next_wake_at 且
      非明確休眠中(OFFLINE 空窗 / 崩潰重啟 / 無 state row)→ 回 ``reason="about_to_wake"``。
      此判定與佔用登記在同一鎖內完成(原子),消掉舊 mount-tracker「先判定再 ensure」的
      TOCTOU 與「OFFLINE 空窗誤判可借」bug。SCHEDULER / 非 check_wake 呼叫不受影響。

    借用型 owner acquire 成功 → registry `set_pause(device, True)`;
    釋放 / 被搶佔 → `set_pause(device, False)`(見 §1.4)。
    """
    with _lock:
        # human_played 硬前置:任何 owner 都不得佔用保護帳號。
        if role_id is not None and role_id in _protected_role_ids():
            return AcquireResult(ok=False, reason="protected")
        if _is_human_played_device(device):
            return AcquireResult(ok=False, reason="protected")

        existing = _leases.get(device)
        if existing is not None and existing.owner is owner:
            # 續租:更新細節,不觸發 preempted,借用型確保仍暫停(冪等)。
            # 已持有此借用 → 不再套即將喚醒閘門(續用交給呼叫端 poll preempted)。
            existing.channel = channel
            existing.label = label
            existing.acquired_at = _now()
            if role_id is not None:
                existing.role_id = role_id
            if _is_borrowing(owner):
                _safe_set_pause(device, True)
            return AcquireResult(ok=True, lease=existing)

        if existing is None:
            # 全新借用一台空閒裝置:借用型 + check_wake 才套即將喚醒/空窗保守閘門。
            # 只在裝置空閒時檢查——被別人佔用一律走下面的 conflict(語意更精確)。
            if check_wake and _is_borrowing(owner) and _borrow_wake_unsafe(device):
                return AcquireResult(ok=False, reason="about_to_wake")

        if existing is not None:
            can_preempt = preempt and _PRIORITY[owner] > _PRIORITY[existing.owner]
            if not can_preempt:
                return AcquireResult(ok=False, conflict=existing)
            # 搶佔:通知現任讓位,並解除它(若為借用型)對 bot loop 的暫停。
            existing.preempted.set()
            _end_lease_locked(existing)

        lease = Lease(device=device, owner=owner, channel=channel, label=label,
                      acquired_at=_now(), role_id=role_id)
        _leases[device] = lease
        if _is_borrowing(owner):
            _safe_set_pause(device, True)
        logger.info("session_registry acquire device=%s owner=%s channel=%s%s",
                    device, owner.value, channel.value,
                    " (preempt)" if existing is not None else "")
        return AcquireResult(ok=True, lease=lease)


def release(device: str, owner: Owner) -> None:
    """釋放 ``device`` 的佔用(僅當前 owner 相符才釋放;冪等)。

    釋放借用型 owner 會恢復該機 bot loop(set_pause False)。
    """
    with _lock:
        existing = _leases.get(device)
        if existing is None or existing.owner is not owner:
            return
        _leases.pop(device, None)
        _end_lease_locked(existing)
        logger.info("session_registry release device=%s owner=%s",
                    device, owner.value)


def peek(device: str) -> Optional[Lease]:
    """唯讀查詢當前佔用者(無副作用)。回傳 live lease 供借用者 poll ``preempted``。"""
    with _lock:
        return _leases.get(device)


def peek_all() -> dict[str, Lease]:
    """全裝置佔用快照(mapping 淺拷貝,Lease 物件共用)。供 /api/status 一次讀出。

    只讀 lease 欄位做顯示,不改動 Lease;拷貝的是 dict 本身,避免呼叫端遍歷時
    registry 併發增刪造成 RuntimeError。
    """
    with _lock:
        return dict(_leases)


# --- 內部 helper ------------------------------------------------------------

def _end_lease_locked(lease: Lease) -> None:
    """一條 lease 生命週期結束(release / 被搶佔)的收尾(需持 `_lock`)。

    借用型 owner → 恢復該機 bot loop。SCHEDULER 不涉及 pause。
    """
    if _is_borrowing(lease.owner):
        _safe_set_pause(lease.device, False)


def _now() -> float:
    return time.time()


def _device_state(device: str) -> Optional[dict]:
    """讀取單一裝置的即時狀態 row(bot_state);讀取失敗 / 無 row 回 None。此為測試 seam。"""
    try:
        import bot_state
        return bot_state.get_all_states().get(device)
    except Exception:  # noqa: BLE001 — 狀態讀取不可弄垮 acquire 判定
        return None


def _borrow_wake_unsafe(device: str) -> bool:
    """借用型 owner 取用一台空閒裝置前的即將喚醒/空窗保守閘門(True = 不安全,拒絕借用)。

    - next_wake_at 存在:距下次喚醒 <=_HANDOFF_LEAD_SEC → 不安全(讓位給自身 bot loop);
      壞值也保守視為不安全。
    - next_wake_at 缺失:只有 task 明確休眠中/啟動後休眠才安全(睡著,只是還沒算喚醒時刻);
      其餘(OFFLINE 空窗 / 崩潰重啟 / 無 state row / 執行中任務)一律保守拒絕借用。

    修掉舊 mount-tracker「_is_idle 對無 row/OFFLINE 回 True + _about_to_wake 對缺
    next_wake_at 回 False」組合誤判『空窗可借』的 bug#2。
    """
    st = _device_state(device)
    nwa = (st or {}).get("next_wake_at")
    if nwa is not None:
        try:
            return (float(nwa) - _now()) <= _HANDOFF_LEAD_SEC
        except (TypeError, ValueError):
            return True
    return str((st or {}).get("task") or "") not in _IDLE_TASKS


def _safe_set_pause(device: str, paused: bool) -> None:
    """包一層 try/except 的 bot_state.set_pause,避免影響佔用登記。此為測試 seam。"""
    try:
        import bot_state
        bot_state.set_pause(device, paused)
    except Exception:
        logger.exception("session_registry set_pause(%s, %s) 失敗", device, paused)


# --- human_played 保護(延遲載入 seam,可 monkeypatch) ----------------------

_protected_cache: Optional[frozenset] = None


def _load_protected_role_ids() -> frozenset:
    """從 online_monitor 解析 human_played 裝置的 roleId(延遲載入本體)。"""
    from ws_token.online_monitor import resolve_protected_role_ids
    return resolve_protected_role_ids()


def _protected_role_ids() -> frozenset:
    """protected roleId 集合(快取一次)。此為測試 seam,可整個 monkeypatch。"""
    global _protected_cache
    if _protected_cache is None:
        try:
            _protected_cache = _load_protected_role_ids()
        except Exception:
            logger.exception("session_registry 載入 protected role_ids 失敗,暫視為空集")
            _protected_cache = frozenset()
    return _protected_cache


def _is_human_played_device(device: str) -> bool:
    """裝置本身是否標記 human_played(config)。此為測試 seam。"""
    try:
        import config_manager
        return device in set(config_manager.get_human_played_devices())
    except Exception:
        return False
