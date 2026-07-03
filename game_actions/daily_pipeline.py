"""Daily task pipeline — per-wake 20-task sequence for one device.

Extracted verbatim from `new_main_v2.py` (`_run_daily_tasks`) as Phase 7
of the slim-down plan (plan A — conservative). The 20 tasks run in the
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
import logging
import random
import time
from dataclasses import dataclass
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
from game_actions.ladder_reward_weekly import run_ladder_reward_if_due
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
from game_actions.skill_manager import switch_skill
from game_actions.stage_guard import _run_at_main_page, get_stage_with_check
from game_actions import task_due
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
            return sea_v2_sea(ip, d)
    return sea(ip, d)


class _ConsecutiveMismatchAbort(Exception):
    """連續 N 個任務不在主頁面時中止本輪 pipeline。

    由 ``_run_tasks`` 內部丟出、並在 ``run`` 邊界被攔下：app 已被強制關閉，
    ``run`` 直接返回，讓上層 wake loop 走正常休眠週期、於下次對齊喚醒以全新
    啟動重試（而非讓未攔截的例外冒泡到 new_main_v2 外層 handler 被當成
    「未預期錯誤」，把整條 device thread 拆掉）。
    """


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


def run(ctx: DailyContext) -> None:
    """Execute the per-wake task sequence; recover from consecutive-mismatch abort.

    Thin wrapper around :func:`_run_tasks`. When too many consecutive tasks land
    off the main page, ``_run_tasks`` force-stops the game app and raises
    ``_ConsecutiveMismatchAbort``. We swallow it here and return normally so the
    caller's wake loop sleeps and re-wakes at the next aligned window — instead of
    the abort escaping to new_main_v2's outer ``except Exception`` (which logged it
    as 「未預期錯誤」 and tore the device thread down → set_offline + scanner
    respawn → immediate re-run with no sleep).
    """
    try:
        _run_tasks(ctx)
    except _ConsecutiveMismatchAbort as exc:
        logger.info(
            f"[{ctx.ip}] 本輪 pipeline 已中止並關閉 app（{exc}）；"
            "等待下次對齊喚醒重新啟動"
        )


def _run_tasks(ctx: DailyContext) -> None:
    """Execute the full per-wake task sequence (20 tasks) for one device."""
    d = ctx.d
    ip = ctx.ip
    Cnn_model = ctx.Cnn_model
    clf = ctx.clf
    rl_recorder = ctx.rl_recorder
    current_time = ctx.current_time
    # NB: ctx.enable_dungeon_manager 欄位保留（呼叫介面不變），但 _run_tasks
    # 內已全面改用 granular flag，不再解包它。
    enable_hellgate = ctx.enable_hellgate
    enable_arena = ctx.enable_arena
    enable_mining = ctx.enable_mining
    enable_wanshen = ctx.enable_wanshen
    enable_cloud_battle = ctx.enable_cloud_battle
    enable_biweekly = ctx.enable_biweekly
    wheel_manager = ctx.wheel_manager
    mission_manager = ctx.mission_manager
    family_manager = ctx.family_manager

    def _force_sleep_checkpoint() -> None:
        """共用中斷點：force-sleep 一旦出現就立刻轉入睡眠流程。"""
        if bot_state.check_force_sleep(ip):
            raise ForceSleepRequested("force sleep requested from dashboard")

    def _ws_skip(task_name: str) -> bool:
        """WS 階段已完成 → 記 log + 更新狀態並跳過該任務。"""
        if task_name in ctx.ws_done:
            logger.info(f"[{ip}] {task_name}: WS 階段已完成，跳過")
            bot_state.update_state(ip, task=task_name, step="WS 已完成，跳過")
            return True
        return False

    _streak = [0]  # 連續不在主頁面計數

    def _track(stage: str) -> str:
        """記錄 stage；連續失敗 >= 4 次時強制關閉 app 並中止 pipeline。"""
        if stage == "主頁面":
            _streak[0] = 0
        else:
            _streak[0] += 1
            if _streak[0] >= 4:
                count = _streak[0]
                logger.error(
                    f"[{ip}] 連續 {count} 個任務不在主頁面，"
                    "中止本輪 pipeline，強制關閉 app"
                )
                _streak[0] = 0
                try:
                    d.app_stop("com.mxdzz.tw.and")
                except Exception:
                    pass
                raise _ConsecutiveMismatchAbort(f"連續 {count} 個任務不在主頁面")
        return stage

    def _guarded_run(task_name, mismatch_reason, fn, *, step="執行中", log=None) -> str:
        _force_sleep_checkpoint()
        return _track(
            _run_at_main_page(d, ip, Cnn_model, task_name, mismatch_reason, fn, step=step, log=log)
        )

    # Task 0 (experimental): 紅包檢查 — web_h5 + flag-gated, no-op for others
    _force_sleep_checkpoint()
    if not _ws_skip("紅包檢查"):
        run_redpack_check_if_due(d, ip)

    # Task 0.5 (experimental): carpark reconciliation — same gating as redpack
    _force_sleep_checkpoint()
    run_carpark_check_if_due(d, ip)
    click_white(d)  # dismiss any popup triggered during carpark (e.g. car-attacked notification)

    # Device startup: 5558 啟動切換到「戰士推圖」方案 (cleanup 時切回「騙人用」)
    _force_sleep_checkpoint()
    if ip == "emulator-5558":
        switch_skill(d, '戰士推圖')

    # Task 1: 地獄之門
    _force_sleep_checkpoint()
    if not enable_hellgate:
        logger.info("[%s] 地獄之門：已停用，跳過", ip)
    else:
        stage = get_stage_with_check(d, ip, Cnn_model)
        logging.info("目前頁面: {}, 當前時間: {}:{}".format(stage, current_time.tm_hour, current_time.tm_min))
        # due 判斷唯一來源：task_due.is_due("地獄之門")（record.is_next_day + 當前分鐘<20）。
        # 傳入 pipeline 開頭捕捉的 current_time（非即時 now）：Task 1 前隔了紅包/車位/
        # get_stage_with_check（秒級），即時 now 可能跨過 :20 minute 邊界而漏做，故用捕捉時鐘
        # 還原原碼 current_time.tm_min 的等價（_due_hellgate 只讀 .minute，naive datetime 即足）。
        if task_due.is_due("地獄之門", ip, datetime.datetime(*current_time[:6])):
            if stage == "主頁面":
                bot_state.update_state(ip, task="地獄之門", step="戰鬥執行中")
                new_battle.hell_door(d, ip)
                time_recording(ip, name="地獄之門")
            else:
                log_main_page_mismatch(d, ip, stage, "地獄之門", "地獄之門到達執行時間但不在主頁面")
        else:
            logger.info("地獄之門: 尚未到達執行時間或已執行過")

    # Task 2: 農場任務
    if not _ws_skip("農場任務"):
        stage = _guarded_run(
            task_name="農場任務",
            mismatch_reason="農場任務前不在主頁面",
            fn=lambda: farm_manager.farm(d, ip, Cnn_model),
            step="準備進入",
        )

    # Task 3: 點擊寶箱
    def _tap_chest():
        d.tap(random.randint(261, 271), 369)
        time.sleep(1)
        reward(d)
        time.sleep(3)
    if not _ws_skip("點擊寶箱"):
        _guarded_run(
            task_name="點擊寶箱",
            mismatch_reason="點擊寶箱前不在主頁面",
            fn=_tap_chest,
            step="領取獎勵",
        )

    # Task 4: 家族任務 — stage reused by Tasks 5+6
    if not _ws_skip("家族任務"):
        stage = _guarded_run(
            task_name="家族任務",
            mismatch_reason="家族任務前不在主頁面",
            fn=family_manager.go_to_family,
            step="執行中",
        )
    else:
        stage = _track(get_stage_with_check(d, ip, Cnn_model))

    # Task 5 & 6: 守護靈 + 技能夥伴 (reuse stage from Task 4, matching original)
    _force_sleep_checkpoint()
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False) and not _ws_skip("領取守護靈"):
        if stage == "主頁面":
            guardian_record = return_time(ip, name="guardian_spirit")
            should_get_guardian = True
            if guardian_record is not None:
                should_get_guardian = guardian_record.get("is_next_day", False)
            if should_get_guardian:
                bot_state.update_state(ip, task="領取守護靈", step="領取中")
                get_Guardian_Spirit(d)
                time_recording(ip, name="guardian_spirit")
        else:
            log_main_page_mismatch(d, ip, stage, "領取守護靈", "領取守護靈前不在主頁面")
    _force_sleep_checkpoint()
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False):
        if stage == "主頁面":
            # WS 付費抽 (gacha 0x0902) 已完成 → 只跳過 ADB 週末付費抽 (weekend_to_buy)，
            # 但每日免費紅點抽 (get_skill_and_partner 前半段) WS 不涵蓋（free_daily 永遠
            # 關，遊戲自理，見 tasks/lessons.md 2026-06-15），故仍照跑，避免漏做。
            # skip 來源：WS_TO_PIPELINE_SKIPS["gacha"] → "抽技能夥伴"（ctx.ws_done）。
            skip_weekend_draw = "抽技能夥伴" in ctx.ws_done
            if skip_weekend_draw:
                logger.info("[%s] 抽技能夥伴: WS 付費抽已完成，跳過週末購買（免費紅點抽照跑）", ip)
            bot_state.update_state(ip, task="抽技能夥伴", step="領取中")
            get_skill_and_partner(d, skip_weekend_draw=skip_weekend_draw)
            time.sleep(3)
        else:
            log_main_page_mismatch(d, ip, stage, "抽技能夥伴", "抽技能夥伴前不在主頁面")

    # Task 7: 商店購買
    _force_sleep_checkpoint()
    if not _ws_skip("商店購買"):
        stage = _track(get_stage_with_check(d, ip, Cnn_model))
        if stage == "主頁面":
            device_cfg = config_manager.get_device_config(ip)
            if device_cfg.get("enable_shop_manager", True):
                store_record = return_time(ip, name="Store")
                should_check_store = is_record_expired(store_record, 10800) or current_time.tm_hour == 23
                if should_check_store:
                    bot_state.update_state(ip, task="商店購買", step="執行中")
                    Store.buy_store(d, Cnn_model)
                    time_recording(ip, name="Store")
                else:
                    logger.info("商店購買: 尚未過期且非23點，跳過")
            else:
                logger.info(f"[{ip}] 購物管家已停用，跳過商店購買")
        else:
            bot_state.update_state(ip, task="商店購買", step=f"未在主頁面: {stage}")
            screenshot_path = save_error_screenshot(d, ip, stage, "商店購買前不在主頁面")
            logger.error(f"[{ip}] 商店購買前不在主頁面，stage={stage}, screenshot={screenshot_path}")

    # Task 8: 坐騎強化
    _force_sleep_checkpoint()
    stage = _guarded_run(
        task_name="坐騎強化",
        mismatch_reason="坐騎強化前不在主頁面",
        fn=lambda: rank_events.park_spring(d, ip),
    )

    # Task 9: 每日加速 (no main-page guard)
    _force_sleep_checkpoint()
    bot_state.update_state(ip, task="每日加速", step="領取中")
    daily_acceleration(d, ip, Cnn_model)

    # Task 10: 競技場挑戰
    _force_sleep_checkpoint()
    if not enable_arena:
        logger.info("[%s] 競技場：已停用，跳過", ip)
    else:
        stage = _guarded_run(
            task_name="競技場挑戰",
            mismatch_reason="競技場挑戰前不在主頁面",
            fn=lambda: click_arena_challenges(d, ip),
            step="領取中",
        )

    # Task 11: 挖礦/Oracle (original had duplicate get_stage_with_check — collapsed to one via helper)
    _force_sleep_checkpoint()
    if not enable_mining:
        logger.info("[%s] 挖礦/Oracle：已停用，跳過", ip)
    elif not _ws_skip("挖礦/Oracle"):
        stage = _guarded_run(
            task_name="挖礦/Oracle",
            mismatch_reason="挖礦/Oracle 前不在主頁面",
            fn=lambda: oracle(
                d, None, ip=ip, clf=clf, rl_recorder=rl_recorder,
                Cnn_model=Cnn_model,
                max_duration_minutes=config_manager.get_device_config(ip).get("mining_duration_min", 6),
            ),
            log="開始執行挖礦任務",
        )

    # Task 12: 所有日常任務 (20:00–23:00 only)
    _force_sleep_checkpoint()
    if 20 <= current_time.tm_hour < 23 and not _ws_skip("所有日常任務"):
        stage = _run_at_main_page(
            d, ip, Cnn_model,
            task_name="所有日常任務",
            mismatch_reason="所有日常任務執行前不在主頁面",
            fn=lambda: mission_manager.do_allmission(),
            step="檢查/執行中",
        )

    # Task 13: 菇菇武道會
    _force_sleep_checkpoint()
    _guarded_run(
        task_name="菇菇武道會",
        mismatch_reason="菇菇武道會前不在主頁面",
        fn=lambda: _run_periodic_cycle(
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

    # Task 13.5: 菇菇雕像每週五一鍵消耗 (gated by cfg.statue_weekly.enabled)
    _force_sleep_checkpoint()
    _guarded_run(
        task_name="菇菇雕像每週",
        mismatch_reason="菇菇雕像每週執行前不在主頁面",
        fn=lambda: run_statue_weekly_if_due(d, ip),
        step="週五檢查/執行",
    )

    # Task 14: 航海任務
    _force_sleep_checkpoint()
    _guarded_run(
        task_name="航海任務 (Sea)",
        mismatch_reason="航海任務前不在主頁面",
        fn=lambda: _run_periodic_cycle(
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

    # Task 14.5: 龍骸聖域（flag 預設 off；H5 only，adb 會自行 abort）
    _force_sleep_checkpoint()
    try:
        run_dragon_realm_if_due(ip, d)
    except Exception:
        logger.exception("[%s] 龍骸聖域 任務異常", ip)

    # Task 14.6: 煩惱消（flag 預設 off；H5 only、每日一次、整局免費有界）
    _force_sleep_checkpoint()
    try:
        run_fannaoxiao_if_due(d, ip)
    except Exception:
        logger.exception("[%s] 煩惱消 任務異常", ip)

    # Task 14.7: 天梯每週獎勵（每週二一次；H5 only，走頁面 WS 0x4001；無記錄則跳過）
    _force_sleep_checkpoint()
    try:
        run_ladder_reward_if_due(d, ip)
    except Exception:
        logger.exception("[%s] 天梯每週獎勵 任務異常", ip)

    # Task 15: 萬神試煉
    _force_sleep_checkpoint()
    if not _ws_skip("萬神試煉"):
        stage = get_stage_with_check(d, ip, Cnn_model)
        _run_weekly_dungeon(d, ip, stage, enable_wanshen, current_time)

    # Task 16: 雲端戰鬥
    _force_sleep_checkpoint()
    if enable_cloud_battle:
        _run_at_main_page(
            d, ip, Cnn_model,
            task_name="雲端戰鬥",
            mismatch_reason="雲端戰鬥前不在主頁面",
            fn=lambda: new_battle.run_weekly_cloud_fighting_single(d, ip),
            step="領取中",
        )
    else:
        logger.info(f"[{ip}] 雲端戰鬥已停用，跳過")

    # Task 17: 雙週副本
    _force_sleep_checkpoint()
    stage = get_stage_with_check(d, ip, Cnn_model)
    now_local = time.localtime()
    _run_biweekly_dungeon(d, ip, stage, enable_biweekly, now_local)

    # Task 18: 好友每日禮物 — stage refreshed after run for Task 19 (lamp)
    _force_sleep_checkpoint()
    if not _ws_skip("好友每日禮物"):
        stage = _guarded_run(
            task_name="好友每日禮物",
            mismatch_reason="好友每日禮物前不在主頁面",
            fn=lambda: daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1),
            step="領取中",
        )
        if stage == "主頁面":
            stage = get_stage_with_check(d, ip, Cnn_model)
    else:
        stage = get_stage_with_check(d, ip, Cnn_model)

    # Task 19: 開神燈
    _force_sleep_checkpoint()
    if not _ws_skip("開神燈"):
        _run_lamp_if_due(d, ip, stage)

    # Task 20: 轉盤金幣
    _force_sleep_checkpoint()
    def _spin_wheel():
        logger.info(f"[{ip}] 準備執行轉盤金幣")
        if wheel_manager.spin_and_send_gold():
            logger.info(f"[{ip}] 轉盤金幣執行成功，本次確實完成轉盤操作")
        else:
            logger.info(f"[{ip}] 轉盤金幣本次未執行或未偵測到紅點，已略過")
    if not _ws_skip("轉盤金幣"):
        _guarded_run(
            task_name="轉盤金幣",
            mismatch_reason="轉盤金幣執行前不在主頁面",
            fn=_spin_wheel,
            step="執行中",
        )

    # Device cleanup
    if ip == "emulator-5558":
        switch_skill(d, '騙人用')
    if "fc65396d" in ip:
        d.app_start("com.android.chrome")
        time.sleep(2)
        d.app_stop("com.mxdzz.tw.and")
        time.sleep(1)
    else:
        d.app_stop("com.mxdzz.tw.and")
