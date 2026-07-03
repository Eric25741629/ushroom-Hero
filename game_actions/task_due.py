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
    # 對照 game_actions/daily_pipeline.py:225-230
    record = json_manager.return_time(ip, name="地獄之門")
    if record is None:
        should_execute = True
    else:
        should_execute = record.get("is_next_day", False)
    return bool(should_execute and now.minute < 20)


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
    # 對照 battle/cloud.py:219-244（run_weekly_cloud_fighting_single 的 due 判斷）
    if now.weekday() != 0:  # 僅週一
        return False
    if now.hour < 3:  # 週一凌晨 3 點後
        return False

    try:
        rec = json_manager.return_time(ip, name="cloud_fighting_weekly")
    except Exception:
        rec = None

    # 已於本週執行過 → not due；週序比較異常時視為未執行（due），與原邏輯一致。
    if rec and isinstance(rec, dict) and rec.get("timestamp"):
        try:
            last_ts = float(rec.get("timestamp"))
            last_week = datetime.datetime.fromtimestamp(last_ts, now.tzinfo).isocalendar()[1]
            curr_week = now.date().isocalendar()[1]
            if last_week == curr_week:
                return False
        except Exception:
            return True
    return True


def _due_mushroom_arena(ip: str, now: datetime.datetime) -> bool:
    # 對照 game_actions/periodic_tasks.py:44-48（_run_periodic_cycle 的 cycle + daily_limit）
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
