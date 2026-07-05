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


# ============================================================================
# Task 5：scan_cycle — 掃描核心（依賴注入、純可測）
# ----------------------------------------------------------------------------
# 以權重排序 known players 當「房東（owner）」候選，逐一借 idle 裝置開一次性 WS
# 連線讀該房東的車位佔用，比對是否停著追蹤目標；順手把新看到的佔用者滾進 known
# （雪球）、更新房東車位加成與 host_hits。所有 IO（reader / idle_picker / sleeper /
# now_fn）皆注入，測試不碰真 socket / 真 sleep / 真時鐘。
# ============================================================================

# reader(device, owner_role_id) -> parse_lot_occupants dict | None（None=讀失敗）
Reader = Callable[[str, int], Optional[dict]]
IdlePicker = Callable[[], Optional[str]]
Sleeper = Callable[[float], Any]
NowFn = Callable[[], float]


def scan_cycle(
    store: MountTrackerStore,
    reader: Reader,
    idle_picker: IdlePicker,
    sleeper: Sleeper,
    now_fn: NowFn,
    *,
    top_n: int = 1600,
    budget_s: float = 1500,
    cooldown_s: float = 3.0,
    max_per_target: int = 5,
) -> dict:
    """跑一輪坐騎掃描，回傳摘要 dict。

    流程：
      1. 無追蹤目標 → 直接回 ``{"skipped": "no_targets"}``。
      2. 以 known players（含 targets 自身）依 :func:`mount_scan.weight` 由高到低
         排序，取前 ``top_n`` 個當房東候選。
      3. 逐一借 idle 裝置讀車位；每讀一次前 sleeper(cooldown_s)；無裝置則 sleeper
         後換下一個房東。
      4. 車位裡的佔用者：是追蹤目標且該目標尚未收滿 ``max_per_target`` → 收錄；
         一律 upsert 進 known（雪球）。
      5. 更新房東 coin（車位加成）與 host_hits，寫回 results / last_run。
      6. 超過 ``budget_s`` 或所有目標都收滿 → 提早結束。
    """
    targets = store.get_targets()
    if not targets:
        return {"skipped": "no_targets"}

    target_ids = {int(t["role_id"]) for t in targets if t.get("role_id") is not None}
    known = store.get_known()

    # 目標所屬公會集合（供 weight 的同公會加權）。
    target_guilds: set = set()
    for rid in target_ids:
        guild = (known.get(str(rid)) or {}).get("guild")
        if guild:
            target_guilds.add(guild)

    # 房東候選 = known players（含尚未在 known 的 target 自身），依權重排序取 top_n。
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
    scanned_owners: set = set()   # 本輪成功讀到車位的房東
    host_hit_ids: set = set()     # 本輪車位停著追蹤目標的房東

    for owner_entry in queue:
        # 所有目標都收滿 → 沒必要再掃。
        if all(len(found[rid]) >= max_per_target for rid in target_ids):
            break
        # 超過時間預算 → 提早收工，剩下的留給下一輪。
        if now_fn() >= deadline:
            break

        owner = int(owner_entry["role_id"])
        dev = idle_picker()
        if dev is None:
            sleeper(cooldown_s)  # 暫無 idle 裝置：冷卻後換下一個房東
            continue

        sleeper(cooldown_s)      # 每次讀取前冷卻，避免打太快
        lot = reader(dev, owner)
        if lot is None:
            continue             # 讀失敗（被踢 / 例外）→ 換下一個房東
        scanned_owners.add(owner)

        for space in lot.get("spaces", []):
            occ = space.get("role_id")
            if not occ:
                continue
            occ = int(occ)
            name = space.get("name")
            store.upsert_known(occ, name=name)  # 雪球：新佔用者滾進 known
            if occ in target_ids and len(found[occ]) < max_per_target:
                found[occ].append({
                    "owner": owner,
                    "pos": space.get("pos"),
                    "start_time": space.get("start_time"),
                    "found_ts": now_fn(),
                    "name": name,
                })
                host_hit_ids.add(owner)

        # 房東車位加成（菇車幣）+ 掃描時間戳。
        bonus = parking_bonus.lot_bonus(lot.get("skin_list", []))
        store.upsert_known(owner, coin=bonus.get("coin"), last_scanned_ts=now_fn())

    _update_host_hits(store, scanned_owners, host_hit_ids)

    results = {str(rid): found[rid] for rid in target_ids}
    store.set_results(results)
    summary = {
        "scanned": len(scanned_owners),
        "queued": len(queue),
        "found": {str(rid): len(found[rid]) for rid in target_ids},
        "target_guilds": sorted(target_guilds),
    }
    store.set_last_run({"ts": now_fn(), **summary})
    return summary


