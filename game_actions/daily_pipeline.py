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
from farm import farm_manager
from Sea import sea
from Skill import get_skill_and_partner

from game_actions.daily_tasks import click_arena_challenges, daily_acceleration
from game_actions.dungeon_scheduler import _run_biweekly_dungeon, _run_weekly_dungeon
from game_actions.lamp_scheduler import _run_lamp_if_due
from game_actions.miner_action import oracle
from game_actions.periodic_tasks import (
    _run_periodic_cycle,
    mushroom_arena,
    should_execute_mushroom_arena,
)
from game_actions.reward_manager import reward
from game_actions.skill_manager import switch_skill
from game_actions.stage_guard import _run_at_main_page, get_stage_with_check
from json_manager import (
    is_record_expired,
    return_time,
    should_execute_sea_with_cooldown,
    time_recording,
)
from utils.logging_utils import logger
from utils.screenshot_helpers import log_main_page_mismatch, save_error_screenshot

# Devices that should skip guardian spirit / skill partner collection.
# Keep legacy behavior: emulator-5558 is excluded from these tasks.
_DEVICE_SKIP_GUARDIAN = {
    "emulator-5558": True,
}


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


def run(ctx: DailyContext) -> None:
    """Execute the full per-wake task sequence (20 tasks) for one device."""
    d = ctx.d
    ip = ctx.ip
    Cnn_model = ctx.Cnn_model
    clf = ctx.clf
    rl_recorder = ctx.rl_recorder
    current_time = ctx.current_time
    enable_dungeon_manager = ctx.enable_dungeon_manager
    wheel_manager = ctx.wheel_manager
    mission_manager = ctx.mission_manager
    family_manager = ctx.family_manager

    # Task 1: 地獄之門
    stage = get_stage_with_check(d, ip, Cnn_model)
    record_time = return_time(ip, name="地獄之門")
    logging.info("目前頁面: {}, 當前時間: {}:{}".format(stage, current_time.tm_hour, current_time.tm_min))
    logging.info("地獄之門紀錄: {}".format(record_time))
    hell_gate_time = 1
    if record_time is None:
        hell_gate_time = 0
        should_execute = True
    else:
        should_execute = record_time.get("is_next_day", False) or hell_gate_time == 0
        logging.info("hell_gate_time: {}, should_execute: {}, record_time: {}".format(hell_gate_time, should_execute, record_time))
    if should_execute and current_time.tm_min < 20:
        if stage == "主頁面":
            bot_state.update_state(ip, task="地獄之門", step="戰鬥執行中")
            new_battle.hell_door(d, ip)
            time_recording(ip, name="地獄之門")
        else:
            log_main_page_mismatch(d, ip, stage, "地獄之門", "地獄之門到達執行時間但不在主頁面")
    else:
        logger.info("地獄之門: 尚未到達執行時間或已執行過")

    # Task 2: 農場任務
    stage = _run_at_main_page(
        d, ip, Cnn_model,
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
    _run_at_main_page(
        d, ip, Cnn_model,
        task_name="點擊寶箱",
        mismatch_reason="點擊寶箱前不在主頁面",
        fn=_tap_chest,
        step="領取獎勵",
    )

    # Task 4: 家族任務 — stage reused by Tasks 5+6
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="家族任務",
        mismatch_reason="家族任務前不在主頁面",
        fn=family_manager.go_to_family,
        step="執行中",
    )

    # Task 5 & 6: 守護靈 + 技能夥伴 (reuse stage from Task 4, matching original)
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False):
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
    if not _DEVICE_SKIP_GUARDIAN.get(ip, False):
        if stage == "主頁面":
            bot_state.update_state(ip, task="抽技能夥伴", step="領取中")
            get_skill_and_partner(d)
            time.sleep(3)
        else:
            log_main_page_mismatch(d, ip, stage, "抽技能夥伴", "抽技能夥伴前不在主頁面")

    # Task 7: 商店購買
    stage = get_stage_with_check(d, ip, Cnn_model)
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
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="坐騎強化",
        mismatch_reason="坐騎強化前不在主頁面",
        fn=lambda: rank_events.park_spring(d, ip),
    )

    # Task 9: 每日加速 (no main-page guard)
    bot_state.update_state(ip, task="每日加速", step="領取中")
    daily_acceleration(d, ip, Cnn_model)

    # Task 10: 競技場挑戰
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="競技場挑戰",
        mismatch_reason="競技場挑戰前不在主頁面",
        fn=lambda: click_arena_challenges(d, ip),
        step="領取中",
    )

    # Task 11: 挖礦/Oracle (original had duplicate get_stage_with_check — collapsed to one via helper)
    stage = _run_at_main_page(
        d, ip, Cnn_model,
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
    if 20 <= current_time.tm_hour < 23:
        stage = _run_at_main_page(
            d, ip, Cnn_model,
            task_name="所有日常任務",
            mismatch_reason="所有日常任務執行前不在主頁面",
            fn=lambda: mission_manager.do_allmission(),
            step="檢查/執行中",
        )

    # Task 13: 菇菇武道會
    _run_at_main_page(
        d, ip, Cnn_model,
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

    # Task 14: 航海任務
    _run_at_main_page(
        d, ip, Cnn_model,
        task_name="航海任務 (Sea)",
        mismatch_reason="航海任務前不在主頁面",
        fn=lambda: _run_periodic_cycle(
            ip,
            record_name="sea_last_execution",
            should_execute_fn=should_execute_sea_with_cooldown,
            action_fn=sea,
            display_name="sea",
            d=d,
            cycle_record_name="sea_cycle_start",
        ),
        step="週期檢查/執行",
    )

    # Task 15: 萬神試煉
    stage = get_stage_with_check(d, ip, Cnn_model)
    _run_weekly_dungeon(d, ip, stage, enable_dungeon_manager, current_time)

    # Task 16: 雲端戰鬥
    if enable_dungeon_manager:
        _run_at_main_page(
            d, ip, Cnn_model,
            task_name="雲端戰鬥",
            mismatch_reason="雲端戰鬥前不在主頁面",
            fn=lambda: new_battle.run_weekly_cloud_fighting_single(d, ip),
            step="領取中",
        )
    else:
        logger.info(f"[{ip}] 副本管家已停用，跳過雲端戰鬥")

    # Task 17: 雙週副本
    stage = get_stage_with_check(d, ip, Cnn_model)
    now_local = time.localtime()
    _run_biweekly_dungeon(d, ip, stage, enable_dungeon_manager, now_local)

    # Task 18: 好友每日禮物 — stage refreshed after run for Task 19 (lamp)
    stage = _run_at_main_page(
        d, ip, Cnn_model,
        task_name="好友每日禮物",
        mismatch_reason="好友每日禮物前不在主頁面",
        fn=lambda: daily_gift_task.buy_gift_for_friend_daily(d, ip, times=1),
        step="領取中",
    )
    if stage == "主頁面":
        stage = get_stage_with_check(d, ip, Cnn_model)

    # Task 19: 開神燈
    _run_lamp_if_due(d, ip, stage)

    # Task 20: 轉盤金幣
    def _spin_wheel():
        logger.info(f"[{ip}] 準備執行轉盤金幣")
        if wheel_manager.spin_and_send_gold():
            logger.info(f"[{ip}] 轉盤金幣執行成功，本次確實完成轉盤操作")
        else:
            logger.info(f"[{ip}] 轉盤金幣本次未執行或未偵測到紅點，已略過")
    _run_at_main_page(
        d, ip, Cnn_model,
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
