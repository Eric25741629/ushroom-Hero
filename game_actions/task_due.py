"""任務 due-registry — 各 client 任務「今天/本週期是否該執行」的單一真相。

Phase A：把散在各 execute 函式內的 due 判斷收斂到這裡的純 predicate。
所有判準 **複用現有 json_manager 記錄 + 時間/排程**，不重寫 due 數學。

predicate 只回答「due 與否」：只讀記錄 + 時間，**不 side-effect、不寫記錄、
不碰遊戲客戶端**。時間一律台北時區。

對外只有 ``is_due(task, ip, now=None) -> bool``；未知 task raise ``KeyError``，
呼叫端自行決定 fallback。
"""

import datetime
from typing import Callable, Dict, Optional

import json_manager

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _resolve_now(now: Optional[datetime.datetime]) -> datetime.datetime:
    return now if now is not None else datetime.datetime.now(_TPE)


# --------------------------------------------------------------------------
# predicates（每個對照現有邏輯 1:1）
# --------------------------------------------------------------------------
def _due_hellgate(ip: str, now: datetime.datetime) -> bool:
    # 每日一次；時段窗 01:00~23:00（00:00~01:00 不跑，避開跨日重置窗）。
    # 2026-08-09 使用者要求：地獄之門已不再限每小時 0~20 分，任一時刻皆可打，
    # 故只保留時段下限（hour>=1），移除原 minute<20 限制。
    record = json_manager.return_time(ip, name="地獄之門")
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_day", False)
    return bool(should_execute and now.hour >= 1)


