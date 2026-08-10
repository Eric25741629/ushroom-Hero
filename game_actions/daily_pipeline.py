"""Daily task pipeline — per-wake task sequence for one device.

Extracted verbatim from `new_main_v2.py` (`_run_daily_tasks`) as Phase 7
of the slim-down plan (plan A — conservative). The 28 tasks run in the
same order, with the same implicit contracts preserved:

- Task 4's stage is reused by Tasks 5 and 6 (guardian / skill partner).
- Task 18 refreshes stage before Task 19's lamp call.
- Device-specific cleanup branches (emulator-5558 / fc65396d) remain at
  the tail of the pipeline.

No behavioral changes — only the callable signature changes: the 10
parameters are now packaged in a `DailyContext` dataclass and the public
entry point is `run(ctx)`.
"""
from __future__ import annotations

import datetime
import importlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import bot_state
import config_manager
import daily_gift_task
import new_battle
import rank_events
import Store
from everyday_mission.Guardian_Spirit_manger import get_Guardian_Spirit
from farm_v2 import manager as farm_manager
from Sea import sea
from Skill import get_skill_and_partner

from game_actions.carpark_scheduler import run_carpark_check_if_due
from game_actions.dragon_realm_scheduler import run_dragon_realm_if_due
from game_actions.fannaoxiao_scheduler import run_fannaoxiao_if_due
from game_actions.escort_scheduler import run_escort_if_due
from game_actions.ladder_reward_weekly import run_ladder_reward_if_due
from game_actions.seven_login_daily import run_seven_login_if_due
from game_actions.daily_tasks import click_arena_challenges, daily_acceleration
from game_actions.dungeon_scheduler import _run_biweekly_dungeon, _run_weekly_dungeon
from game_actions.lamp_scheduler import _run_lamp_if_due
from game_actions.miner_action import oracle
from game_actions.redpack_scheduler import run_redpack_check_if_due
from game_actions.statue_weekly import run_statue_weekly_if_due
from game_actions.periodic_tasks import (
    _run_periodic_cycle,
    mushroom_arena,
    should_execute_mushroom_arena,
)
from game_actions.reward_manager import reward
from game_actions.skill_manager import switch_skill_h5
from game_actions.stage_guard import _run_at_main_page, get_stage_with_check
from game_actions import task_due
from game_actions import special_wanshen
from game_actions.task_registry import (
    TaskOutcome,
    TaskResult,
    iter_pipeline_task_definitions,
)
from runtime_services.device_runtime_service import ForceSleepRequested
from json_manager import (
    is_record_expired,
    return_time,
    should_execute_sea_with_cooldown,
    time_recording,
)
from tools import click_white
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch, save_error_screenshot

# Devices that should skip guardian spirit / skill partner collection.
# Keep legacy behavior: emulator-5558 is excluded from these tasks.
_DEVICE_SKIP_GUARDIAN = {
    "emulator-5558": True,
}


def _sea_dispatch(ip, d, **kwargs):
    """航海 router. H5 backend → sea_v2 (deterministic nav + OCR 駐守/進攻/領獎; 修船
    best-effort, 待補 auto-return-to-base). adb backend → legacy ``Sea.sea`` (sea_v2 is
    H5-only for now). Gated by the ``sea_v2_enabled`` flag (per-device or global);
    default off keeps legacy until enabled in bot_config.json."""
    if getattr(d, "_page", None) is not None:
        from sea_v2 import sea as sea_v2_sea, use_sea_v2
        if use_sea_v2(ip, config_manager.load_config()):
            report = sea_v2_sea(ip, d)
            aborted_reason = getattr(report, "aborted_reason", None)
            if aborted_reason:
                logger.warning("[sea] Sea V2 中止，不寫入完成記錄: %s", aborted_reason)
                return False
            return report
    return sea(ip, d)


