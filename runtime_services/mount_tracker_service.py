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

import logging
import threading
import time
from typing import Any, Callable, Optional

from ws_token import mount_scan, parking_bonus
from ws_token.state import load_state, save_state

# 模組級鎖：保護每個 public 方法的 read-modify-write（load -> mutate -> save）。
_LOCK = threading.RLock()

# 用 stdlib logger（getLogger 無副作用），維持本模組 import 時零副作用。
logger = logging.getLogger(__name__)


# ============================================================================
# 掃描即時進度（純記憶體，NO file I/O）
# ----------------------------------------------------------------------------
# bootstrap（~10-20 分）+ scan（~25 分）過程中只在最後才寫 results/last_run 進磁碟，
# dashboard 期間一路顯示靜態「掃描中…／0／未建立」。為了讓數字即時跳動，這裡維護一份
# 純記憶體進度物件：daemon 側於 bootstrap / scan 過程持續更新，results API 直接吐出，
# 頁面輪詢即時反映。刻意「不落地」——整體設計就是批次寫檔以護 NAS I/O，進度只在記憶體。
# ============================================================================

# 進度鎖：保護 _progress 的讀改寫（get 回傳 copy、set 合併、reset 清零）。
_progress_lock = threading.Lock()
# 單一全域進度物件（所有欄位一律純量 / None，故 dict() 淺拷貝即完整隔離）。
_progress: dict = {
    "phase": "idle",      # idle | bootstrap | scan
    "scanned": 0,         # scan：本輪已讀到車位的車位主人數
    "found": 0,           # scan：本輪已找到的追蹤目標坐騎總數（純量）
    "known": 0,           # scan/bootstrap：已知玩家庫目前規模
    "guilds": 0,          # bootstrap 階段1：已蒐集的合格公會數
    "members": 0,         # bootstrap 階段2：已展開的成員數
    "started_ts": None,   # 本輪起跑時間戳（秒）；idle 時保留上一輪值
    "next_scan_ts": None, # 下一輪預估起跑時間戳（秒）；由 _run_loop 依自適應間隔寫入
}


def get_progress() -> dict:
    """回傳目前掃描進度的複本（in-memory，供 results API 吐給頁面輪詢）。

    回傳淺拷貝——所有欄位皆純量 / None，故呼叫端任意改動不會回寫內部狀態。
    """
    with _progress_lock:
        return dict(_progress)


def _set_progress(**fields: Any) -> None:
    """合併更新進度欄位（只覆蓋傳入的鍵，其餘保留）。"""
    with _progress_lock:
        _progress.update(fields)