def _due_daily_acceleration(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_tasks.py:16-21
    record = json_manager.return_time(ip, name="daily_acceleration")
    if record is None:
        return True
    return bool(record.get("is_next_day", False))


def _due_arena_challenges(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_tasks.py:82-87
    record = json_manager.return_time(ip, name="arena_challenges")
    if record is None:
        return True
    return bool(record.get("is_next_day", False))


def _due_mount_sprint(ip: str, now: datetime.datetime) -> bool:
    # 對照 rank_events.py:77-109（park_spring 的 due 判斷）
    # lazy import 破循環：rank_events.park_spring 反過來會 import 本模組。
    import rank_events

    # 1. 僅限活動開放窗（週二~週三22:00）
    if not rank_events.is_mount_sprint_open(now):
        return False
    # 2. 本週是否已執行過（record 存在且非跨週 → not due）
    record = json_manager.return_time(ip, name=rank_events.SPRINT_RECORD)
    if record and not record.get("is_next_week", True):
        return False
    # 3. 是否為 4 週週期的執行週
    should_run, _ = json_manager.should_execute_cycle(
        ip,
        rank_events.SPRINT_RECORD,
        cycle_weeks=4,
        allowed_weekdays=rank_events.ALLOWED_WEEKDAYS,
        today=now.date(),
    )
    return bool(should_run)


def _due_cloud_fighting(ip: str, now: datetime.datetime) -> bool:
    # 每週一次；放寬自「僅週一」→ 任一天手機在線即可補跑（2026-07-05，使用者要求）。
    # 原「僅週一」在手機該週一整天 ADB 離線時會整週報廢：雲端戰鬥是純視覺 OCR 任務、
    # 無 WS 版，ADB 不在線就跑不了，錯過週一就得等下週。改為 once-per-ISO-week，
    # 哪天手機在線就哪天補打。僅週一保留凌晨 3 點下限，避開每週重置前空跑；
    # 週二起重置已過，無下限。
    if now.weekday() == 0 and now.hour < 3:
        return False

    try:
        rec = json_manager.return_time(ip, name="cloud_fighting_weekly")
    except Exception:
        rec = None

    # 本 ISO 週已執行過 → not due；週序比較異常時視為未執行（due），與原邏輯一致。
    # 比 (iso_year, iso_week) 而非只比週序號，避免跨年同週號誤判。
    if rec and isinstance(rec, dict) and rec.get("timestamp"):
        try:
            last_ts = float(rec.get("timestamp"))
            last_yw = datetime.datetime.fromtimestamp(last_ts, now.tzinfo).isocalendar()[:2]
            curr_yw = now.isocalendar()[:2]
            if tuple(last_yw) == tuple(curr_yw):
                return False
        except Exception:
            return True
    return True


def _due_mushroom_arena(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/periodic_tasks.py（固定日曆活動週 + daily_limit）
    # lazy import：periodic_tasks 會拉 img_tools/tools 等重模組。
    from game_actions import periodic_tasks

    should, _ = periodic_tasks.should_execute_mushroom_arena(ip)
    if not should:
        return False
    daily = json_manager.return_time(ip, name="mushroom_arena_daily")
    if daily is None:
        return True
    return bool(daily.get("is_next_day", False))


def _due_sea(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/daily_pipeline.py:402-405（_run_periodic_cycle 用
    # should_execute_sea_with_cooldown 當 should_execute_fn，無 daily_limit）。
    # 該函式已含時段視窗 + 冷卻 + 日曆錨點。
    should, _ = json_manager.should_execute_sea_with_cooldown(ip, now=now)
    return bool(should)


def _due_wanshen(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/dungeon_scheduler.py:41-61（_run_weekly_dungeon 的排程判斷）。
    # 只抽「本週未做（is_next_week）+ 星期時間窗」；enable_wanshen flag 與
    # 主頁面/戰鬥皆為呼叫端/side-effect，不進 predicate。
    record = json_manager.return_time(ip, name="萬神試煉")
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_week", False)
    if not should_execute:
        return False
    # 時間窗：週一下午(weekday==0 且 hour>12) 或 週二~週六(1<=weekday<=5)，週日(6)跳過。
    # datetime.weekday()：Mon=0..Sun=6，與 time.struct_time.tm_wday 一致。
    return bool(
        (now.weekday() == 0 and now.hour > 12)
        or (1 <= now.weekday() <= 5)
    )


def _due_biweekly(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/dungeon_scheduler.py:94-101（_run_biweekly_dungeon 的排程判斷）。
    # 只抽「本雙週未做（is_next_biweek）+ 週六/日 20:xx 時間窗」；
    # 原碼的 ip=="emulator-5556" 屬裝置範圍限定（等同 enable，呼叫端只在該機呼叫），
    # 不進 predicate。
    record = json_manager.return_time(ip, name="雙週副本")
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_biweek", False)
    if not should_execute:
        return False
    return bool(now.weekday() in (5, 6) and now.hour == 20)


def _due_ladder_reward(ip: str, now: datetime.datetime) -> bool:
    # 對照 ws_token/ladder_reward.py:165-183（is_due gate）— 被
    # game_actions/ladder_reward_weekly.py:27 完整委派。gate = 週二 + 有記錄 +
    # 該記錄 enabled + 有 body + 本週未套用；純讀 ws_token/data/ladder_reward.json，
    # 無 client/page side-effect。此處的 rec.enabled 是「捕捉記錄」旗標（排程/記錄層），
    # 非 config enable flag。
    # lazy import：task_due 頂層只留 json_manager。ladder_reward 依賴輕（json/os/pathlib）。
    from ws_token import ladder_reward

    due, _reason = ladder_reward.is_due(ip, now.date())
    return bool(due)


def _due_statue_weekly(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/statue_weekly.py:88-99（_should_execute_for，純日期規則：
    # 週五 + 每日一次）+ :130-136（_should_execute_for_ip 讀 json_manager 記錄）。
    # 直接複用該純函式；enable flag 與 WS fast-exit 是呼叫端責任，不進 predicate。
    # lazy import：statue_weekly 頂層 import img_tools（重）→ 函式內 import。
    from game_actions import statue_weekly

    return bool(statue_weekly._should_execute_for_ip(ip, today=now.date()))


def _due_dragon_realm(ip: str, now: datetime.datetime) -> bool:
    # 單一來源：完全委派 dragon_realm_scheduler._is_due（三周週期活動週 ∧
    # 週三四五 10-22 窗 ∧ per-device 20h 冷卻）。task_due 只包一層，不重寫 due 數學。
    # use_dragon_realm flag 是呼叫端責任，不進 predicate。
    # lazy import：dragon_realm_scheduler 會拉 dragon_realm 套件（重），函式內 import。
    from game_actions import dragon_realm_scheduler as drs

    return bool(drs._is_due(ip, now))


def _due_fannaoxiao(ip: str, now: datetime.datetime) -> bool:
    # 單一來源：完全委派 fannaoxiao_scheduler._is_due（每日一次：is_record_expired
    # 20h 冷卻 + 跨日視為過期）。task_due 只包一層，不重寫。now 不參與（純記錄冷卻）。
    # enable_fannaoxiao / backend==web_h5 是呼叫端責任，不進 predicate。
    # lazy import：fannaoxiao_scheduler 依賴輕，但沿用 lazy 慣例避免頂層擴散。
    from game_actions import fannaoxiao_scheduler as fx

    return bool(fx._is_due(ip))


def _due_gacha_skill_partner(ip: str, now: datetime.datetime) -> bool:
    # 對照 ws_token/runner.py:_run_gacha_free 的每日 gate（ws_state 的
    # gacha_free.last_date）。== 今天(台北) → 本日已跑客戶端抽 → not due；
    # 否則 due。讀不到 state / 無 gacha_free / 無 last_date → 保守回 True
    # （due = 仍需客戶端，Phase D 不會誤跳這個唯一靠截圖紅點的任務）。
    # lazy import：task_due 頂層只依賴 json_manager；ws_token.state 依賴輕
    #（json/os/pathlib）但沿用 lazy 慣例避免頂層擴散並防 import 循環。
    try:
        from ws_token import state as ws_state

        st = ws_state.load_state(ip)
        last_date = (st.get("gacha_free") or {}).get("last_date")
    except Exception:
        return True
    if not last_date:
        return True
    return bool(last_date != now.strftime("%Y-%m-%d"))


_REGISTRY: Dict[str, Callable[[str, datetime.datetime], bool]] = {
    "地獄之門": _due_hellgate,
    "每日加速": _due_daily_acceleration,
    "競技場挑戰": _due_arena_challenges,
    "坐騎衝刺": _due_mount_sprint,
    "雲端戰鬥": _due_cloud_fighting,
    "菇菇武道會": _due_mushroom_arena,
    "航海": _due_sea,
    "萬神試煉": _due_wanshen,
    "雙週副本": _due_biweekly,
    "天梯每週獎勵": _due_ladder_reward,
    "菇菇雕像每週": _due_statue_weekly,
    "龍骸聖域": _due_dragon_realm,
    "煩惱消": _due_fannaoxiao,
    "抽技能夥伴": _due_gacha_skill_partner,
}


def is_due(task: str, ip: str, now: Optional[datetime.datetime] = None) -> bool:
    """任務今天/本週期是否該執行。未知 task → raise KeyError。

    Args:
        task: 任務名（見 _REGISTRY key）。
        ip: 裝置 id。
        now: 供測試注入；預設台北時間現在。
    """
    try:
        predicate = _REGISTRY[task]
    except KeyError:
        raise KeyError(f"unknown task for due-check: {task!r}")
    return predicate(ip, _resolve_now(now))


# --------------------------------------------------------------------------
# any_client_due — Phase D1：本輪是否還有任何「需要開瀏覽器客戶端」的任務該做。
#
# 每個 task 的 enable gate 對照真實 pipeline/scheduler（1:1 mirror，勿自創）：
#   task          | enable gate（file:line）
#   --------------|-------------------------------------------------------------
#   地獄之門       | cfg.enable_hellgate 預設 True（new_main_v2.py:146 → daily_pipeline.py:217）
#   每日加速       | 無 flag，恆啟用（daily_pipeline.py:330-333）
#   競技場挑戰     | cfg.enable_arena 預設 True（new_main_v2.py:150 → daily_pipeline.py:337）
#   坐騎衝刺       | cfg.enable_mount_sprint 預設 True（rank_events.py:104）
#   雲端戰鬥       | cfg.enable_cloud_battle 預設 True（new_main_v2.py:148 → daily_pipeline.py:446）
#   菇菇武道會     | 無 flag，恆啟用（daily_pipeline.py:375-389）
#   航海          | 無 flag，恆啟用（daily_pipeline.py:400-415）
#   萬神試煉       | cfg.enable_wanshen 預設 True（new_main_v2.py:147 → dungeon_scheduler.py:58）
#   雙週副本       | cfg.enable_biweekly 預設 True AND ip=="emulator-5556"（dungeon_scheduler.py:88）
#   天梯每週獎勵   | backend=="web_h5"（ladder_reward_weekly.py:23-24，僅有 _page 時跑）
#   菇菇雕像每週   | statue_weekly._is_enabled(cfg)（nested statue_weekly.enabled 預設 False）
#   龍骸聖域       | dragon_realm.use_dragon_realm(ip, load_config()) 預設 True（per-device 覆寫 global）
#   煩惱消        | fannaoxiao_scheduler._is_enabled(ip)（enable_fannaoxiao 預設 False AND backend==web_h5）
#
# **排除** 抽技能夥伴（遊戲自理，唯一靠截圖紅點）與車位（WS 自足，無 predicate）。
# --------------------------------------------------------------------------
def _en_always(ip: str, cfg: dict) -> bool:
    return True


def _en_hellgate(ip: str, cfg: dict) -> bool:
    return bool(cfg.get("enable_hellgate", True))


def _en_arena(ip: str, cfg: dict) -> bool:
    return bool(cfg.get("enable_arena", True))


def _en_mount_sprint(ip: str, cfg: dict) -> bool:
    return bool(cfg.get("enable_mount_sprint", True))


def _en_cloud_battle(ip: str, cfg: dict) -> bool:
    return bool(cfg.get("enable_cloud_battle", True))


def _en_wanshen(ip: str, cfg: dict) -> bool:
    return bool(cfg.get("enable_wanshen", True))


def _en_biweekly(ip: str, cfg: dict) -> bool:
    # 裝置範圍限定：呼叫端只在 emulator-5556 呼叫（dungeon_scheduler.py:88）。
    return bool(cfg.get("enable_biweekly", True)) and ip == "emulator-5556"


def _en_ladder(ip: str, cfg: dict) -> bool:
    # web_h5 only：ladder_reward_weekly.run_ladder_reward_if_due 僅在有 _page（web_h5）
    # 時執行；record.enabled / 週二 / body / 本週未套用 皆已在 is_due 內判。
    return str(cfg.get("backend", "adb")).strip().lower() == "web_h5"


def _en_statue(ip: str, cfg: dict) -> bool:
    # 1:1 委派 statue_weekly._is_enabled(cfg)（nested statue_weekly.enabled 預設 False）。
    from game_actions import statue_weekly

    return bool(statue_weekly._is_enabled(cfg))


def _en_dragon(ip: str, cfg: dict) -> bool:
    # 1:1 委派 dragon_realm.use_dragon_realm(ip, load_config())（讀 global/devices，
    # 非 get_device_config；per-device dragon_realm_enabled 覆寫 global，預設 True）。
    import config_manager
    from dragon_realm import use_dragon_realm

    return bool(use_dragon_realm(ip, config_manager.load_config()))


def _en_fannaoxiao(ip: str, cfg: dict) -> bool:
    # 1:1 委派 fannaoxiao_scheduler._is_enabled(ip)（enable_fannaoxiao 預設 False
    # AND backend==web_h5；它自己讀 get_device_config(ip)）。
    from game_actions import fannaoxiao_scheduler as fx

    return bool(fx._is_enabled(ip))


# task → enable predicate（審核對照表，順序即 any_client_due 的短路順序）。
_CLIENT_ENABLE: Dict[str, Callable[[str, dict], bool]] = {
    "地獄之門": _en_hellgate,
    "每日加速": _en_always,
    "競技場挑戰": _en_arena,
    "坐騎衝刺": _en_mount_sprint,
    "雲端戰鬥": _en_cloud_battle,
    "菇菇武道會": _en_always,
    "航海": _en_always,
    "萬神試煉": _en_wanshen,
    "雙週副本": _en_biweekly,
    "天梯每週獎勵": _en_ladder,
    "菇菇雕像每週": _en_statue,
    "龍骸聖域": _en_dragon,
    "煩惱消": _en_fannaoxiao,
}


def any_client_due(ip: str, now: Optional[datetime.datetime] = None) -> bool:
    """本輪是否還有任何『需要開瀏覽器客戶端』的任務該做。

    = OR over 13 個任務的 ``(enable gate) AND is_due(task, ip, now)``。
    **fail-safe**：讀 config / 任一 enable / 任一 predicate raise → 該任務保守當
    「due」→ 回 True（寧可開瀏覽器，絕不誤跳過而漏做任務）。
    """
    resolved = _resolve_now(now)
    try:
        import config_manager

        cfg = config_manager.get_device_config(ip)
    except Exception:
        return True  # 讀 config 失敗 → 保守：當作有任務要做
    for task, enable_fn in _CLIENT_ENABLE.items():
        try:
            enabled = enable_fn(ip, cfg)
        except Exception:
            return True  # enable 讀取失敗 → 該任務保守當 due
        if not enabled:
            continue
        try:
            due = is_due(task, ip, resolved)
        except Exception:
            return True  # predicate 失敗 → 該任務保守當 due
        if due:
            return True
    return False