class _ConsecutiveMismatchAbort(Exception):
    """連續 N 個任務不在主頁面時中止本輪 pipeline。

    由 ``_run_tasks`` 內部丟出、並在 ``run`` 邊界被攔下：app 已被強制關閉，
    ``run`` 直接返回，讓上層 wake loop 走正常休眠週期、於下次對齊喚醒以全新
    啟動重試（而非讓未攔截的例外冒泡到 new_main_v2 外層 handler 被當成
    「未預期錯誤」，把整條 device thread 拆掉）。
    """

    def __init__(self, message: str, *, report: RunReport | None = None):
        super().__init__(message)
        self.report = report


@dataclass
class _GuardedAction:
    """描述需要由 registry loop 統一執行的主頁面 action。"""

    task_name: str
    mismatch_reason: str
    fn: Any
    step: str = "執行中"
    log: str | None = None
    after: Any = None


@dataclass
class DailyContext:
    """Packages the 10 parameters that `_run_daily_tasks` used to receive."""

    d: Any
    ip: str
    Cnn_model: Any
    clf: Any
    rl_recorder: Any
    current_time: Any
    enable_dungeon_manager: bool
    wheel_manager: Any
    mission_manager: Any
    family_manager: Any
    # Granular 每日任務開關（皆有預設 True，向後相容舊呼叫點）
    enable_hellgate: bool = True
    enable_arena: bool = True
    enable_mining: bool = True
    enable_wanshen: bool = True
    enable_cloud_battle: bool = True
    enable_biweekly: bool = True
    ws_done: frozenset = frozenset()  # WS 階段已完成的任務名（ws_phase 對照表輸出）
    special_wanshen_claimed: bool = False


@dataclass
class RunReport:
    """單次 client pipeline 的結果摘要，欄位形狀對齊 WS ``RunReport``。

    client 任務仍保留既有 action 的原始回傳值；registry loop 只負責把它們
    收進 ``tasks``，不改變 action 的結果語意。中斷只標在 ``aborted``，不把
    使用者強制休眠誤列為一般錯誤。
    """

    device: str
    login_ok: bool = True
    spend: bool = False
    tasks: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    kicked: bool = False
    kick_reason: str | None = None
    aborted: bool = False
    close_reason: str | None = None
    close_detail: str | None = None


def run(ctx: DailyContext) -> RunReport:
    """Execute the per-wake task sequence; recover from consecutive-mismatch abort.

    Thin wrapper around :func:`_run_tasks`. When too many consecutive tasks land
    off the main page, ``_run_tasks`` force-stops the game app and raises
    ``_ConsecutiveMismatchAbort``. We swallow it here and return normally so the
    caller's wake loop sleeps and re-wakes at the next aligned window — instead of
    the abort escaping to new_main_v2's outer ``except Exception`` (which logged it
    as 「未預期錯誤」 and tore the device thread down → set_offline + scanner
    respawn → immediate re-run with no sleep).
    """
    def _publish(report: RunReport) -> RunReport:
        # Dashboard diagnostics are best-effort and must never affect the task
        # pipeline (including worker processes where the UI is not present).
        try:
            from runtime_services.report_store import publish
            publish(ctx.ip, report, source="client")
        except Exception:  # noqa: BLE001
            logger.debug("[%s] publish client RunReport failed", ctx.ip,
                         exc_info=True)
        return report

    cfg = config_manager.get_device_config(ctx.ip)
    special_active = bool(cfg.get("special_wanshen_account", False)) and bool(
        cfg.get("special_wanshen_enabled", False)
    )
    if special_active:
        if not ctx.special_wanshen_claimed:
            return _publish(RunReport(device=ctx.ip))
        # 跟一般玩家喚醒相同：復用 handle_game_startup_pages 清公告/未知等彈窗、
        # back/重啟、主頁面雙確認，避免特殊模式單次讀到「未知」就整天放棄。
        from adb_operations import start_game_by_icon
        from game_initialization import handle_game_startup_pages

        if not handle_game_startup_pages(
            ctx.d, ctx.ip, start_game_fn=start_game_by_icon, reward_fn=reward
        ):
            logger.warning(
                "[%s] 萬神專用排程未能進入主頁面，本日不再嘗試", ctx.ip
            )
            bot_state.update_state(
                ctx.ip, task="萬神試煉", step="未到達主頁面，順延下一個有效日"
            )
            return _publish(RunReport(device=ctx.ip))
        special_wanshen.run_claimed(
            ctx.d, ctx.ip, cfg=cfg, fight_fn=new_battle.fight_test
        )
        return _publish(RunReport(device=ctx.ip, tasks={"special_wanshen": {}}))

    try:
        return _publish(_run_tasks(ctx))
    except _ConsecutiveMismatchAbort as exc:
        logger.info(
            f"[{ctx.ip}] 本輪 pipeline 已中止並關閉 app（{exc}）；"
            "等待下次對齊喚醒重新啟動"
        )
        if exc.report is not None:
            return _publish(exc.report)
        return _publish(RunReport(device=ctx.ip, aborted=True))