def _reset_progress(phase: str) -> None:
    """把計數歸零、切換 ``phase`` 並記錄本輪起跑時間戳（daemon 側呼叫，用真時鐘）。"""
    with _progress_lock:
        _progress.update({
            "phase": phase,
            "scanned": 0,
            "found": 0,
            "known": 0,
            "guilds": 0,
            "members": 0,
            "started_ts": time.time(),
        })


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

    def bulk_upsert_known(self, updates: dict) -> None:
        """一次合併多筆已知玩家更新，只 load / save 一次（NAS I/O 友善）。

        ``updates`` 為 ``{role_id: {field: value}}``；沿用 upsert 的規則：
        value 為 None 的欄位一律略過（不覆蓋既有值）。掃描一輪上千車位時用它取代
        數千次 :meth:`upsert_known` 全檔重寫。
        """
        if not updates:
            return
        with _LOCK:
            data = self._load()
            kp: dict = data.setdefault("known_players", {})
            for role_id, fields in updates.items():
                entry: dict = kp.setdefault(str(role_id), {})
                for key, value in (fields or {}).items():
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

    # ---- 已打掉標記 attacked ------------------------------------------------
    # 軟標記：使用者打掉某台停在他人車位的坐騎後，把它標成「已打掉」，跨掃描保留，
    # 直到該實例移走（車位主人或停車時間改變）才自然消失。實例唯一鍵 =
    # ``f"{owner_role_id}:{start_time}"``；外層 key 為 ``str(target_role_id)``。
    def get_attacked(self) -> dict:
        """回傳已打掉標記表：``{str(target_role_id): {"<owner>:<start_time>": marked_ts}}``。"""
        with _LOCK:
            return self._load().get("attacked", {})

    def set_attacked(self, attacked: dict) -> None:
        """整批寫入已打掉標記表（供 scan_cycle 批次寫回，維持 NAS I/O 友善，一次全檔寫）。"""
        with _LOCK:
            data = self._load()
            data["attacked"] = attacked
            self._save(data)

    def mark_attacked(self, target_role_id: int, owner_role_id: int,
                      start_time: int, on: bool = True) -> None:
        """標記 / 取消標記某台坐騎實例為「已打掉」（軟標記，read-modify-write in _LOCK）。

        ``key = f"{owner_role_id}:{start_time}"``。``on=True`` 寫入 ``time.time()`` 標記戳；
        ``on=False`` 移除該鍵（該 target 清空後連同外層 dict 一併移除）。
        """
        key = f"{owner_role_id}:{start_time}"
        tkey = str(target_role_id)
        with _LOCK:
            data = self._load()
            attacked: dict = data.setdefault("attacked", {})
            if on:
                attacked.setdefault(tkey, {})[key] = time.time()
            else:
                bucket = attacked.get(tkey)
                if bucket is not None:
                    bucket.pop(key, None)
                    if not bucket:
                        attacked.pop(tkey, None)
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

    # ---- 冷啟 bootstrap 完成戳 bootstrap_done -------------------------------
    # 冷啟（known_players 空）時 daemon 會借一台 idle 裝置跑一次性 guild-scan 灌
    # known_players；完成後寫入時間戳，之後不再重跑（避免每輪重灌）。使用者要重建
    # 玩家庫時把它設回 None 即可讓 daemon 下一輪重跑一次。
    def get_bootstrap_done(self) -> float | None:
        """回傳冷啟 bootstrap 完成時間戳（秒）；尚未跑過回 None。"""
        with _LOCK:
            return self._load().get("bootstrap_done")

    def set_bootstrap_done(self, ts) -> None:
        """寫入 / 清除冷啟 bootstrap 完成戳；``ts`` 為 None 時移除該鍵（觸發重建）。"""
        with _LOCK:
            data = self._load()
            if ts is None:
                data.pop("bootstrap_done", None)
            else:
                data["bootstrap_done"] = ts
            self._save(data)

    # ---- 快照 ---------------------------------------------------------------
    def snapshot(self) -> dict:
        """一次讀出整體狀態摘要，供 dashboard / API 使用。"""
        with _LOCK:
            data = self._load()
            known = data.get("known_players", {})
            attacked = data.get("attacked", {})
            # 每筆 found 的 attacked 於「讀取時」即時重算（snapshot 是唯一讀出口）：
            # scan_cycle 只在掃描當下 stamp 一次，若不在此重算，使用者標記/取消標記
            # 要等下一輪掃描才會反映。key=f"{owner}:{start_time}"（與 mark_attacked 一致）。
            results = {
                tkey: [
                    {**r, "attacked":
                        f"{r.get('owner')}:{r.get('start_time')}" in attacked.get(tkey, {})}
                    for r in rows
                ]
                for tkey, rows in data.get("results", {}).items()
            }
            return {
                "targets": data.get("targets", []),
                "results": results,
                "known_count": len(known),
                "last_run": data.get("last_run", {}),
                "running": data.get("running", False),
                "bootstrap_done": data.get("bootstrap_done"),
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


# ============================================================================
# Task 5：scan_cycle — 掃描核心（依賴注入、純可測）
# ----------------------------------------------------------------------------
# 以權重排序 known players 當「車位主人（owner）」候選，借「所有」安全 idle 裝置各開一條
# worker 執行緒並行掃描：每台各自冷卻讀車位佔用，比對是否停著追蹤目標；順手把新看到的
# 佔用者滾進 known（雪球）、更新車位主人車位加成與 host_hits。所有 IO（reader / still_idle /
# sleeper / now_fn）與並行裝置清單（worker_devices）皆注入，測試不碰真 socket / 真 sleep /
# 真時鐘。
# ============================================================================

# reader(device, owner_role_id) -> parse_lot_occupants dict | None（None=讀失敗）
Reader = Callable[[str, int], Optional[dict]]
StillIdle = Callable[[str], bool]  # 該裝置是否仍可續用（idle 且未即將自我喚醒）
Sleeper = Callable[[float], Any]
NowFn = Callable[[], float]

# 單一 worker 連續讀失敗達此數 → 視為該台連線壞掉，收掉它，避免它把共享 queue 一路吃成
# None 餓死其他台（ponytail 上限：非精算，壞台隔到下一輪由權重重排自然重試）。
_MAX_CONSEC_READ_FAIL = 3


def scan_cycle(
    store: MountTrackerStore,
    reader: Reader,
    worker_devices: list[str],
    still_idle: StillIdle,
    sleeper: Sleeper,
    now_fn: NowFn,
    *,
    top_n: int = 1600,
    budget_s: float = 1500,
    cooldown_s: float = 2.0,
    max_per_target: int = 5,
    focus_owners: Optional[list[int]] = None,
    on_progress: Optional[Callable[..., None]] = None,
) -> dict:
    """跑一輪坐騎掃描，回傳摘要 dict。

    ``on_progress``（選用、DI）：每處理完一個車位主人就以
    ``on_progress(scanned=..., found=..., known=...)`` 回報即時進度（純記憶體，
    供 dashboard 輪詢跳動）。為 None 時完全不影響行為與回傳。

    流程：
      1. 無追蹤目標 → 直接回 ``{"skipped": "no_targets"}``。
      2. 以 known players（含 targets 自身）依 :func:`mount_scan.weight` 由高到低
         排序，取前 ``top_n`` 個當車位主人候選。``focus_owners`` 提供時改「聚焦模式」：
         queue 只含這些車位主人、跳過權重展開——用於「全部找到後只盯現有位置的更新」。
      3. ``worker_devices`` 每台各開一條 worker 執行緒並行掃描：共享游標依序取車位
         主人，每台各自 ``sleeper(cooldown_s)`` 冷卻後讀一台（N 台 → 聚合約
         N 台/cooldown）。``still_idle(dev)`` 為 False（即將自我喚醒 / 不再 idle）
         即收掉該 worker；連續讀失敗達 :data:`_MAX_CONSEC_READ_FAIL` 亦收掉。
      4. 車位裡的佔用者：是追蹤目標且該目標尚未收滿 ``max_per_target`` → 收錄；
         一律 upsert 進 known（雪球）。共享狀態變更皆在單一 lock 內序列化。
      5. 更新車位主人 coin（車位加成）與 host_hits，寫回 results / last_run。
      6. 超過 ``budget_s`` 或所有目標都收滿 → 提早結束。
    """
    targets = store.get_targets()
    if not targets:
        return {"skipped": "no_targets"}

    target_ids = {int(t["role_id"]) for t in targets if t.get("role_id") is not None}
    known = store.get_known()
    # 本輪開頭的「已打掉」標記快照：供每筆 found 標註 attacked，並於迴圈後剪枝。
    attacked = store.get_attacked()

    # 目標所屬公會集合（供 weight 的同公會加權）。
    target_guilds: set = set()
    for rid in target_ids:
        guild = (known.get(str(rid)) or {}).get("guild")
        if guild:
            target_guilds.add(guild)

    if focus_owners:
        # 聚焦模式：所有目標已收滿 → 只重掃「目前停著坐騎的車位主人」，偵測坐騎是否
        # 移動 / 更新（省去掃廣域候選）。任一台移走 → 該筆 found 消失 → found_total 掉，
        # 下輪 _run_one_cycle 判定未滿即回全域掃描重找。dict.fromkeys 去重、保序。
        queue = [{"role_id": int(o)} for o in dict.fromkeys(focus_owners)]
    else:
        # 車位主人候選 = known players（含尚未在 known 的 target 自身），依權重排序取 top_n。
        entries: list[dict] = []
        seen: set = set()
        for key, val in known.items():
            try:
                rid = int(key)
            except (TypeError, ValueError):
                continue
            entry = dict(val)
            entry["role_id"] = rid
            entries.append(entry)
            seen.add(rid)
        for rid in target_ids:
            if rid not in seen:
                entry = dict(known.get(str(rid), {}))
                entry["role_id"] = rid
                entries.append(entry)
                seen.add(rid)
        entries.sort(key=lambda e: mount_scan.weight(e, target_guilds), reverse=True)
        queue = entries[:top_n]

    deadline = now_fn() + budget_s
    found: dict[int, list] = {rid: [] for rid in target_ids}
    scanned_owners: set = set()   # 本輪成功讀到車位的車位主人
    host_hit_ids: set = set()     # 本輪車位停著追蹤目標的車位主人
    # 本輪所有 known 更新累積在記憶體，迴圈結束後「一次寫回」，避免每個車位一次
    # 全檔重寫（NAS/SMB I/O 敏感）。{role_id: {field: value}}。
    pending: dict[int, dict] = {}
    # 並行掃描：worker_devices 每台一條執行緒，共享 queue 游標 + 累積結構；所有共享
    # 狀態變更都在此 lock 內序列化（reader / sleeper / still_idle 在鎖外並行）。
    lock = threading.Lock()
    cursor = 0

    def _stage(role_id: int, **fields: Any) -> None:
        """把一筆 known 更新累積進 pending（None 欄位跳過）。呼叫端須持有 lock。"""
        target = pending.setdefault(role_id, {})
        for key, value in fields.items():
            if value is not None:
                target[key] = value

    def _next_owner() -> Optional[int]:
        """取下一個待掃車位主人 role_id；佇列空 / 早停條件成立回 None（含鎖）。"""
        nonlocal cursor
        with lock:
            if cursor >= len(queue):
                return None
            # 所有目標都收滿 / 超過時間預算 → 提早收工，剩下的留給下一輪。
            if all(len(found[rid]) >= max_per_target for rid in target_ids):
                return None
            if now_fn() >= deadline:
                return None
            entry = queue[cursor]
            cursor += 1
            return int(entry["role_id"])

    def _worker(dev: str) -> None:
        consec_fail = 0
        while True:
            # 該台進入自身喚醒窗 / 不再 idle → 收掉此 worker（連線由呼叫端 finally 歸還）。
            if not still_idle(dev):
                return
            owner = _next_owner()
            if owner is None:
                return
            sleeper(cooldown_s)      # 每台各自冷卻，避免單一帳號 WS 打太快
            lot = reader(dev, owner)
            if lot is None:
                # 連續讀失敗達上限 → 視為此台壞掉，收掉避免餓死其他台（見常數註解）。
                consec_fail += 1
                if consec_fail >= _MAX_CONSEC_READ_FAIL:
                    return
                continue
            consec_fail = 0
            with lock:
                scanned_owners.add(owner)
                for space in lot.get("spaces", []):
                    occ = space.get("role_id")
                    if not occ:
                        continue
                    occ = int(occ)
                    name = space.get("name")
                    _stage(occ, name=name)  # 雪球：新佔用者滾進 known（None name 也建空 entry）
                    if occ in target_ids and len(found[occ]) < max_per_target:
                        start_time = space.get("start_time")
                        # 車位主人名字/公會取自本輪開頭的 known 快照（純記憶體讀）；未進 known
                        # 時為 None。dashboard 需要 owner_name/owner_guild 顯示坐騎停在誰家。
                        owner_info = known.get(str(owner)) or {}
                        found[occ].append({
                            "owner": owner,
                            "owner_name": owner_info.get("name"),
                            "owner_guild": owner_info.get("guild"),
                            "pos": space.get("pos"),
                            "start_time": start_time,
                            "found_ts": now_fn(),
                            "name": name,
                            # 沿用本輪標記快照標註；同一實例（owner+start_time）已被標記則 True。
                            "attacked": f"{owner}:{start_time}" in attacked.get(str(occ), {}),
                        })
                        host_hit_ids.add(owner)

                # 車位主人車位加成（菇車幣）+ 掃描時間戳。
                bonus = parking_bonus.lot_bonus(lot.get("skin_list", []))
                _stage(owner, coin=bonus.get("coin"), last_scanned_ts=now_fn())

                # 即時進度回報（純記憶體）：scanned / found（純量）/ known 目前規模。
                # known = 開頭快照 + 本輪雪球新增（pending 中尚未在 known 的 role_id）。
                if on_progress:
                    on_progress(
                        scanned=len(scanned_owners),
                        found=sum(len(found[rid]) for rid in target_ids),
                        known=len(known) + sum(1 for rid in pending if str(rid) not in known),
                    )

    threads = [threading.Thread(target=_worker, args=(dev,), daemon=True)
               for dev in worker_devices]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # host_hits：本輪掃到的車位主人，有停到目標 +1（上限 5），否則 decay，不 <0。
    # 基準值取本輪開頭的 known 快照（沿用舊值）；沒掃到的車位主人不動，靠權重排序自我收斂。
    for owner in scanned_owners:
        cur = int((known.get(str(owner)) or {}).get("host_hits", 0) or 0)
        if owner in host_hit_ids:
            _stage(owner, host_hits=min(cur + 1, 5))
        elif cur > 0:
            _stage(owner, host_hits=cur - 1)

    # 已打掉標記剪枝：只有「該實例的車位主人本輪確實被掃到」時才有資格判定它是否還在。
    # found 受 max_per_target / 提早收工 / top_n / 車位主人是否被掃 影響（非全量），因此
    # 車位主人本輪沒被掃（owner not in scanned_owners）時一律保留標記，避免誤刪仍停著的標記。
    new_attacked: dict = {}
    for rid in target_ids:
        prev = attacked.get(str(rid))
        if not prev:
            continue
        present = {f"{e['owner']}:{e['start_time']}" for e in found[rid]}
        kept: dict = {}
        for key, value in prev.items():
            try:
                key_owner = int(key.split(":")[0])
            except (TypeError, ValueError, IndexError):
                # 壞鍵（理論上不會出現，mark_attacked 一律 f"{owner}:{start_time}"）→ 保守保留。
                kept[key] = value
                continue
            # 車位主人未被掃 → 無從判定，保留；車位主人被掃到但實例已不在 found → 剪掉。
            if key_owner not in scanned_owners or key in present:
                kept[key] = value
        if kept:
            new_attacked[str(rid)] = kept

    # 一次寫回：known 批次 + results + attacked + last_run（全輪最多 4 次全檔寫）。
    store.bulk_upsert_known(pending)
    results = {str(rid): found[rid] for rid in target_ids}
    store.set_results(results)
    store.set_attacked(new_attacked)
    summary = {
        "scanned": len(scanned_owners),
        "queued": len(queue),
        # per-target 命中數（dict），保留供細分；dashboard 需要純量避免 Number(dict)=NaN。
        "found": {str(rid): len(found[rid]) for rid in target_ids},
        "found_total": sum(len(found[rid]) for rid in target_ids),
        "target_guilds": sorted(target_guilds),
    }
    store.set_last_run({"ts": now_fn(), **summary})
    return summary


# ============================================================================
# 冷啟 bootstrap：一次性 guild-scan 灌 known_players（依賴注入、純可測）
# ----------------------------------------------------------------------------
# 冷啟時 known_players 空，只靠 scan_cycle 的雪球擴散很慢。此函式借一台 idle 裝置
# 掃全服公會 → 成員 → 批次補等級，一次性把 level>=5 公會的全體成員（含 name/guild/
# level）灌進 known，讓權重掃描一開始就有正確的候選車位主人。純函式：不碰 socket / sleep /
# 時鐘，只用注入的 caller / sleeper / now_fn / should_continue + mount_scan 的 enc/parse。
# ============================================================================

# caller(cmd, body) -> bytes | None（None = 呼叫失敗 / 跳過該次）
Caller = Callable[[int, bytes], Optional[bytes]]
# should_continue() -> bool（False = 停止當前階段，例如借用裝置即將自我喚醒）
ShouldContinue = Callable[[], bool]


def bootstrap_known(
    caller: Caller,
    sleeper: Sleeper,
    now_fn: NowFn,
    should_continue: ShouldContinue,
    *,
    deadline: float,
    cooldown_s: float = 3.0,
    min_guild_level: int = 5,
    max_pages: int = 90,
    batch: int = 40,
    on_progress: Optional[Callable[..., None]] = None,
) -> dict[int, dict]:
    """一次性掃全服公會，回傳 ``{role_id: {"name", "guild", "level"}}`` 供批次灌 known。

    ``on_progress``（選用、DI）：階段1 每頁回報 ``guilds=``、階段2 每公會回報
    ``members=``、階段3 每批回報 ``known=``，供 dashboard 即時顯示玩家庫建立進度。
    為 None 時完全不影響行為與回傳（維持純函式可測）。

    三階段（每次網路呼叫前先 ``sleeper(cooldown_s)``；每次迴圈開頭若
    ``now_fn() >= deadline`` 或 ``not should_continue()`` 即停止該階段）：

    1. **公會蒐集**：``typ ∈ (2, 3)``，逐頁 :data:`mount_scan.CMD_GUILD_SEARCH`；
       收 ``guild_level >= min_guild_level`` 且有 guild_id 的公會進 ``{guild_id: name}``。
       某 type 的某「非空頁」若 0 筆合格公會即提早收該 type（列表依等級遞減排序，深頁
       全是低等公會）；或 ``page >= page_num-1`` 時收該 type。回應 None → 跳過該頁。
    2. **成員展開**：對每個公會 :data:`mount_scan.CMD_GUILD_MEMBERS`；成員以
       ``setdefault`` 收錄（同一玩家橫跨多公會時「先到的公會」勝出）。
    3. **等級補齊**：把成員 id 依 ``batch`` 切塊，逐塊 :data:`mount_scan.CMD_ROLE_OTHERS`，
       回填 ``level``。
    """
    def _stop() -> bool:
        # 時間預算用盡或借用裝置即將自我喚醒 → 停止當前階段。
        return now_fn() >= deadline or not should_continue()

    # --- 階段 1：蒐集 level>=min_guild_level 的公會 --------------------------
    guilds: dict[int, str | None] = {}
    for typ in (2, 3):
        if _stop():
            break
        for page in range(max_pages):
            if _stop():
                break
            sleeper(cooldown_s)
            body = caller(mount_scan.CMD_GUILD_SEARCH,
                          mount_scan.enc_guild_search(typ, page))
            if body is None:
                continue  # 該頁呼叫失敗 → 跳過（不影響提早收 type 的判定）
            parsed = mount_scan.parse_guild_search(body)
            page_num = parsed.get("page_num")
            page_guilds = parsed.get("guilds", [])
            qualifying = 0
            for g in page_guilds:
                gid = g.get("guild_id")
                if gid and (g.get("guild_level") or 0) >= min_guild_level:
                    guilds.setdefault(gid, g.get("name"))
                    qualifying += 1
            if on_progress:
                on_progress(guilds=len(guilds))  # 階段1：每頁回報累積合格公會數
            # 提早收該 type：非空頁 0 筆合格（深頁全低等）或已到最後一頁。
            if (page_guilds and qualifying == 0) or \
               (page_num is not None and page >= page_num - 1):
                break

    # --- 階段 2：展開各公會成員（先到的公會勝出）----------------------------
    members: dict[int, dict] = {}
    for gid, gname in guilds.items():
        if _stop():
            break
        sleeper(cooldown_s)
        body = caller(mount_scan.CMD_GUILD_MEMBERS, mount_scan.enc_guild_members(gid))
        if body is None:
            continue
        for m in mount_scan.parse_guild_members(body):
            rid = m.get("role_id")
            if rid:
                members.setdefault(rid, {"name": m.get("name"), "guild": gname})
        if on_progress:
            on_progress(members=len(members))  # 階段2：每公會回報累積成員數

    # --- 階段 3：批次補等級 --------------------------------------------------
    ids = list(members)
    for i in range(0, len(ids), batch):
        if _stop():
            break
        sleeper(cooldown_s)
        body = caller(mount_scan.CMD_ROLE_OTHERS,
                      mount_scan.enc_role_others(ids[i:i + batch]))
        if body is None:
            continue
        for rid, info in mount_scan.parse_role_others(body).items():
            if rid in members:
                members[rid]["level"] = info.get("level")
        if on_progress:
            on_progress(known=len(members))  # 階段3：每批回報 known（成員）數

    return members


# ============================================================================
# Task 6：daemon + 真 IO 綁定 + 冪等啟動
# ----------------------------------------------------------------------------
# 共用 store singleton、hourly daemon（可停可催的 Event.wait）、真 reader（借
# dashboard 純 WS 連線讀車位）。master-only；import 本模組不啟動任何 thread、不讀
# 任何檔——只有呼叫 ensure_mount_tracker_started() 才起 daemon。
# ============================================================================

# 共用 store（dashboard 與 daemon 讀同一份）。
_store: Optional[MountTrackerStore] = None
_store_lock = threading.Lock()

# daemon thread 狀態（冪等啟動）。
_thread: Optional[threading.Thread] = None
_started = False
_start_lock = threading.Lock()
# 可被 set() 提早喚醒（催掃）、亦作為 daemon 迴圈之間的睡眠閘。
_wake = threading.Event()

_CYCLE_INTERVAL_SEC = 3600.0
# 找滿目標後改用的「放慢」間隔（3 小時）：目標坐騎都已找到就沒必要每小時再打全服 WS。
_IDLE_INTERVAL_SEC = 10800.0
# 每個目標「找滿」的上限筆數（與 scan_cycle 的 max_per_target 對齊）。
_MAX_PER_TARGET = 5
# 判定「找滿」時容許的缺額：部分實例可能剛好收不到，留 2 筆寬容避免一直卡在快掃。
_SATISFIED_GRACE = 2
# 冷啟 bootstrap 單輪時間預算（秒）：全服公會掃描較久，給 20 分鐘上限，逾時就地收工
# （已灌到的照樣寫入、標記完成，剩下的交給雪球補齊）。
_BOOTSTRAP_BUDGET_S = 1200.0


def _cooldown(sec: float) -> None:
    """掃描專用冷卻：真的睡 ``sec`` 秒。

    刻意用 ``time.sleep`` 而非 ``_wake.wait``——``_wake`` 是每小時間隔 / 立即催掃的
    event，一旦被 set()，用它當 sleeper 會讓 cycle 內剩下的冷卻全部瞬間返回，導致
    WS 打太快（封號風險）。冷卻與間隔必須分離。
    """
    time.sleep(sec)


def get_store() -> MountTrackerStore:
    """回傳全域共用的 MountTrackerStore singleton（dashboard 與 daemon 共用）。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = MountTrackerStore()
        return _store


def wake_scan() -> None:
    """催醒 daemon 立刻掃一輪（例如剛把開關切成啟用時）。

    只 set() 間隔 event，daemon 迴圈末端的 ``_wake.wait`` 會立即返回並跑下一輪；
    未起 daemon 時亦為安全 no-op（event 已 set，首輪起跑時立即通過）。
    """
    _wake.set()


def _get_ws_client(dev: str):
    """借用 ``dev`` 的 dashboard 純 WS 連線並取得 live client。

    透過 ``ws_session.ensure`` 註冊借用（會暫停該機 bot loop，讓喚醒禮讓），
    再取回 live client。取不到（連線失敗 / 已死）回 None。此間接層供測試 monkeypatch，
    測試不需真的開 socket。
    """
    try:
        from control_panel import ws_session
    except Exception:  # noqa: BLE001
        return None
    res = ws_session.ensure(dev)
    if res.get("status") != "ok":
        return None
    return ws_session.get_client(dev)


def _release_dev(dev: str) -> None:
    """歸還借用的裝置連線並恢復其 bot loop（冪等）。"""
    try:
        from control_panel import ws_session
        ws_session.disconnect(dev)
    except Exception:  # noqa: BLE001 — 歸還失敗不可弄垮 cycle
        logger.debug("[mount-tracker] release dev=%s failed", dev, exc_info=True)


def _read_lot(dev: str, owner_role_id: int,
              on_ensure: Optional[Callable[[str], None]] = None) -> Optional[dict]:
    """真 reader：借 ``dev`` 讀 ``owner_role_id`` 的車位佔用並解析。任何例外回 None。

    ``on_ensure``：``ws_session.ensure`` 成功（拿到 live client）即回呼一次，讓呼叫端在
    「連線建立當下」就登記借用——即使本次 ``call`` 立刻被踢，收尾仍會歸還此連線，杜絕洩漏。
    """
    client = _get_ws_client(dev)
    if client is None:
        return None
    if on_ensure is not None:
        on_ensure(dev)
    try:
        body = client.call(mount_scan.CMD_LOT_INFO,
                           mount_scan.enc_lot_info(int(owner_role_id)))
    except Exception:  # noqa: BLE001 — 被踢 / timeout / 例外一律視為讀失敗
        logger.debug("[mount-tracker] read lot failed dev=%s owner=%s",
                     dev, owner_role_id, exc_info=True)
        return None
    return mount_scan.parse_lot_occupants(body)


def _safe_call(client, cmd: int, body: bytes) -> Optional[bytes]:
    """對借用中的 live client 發一次 WS 呼叫，供 bootstrap 的 caller 用。

    被踢（``is_kicked()``）/ timeout / 任何例外一律回 None，讓 :func:`bootstrap_known`
    當作「該次呼叫跳過」而不中斷整個 bootstrap。
    """
    try:
        if client.is_kicked():
            return None
        return client.call(cmd, body, timeout=12)
    except Exception:  # noqa: BLE001 — 被踢 / timeout / 例外一律視為該次呼叫失敗
        return None


def _focus_owners_if_satisfied(store: MountTrackerStore) -> Optional[list[int]]:
    """所有目標都收滿（每個目標達 :data:`_MAX_PER_TARGET`）→ 回目前結果中的車位主人清單
    （聚焦重掃用）；否則回 None（全域掃描）。

    任一坐騎移走使某目標未滿 → 回 None，下輪自動回全域掃描把缺的找回來。回傳去重且保序。
    """
    targets = store.get_targets()
    if not targets:
        return None
    results = store.get_results() or {}
    full = all(
        len(results.get(str(t["role_id"]), [])) >= _MAX_PER_TARGET
        for t in targets if t.get("role_id") is not None
    )
    if not full:
        return None
    owners: list[int] = []
    seen: set = set()
    for rows in results.values():
        for e in rows:
            o = e.get("owner")
            if o is not None and int(o) not in seen:
                seen.add(int(o))
                owners.append(int(o))
    return owners or None


def _run_one_cycle() -> None:
    """跑一輪真掃描：並行借「所有」安全 idle 裝置、連線重用、結束時歸還所有借用連線。

    每台安全 idle 且能建立連線的裝置各開一條 worker 並行掃描；scan_cycle 內每次讀取前
    以 still_idle(dev) 重檢「仍 idle 且尚未即將自我喚醒」，一旦某台進入自身喚醒窗即收掉
    該 worker（該連線由 finally 統一歸還），讓位給它自身 bot loop（spec §6）。scan_cycle
    只看到 reader / still_idle / worker_devices 三個注入點。
    """
    store = get_store()
    borrowed: list[str] = []

    def _mark_borrowed(dev: str) -> None:
        # ensure 成功即登記借用（連線建立當下），即使本次讀取被踢，收尾 finally 仍會歸還。
        if dev not in borrowed:
            borrowed.append(dev)

    def still_idle(dev: str) -> bool:
        # 續用判定：仍 idle 且尚未即將自我喚醒。不可用 is_safe_to_borrow——它要求
        # _ws_active==False，但我們正持有此機 WS 連線（active），會誤判不可借。
        states = _states()
        return _is_idle(dev, states) and not _about_to_wake(dev, states)

    def reader(dev: str, owner: int) -> Optional[dict]:
        # on_ensure 在連線建立當下登記借用（見 _read_lot），故被踢也不會漏歸還。
        return _read_lot(dev, owner, on_ensure=_mark_borrowed)

    store.set_running(True)
    try:
        # 本輪起跑：先決定會不會 bootstrap，據以重置即時進度的 phase / 計數。
        will_bootstrap = store.get_bootstrap_done() is None
        _reset_progress("bootstrap" if will_bootstrap else "scan")
        # --- 冷啟一次性 guild-scan bootstrap（僅在尚未跑過時）------------------
        # 借一台 idle 裝置掃全服公會灌 known_players，之後不再重跑。借用透過
        # _mark_borrowed 登記，收尾 finally 一律歸還；完成（即使部分）就標記，
        # 避免每輪重灌，剩下的交給 scan_cycle 雪球補齊。
        if will_bootstrap:
            dev = pick_idle_device()
            if dev:
                client = _get_ws_client(dev)
                _mark_borrowed(dev)  # ensure 當下即登記借用，收尾 finally 會歸還
                if client:
                    deadline = time.time() + _BOOTSTRAP_BUDGET_S
                    # 借用裝置一旦接近自身喚醒窗即停止 bootstrap，讓位給它的 bot loop。
                    should_continue = lambda: not _about_to_wake(dev, _states())  # noqa: E731
                    caller = lambda cmd, body: _safe_call(client, cmd, body)      # noqa: E731
                    # on_progress：把 bootstrap 三階段的 guilds/members/known 灌進記憶體進度。
                    seed = bootstrap_known(caller, _cooldown, time.time,
                                           should_continue, deadline=deadline,
                                           on_progress=lambda **kw: _set_progress(**kw))
                    if seed:
                        store.bulk_upsert_known(seed)   # 一次批次寫回（NAS I/O 友善）
                    store.set_bootstrap_done(time.time())  # 即使部分完成也標記，避免重跑
                    wake_scan()  # 催下一輪（很快）跑一次「已灌種子」的正式掃描
                    return       # 本輪跳過 scan_cycle（finally 仍歸還借用 + set_running）
            # 無 idle 裝置（dev 為 None）→ 不標記完成，下一輪重試 bootstrap。
        # sleeper 用 _cooldown（真 sleep），不可用 _wake.wait——見 _cooldown docstring。
        _set_progress(phase="scan")  # 進入正式掃描階段（頁面顯示 scanned/found/known）
        # 借「所有」目前安全可借的裝置並各自建立 WS 連線，交給 scan_cycle 各台並行掃描
        # （每台自冷卻）。只納入連得上的裝置，避免壞台空跑吃掉共享 queue。
        worker_devices: list[str] = []
        for dev in CANDIDATES:
            if not is_safe_to_borrow(dev):
                continue
            client = _get_ws_client(dev)
            # ensure 可能已登記借用（即使隨後 get_client 回 None）→ 一律列入 borrowed，
            # 由 finally 統一歸還，杜絕「連上但取不到 client」時的借用洩漏。
            _mark_borrowed(dev)
            if client is not None:
                worker_devices.append(dev)
        # 全部目標都收滿 → 聚焦模式：只重掃目前停著坐騎的車位主人（偵測是否移動/更新），
        # 不再掃廣域候選。未滿則 focus=None → 全域掃描把缺的找回來。
        focus = _focus_owners_if_satisfied(store)
        scan_cycle(store, reader, worker_devices, still_idle, _cooldown, time.time,
                   focus_owners=focus, on_progress=lambda **kw: _set_progress(**kw))
    finally:
        for dev in borrowed:
            _release_dev(dev)
        store.set_running(False)
        _set_progress(phase="idle")  # 收尾：頁面停止顯示「掃描中…」


def _next_interval() -> float:
    """依上輪掃描成果決定下一輪間隔：目標坐騎幾乎都找齊 → 放慢（省 WS 打點）；否則維持快掃。

    找滿門檻 = ``max(1, len(targets) * _MAX_PER_TARGET - _SATISFIED_GRACE)``。有目標且上輪
    ``found_total`` 達門檻視為找滿，回 :data:`_IDLE_INTERVAL_SEC`；否則（含無目標 / 找不夠）
    回 :data:`_CYCLE_INTERVAL_SEC`，讓使用者剛加目標或還缺坐騎時保持快掃。
    """
    store = get_store()
    targets = store.get_targets()
    found_total = (store.get_last_run() or {}).get("found_total") or 0
    if targets and found_total >= max(1, len(targets) * _MAX_PER_TARGET - _SATISFIED_GRACE):
        return _IDLE_INTERVAL_SEC
    return _CYCLE_INTERVAL_SEC


def _run_loop() -> None:
    """daemon 主迴圈：啟用時掃一輪，找滿目標則放慢間隔；可被 _wake.set() 催醒。"""
    logger.info("[mount-tracker] daemon started")
    while True:
        try:
            from utils.dashboard_settings import get_mount_tracker_enabled
            if get_mount_tracker_enabled():
                _run_one_cycle()
        except Exception:  # noqa: BLE001 — 迴圈永不因單輪錯誤而死
            logger.warning("[mount-tracker] cycle error", exc_info=True)
        # 自適應間隔：找滿目標放慢到 _IDLE_INTERVAL_SEC，否則維持 _CYCLE_INTERVAL_SEC。
        interval = _next_interval()
        _set_progress(next_scan_ts=time.time() + interval)
        _wake.wait(interval)
        _wake.clear()


def ensure_mount_tracker_started() -> None:
    """啟動單一背景 daemon thread（master-only、冪等）。"""
    global _thread, _started
    with _start_lock:
        if _started and _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(
            target=_run_loop, name="mount-tracker", daemon=True)
        _thread.start()
        _started = True