def _update_host_hits(store: MountTrackerStore, scanned_owners: set, host_hit_ids: set) -> None:
    """更新本輪掃過房東的 host_hits：停著目標 +1（上限 5），否則 decay，不 <0。

    只動本輪實際掃到的房東——沒掃到的沿用舊值（下一輪權重排序仍會優先掃到高
    host_hits 者，故會自我收斂，不需每輪掃全表）。
    """
    known_now = store.get_known()
    for owner in scanned_owners:
        cur = int((known_now.get(str(owner)) or {}).get("host_hits", 0) or 0)
        if owner in host_hit_ids:
            store.upsert_known(owner, host_hits=min(cur + 1, 5))
        elif cur > 0:
            store.upsert_known(owner, host_hits=cur - 1)


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


def get_store() -> MountTrackerStore:
    """回傳全域共用的 MountTrackerStore singleton（dashboard 與 daemon 共用）。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = MountTrackerStore()
        return _store


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


def _read_lot(dev: str, owner_role_id: int) -> Optional[dict]:
    """真 reader：借 ``dev`` 讀 ``owner_role_id`` 的車位佔用並解析。任何例外回 None。"""
    client = _get_ws_client(dev)
    if client is None:
        return None
    try:
        body = client.call(mount_scan.CMD_LOT_INFO,
                           mount_scan.enc_lot_info(int(owner_role_id)))
    except Exception:  # noqa: BLE001 — 被踢 / timeout / 例外一律視為讀失敗
        logger.debug("[mount-tracker] read lot failed dev=%s owner=%s",
                     dev, owner_role_id, exc_info=True)
        return None
    return mount_scan.parse_lot_occupants(body)


def _run_one_cycle() -> None:
    """跑一輪真掃描：借 idle 裝置、連線重用、結束時歸還所有借用連線。

    idle_picker 先沿用本輪已借且仍活著的裝置（連線重用、借用維持短），該裝置被踢
    才換新的 idle 裝置；scan_cycle 只看到 reader / idle_picker 兩個注入點。
    """
    store = get_store()
    borrowed: list[str] = []

    def idle_picker() -> Optional[str]:
        # 沿用已借且連線仍活著的裝置（避免每個房東都重挑 / 重連）。
        for dev in borrowed:
            if _ws_active(dev):
                return dev
        return pick_idle_device()

    def reader(dev: str, owner: int) -> Optional[dict]:
        lot = _read_lot(dev, owner)
        if dev not in borrowed and _ws_active(dev):
            borrowed.append(dev)   # ensure 成功建立了連線 → 記錄以便收尾歸還
        return lot

    store.set_running(True)
    try:
        scan_cycle(store, reader, idle_picker, _wake.wait, time.time)
    finally:
        for dev in borrowed:
            _release_dev(dev)
        store.set_running(False)


def _run_loop() -> None:
    """daemon 主迴圈：啟用時每小時掃一輪；可被 _wake.set() 催醒。"""
    logger.info("[mount-tracker] daemon started")
    while True:
        try:
            from utils.dashboard_settings import get_mount_tracker_enabled
            if get_mount_tracker_enabled():
                _run_one_cycle()
        except Exception:  # noqa: BLE001 — 迴圈永不因單輪錯誤而死
            logger.warning("[mount-tracker] cycle error", exc_info=True)
        _wake.wait(_CYCLE_INTERVAL_SEC)
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