def _run_tasks(ctx: DailyContext) -> RunReport:
    """依 registry 順序執行 client 任務，集中處理共用生命週期關切。

    每個 handler 只保留該任務的既有 action 與特殊契約；force-sleep、結果
    收集與中斷標記都在同一個 loop 處理。這是 code motion：handler 不新增
    retry、due 或 backend 語意，讓既有 scheduler 繼續作為行為真相來源。
    """
    d = ctx.d
    ip = ctx.ip
    Cnn_model = ctx.Cnn_model
    clf = ctx.clf
    rl_recorder = ctx.rl_recorder
    current_time = ctx.current_time
    wheel_manager = ctx.wheel_manager
    mission_manager = ctx.mission_manager
    family_manager = ctx.family_manager
    report = RunReport(device=ip)
    stage: str | None = None
    streak = [0]

    def _force_sleep_checkpoint() -> None:
        """每個任務開始前共用的中斷點。"""
        if bot_state.check_force_sleep(ip):
            report.aborted = True
            raise ForceSleepRequested("force sleep requested from dashboard")

    def _ws_result() -> TaskResult:
        return TaskResult(TaskOutcome.SKIPPED, detail="WS 已完成，跳過")

    def _track(current_stage: str) -> str:
        """維持既有連續非主頁面中止護欄。"""
        if current_stage == "主頁面":
            streak[0] = 0
        else:
            streak[0] += 1
            if streak[0] >= 4:
                count = streak[0]
                logger.error(
                    f"[{ip}] 連續 {count} 個任務不在主頁面，"
                    "中止本輪 pipeline，強制關閉 app"
                )
                streak[0] = 0
                try:
                    d.app_stop("com.mxdzz.tw.and")
                except Exception:
                    pass
                report.aborted = True
                raise _ConsecutiveMismatchAbort(
                    f"連續 {count} 個任務不在主頁面",
                    report=report,
                )
        return current_stage

    def _configured_backend() -> str | None:
        """讀取目前裝置 backend；缺省設定保留舊測試／呼叫點語意。"""
        try:
            device_cfg = config_manager.get_device_config(ip)
        except Exception:
            return None
        backend = device_cfg.get("backend") if hasattr(device_cfg, "get") else None
        if backend in {"adb", "web_h5"}:
            return backend
        return None

    backend = _configured_backend()
    effective_backend = backend or (
        "web_h5" if getattr(d, "_page", None) is not None else "adb"
    )
    definitions = {
        definition.task_id: definition
        for definition in iter_pipeline_task_definitions()
    }
    generic_pipeline_ref = "game_actions.daily_pipeline:run"
    missing_executor = object()

    def _invoke_registered(
        task_id: str,
        *args: Any,
        fallback: Any = None,
    ) -> Any:
        """依 registry/backend 消費專用 executor；共享 entrypoint 留給 handler。"""
        definition = definitions[task_id]
        reference = definition.executors.get(effective_backend)
        if not reference or reference == generic_pipeline_ref:
            if fallback is None:
                return missing_executor
            return fallback()
        module_name, symbol = reference.split(":", 1)
        executor = getattr(importlib.import_module(module_name), symbol)
        # 有明確 backend 的 live config 必須走 adapter 的預設實作；沒有
        # backend 的舊呼叫點才保留傳入的 legacy callable 作為相容 fallback。
        callback = fallback if backend is None else None
        kwargs = {"action": callback} if callback is not None else {}
        return executor(*args, **kwargs)

    def _record_ws_skip(definition) -> None:
        logger.info(f"[{ip}] {definition.display_name}: WS 階段已完成，跳過")
        bot_state.update_state(
            ip, task=definition.display_name, step="WS 已完成，跳過"
        )

    def _record_task_start(definition, step: str = "執行中") -> None:
        bot_state.update_state(ip, task=definition.display_name, step=step)

    def _task_redpack():
        return run_redpack_check_if_due(d, ip)

    def _task_seven_login():
        return _GuardedAction(
            "七日登入獎勵", "七日登入獎勵前不在主頁面",
            lambda: run_seven_login_if_due(d, ip), step="查詢/領取中",
        )

    def _task_carpark():
        result = run_carpark_check_if_due(d, ip)
        click_white(d)
        # Device startup：5558 透過 CDP 直呼 H5 RoleControl 切方案，不走 OCR。
        if ip == "emulator-5558":
            _force_sleep_checkpoint()
            switch_skill_h5(ip, "戰士推圖")
        return result

    def _task_hellgate():
        if not ctx.enable_hellgate:
            logger.info("[%s] 地獄之門：已停用，跳過", ip)
            return TaskResult(TaskOutcome.SKIPPED, detail="功能已停用")
        current_stage = get_stage_with_check(d, ip, Cnn_model)
        logging.info(
            "目前頁面: {}, 當前時間: {}:{}".format(
                current_stage, current_time.tm_hour, current_time.tm_min
            )
        )
        if task_due.is_due(
            "地獄之門", ip, datetime.datetime(*current_time[:6])
        ):
            if current_stage == "主頁面":
                new_battle.hell_door(d, ip)
                return TaskResult(TaskOutcome.COMPLETED, detail="地獄之門已執行")
            else:
                log_main_page_mismatch(
                    d, ip, current_stage, "地獄之門",
                    "地獄之門到達執行時間但不在主頁面",
                )
        else:
            logger.info("地獄之門: 尚未到達執行時間或已執行過")
        return TaskResult(TaskOutcome.SKIPPED, detail="尚未到達執行時間或不在主頁面")

    def _task_farm():
        return _GuardedAction(
            "農場任務", "農場任務前不在主頁面",
            lambda: _invoke_registered(
                "farm", d, ip, Cnn_model,
                fallback=lambda: farm_manager.farm(d, ip, Cnn_model),
            ),
            step="準備進入",
        )

    def _task_idle_reward():
        def _tap_chest():
            d.tap(random.randint(261, 271), 369)
            time.sleep(1)
            reward(d)
            time.sleep(3)

        return _GuardedAction(
            "點擊寶箱", "點擊寶箱前不在主頁面", _tap_chest,
            step="領取獎勵",
        )

    def _task_guild():
        def _save_stage(result):
            nonlocal stage
            stage = result

        return _GuardedAction(
            "家族任務", "家族任務前不在主頁面",
            family_manager.go_to_family, step="執行中", after=_save_stage,
        )

    def _task_spirit():
        if ip == "emulator-5558":
            return TaskResult(TaskOutcome.SKIPPED, detail="裝置排除")
        if stage == "主頁面":
            guardian_record = return_time(ip, name="guardian_spirit")
            should_get_guardian = True
            if guardian_record is not None:
                should_get_guardian = guardian_record.get("is_next_day", False)
            if should_get_guardian:
                get_Guardian_Spirit(d)
                return TaskResult(TaskOutcome.COMPLETED, detail="守護靈已領取")
        else:
            log_main_page_mismatch(
                d, ip, stage, "領取守護靈", "領取守護靈前不在主頁面"
            )

    def _task_gacha():
        if ip == "emulator-5558":
            return TaskResult(TaskOutcome.SKIPPED, detail="裝置排除")
        if stage == "主頁面":
            skip_weekend_draw = "抽技能夥伴" in ctx.ws_done
            if skip_weekend_draw:
                logger.info(
                    f"[{ip}] 抽技能夥伴: WS 付費抽已完成，"
                    "跳過週末購買（免費紅點抽照跑）"
                )
            get_skill_and_partner(d, skip_weekend_draw=skip_weekend_draw)
            time.sleep(3)
        else:
            log_main_page_mismatch(
                d, ip, stage, "抽技能夥伴", "抽技能夥伴前不在主頁面"
            )

    def _task_steward():
        nonlocal stage
        stage = _track(get_stage_with_check(d, ip, Cnn_model))
        if stage == "主頁面":
            device_cfg = config_manager.get_device_config(ip)
            if device_cfg.get("enable_shop_manager", True):
                store_record = return_time(ip, name="Store")
                should_check_store = (
                    is_record_expired(store_record, 10800)
                    or current_time.tm_hour == 23
                )
                if should_check_store:
                    Store.buy_store(d, Cnn_model)
                    return TaskResult(TaskOutcome.COMPLETED, detail="商店已檢查")
                else:
                    logger.info("商店購買: 尚未過期且非23點，跳過")
            else:
                logger.info(f"[{ip}] 購物管家已停用，跳過商店購買")
        else:
            screenshot_path = save_error_screenshot(
                d, ip, stage, "商店購買前不在主頁面"
            )
            logger.error(
                f"[{ip}] 商店購買前不在主頁面，stage={stage}, "
                f"screenshot={screenshot_path}"
            )
        return TaskResult(TaskOutcome.SKIPPED, detail="商店未執行")

    def _task_mount():
        def _save_stage(result):
            nonlocal stage
            stage = result

        return _GuardedAction(
            "坐騎強化", "坐騎強化前不在主頁面",
            lambda: rank_events.park_spring(d, ip),
            after=_save_stage,
        )

    def _task_daily_acceleration():
        return daily_acceleration(d, ip, Cnn_model)

    def _task_arena():
        if not ctx.enable_arena:
            logger.info("[%s] 競技場：已停用，跳過", ip)
            return TaskResult(TaskOutcome.SKIPPED, detail="功能已停用")
        return _GuardedAction(
            "競技場挑戰", "競技場挑戰前不在主頁面",
            lambda: click_arena_challenges(d, ip), step="領取中",
        )

    def _task_mining():
        if not ctx.enable_mining:
            logger.info("[%s] 挖礦/Oracle：已停用，跳過", ip)
            return TaskResult(TaskOutcome.SKIPPED, detail="功能已停用")
        return _GuardedAction(
            "挖礦/Oracle", "挖礦/Oracle 前不在主頁面",
            lambda: oracle(
                d, None, ip=ip, clf=clf, rl_recorder=rl_recorder,
                Cnn_model=Cnn_model,
                max_duration_minutes=config_manager.get_device_config(ip).get(
                    "mining_duration_min", 6
                ),
            ),
            log="開始執行挖礦任務",
        )

    def _task_main_tasks():
        if 20 <= current_time.tm_hour < 23:
            return _GuardedAction(
                "所有日常任務", "所有日常任務執行前不在主頁面",
                lambda: _invoke_registered(
                    "main_tasks",
                    mission_manager,
                    fallback=mission_manager.do_allmission,
                ),
                step="檢查/執行中",
            )

    def _task_kungfu():
        return _GuardedAction(
            "菇菇武道會", "菇菇武道會前不在主頁面",
            lambda: _run_periodic_cycle(
                ip,
                record_name="mushroom_arena_cycle_start",
                should_execute_fn=should_execute_mushroom_arena,
                action_fn=mushroom_arena,
                display_name="菇菇武道會",
                d=d,
                daily_limit_name="mushroom_arena_daily",
            ),
            step="週期檢查/執行",
        )

    def _task_statue():
        return _GuardedAction(
            "菇菇雕像每週", "菇菇雕像每週執行前不在主頁面",
            lambda: run_statue_weekly_if_due(d, ip), step="週五檢查/執行",
        )

    def _task_sea():
        return _GuardedAction(
            "航海任務 (Sea)", "航海任務前不在主頁面",
            lambda: _run_periodic_cycle(
                ip,
                record_name="sea_last_execution",
                should_execute_fn=should_execute_sea_with_cooldown,
                action_fn=_sea_dispatch,
                display_name="sea",
                d=d,
                cycle_record_name="sea_cycle_start",
            ),
            step="週期檢查/執行",
        )

    def _safe_scheduler(task_id: str, label: str, fn):
        try:
            return fn()
        except Exception as exc:
            logger.exception("[%s] %s 任務異常", ip, label)
            report.errors[task_id] = str(exc)
            return TaskResult(TaskOutcome.PERMANENT_FAILURE, detail=str(exc))

    def _task_dragon():
        return _safe_scheduler(
            "dragon_realm", "龍骸聖域",
            lambda: _invoke_registered(
                "dragon_realm", d, ip,
                fallback=lambda: run_dragon_realm_if_due(ip, d),
            ),
        )

    def _task_fannaoxiao():
        return _safe_scheduler(
            "fannaoxiao", "煩惱消",
            lambda: _invoke_registered(
                "fannaoxiao", d, ip,
                fallback=lambda: run_fannaoxiao_if_due(d, ip),
            ),
        )

    def _task_escort():
        return _safe_scheduler(
            "escort", "賞金之路", lambda: run_escort_if_due(d, ip)
        )

    def _task_ladder():
        return _safe_scheduler(
            "ladder_reward", "天梯每週獎勵",
            lambda: run_ladder_reward_if_due(d, ip),
        )

    def _task_dungeon():
        current_stage = get_stage_with_check(d, ip, Cnn_model)
        return _run_weekly_dungeon(
            d, ip, current_stage, ctx.enable_wanshen, current_time
        )

    def _task_cloud():
        if not ctx.enable_cloud_battle:
            logger.info(f"[{ip}] 雲端戰鬥已停用，跳過")
            return TaskResult(TaskOutcome.SKIPPED, detail="功能已停用")
        return _GuardedAction(
            "雲端戰鬥", "雲端戰鬥前不在主頁面",
            lambda: new_battle.run_weekly_cloud_fighting_single(d, ip),
            step="領取中",
        )

    def _task_biweekly():
        current_stage = get_stage_with_check(d, ip, Cnn_model)
        now_local = time.localtime()
        return _run_biweekly_dungeon(
            d, ip, current_stage, ctx.enable_biweekly, now_local
        )

    def _task_couple():
        def _buy_gift():
            return daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1)

        def _save_stage(result):
            nonlocal stage
            stage = result
            if stage == "主頁面":
                stage = get_stage_with_check(d, ip, Cnn_model)

        return _GuardedAction(
            "好友每日禮物", "好友每日禮物前不在主頁面",
            _buy_gift,
            step="領取中", after=_save_stage,
        )

    def _task_lamp():
        result = _invoke_registered(
            "lamp", d, ip, stage,
            fallback=lambda: _run_lamp_if_due(d, ip, stage),
        )
        return result if result is not missing_executor else None

    def _task_turntable():
        def _spin_wheel():
            logger.info(f"[{ip}] 準備執行轉盤金幣")
            if wheel_manager.spin_and_send_gold():
                logger.info(f"[{ip}] 轉盤金幣執行成功，本次確實完成轉盤操作")
            else:
                logger.info(f"[{ip}] 轉盤金幣本次未執行或未偵測到紅點，已略過")

        return _GuardedAction(
            "轉盤金幣", "轉盤金幣執行前不在主頁面", _spin_wheel,
            step="執行中",
        )

    handlers = {
        "redpack": _task_redpack,
        "seven_login": _task_seven_login,
        "carpark": _task_carpark,
        "hellgate": _task_hellgate,
        "farm": _task_farm,
        "idle_reward": _task_idle_reward,
        "guild": _task_guild,
        "spirit": _task_spirit,
        "gacha": _task_gacha,
        "steward": _task_steward,
        "mount_sprint": _task_mount,
        "daily_acceleration": _task_daily_acceleration,
        "arena": _task_arena,
        "mining": _task_mining,
        "main_tasks": _task_main_tasks,
        "kungfu_worship": _task_kungfu,
        "statue": _task_statue,
        "sea_season": _task_sea,
        "dragon_realm": _task_dragon,
        "fannaoxiao": _task_fannaoxiao,
        "escort": _task_escort,
        "ladder_reward": _task_ladder,
        "dungeon": _task_dungeon,
        "cloud_ladder": _task_cloud,
        "biweekly": _task_biweekly,
        "couple": _task_couple,
        "lamp": _task_lamp,
        "turntable": _task_turntable,
    }

    def _should_ws_skip(definition) -> bool:
        """依 registry tag 集中判斷 client 是否被 WS 結果取代。"""
        if "direct-client-skip" not in definition.tags:
            return False
        # 保留舊流程的短路順序：5558 不會為守護靈寫 WS skip 狀態；
        # 功能關閉/不在每日任務時段也不應消費 skip 訊號。
        if definition.task_id == "spirit" and ip == "emulator-5558":
            return False
        if definition.task_id == "mining" and not ctx.enable_mining:
            return False
        if definition.task_id == "cloud_ladder" and not ctx.enable_cloud_battle:
            return False
        if definition.task_id == "main_tasks" and not (
            20 <= current_time.tm_hour < 23
        ):
            return False
        return definition.display_name in ctx.ws_done

    task_steps = {
        "hellgate": "戰鬥執行中",
        "gacha": "領取中",
        "daily_acceleration": "領取中",
    }

    for definition in iter_pipeline_task_definitions():
        _force_sleep_checkpoint()
        if _should_ws_skip(definition):
            _record_ws_skip(definition)
            if definition.task_id == "guild":
                stage = _track(get_stage_with_check(d, ip, Cnn_model))
            elif definition.task_id == "couple":
                stage = get_stage_with_check(d, ip, Cnn_model)
            report.tasks[definition.task_id] = _ws_result()
            continue
        if backend is not None and backend not in definition.executors:
            logger.info(
                "[%s] %s: backend=%s 沒有可用 executor，乾淨跳過",
                ip, definition.display_name, backend,
            )
            report.tasks[definition.task_id] = TaskResult(
                TaskOutcome.SKIPPED,
                detail=f"backend {backend} 不支援此任務",
            )
            continue
        if not definition.needs_main_page:
            _record_task_start(
                definition, task_steps.get(definition.task_id, "執行中")
            )
        invocation = handlers[definition.task_id]()
        if isinstance(invocation, _GuardedAction):
            action_result = missing_executor

            def _capture_action_result():
                nonlocal action_result
                action_result = invocation.fn()
                return action_result

            if definition.needs_main_page:
                stage_result = _track(
                    _run_at_main_page(
                        d,
                        ip,
                        Cnn_model,
                        invocation.task_name,
                        invocation.mismatch_reason,
                        _capture_action_result,
                        step=invocation.step,
                        log=invocation.log,
                    )
                )
            else:
                stage_result = invocation.fn()
            if invocation.after is not None:
                invocation.after(stage_result)
            result = (
                action_result
                if action_result is not missing_executor
                else stage_result
            )
        else:
            result = invocation
        report.tasks[definition.task_id] = (
            result if result is not None else {}
        )
        if (
            definition.record_name
            and isinstance(result, TaskResult)
            and result.outcome is TaskOutcome.COMPLETED
        ):
            time_recording(ip, name=definition.record_name)

    # Device cleanup：沿用原本在所有 client 任務後才執行的順序。
    if ip == "emulator-5558":
        switch_skill_h5(ip, "騙人用")
    if "fc65396d" in ip:
        d.app_start("com.android.chrome")
        time.sleep(2)
        d.app_stop("com.mxdzz.tw.and")
        time.sleep(1)
    else:
        d.app_stop("com.mxdzz.tw.and")
    return report
