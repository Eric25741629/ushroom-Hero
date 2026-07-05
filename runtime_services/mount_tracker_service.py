"""坐騎追蹤器（MountTracker）狀態持久化。

以 ``ws_token.state`` 的 per-device JSON 存檔為底層，把追蹤目標、已知玩家、
掃描結果等資料持久化到 ``ws_state/_mount_tracker.json``。所有讀改寫皆在
模組級 RLock 下進行，供之後同檔加入的 scan_cycle / daemon 共用。

設計約束（重要）：
- import 時無副作用：不啟動 thread、不讀檔。每個方法即時 ``load_state`` 取最新
  磁碟內容，因此同一 state_dir 上的兩個實例彼此一致（無記憶體快取分歧）。
- 不依賴 device / cv2 / torch。JSON key 一律為字串，故 known_players 以
  ``str(role_id)`` 為 key。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from ws_token import mount_scan, parking_bonus
from ws_token.state import load_state, save_state

# 模組級鎖：保護每個 public 方法的 read-modify-write（load -> mutate -> save）。
_LOCK = threading.RLock()


class MountTrackerStore:
    """坐騎追蹤器狀態存取層（薄封裝於 ws_token.state 之上）。"""

    def __init__(self, device_key: str = "_mount_tracker", state_dir: str | None = None) -> None:
        # device_key 對應到 ws_state/<device_key>.json；state_dir 為 None 時
        # 沿用 ws_token.state 的預設目錄。__init__ 不讀檔（lazy）。
        self._key = device_key
        self._state_dir = state_dir

    # ---- 內部：載入 / 寫回 --------------------------------------------------
    def _load(self) -> dict:
        """讀取最新磁碟狀態；missing/corrupt -> {}。"""
        if self._state_dir is not None:
            return load_state(self._key, state_dir=self._state_dir)
        return load_state(self._key)

    def _save(self, data: dict) -> None:
        """原子寫回磁碟。"""
        if self._state_dir is not None:
            save_state(self._key, data, state_dir=self._state_dir)
        else:
            save_state(self._key, data)

    # ---- 追蹤目標 targets ---------------------------------------------------
    def get_targets(self) -> list[dict]:
        """回傳追蹤目標清單。"""
        with _LOCK:
            return self._load().get("targets", [])

    def add_target(self, t: dict) -> None:
        """新增追蹤目標；以 role_id 去重（既有同 role_id 者取代，否則附加）。"""
        with _LOCK:
            data = self._load()
            targets: list[dict] = data.get("targets", [])
            role_id = t.get("role_id")
            targets = [x for x in targets if x.get("role_id") != role_id]
            targets.append(t)
            data["targets"] = targets
            self._save(data)

    def remove_target(self, role_id: int) -> None:
        """移除指定 role_id 的追蹤目標。"""
        with _LOCK:
            data = self._load()
            targets: list[dict] = data.get("targets", [])
            data["targets"] = [x for x in targets if x.get("role_id") != role_id]
            self._save(data)

    # ---- 已知玩家 known_players（key = str(role_id）） -----------------------
    def get_known(self) -> dict:
        """回傳已知玩家對照表（key 為 str(role_id)）。"""
        with _LOCK:
            return self._load().get("known_players", {})

    def upsert_known(self, role_id: int, **fields: Any) -> None:
        """更新 / 插入已知玩家欄位；value 為 None 的欄位一律略過（不覆蓋既有值）。"""
        with _LOCK:
            data = self._load()
            kp: dict = data.setdefault("known_players", {})
            entry: dict = kp.setdefault(str(role_id), {})
            for key, value in fields.items():
                if value is not None:
                    entry[key] = value
            self._save(data)

    # ---- 掃描結果 results ---------------------------------------------------
    def get_results(self) -> dict:
        """回傳最近一次掃描結果。"""
        with _LOCK:
            return self._load().get("results", {})

    def set_results(self, results: dict) -> None:
        """寫入掃描結果。"""
        with _LOCK:
            data = self._load()
            data["results"] = results
            self._save(data)

    # ---- 上次執行資訊 last_run ----------------------------------------------
    def get_last_run(self) -> dict:
        """回傳上次掃描執行資訊。"""
        with _LOCK:
            return self._load().get("last_run", {})

    def set_last_run(self, info: dict) -> None:
        """寫入上次掃描執行資訊。"""
        with _LOCK:
            data = self._load()
            data["last_run"] = info
            self._save(data)

    # ---- 執行中旗標 running -------------------------------------------------
    def set_running(self, running: bool) -> None:
        """寫入掃描是否進行中旗標。"""
        with _LOCK:
            data = self._load()
            data["running"] = running
            self._save(data)

    # ---- 快照 ---------------------------------------------------------------
    def snapshot(self) -> dict:
        """一次讀出整體狀態摘要，供 dashboard / API 使用。"""
        with _LOCK:
            data = self._load()
            known = data.get("known_players", {})
            return {
                "targets": data.get("targets", []),
                "results": data.get("results", {}),
                "known_count": len(known),
                "last_run": data.get("last_run", {}),
                "running": data.get("running", False),
            }


# ============================================================================
# Task 4：idle 裝置借用判定
# ----------------------------------------------------------------------------
# 坐騎掃描要借「正在休眠、且不會馬上被叫醒、也沒被 dashboard 純 WS 佔用」的裝置
# 來開一次性連線讀車位。判定沿用 online_monitor 的「安全 detector」語意，額外加上
# 「非 human_played 保護帳號」閘門。所有外部依賴走薄封裝間接層，測試可 monkeypatch。
# ============================================================================

# 距離下次喚醒少於此秒數 → 讓位給裝置自身 bot loop，不借用。
_HANDOFF_LEAD_SEC = 120
# 視為 idle（可安全登入、不會踢掉 live session）的 task 字串。
_IDLE_TASKS = ("休眠中", "啟動後休眠")
# 預設候選借用裝置（皆為受控 bot 帳號；human_played 帳號另由保護集合擋掉）。
CANDIDATES = ["7fe98fc6", "emulator-5554", "emulator-5556", "emulator-5560"]


# ---- 外部依賴間接層（供測試 monkeypatch）-----------------------------------

def _states() -> dict:
    """回傳所有裝置的即時狀態表；讀取失敗回空 dict。"""
    try:
        import bot_state
        return bot_state.get_all_states()
    except Exception:  # noqa: BLE001 — 狀態讀取不可弄垮判定
        return {}


def _ws_active(ip: str) -> bool:
    """該裝置是否已有 dashboard 純 WS 連線在跑（借用會互踢）。容錯回 False。"""
    try:
        from control_panel import ws_session
        return bool(ws_session.is_active(ip))
    except Exception:  # noqa: BLE001
        return False


def _protected_roles() -> set:
    """human_played 保護帳號的 roleId 集合（絕不借用）。容錯回空集合。"""
    try:
        from ws_token.online_monitor import resolve_protected_role_ids
        return set(resolve_protected_role_ids())
    except Exception:  # noqa: BLE001
        return set()


def _role_id(ip: str) -> Optional[int]:
    """讀取裝置對應的 roleId（寬鬆讀 capture 檔）；無 creds 回 None。"""
    try:
        from ws_token.creds import load_role_id
        return load_role_id(ip)
    except Exception:  # noqa: BLE001
        return None


def _now() -> float:
    """現在時間（秒）。"""
    return time.time()


# ---- 判定 -------------------------------------------------------------------

def _is_idle(ip: str, states: dict) -> bool:
    """裝置是否 idle（登入不會踢掉 live session）。

    無 row（thread 未起）/ status OFFLINE / task ∈ 休眠中｜啟動後休眠 → idle。
    """
    st = states.get(ip)
    if not st:
        return True  # 沒有跑中的 thread → 安全
    if str(st.get("status") or "").upper() == "OFFLINE":
        return True
    return str(st.get("task") or "") in _IDLE_TASKS


def _about_to_wake(ip: str, states: dict) -> bool:
    """裝置是否即將（<_HANDOFF_LEAD_SEC）被自身排程叫醒。"""
    nwa = (states.get(ip) or {}).get("next_wake_at")
    if not nwa:
        return False
    try:
        return (float(nwa) - _now()) <= _HANDOFF_LEAD_SEC
    except (TypeError, ValueError):
        return False


def is_safe_to_borrow(ip: str) -> bool:
    """能否安全借用 ``ip`` 開一次性 WS 連線掃車位。

    全部成立才算安全：
      1. idle（休眠 / OFFLINE / 無 thread）。
      2. 不在 _HANDOFF_LEAD_SEC 內即將自我喚醒。
      3. 沒有 dashboard 純 WS 連線佔用（否則同帳號互踢）。
      4. 不是 human_played 保護帳號；有保護集合時，roleId 讀不出來也一律不借。
    """
    states = _states()
    if not _is_idle(ip, states):
        return False
    if _about_to_wake(ip, states):
        return False
    if _ws_active(ip):
        return False
    protected = _protected_roles()
    if protected:
        rid = _role_id(ip)
        if rid is None or int(rid) in protected:
            return False
    return True


def pick_idle_device(candidates: list[str] = CANDIDATES) -> Optional[str]:
    """回傳第一個可安全借用的候選裝置；全部不安全回 None。"""
    for ip in candidates:
        if is_safe_to_borrow(ip):
            return ip
    return None
