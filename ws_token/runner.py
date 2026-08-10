"""Single-device daily-task orchestrator for the ws_token backend.

``run_device(device, *, spend=False)`` builds ONE :class:`WSGameClient` from the
device's captured creds, connects once (so a single background heartbeat keeps
the session alive), runs every pure-WS daily task in a fixed order, and closes
the client in a ``finally``. Each task is wrapped in its own try/except (incl.
:class:`WSTimeoutError`) so a dormant / event-gated / failing task never aborts
the others — every per-task result or error is collected into the frozen
:class:`RunReport`.

Task order (matches the in-game daily flow's free-then-paid grouping):

  1. main_tasks  — free: collect login-push state, then claim daily tasks +
                   默契考驗 好感週任務 (Marry type 6) + daily activity box +
                   weekly box + achievement milestones.
  2. league_solo — free: claim every claimable 烈焰山洞 / 魔法劇場 box (types 1-4).
  3. redpack     — free: list grab_list and claim every claimable 紅包.
  4. guild       — help_all (free); donate_until_capped (spend); treasure open
                   only when a round is active (event-gated; spend).
  5. steward     — read_info; shopping (spend) + active dungeon sweep (every wake)
                   renewal only when spend AND the service is expired.
  6. spirit      — free: 守護靈免費召喚 (draw_all_free; only free_times,
                   never buys 招喚貨幣 — item 800003 does not exist).
  7. workshop    — 加工坊 12h 兩配方輪換 (rotate_team_recipes): 只在目標配方
                   可製作時切換，已是目標或缺料時保留現況。可做量由
                   inventory_tracker (0x0402/0x0401 原料庫存快照) 算，狀態存於
                   ws_state，gated by ``workshop_rotate``, default on.
  8. couple      — 伴侶: 奶茶+玫瑰一天送一次 (give_all_in_hand; 日期閘存在
                   ws_state/<device>.json; ``couple_gifts`` default on) + 戒指錘鍊
                   (``forge_ring`` opt-in, default off). Skipped without a partner.
  9. mining      — 挖礦: opt-in (``mining_config.enabled=True``) only. The pickaxe
                   (axe) count is NOT in the 0x0402 login snapshot (it isn't
                   reliably pushed) — it arrives via the 0x0402 consume push after
                   each dig. So mining seeds a count, digs a server-valid frontier
                   cell, then adopts the real remaining count from the consume
                   push and re-plans, until pickaxes hit 0. Consumes pickaxe/bomb/
                   drill only through explicit config flags.
 10. lamp        — 開神燈: opt-in (``open_lamp=True``) only. Spends 神燈 items and
                   auto-equips/sells drops, so it is gated behind its own flag
                   (NOT ``spend``) and OFF by default. Runs one batch of up to
                   ``batch_num`` boxes (lamp.open_lamp's own cap), never unbounded.

Default ``spend=False`` runs free reads + claims (incl. redpack) and an already
active dungeon-housekeeper sweep. ``spend=True`` additionally donates, shops, and (if
expired) renews — see the per-task spend gates below. ``open_lamp`` is an
independent opt-in: it is OFF by default and consumes 神燈 items only when True.

CLI:  python -m ws_token.runner --device <dev> [--spend] [--open-lamp] [--mine]
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Sequence

import json_manager
from game_actions.task_registry import ws_task_ids
from ws_token import (
    ad_reward, arena_fight, carpark, cloud_ladder, couple, dragon_realm, dungeon,
    escort_fight,
    farm, gacha, guild, hellgate, idle_reward, kungfu_race, kungfu_store, ladder_reward, league_solo,
    main_tasks, mining, mining_supervised, pay_mall, redpack, relic, relic_sprint, rogue,
    seven_login,
    secret_jewel, spirit, statue, steward, turntable, tycoon, workshop,
    mount_sprint,
    xwar_idle,
)
from ws_token import state as ws_state
from ws_token.abort import WSRunAborted
from ws_token.client import WSGameClient, WSError, WSLoginError, WSTimeoutError
from ws_token.creds import load_creds

logger = logging.getLogger(__name__)

# Seconds to wait after connect for the login-time PUSH frames (task_all /
# daily_point / weekly_box) to drain before snapshotting the main-task state.
_PUSH_SETTLE_S: float = 1.5

# Short probe timeout for the event-gated guild treasure read: a dormant 尋寶
# event never answers guild_treasure_info, so don't block the daily run on the
# full call timeout when we're only checking whether a round is live.
_TREASURE_PROBE_S: float = 6.0

# 加工坊兩配方輪換間隔（使用者指定：每 12 小時切一次）。
_WORKSHOP_ROTATE_S: float = 12 * 3600.0

LOGIN_TASK = "login"
# carpark 排第一：跨界車位要搶（10:00 開窗即被掃空），登入後最先送停車，
# 不等其他任務（plan 關閉時 carpark 會立刻 skip，不影響其他裝置）。
# 單一真相來源：registry 的 WS projection 已由 AST 測試釘住實際 `_step`
# 順序，並包含主連線關閉後才執行的尾端 main_chapter_kills。
TASK_ORDER: tuple[str, ...] = ws_task_ids()

# 開神燈 API 單次上限是 20；總量靠單線程連續批次累積。
_LAMP_BATCH_NUM: int = 20
_LAMP_MAX_BATCHES: int = 500  # 20 * 500 = 10000
_LAMP_BATCH_DELAY_SEC: float = 0.2

# 萬神試煉 本周積分獎勵：每週五領一次（使用者 2026-06-13 指定）。
# Python weekday(): Mon=0 … Fri=4 … Sun=6.
_ROGUE_WEEKDAY: int = 4
# 休眠的萬神試煉週積分事件不回任何 frame（連 0x0201 都不發）→ 短探測 timeout 快速降級，
# 不空等 15s 預設 call_timeout（與 guild 尋寶 _TREASURE_PROBE_S 同型）。
_ROGUE_PROBE_S: float = 6.0
# 菇菇雕像 每週五消耗果蔬：同樣週五一次。
_STATUE_WEEKDAY: int = 4


@dataclass(frozen=True)
class RunReport:
    """Outcome of one ``run_device`` pass.

    ``tasks`` maps each run task name to its result summary (whatever the task's
    orchestrator returned, or a small dict assembled here). ``errors`` maps a
    task name (or ``"login"``) to a short error string for whatever failed; a
    successful run has an empty ``errors``.

    ``kicked`` is the legacy broad interruption flag: True when the connection
    was kicked mid-run by cmd 259 or when the server dropped the socket. Use
    ``close_reason`` to distinguish explicit login conflict from transport drop;
    the in-flight tasks usually fail (connection gone) and land in ``errors``.

    ``close_reason`` is one of ``explicit_login_conflict``, ``transport_drop``,
    ``intentional_close`` or ``session_handoff`` when the client exposed one.
    ``close_detail`` retains the cmd-259 reason or transport exception text.

    ``aborted`` is True iff the run was stopped early by an external
    ``should_abort`` signal (e.g. a pending「開啟瀏覽器」request). The
    in-progress + remaining tasks are left pending (absent from both ``tasks``
    and ``errors``); the caller persists what got done and resumes the rest.
    """

    device: str
    login_ok: bool
    spend: bool
    tasks: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    kicked: bool = False
    kick_reason: Optional[str] = None
    aborted: bool = False
    close_reason: Optional[str] = None
    close_detail: Optional[str] = None


def _make_client(creds, **kwargs) -> WSGameClient:
    """Construct the WSGameClient. Indirected so tests can inject a fake."""
    return WSGameClient(creds, **kwargs)


def _client_close_metadata(client) -> tuple[Optional[str], Optional[str]]:
    """Read optional close metadata without breaking legacy fake clients."""
    reason = getattr(client, "close_reason", None)
    detail = getattr(client, "close_detail", None)
    if callable(reason):
        reason = reason()
    if callable(detail):
        detail = detail()
    value = getattr(reason, "value", reason)
    return (str(value) if value is not None else None,
            str(detail) if detail is not None else None)

def _client_kick_reason(client) -> Optional[str]:
    """讀取 client 的 kick reason，兼容舊測試 fake 與未升級 client。"""
    getter = getattr(client, "get_kick_reason", None)
    value = getter() if callable(getter) else getattr(client, "kick_reason", None)
    if callable(value):
        value = value()
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


def _client_is_kicked(client) -> bool:
    """任務邊界檢查連線是否已被踢或非預期斷線。"""
    try:
        return bool(client.is_kicked())
    except Exception:  # noqa: BLE001 — 舊/不完整 fake 可能沒有此方法
        return False


def _load_lamp():
    """Lazy import 開神燈 deps; runner import should stay pure-WS/light."""
    from ws_token import lamp
    return lamp


# --- per-task runners (each returns a summary; raising is caught by run_device)

def _run_main_tasks(client, collector: main_tasks.TaskCollector, *,
                    now=None) -> dict:
    """Free: snapshot login-push state, then claim every free reward.

    Runs on EVERY wake, including before 08:00 (no once-per-day/time gate).
    Each claim function is state-gated — it claims only STATE_CLAIMABLE /
    BOX_CLAIMABLE items and sends no commit frame when nothing is claimable — so
    re-running is safe and catches rewards that only became claimable later.
    ``now`` remains test/caller-compatible but is intentionally not a gate.
    """
    state = main_tasks.collect_state(client, collector, settle=_PUSH_SETTLE_S)
    daily = main_tasks.claim_daily_tasks(client, state)
    marry = main_tasks.claim_marry_tasks(client, state)  # 默契考驗 好感週任務 (type 6)
    # 領完每日任務後活躍度才上升、活躍度寶箱才變可領；server 會 push 更新的
    # task_daily_point_s2c，故重新快照再領寶箱（否則用領取前的舊快照會漏領）。
    state = main_tasks.collect_state(client, collector, settle=_PUSH_SETTLE_S)
    daily_box = main_tasks.claim_daily_box(client, state)
    # 真的送出寶箱領取後，再等 server push 最終狀態，摘要不可拿領取前快照猜測。
    if daily_box:
        state = main_tasks.collect_state(client, collector, settle=_PUSH_SETTLE_S)
    weekly_box = main_tasks.claim_weekly_box(client, state)
    achievement = main_tasks.claim_achievement(client)
    progress = main_tasks.summarize_daily_progress(
        state, newly_claimed=daily.get("claimed", 0)
    )
    logger.info("ws_token tasks: %s", progress["detail"])
    return {
        "daily_tasks": daily,
        "marry_tasks": marry,
        "daily_box": daily_box,
        "weekly_box": weekly_box,
        "achievement": achievement,
        "daily_progress": progress,
    }


def _run_league_solo(client) -> dict:
    """Free: claim every claimable 烈焰山洞 / 魔法劇場 box (types 1-4)."""
    return league_solo.claim_available(client)


def _run_redpack(client) -> dict:
    """Free: list grab_list and claim every claimable 紅包.

    grab is always free (no cost gate), so this runs unconditionally in the free
    task group. Returns redpack.grab_claimable's summary
    ``{attempted, claimed, results}``.
    """
    return redpack.grab_claimable(client)


def _run_ladder_reward(client, *, device: str) -> dict:
    """每週二以 0x4001 套用天梯獎勵選擇；沒有專屬紀錄時用共用模板。"""
    import datetime

    tz = datetime.timezone(datetime.timedelta(hours=8))
    result = ladder_reward.apply_if_due(
        device,
        datetime.datetime.now(tz).date(),
        client=client,
    )
    if result.get("ok") is False:
        raise RuntimeError(result.get("error") or "ladder reward send failed")
    return result


def _run_seven_login(client, *, device: str) -> dict:
    """查詢並領取七日登入獎勵；不可領時以安全 skip 返回。"""
    result = seven_login.apply_via_client(client, device=device)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result


def _run_cloud_ladder(client, *, device: str, should_abort=None,
                      on_progress=None) -> dict:
    """每週雲纏天梯：安全站位後，純 WS 逐關即時結算至最高關。"""
    return cloud_ladder.run_weekly(
        client,
        device,
        should_abort=should_abort,
        progress=on_progress,
    )


def _run_mail(client, *, device: str, gem_threshold: Optional[int] = None,
              skill_threshold: Optional[int] = None, state_dir=None,
              now=None) -> dict:
    """每日自動領取全部郵件附件 — 每日一次 (free; once-daily gated in mail_scheduler).

    Delegates to game_actions.mail_scheduler.run_mail_claim_if_due: skips without
    sending when already claimed today, else reads an optional capacity advisory
    (武魂 / 神器附魔寶石 thresholds — never blocks, client has no hard cap) then
    sends the in-game 一鍵領取全部附件 (mail_claim_c2s {mail_id:0}). The date marker
    is persisted in ws_state/<device>.json only on a successful reply.
    """
    from game_actions import mail_scheduler
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    return mail_scheduler.run_mail_claim_if_due(
        client, device=device, gem_threshold=gem_threshold,
        skill_threshold=skill_threshold, now=now, **kw)


def _run_idle_reward(client, offline_pushes: list) -> dict:
    """Free: claim ONLINE accrual (claim{1}) + the OFFLINE reward pushed at login.

    The OFFLINE reward arrives as a reward_info_s2c{type:2} PUSH right after login;
    ``offline_pushes`` collects those bodies (see the composite push handler in
    run_device). claim_online / claim_offline_from_push are no-ops when nothing is
    claimable (returns None), so this is safe to run unconditionally.

    Also claims the「2小時收益」quick income (ad_reward 0x1602, pure WS with
    is_free=1 — no ad SDK needed). Server gates it at 30min cooldown / 3 per
    day and declines with 0x0201, which claim_quick_2h absorbs; the hourly wake
    cadence collects all 3 daily claims naturally. Returns
    ``{online, offline, quick_2h}`` booleans (True = claimed).
    """
    online = idle_reward.claim_online(client)
    summary: dict = {"online": bool(online and online.success), "offline": None}
    if offline_pushes:
        off = idle_reward.claim_offline_from_push(client, offline_pushes[0])
        summary["offline"] = bool(off and off.success)
    quick = idle_reward.claim_quick_2h(client)
    summary["quick_2h"] = quick.success
    return summary


def _run_ad_rewards(client, *, config_ids, enabled: bool,
                    device: Optional[str] = None) -> dict:
    """看廣告獎勵 (鑽石/種子) opt-in: 純 WS 直接領 (is_free=1, 帳號買了免廣告).

    Only reached with a non-empty ``config_ids`` (gated by run_device → enabled).
    Reads today's per-config watch counts once (ad_info 0x1601), then for each
    config_id claims only up to its daily cap and never during a cooldown — both
    gates skip WITHOUT sending a packet (same「讀現值→只補差額→到上限跳過」discipline
    as farm shop). Returns ad_reward.claim_ads' ``{results, total_claimed}``.
    """
    if not enabled or not config_ids:
        return {"skipped": "ad_rewards disabled (set ws_token.ad_rewards.enabled=True)"}
    out = ad_reward.claim_ads(client, list(config_ids), device_id=device)
    if ad_reward.AD_SCIENCE_1 in config_ids:
        _mark_daily_acceleration_done_if_science_ad_succeeded(out, device)
    return out


def _mark_daily_acceleration_done_if_science_ad_succeeded(
        claim_ads_result: dict, device: Optional[str]) -> None:
    """AD_SCIENCE_1 IS the GUI「每日加速」科技園跳過30分鐘 button, over pure WS.

    When this WS claim actually did it (or the daily cap was already spent),
    mark the same ``daily_acceleration`` json_manager record the GUI task
    checks (game_actions/task_due.py:_due_daily_acceleration) so daily_tasks
    skips its redundant trip into 科技園. If it was skipped because no
    research is in progress, leave the record alone — nothing was done.
    """
    if not device:
        return
    res = claim_ads_result.get("results", {}).get(ad_reward.AD_NAMES[ad_reward.AD_SCIENCE_1])
    if not res:
        return
    if res.get("claimed") or "maxed" in str(res.get("skipped", "")):
        json_manager.time_recording(device, name="daily_acceleration")


def _run_turntable(client) -> dict:
    """Free: bank today's 轉盤 ad-funded spins, then spin every available turn.

    Delegates to turntable.run_daily: ad_reward.claim_ad(config 13, 2/day) banks
    the daily "watch ad" spins (NO_ADS = free instant; each claim bumps the wheel
    pool by 1) cap/cooldown-gated, then spin_all_free drains the pool (per-spin
    cooldown stops the drain after ~1/session; turns accumulate across wakes).
    Returns ``{spun, results, declined, ad_topup}``.
    """
    return turntable.run_daily(client)


def _run_tycoon(client, *, enabled: bool, max_rolls: int) -> dict:
    """傳奇大亨 (大富翁) 自動擲骰: opt-in, default off; FREE dice → pure gain.

    Only reached when ``enabled`` (gated by run_device). Delegates to
    tycoon.auto_play, which rolls act_monopoly_dice (0x18A9 {act_type=4003}) until
    the server rejects (out of dice) on 0x0201 or ``max_rolls`` is hit — each
    roll's landed-tile reward is auto-granted server-side (no claim cmd). When the
    activity is closed the FIRST roll returns 0x0201 and auto_play stops, so a
    closed event is a safe no-op. The dice are free (regen from a timer), so this
    is a pure-gain free task but stays opt-in to avoid touching the board outside
    the player's intent. Returns tycoon.auto_play's summary
    ``{rolls, total_rewards, last_pos, last_circle, stopped_reason}``.
    """
    if not enabled:
        return {"skipped": "tycoon disabled (set ws_token.tycoon=True)"}
    return tycoon.auto_play(client, max_rolls=max_rolls)


def _run_gacha(client, inventory_tracker, *,
               gacha_config: Optional[dict], device: str,
               state_dir=None, now=None) -> dict:
    """抽卡 (技能/同伴) — opt-in, default off; SPENDS draw tickets (1012/1013).

    Only reached when ``gacha_config.enabled``. For each type in ``types``
    (default [1,2]) calls gacha.run_gacha, which sends draw cmd 0x0902 and steps
    the 999/35/15 bundle ladder, stopping on the server's immediate 0x0201 reject
    (insufficient tickets) — no timeout-probing. Seeds the per-type ticket budget
    from the login 0x0402 snapshot via ``inventory_tracker`` when present. Returns
    a per-type ``{drawn, bundles, stopped}`` summary, or ``{skipped}``.
    """
    if not gacha_config or not gacha_config.get("enabled"):
        return {"skipped": "gacha disabled (set ws_token.gacha.enabled=True)"}
    import datetime as _dt

    current = now or _dt.datetime.now()
    if gacha_config.get("weekend_only"):
        if current.weekday() < 5:
            return {"skipped": "weekend_only: not Sat/Sun"}
    raw_types = gacha_config.get("types") or [gacha_config.get("type", 1)]
    mode = str(gacha_config.get("mode", "drain"))
    try:
        count = int(gacha_config.get("count", 999))
    except (TypeError, ValueError):
        count = 999
    try:
        batches = int(gacha_config.get("batches", 1))
    except (TypeError, ValueError):
        batches = 1

    valid_types: list[int] = []
    for value in raw_types:
        try:
            draw_type = int(value)
        except (TypeError, ValueError):
            continue
        if draw_type in gacha.DRAW_TYPE_NAME and draw_type not in valid_types:
            valid_types.append(draw_type)
    if not valid_types:
        return {"skipped": "no valid gacha types"}

    # 付費抽會在手機 ADB 離線時由 WS 備援每小時重跑，因此必須用持久化
    # at-most-once 閘門。每種類型在送協議前先記 attempted，避免抽完後程序
    # 崩潰／WS 斷線，下一輪又重複扣同一種券。
    state_kw = {"state_dir": state_dir} if state_dir is not None else {}
    state = ws_state.load_state(device, **state_kw)
    today = current.date().isoformat()
    paid = state.get("gacha_paid")
    if not isinstance(paid, dict) or paid.get("last_date") != today:
        paid = {
            "last_date": today,
            "attempted_types": [],
            "results": {},
            "mode": mode,
            "count": count,
            "batches": batches,
        }
    attempted = {
        int(value)
        for value in (paid.get("attempted_types") or [])
        if str(value).isdigit()
    }
    if all(draw_type in attempted for draw_type in valid_types):
        logger.info("[%s] WS 週末抽卡今日已嘗試，跳過重複扣券", device)
        # 這仍算「實質完成」，讓後續 ADB pipeline 繼續跳過 weekend_to_buy。
        # 若回傳 skipped，手機由離線恢復 ADB 後可能在同日再付費抽一次。
        return {"already_attempted": True, "last_date": today}

    out: dict = {}
    for dt in valid_types:
        if dt in attempted:
            continue

        attempted.add(dt)
        paid["attempted_types"] = sorted(attempted)
        paid["last_attempt_ts"] = current.timestamp()
        state["gacha_paid"] = paid
        ws_state.save_state(device, state, **state_kw)

        name = gacha.DRAW_TYPE_NAME.get(dt, str(dt))
        logger.info(
            "[%s] WS 週末抽卡開始: %s，%s×%s（mode=%s）",
            device, name, count, batches, mode,
        )
        try:
            rep = gacha.run_gacha(
                client,
                inventory_tracker,
                enabled=True,
                draw_type=dt,
                mode=mode,
                count=count,
                batches=batches,
            )
            result = {
                "drawn": rep.total_drawn,
                "bundles": rep.bundles,
                "stopped": rep.stopped_reason,
            }
            logger.info(
                "[%s] WS 週末抽卡完成: %s drawn=%s stopped=%s",
                device, name, rep.total_drawn, rep.stopped_reason,
            )
        except Exception as exc:  # noqa: BLE001
            # attempted 已先落盤；不在同日自動重試不確定是否已扣券的請求。
            result = {"error": f"{type(exc).__name__}: {exc}"}
            logger.exception("[%s] WS 週末抽卡失敗: %s（本日不重試）", device, name)
        out[name] = result
        paid.setdefault("results", {})[str(dt)] = result
        state["gacha_paid"] = paid
        ws_state.save_state(device, state, **state_kw)
    return out


def _run_gacha_free(client, *, device: str, state_dir=None, now=None) -> dict:
    """每日免費召喚 (0x1602): 技能 slot=8 + 同伴 slot=7, 最多 3 次/slot/日.

    Gated by ws_state gacha_free.last_date to avoid re-running the same day.
    Error 89 = daily limit hit (server-side guard); we stop cleanly on that.
    """
    from datetime import datetime
    now_dt = datetime.now() if now is None else now
    today = now_dt.strftime("%Y-%m-%d")
    kw: dict = {"state_dir": state_dir} if state_dir is not None else {}
    st = ws_state.load_state(device, **kw)
    gf = st.get("gacha_free") or {}
    if gf.get("last_date") == today:
        return {"skipped": f"already done {today}"}
    results = gacha.free_draw_all(client)
    total = sum(v.get("drawn", 0) for v in results.values())
    gf["last_date"] = today
    gf["last_total"] = total
    st["gacha_free"] = gf
    ws_state.save_state(device, st, **kw)
    return {"free_draws": results, "total": total}


# home module(12) home_farm_info(3077)/harvest(3081) is intermittently UNanswered
# over a pure-WS session (~50% timeout live, 5554 2026-06-14). Bound the home-module
# calls so a flaky read fails fast and degrades instead of burning the full 15s
# call_timeout — and never lets that timeout abort the reliable buy/打工 steps.
_FARM_HOME_TIMEOUT_S = 5.0


def _run_farm(client, *, role_id: int, farm_config: Optional[dict],
              inventory_tracker=None, device: Optional[str] = None) -> dict:
    """農場/打工: 收成 (免費) + 種植 / 打工 / 莊園購買 (依設定)。

    home module(12) 的 read_farm/harvest 在純 WS 下回應不穩，且當 server 端管家
    (打工) 運作中時手動收成既多餘、又會與管家搶同一塊地造成 no-reply timeout
    (= 使用者回報的 cmd=3081)。因此分三段、可靠模組與 flaky 模組解耦：

      1. 先用可靠的 worker module(73) ``read_work_status`` 判打工是否運作。
      2. 打工運作中 → 跳過手動 read_farm/harvest/plant（管家代收+代種）。
         打工關 → best-effort 手動收成/種植（短 timeout + try/except；timeout 記
         skipped 不 raise），避免 home-module flaky 連帶吞掉後面可靠的 buy。
      3. ``start_work``（worker）與 ``buy``（shop）走可靠模組，獨立於 home module
         之外照跑 —— 修掉「read_farm 一 timeout 就連每日種子/肥料購買都漏掉」。

    Returns ``{work_status, harvest, plant, work, buy}``。
    """
    cfg = farm_config or {}
    seed_id = cfg.get("seed_id")
    team_cfg_id = cfg.get("team_cfg_id")
    buy_list = cfg.get("buy")
    summary: dict = {"work_status": None, "harvest": None, "plant": None,
                     "work": None, "buy": None}

    # 1) 打工偵測 (worker module 73, reliable) — 決定是否需要手動收成。
    worker_running = False
    try:
        status = farm.read_work_status(client, role_id, timeout=_FARM_HOME_TIMEOUT_S,
                                       device_id=device)
        summary["work_status"] = status
        worker_running = bool(status.get("running"))
    except WSError as exc:
        summary["work_status"] = {"skipped": f"read_work_status 失敗: {exc}"}

    # 2) 手動收成/種植 (home module 12, flaky；打工運作時多餘) — best-effort。
    if worker_running:
        summary["harvest"] = {"skipped": "打工運作中，管家代收"}
        if seed_id:
            summary["plant"] = {"skipped": "打工運作中，管家代種"}
    else:
        # The live server answers home_farm_info only ONCE per session, so read the
        # farm a single time and reuse the snapshot for both harvest and plant.
        try:
            info = farm.read_farm(client, role_id, timeout=_FARM_HOME_TIMEOUT_S)
            summary["harvest"] = farm.harvest_ready(
                client, role_id, info=info, timeout=_FARM_HOME_TIMEOUT_S,
                device_id=device)
            if seed_id:
                summary["plant"] = farm.plant_empty(
                    client, role_id, int(seed_id), info=info,
                    timeout=_FARM_HOME_TIMEOUT_S, device_id=device)
        except WSError as exc:
            logger.info("ws_token farm: home_farm 不可用，本輪跳過手動收成 (%s)", exc)
            summary["harvest"] = {"skipped": f"home_farm 不可用: {exc}"}

    # 3) 打工設定 + 莊園購買 (worker/shop module, reliable) — 獨立於 home module。
    if team_cfg_id and not worker_running:
        summary["work"] = farm.start_work(client, int(team_cfg_id), device_id=device)
    # 莊園購買: buy each configured farm-shop item UP TO its daily target. Reads
    # today's count first, so an item already bought in the GUI is respected
    # (buys only the remainder; nothing if already at target).
    if buy_list:
        summary["buy"] = farm.buy_farm_shop(client, buy_list, device_id=device)

    return summary


def _harvest_card_week_key(now=None) -> tuple[str, float]:
    """Return (ISO week key, timestamp) for the weekly harvest-card gate."""
    from datetime import datetime

    if now is None:
        now_dt = datetime.now()
    elif isinstance(now, (int, float)):
        now_dt = datetime.fromtimestamp(float(now))
    else:
        now_dt = now
    iso = now_dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}", float(now_dt.timestamp())


def _run_harvest_card(client, *, role_id: int, farm_config: Optional[dict],
                      inventory_tracker=None, device: str = "",
                      state_dir=None, now=None) -> dict:
    """每週豐收卡：獨立 tag，每 ISO week 最多跑一次，預設用 3 張。

    Reuses ``farm.run_harvest_card_cycle`` for the protocol flow; this wrapper
    only owns config extraction and the weekly idempotency gate.
    """
    cfg = farm_config or {}
    hcc_cfg = cfg.get("harvest_card_cycle")
    if not (isinstance(hcc_cfg, dict) and bool(hcc_cfg.get("enabled"))):
        return {"skipped": "harvest_card_cycle disabled"}

    week_key, ts = _harvest_card_week_key(now)
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    state: dict = {}
    if device:
        state = ws_state.load_state(device, **kw)
        hc_state = state.get("harvest_card") or {}
        if hc_state.get("last_week") == week_key:
            return {"skipped": f"already done {week_key}"}

    num_cards = int(hcc_cfg.get("num_cards", 3))
    fert_id = int(hcc_cfg.get("fertilizer_id", farm.FERTILIZER_ID_HIGH_YIELD))
    result = farm.run_harvest_card_cycle(
        client, role_id, num_cards=num_cards, fertilizer_id=fert_id,
        inventory_tracker=inventory_tracker, device_id=device)

    if device and result.get("ok", True):
        state["harvest_card"] = {
            "last_week": week_key,
            "last_ts": ts,
            "num_cards": num_cards,
            "cards_bought": int(result.get("cards_bought") or 0),
        }
        ws_state.save_state(device, state, **kw)
    return result


def _run_dungeon(client, *, sweeps: Sequence[Sequence[int]]) -> dict:
    """深淵之門 / 萬神試煉: 掃蕩 only (battle has anti-cheat risk — never auto-run).

    Each ``sweeps`` entry is ``(type, dungeon_id, num)``; with none configured the
    task is skipped (dungeon_id is a live-confirm value steward does not derive).
    run_sweep takes the reward directly and bypasses the client-side battle, so it
    never sends an unverified client-reported result. Returns ``{sweeps: [...]}``.
    """
    if not sweeps:
        return {"skipped": "no dungeon_sweeps configured"}
    results: list[dict] = []
    for entry in sweeps:
        type_, dungeon_id, num = int(entry[0]), int(entry[1]), int(entry[2])
        r = dungeon.run_sweep(client, type=type_, dungeon_id=dungeon_id, sweep_num=num)
        results.append({
            "type": type_, "dungeon_id": dungeon_id, "num": num,
            "success": r.success, "rewards": r.rewards, "error_code": r.error_code,
        })
    return {"sweeps": results}


def _run_hellgate(client, *, hellgate_config: Optional[dict], should_abort=None) -> dict:
    """穿越深淵之門：WS 進場/結算，官方 B 引擎計算傷害。"""
    cfg = hellgate_config or {}
    if not cfg.get("enabled"):
        return {"skipped": "hellgate pure_ws disabled"}
    b_mode = str(cfg.get("b_mode") or "cdp").strip().lower()
    prefer_ephemeral = b_mode == "ephemeral"
    cdp = cfg.get("cdp_port")
    if not prefer_ephemeral and not cdp:
        return {"skipped": "no hellgate B cdp_port", "success": False}
    report = hellgate.run_with_b(
        client,
        prefer_ephemeral=prefer_ephemeral,
        cdp_port=int(cdp) if cdp else None,
        game_url=cfg.get("game_url"),
        headless=bool(cfg.get("headless", True)),
        ready_timeout_sec=float(cfg.get("ready_timeout_sec") or 90),
        max_frames=int(cfg.get("max_frames") or 30_000),
        speed_scale=float(cfg.get("speed_scale") or 2.0),
        realtime=bool(cfg.get("realtime", True)),
        simulation_timeout_sec=float(cfg.get("simulation_timeout_sec") or 330),
        timeout=float(cfg.get("timeout_sec")) if cfg.get("timeout_sec") else None,
    )
    out = report.as_dict()
    if report.skipped:
        return out
    if not report.success:
        raise RuntimeError(report.error or "hellgate pure_ws failed")
    return out


def _run_arena(client, *, arena_config: Optional[dict], should_abort=None,
               device: str = "") -> dict:
    """競技場 pure WS：``arena_battle_mode=pure_ws`` 時執行。

    arena_config::
      {
        "enabled": bool,
        "fights": 9,
        "gap_sec": 7,
        "b_mode": "ephemeral",   # ephemeral=全新無 profile；cdp=既有 CDP
        "cdp_port": 0,           # b_mode=cdp 時
        "game_url": "...",
        "headless": True,
      }
    """
    cfg = arena_config or {}
    if not cfg.get("enabled"):
        return {"skipped": "arena pure_ws disabled"}
    target = arena_fight.coerce_arena_daily_fights(
        cfg.get("fights") or arena_fight.DEFAULT_FIGHTS
    )
    fought_before, remaining = arena_fight.daily_fight_plan(
        device or None, target
    )
    if remaining == 0:
        return {
            "success": True,
            "fought": 0,
            "fought_today": fought_before,
            "target": target,
            "already_done": True,
        }
    b_mode = str(cfg.get("b_mode") or "ephemeral").strip().lower()
    prefer_ephemeral = b_mode != "cdp"
    cdp = cfg.get("cdp_port")
    if not prefer_ephemeral and not cdp:
        return {"skipped": "no B cdp_port", "success": False}
    report = arena_fight.run_with_b(
        client,
        fights=remaining,
        gap_sec=float(cfg.get("gap_sec") or 7),
        should_abort=should_abort,
        prefer_ephemeral=prefer_ephemeral,
        cdp_port=int(cdp) if cdp else None,
        game_url=cfg.get("game_url"),
        headless=bool(cfg.get("headless", True)),
        ready_timeout_sec=float(cfg.get("ready_timeout_sec") or 90),
        device=device or None,
    )
    out = report.as_dict()
    fought_today = fought_before + report.fought
    out.update({"fought_today": fought_today, "target": target})
    if fought_today >= target:
        out["success"] = True
        return out
    if not report.success:
        raise RuntimeError(report.error or f"arena pure_ws incomplete fought={report.fought}")
    return out


def _run_escort(client, *, device: str, escort_config: Optional[dict],
                should_abort=None) -> dict:
    """賞金之路純 WS：WS 取戰鬥資料，官方 BattleMainServer 算勝負。"""
    cfg = escort_config or {}
    if not cfg.get("enabled"):
        return {"skipped": "escort pure_ws disabled"}
    b_mode = str(cfg.get("b_mode") or "ephemeral").strip().lower()
    prefer_ephemeral = b_mode != "cdp"
    cdp = cfg.get("cdp_port")
    if not prefer_ephemeral and not cdp:
        return {"skipped": "no escort B cdp_port", "success": False}
    report = escort_fight.run_with_b(
        client,
        device=device,
        max_fights=int(cfg.get("max_fights") or escort_fight.DEFAULT_MAX_FIGHTS),
        gap_sec=float(cfg.get("gap_sec") or escort_fight.DEFAULT_GAP_SEC),
        should_abort=should_abort,
        prefer_ephemeral=prefer_ephemeral,
        cdp_port=int(cdp) if cdp else None,
        game_url=cfg.get("game_url"),
        headless=bool(cfg.get("headless", True)),
        ready_timeout_sec=float(cfg.get("ready_timeout_sec") or 90),
    )
    out = report.as_dict()
    if report.skipped:
        return out
    if not report.success:
        raise RuntimeError(report.error or f"escort pure_ws incomplete fought={report.fought}")
    return out


def _run_rogue(client, *, device: str, state_dir=None, now=None) -> dict:
    """萬神試煉 本周積分獎勵一鍵領取 — 每週五一次 (使用者 2026-06-13 指定).

    rogue_week_reward (cmd 19482, empty body) claims every currently-eligible 積分
    里程碑 in one shot (free — only grants earned rewards, no item/currency cost).
    Gated to run once per Friday: the date of the last claimed Friday is persisted
    in ws_state/<device>.json ``{"rogue": {"last_date": "YYYY-MM-DD"}}`` so repeated
    hourly wakes on the same Friday only claim once. Non-Friday wakes skip without
    sending anything. The week marker is written ONLY on a successful reply, so a
    transient failure (0x0201) retries on the next Friday wake.

    A DORMANT event (not open / nothing to claim) answers with NO frame at all —
    not even the 0x0201 the protocol notes assumed — so the call times out. That is
    the same shape as a dormant guild 尋寶 event: treat it as a benign skip (never an
    error), probe with a short timeout to fail fast, and DON'T persist the marker so
    a later Friday wake re-probes once the event opens.

    Returns ``{claimed_run, reason?}`` when skipped, else
    ``{claimed_run, success, claimed, rewards, error_code}``.
    """
    from datetime import datetime
    now = datetime.now() if now is None else now
    if now.weekday() != _ROGUE_WEEKDAY:
        return {"claimed_run": False, "reason": f"not Friday (weekday={now.weekday()})"}

    kw = {"state_dir": state_dir} if state_dir is not None else {}
    today = now.strftime("%Y-%m-%d")
    st = ws_state.load_state(device, **kw)
    if (st.get("rogue") or {}).get("last_date") == today:
        return {"claimed_run": False, "reason": f"already claimed {today}"}

    try:
        r = rogue.claim_week_reward(client, timeout=_ROGUE_PROBE_S)
    except WSTimeoutError:
        # 事件休眠/無可領 → server 不回任何 frame（非 0x0201）。視為跳過（不是任務失敗），
        # 不寫週標記 → 事件之後若開了，下個 Friday 喚醒仍會領到。
        logger.info("ws_token rogue: %s week_reward 無回應（事件休眠/無可領），跳過",
                    device)
        return {"claimed_run": False, "reason": "event dormant (no response)"}
    if r.success:
        st["rogue"] = {"last_date": today, "last_ts": now.timestamp()}
        ws_state.save_state(device, st, **kw)
    return {"claimed_run": True, "success": r.success, "claimed": list(r.claimed),
            "rewards": r.rewards, "error_code": r.error_code}


def _record_statue_executed(device: str) -> None:
    """Write json_manager record so statue_weekly.py sees this Friday as done.

    Lazy-imported to avoid circular imports (game_actions → ws_token).
    """
    try:
        from json_manager import time_recording
        from game_actions.statue_weekly import _RECORD_NAME
        time_recording(device, name=_RECORD_NAME)
    except Exception as exc:
        logger.warning("statue: failed to write json_manager record: %s", exc)


def _run_statue(
    client: WSGameClient,
    *,
    device: str,
    amount: int = 7000,
    state_dir=None,
    now=None,
) -> dict:
    """菇菇雕像 每週五一鍵消耗果蔬貢品 — Friday only, once per Friday.

    Consumes ``amount`` fruits/vegetables at the mushroom statue via pure WS
    (home_farm_spend_fruit, cmd 3107).  Gated to run once per Friday: the date
    is persisted in ws_state/<device>.json ``{"statue": {"last_date": ...}}``.
    Also writes the json_manager record (``statue_weekly_last_execution``) so
    the Playwright/ADB pipeline task (daily_pipeline Task 13.5) skips the UI
    flow on the same day.

    NOT yet live-verified (2026-06-15); protocol confirmed from game client JS.

    Returns ``{claimed_run: False, reason}`` when skipped, else
    ``{claimed_run: True, ok, result, amount}``.
    """
    from datetime import datetime
    now = datetime.now() if now is None else now
    if now.weekday() != _STATUE_WEEKDAY:
        return {"claimed_run": False, "reason": f"not Friday (weekday={now.weekday()})"}

    kw = {"state_dir": state_dir} if state_dir is not None else {}
    today = now.strftime("%Y-%m-%d")
    st = ws_state.load_state(device, **kw)
    if (st.get("statue") or {}).get("last_date") == today:
        return {"claimed_run": False, "reason": f"already spent {today}"}

    r = statue.spend_fruit(client, amount)
    if r.get("ok"):
        st["statue"] = {"last_date": today, "amount": amount, "last_ts": now.timestamp()}
        ws_state.save_state(device, st, **kw)
        _record_statue_executed(device)
    return {"claimed_run": True, **r}


def _run_carpark(client, *, target: Optional[int], auto: bool = False,
                 plan_cfg: Optional[dict] = None, device: str = "",
                 state_dir=None, now=None, sleep_fn=None, time_fn=None,
                 cluster_server_id: Optional[int] = None,
                 decision_log=None) -> dict:
    """停車 (只停不收) — plan 模式 (current-parked + 8h 重停 + 09:59 搶位) 或 legacy.

    Plan 模式 (``plan_cfg.enabled``; 2026-06-13 手機fc 方案):
      1. 倉庫收益每輪先領 (免費已賺，與窗口無關)。
      2. 若醒在「開窗前 open_lead 秒內」→ 等到開窗 (``sleep_fn``) 再停 (搶 10:00)。
      3. 開窗內 (``win.cross > 0``)：讀 read_parked_cross 得「當前在停跨界車數」→
         need = target − current → need>0 才 auto_select_and_park_many
         (跨界限定泊銀, 優先鉑銀 ``silver_levels``)。搶位時對「lot 還沒鋪好」
         短重試。8h 被遊戲自動收回後 current 歸 0，下次喚醒自動補停 (自我修正,
         不需每日配額)。
      4. 算下次該醒的時刻 (最早到期車 8h / 下個開窗−lead) 寫
         ``ws_state.carpark_repark.next_ts``，sleep_service 會把喚醒往前 clamp。
      本服/好友車位遊戲內建自動化，不在此範圍。

    Legacy 模式 (plan 缺/關):
      - explicit ``target``: park into that specific cross lot's first free slot.
      - ``auto``: list parkable cross lots via car_park_search, park one mount.
    Skipped entirely when neither is set.
    """
    if plan_cfg and plan_cfg.get("enabled"):
        import time as _time
        from datetime import datetime as _dt
        from ws_token import carpark_plan as cp
        now = _dt.now() if now is None else now
        sleep_fn = sleep_fn or _time.sleep
        time_fn = time_fn or _time.monotonic
        plans = cp.parse_plan(plan_cfg)
        max_sec = cp.park_max_seconds(plan_cfg)
        open_lead = cp.open_lead_seconds(plan_cfg)
        margin = cp.repark_margin_seconds(plan_cfg)
        offset = cp.start_time_offset(plan_cfg)
        cluster_min = cp.cluster_min(plan_cfg)
        allow_low = cp.allow_low_noncluster(plan_cfg)
        levels = tuple(int(v) for v in (plan_cfg.get("silver_levels") or
                                        carpark.SILVER_PREFERRED_LEVELS))
        kw = {"state_dir": state_dir} if state_dir is not None else {}

        def _decision(event: str, **fields) -> None:
            detail = " ".join(
                [f"event={event}"] + [f"{key}={value}" for key, value in fields.items()])
            logger.info("ws_token carpark: %s %s", device, detail)
            if decision_log is not None:
                try:
                    decision_log(detail)
                except Exception:  # noqa: BLE001 — 日誌失敗不可影響停車
                    logger.debug("ws_token carpark: decision callback failed",
                                 exc_info=True)

        # 倉庫收益每輪先領 — 免費已賺收益，與窗口無關。
        collect: dict | None = None
        try:
            collect = carpark.collect_bag_rewards(client)
        except Exception as exc:  # noqa: BLE001 — 領收益失敗不可擋停車
            logger.warning("ws_token carpark: %s warehouse collect failed: %s",
                           device, exc)
            collect = {"success": False, "error": str(exc)}

        # 09:59 搶位：醒在開窗前 open_lead 內 → 等到開窗瞬間再停。
        grabbing = False
        wait = cp.cross_open_wait(plans, now, open_lead)
        if wait is not None:
            secs, open_dt, _w = wait
            if secs > 0:
                logger.info("ws_token carpark: %s pre-open wait %.0fs until %s "
                            "(grab)", device, secs, open_dt)
                sleep_fn(secs)
            now = open_dt
            grabbing = True

        def _store_next(parked_cross, *, window_name: str = "none",
                        target: int = 0) -> dict:
            next_ts = cp.carpark_wake_ts(parked_cross, plans, now,
                                         max_sec=max_sec, open_lead=open_lead,
                                         margin=margin, offset=offset)
            # 快照寫進 ws_state，供 dashboard 唯讀顯示 + start_time epoch 校準
            # (不從面板主動 WS 登入，以免踢掉裝置 session)。
            cars = [{"mount_id": m.mount_id,
                     "master_id": m.parking_info.master_id,
                     "pos": m.parking_info.pos,
                     "start_time": m.parking_info.start_time}
                    for m in parked_cross if m.parking_info is not None]
            st = ws_state.load_state(device, **kw)
            st["carpark_repark"] = {
                "next_ts": next_ts,
                "captured_ts": now.timestamp(),
                "park_max": max_sec,
                "offset": offset,
                "window": window_name,
                "target": int(target),
                "cars": cars,
            }
            ws_state.save_state(device, st, **kw)
            return {"next_repark_ts": next_ts}

        win = cp.active_window(plans, now)
        if win is None or win.cross <= 0:
            out = {"collect": collect,
                   "skipped": f"carpark plan: no open cross window "
                              f"({win.name if win else 'none'})"}
            out.update(_store_next([], window_name=(win.name if win else "none")))
            return out

        target_n = win.cross
        parked = carpark.read_parked_cross(client)
        current = len(parked)
        need = max(0, target_n - current)
        out = {"window": win.name, "target": target_n, "current": current,
               "collect": collect, "grab": grabbing}
        _decision("context", window=win.name, target=target_n, current=current,
                  need=need, server_id=cluster_server_id)

        if need <= 0:
            out["skipped"] = f"already {current}/{target_n} cross parked"
            out.update(_store_next(parked, window_name=win.name, target=target_n))
            return out

        # --- cluster scan (抱團掃描) or normal grab loop -------------------------
        cs_cfg = cp.parse_cluster_scan(plan_cfg)
        # Run 抱團掃描 on ANY in-window park (not only the 09:59 grab): an 8h
        # auto-collect re-park mid-day should also cluster with same-server cars.
        if cs_cfg.enabled:
            _decision("config", min_allies=cs_cfg.min_allies,
                      excluded=list(cs_cfg.excluded_levels),
                      levels=list(cs_cfg.levels),
                      priority=list(cs_cfg.priority_levels),
                      duration=cs_cfg.duration, interval=cs_cfg.interval)
            if not cluster_server_id:
                res = {"parked_count": 0, "requested": need,
                       "reason": "strict_cluster_server_id_missing",
                       "results": [], "scan_rounds": 0}
                _decision("refused", reason=res["reason"])
                out["cross"] = res
                out.update(_store_next(parked, window_name=win.name,
                                       target=target_n))
                return out

            today_parked = carpark._load_today_parked_master_ids(
                device, **({} if state_dir is None else {"state_dir": state_dir}))
            mounts = carpark.read_my_mounts(client)
            quality_m = [m for m in mounts
                         if carpark.MOUNT_QUALITY.get(m.mount_id, 7)
                         >= carpark.MOUNT_MIN_QUALITY]
            if quality_m:
                mounts = quality_m
            mount_id = mounts[0].mount_id if mounts else None
            if mount_id is None:
                res = {"parked_count": 0, "requested": need,
                       "reason": "strict_cluster_no_mount",
                       "results": [], "scan_rounds": 0}
                _decision("refused", reason=res["reason"])
                out["cross"] = res
                out.update(_store_next(parked, window_name=win.name,
                                       target=target_n))
                return out

            scan_deadline = time_fn() + cs_cfg.duration
            cluster_found = False
            scan_round = 0
            last_audit = {}

            while time_fn() < scan_deadline:
                round_started = time_fn()
                scan_round += 1
                _null, _collect = carpark.read_cross_null_and_collect(client)
                scan_parkable, last_audit = carpark.prepare_cluster_scan_candidates(
                    _null, _collect, excluded_levels=cs_cfg.excluded_levels,
                    today_parked=today_parked)
                _decision("candidates", round=scan_round,
                          source_null=last_audit["source_null"],
                          source_collect=last_audit["source_collect"],
                          merged=last_audit["merged"], kept=len(scan_parkable),
                          removed_full=last_audit["removed_full"],
                          removed_non_silver=last_audit["removed_non_silver"],
                          excluded=last_audit["excluded_levels"],
                          removed_today=last_audit["removed_today"])
                ranked = carpark.scan_lots_same_server(
                    client, scan_parkable, cluster_server_id, cs_cfg.levels,
                    priority_levels=cs_cfg.priority_levels,
                    min_allies=cs_cfg.min_allies,
                    decision_log=lambda msg: _decision(
                        "scan", round=scan_round, detail=msg))
                    # ranked is priority-range-first, so pick the first lot that
                    # clears min_allies (a priority lot at the threshold beats a
                    # higher-ally non-priority lot).
                pick = next((r for r in ranked if r[1] >= cs_cfg.min_allies), None)
                _decision("round_result", round=scan_round,
                          ranked=[(carpark.silver_ceng_to_level(l.ceng),
                                   l.master_id, count) for l, count in ranked],
                          qualified=bool(pick))
                if pick is not None:
                    best_lot, scanned_cnt = pick
                    detail = carpark.read_lot(
                        client, type=carpark.CROSS_TYPE,
                        master_id=best_lot.master_id, ceng=best_lot.ceng)
                    latest_cnt = carpark.count_same_server(detail, cluster_server_id)
                    pos = detail.first_free_cross_pos()
                    _decision("revalidate", round=scan_round,
                              level=carpark.silver_ceng_to_level(best_lot.ceng),
                              master_id=best_lot.master_id,
                              scanned_allies=scanned_cnt, allies=latest_cnt,
                              pos=pos, qualified=(latest_cnt >= cs_cfg.min_allies
                                                 and pos is not None))
                    if latest_cnt >= cs_cfg.min_allies and pos is not None:
                        result = carpark.park_into_cross(
                            client, target_id=best_lot.master_id,
                            pos=pos, mount_id=mount_id)
                        _decision("park_result", round=scan_round,
                                  level=carpark.silver_ceng_to_level(best_lot.ceng),
                                  master_id=best_lot.master_id, pos=pos,
                                  mount_id=mount_id, success=result.success,
                                  error_code=result.error_code)
                        if result.success:
                            carpark._record_park_today(
                                device, best_lot.master_id,
                                **({} if state_dir is None
                                   else {"state_dir": state_dir}))
                            lv = carpark.silver_ceng_to_level(best_lot.ceng)
                            out["cross"] = {
                                "parked_count": 1, "requested": need,
                                "reason": "cluster_scan",
                                "level": lv, "target_id": best_lot.master_id,
                                "allies": latest_cnt, "scan_rounds": scan_round,
                                "results": [{
                                    "target_id": best_lot.master_id,
                                    "pos": pos, "mount_id": mount_id,
                                    "success": True}]}
                            cluster_found = True
                            break
                # 官方刷新冷卻從搜尋送出時起算；詳細 lot 查詢若已耗掉
                # 這段時間，就不應在整輪結束後再固定多等一次。
                cooldown_left = cs_cfg.interval - (time_fn() - round_started)
                if cooldown_left > 0:
                    sleep_fn(cooldown_left)

            if cluster_found:
                parked = carpark.read_parked_cross(client)
                out["current"] = len(parked)
                out.update(_store_next(parked, window_name=win.name,
                                       target=target_n))
                _decision("summary", parked_count=1, reason="cluster_scan",
                          scan_rounds=scan_round,
                          next_repark_ts=out.get("next_repark_ts"))
                return out

            res = {"parked_count": 0, "requested": need,
                   "reason": "strict_cluster_not_found", "results": [],
                   "scan_rounds": scan_round, "audit": last_audit}
            out["cross"] = res
            out.update(_store_next(parked, window_name=win.name,
                                   target=target_n))
            _decision("summary", parked_count=0, reason=res["reason"],
                      scan_rounds=scan_round,
                      next_repark_ts=out.get("next_repark_ts"))
            return out

        # Normal grab loop (cluster_scan disabled or non-grab wake)
        poll = cp.grab_poll_seconds(plan_cfg)
        grab_window = cp.grab_window_seconds(plan_cfg)
        hard_cap = max(1, cp.grab_attempts(plan_cfg))
        if grabbing:
            hard_cap = max(hard_cap, int(grab_window / poll) + 2)
        deadline = time_fn() + grab_window
        res: dict | None = None
        round_i = 0
        while True:
            round_i += 1
            res = carpark.auto_select_and_park_many(
                client, count=need, prefer_levels=levels,
                cluster_server_id=cluster_server_id, cluster_min=cluster_min,
                allow_low_noncluster=allow_low,
                device=device, state_dir=state_dir)
            if int(res.get("parked_count") or 0) > 0:
                break
            if res.get("reason") == "park_timeout":
                break
            if not grabbing:
                break
            if round_i >= hard_cap or time_fn() >= deadline:
                break
            sleep_fn(poll)
            parked = carpark.read_parked_cross(client)   # 重算 need 防重複停
            current = len(parked)
            need = max(0, target_n - current)
            if need <= 0:
                break
        out["cross"] = res
        parked = carpark.read_parked_cross(client)        # refresh start_times
        out["current"] = len(parked)
        out.update(_store_next(parked, window_name=win.name, target=target_n))
        return out

    if target:
        return carpark.auto_park_cross(client, target_id=int(target))
    if auto:
        return carpark.auto_select_and_park(client, device=device,
                                            state_dir=state_dir)
    return {"skipped": "carpark disabled (set carpark_target or carpark_auto)"}


def _run_spirit(client) -> dict:
    """守護靈免費召喚: draw_all_free 只用 free_times, 不買招喚貨幣 (800003 不存在)."""
    return spirit.draw_all_free(client)


def _run_secret_jewel(client, *, draw_free: bool, buy_daily: bool) -> dict:
    """秘寶(塵世)尋寶: 免費抽 (draw_free) + 每日買尋寶圖 (buy_daily, SPENDS 粉鑽).

    兩動作各自 opt-in (使用者 2026-06-27)。只做塵世秘寶 (pool_type=1，傳說/遠古未開放)。
    免費抽只吃 free_times (2/日)；買尋寶圖補到每日 10 (粉鑽，server 端上限/已買數冪等)。
    兩者皆靠 server 端每日計數器,每次喚醒跑都安全 — 無 ws_state 日期閘。
    """
    summary: dict = {"free": None, "buy": None}
    if draw_free:
        summary["free"] = secret_jewel.draw_free(client)
    if buy_daily:
        summary["buy"] = secret_jewel.buy_daily_maps(client)
    return summary


def _run_kungfu_store(client) -> dict:
    """菇菇武道會 競猜商店: 用粉鑽把 4 個競猜幣檔位(免費/600/1500/3000)買到上限.

    Server-gated: the 競猜商店 only opens during 武道會 循環賽/淘汰賽 (= 膜拜前一周);
    outside that window every tier rejects on the first attempt (0x0201) and the
    pass is a cheap idempotent no-op. Per-period caps are server-enforced, so a
    repeat run never over-buys. Spends up to 12,600 粉鑽/week when fully open.
    """
    rep = kungfu_store.claim_guess_coins(client)
    return {"coins": rep.coins, "diamonds_spent": rep.diamonds_spent,
            "bought": rep.bought, "stopped": rep.stopped}


def _run_kungfu_worship(client) -> dict:
    """菇菇武道會膜拜冠軍：空 body 的 16665 純 WS action。

    活動窗口、已膜拜與帳號資格都由 server 判斷；拒絕/逾時只視為本輪
    無可做，不讓它阻斷其他每日任務。
    """
    result = kungfu_race.worship(client)
    if result.success:
        return {"worship": result.worship,
                "response_cmd": result.response_cmd}
    summary = {"skipped": "server_rejected" if result.response_cmd == kungfu_race.CMD_ERROR
               else "no_ack"}
    if result.error_code is not None:
        summary["error_code"] = result.error_code
    if result.error:
        summary["error"] = result.error
    return summary


def _run_pay_mall(client) -> dict:
    """限時商店 -> 每日商店 免費禮包 (150 鑽石/日, bundle_id 20101).

    Server-gated daily cap (error code 173) — idempotent, safe every wake.
    """
    result = pay_mall.claim_free_gift(client)
    return {"success": result.success, "error_code": result.error_code}


def _run_workshop(client, inventory_tracker, *, device: str,
                  state_dir=None, now=None) -> dict:
    """加工坊 12h 配方輪換，並以可製作量作為切換前的安全閘門。

    ``selected_food`` 已經是目標時不重送；目標缺料時不先 cancel，避免把正在
    生產的工坊清成閒置。只有至少一個小隊成功切換或已確認在目標配方時才寫入
    cadence state，否則下次喚醒會重試。
    """
    import time as _time

    now = _time.time() if now is None else now
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    state = ws_state.load_state(device, **kw)
    workshop_state = state.get("workshop") or {}
    last_ts = float(workshop_state.get("last_rotate_ts") or 0)
    if last_ts and now - last_ts < _WORKSHOP_ROTATE_S:
        hours = (now - last_ts) / 3600.0
        return {"rotated": False,
                "reason": f"rotated {hours:.1f}h ago (<12h)"}

    parity = ((int(workshop_state.get("parity") or 0) + 1) % 2
              if last_ts else 0)
    counts = getattr(inventory_tracker, "counts", {}) or {}
    materials = dict(counts)
    needed = {mat for food in workshop.RECIPE_FOOD_IDS
              for mat, _per in workshop.RECIPE_APPROACH.get(food, ())}
    missing = sorted(mat for mat in needed if mat not in counts)
    for mat in missing:
        logger.warning("ws_token runner: %s workshop: 素材 %s 不在庫存快照,"
                       "視為 0 (相關食物 producible=0)", device, mat)

    out = workshop.rotate_team_recipes(
        client, parity=parity, materials=materials)
    switched = out.get("switched") or []
    valid = any(
        (entry.get("chosen") or {}).get("ok")
        or entry.get("reason") == "already_selected"
        for entry in switched
    )
    if not valid:
        logger.warning(
            "ws_token runner: %s workshop rotate had no successful/confirmed "
            "team; state not written, will retry", device)
        result = {"rotated": False,
                  "reason": "no successful or confirmed target", **out}
    else:
        state["workshop"] = {
            "last_rotate_ts": now,
            "parity": parity,
        }
        ws_state.save_state(device, state, **kw)
        result = {"rotated": True, **out}

    if missing:
        result = {**result, "missing_materials": missing}
    return result


def _run_couple(client, *, gifts: bool, forge_ring: bool,
                device: str = "", state_dir=None, now=None) -> dict:
    """伴侶: 奶茶+玫瑰一天送一次 (give_all_in_hand) + 戒指錘鍊 (spend 類).

    花/奶茶 每日只送一次 (使用者 2026-06-14 指定)：用
    ws_state/<device>.json ``{"couple": {"gift_date": "YYYY-MM-DD"}}`` 當日期閘，
    同一天多次喚醒只送一次；隔日自動重送。兩種禮物共用一個日期閘 (一起送或一起跳過)。
    日期在「送禮步驟完成」後寫入 (give_all_in_hand 對缺貨回 dict 不 raise，視為完成；
    連線錯誤會 raise → 不寫 → 下次喚醒重送)。

    默契考驗 (Marry type 6) 已由 _run_main_tasks 的 claim_marry_tasks 領取。
    forge_ring (戒指錘鍊) 為獨立 opt-in，不受每日閘影響。
    無伴侶 (favor list 空且 lover_id=0) → skip (不寫日期閘)。
    """
    partners = couple.read_favor_info(client)
    friend_id = partners[0].role_id if partners else couple.read_partner(client)
    summary: dict = {"partner": friend_id, "milk_tea": None, "flower": None,
                     "ring": None}
    if not friend_id:
        return {**summary, "skipped": "no partner"}
    if gifts:
        from datetime import datetime
        now = datetime.now() if now is None else now
        today = now.strftime("%Y-%m-%d")
        kw = {"state_dir": state_dir} if state_dir is not None else {}
        st = ws_state.load_state(device, **kw)
        if (st.get("couple") or {}).get("gift_date") == today:
            summary["gifts_skipped"] = f"already gifted {today}"
        else:
            summary["milk_tea"] = couple.give_all_in_hand(
                client, friend_id=friend_id, flower_id=couple.MILK_TEA)
            summary["flower"] = couple.give_all_in_hand(
                client, friend_id=friend_id, flower_id=couple.FLOWER)
            st["couple"] = {"gift_date": today, "gift_ts": now.timestamp()}
            ws_state.save_state(device, st, **kw)
    if forge_ring:
        summary["ring"] = couple.forge_ring_until_empty(client)
    return summary


def _run_lamp(client, *, ip: str = "", lamp_percent: float = 0.0,
              lamp_min_keep: int = 0, lamp_daily_min: int = 0,
              initial_count: Optional[int] = None, on_progress=None,
              should_abort: Optional[Callable[[], bool]] = None) -> dict:
    """開神燈: sequentially open up to 10000 boxes and auto-equip/sell drops.

    Only reached when ``open_lamp=True`` (gated by run_device); it consumes 神燈
    items. The server accepts at most 20 per OPEN_ALL call, so this runs a
    single-threaded 20-item batch loop capped at 500 batches (10000 total) and
    stops early when the server reports no lamps left. Returns
    lamp.open_lamp's summary ``{opened, equipped, sold, left, dry_run, target,
    initial_count, remaining}``.

    ``lamp_percent`` (>0 = 依當前神燈總數百分比決定本輪目標) / ``lamp_min_keep``
    (>0 = 剩餘神燈硬地板) 啟用百分比/保留模式；兩者皆 0 時維持舊行為（開到沒燈）。
    ``lamp_daily_min`` (>0) = 每日最少開啟數量，不受百分比規則約束。
    ``initial_count`` 是登入快照撈到的神燈現量（None = 由 lamp 第一批反推）；
    ``on_progress(opened, target)`` 每批回報進度。
    """
    import datetime as _dt
    from json_manager import check_json, record_json

    opened_today = 0
    today_str = _dt.date.today().strftime("%Y-%m-%d")
    if lamp_daily_min > 0 and ip:
        rec = check_json(ip, "ws_lamp_daily_opened")
        if rec and isinstance(rec, dict) and rec.get("date") == today_str:
            opened_today = max(0, int(rec.get("count", 0) or 0))

    result = _load_lamp().open_lamp(
        client,
        dry_run=False,
        batch_num=_LAMP_BATCH_NUM,
        max_batches=_LAMP_MAX_BATCHES,
        batch_delay=_LAMP_BATCH_DELAY_SEC,
        lamp_percent=lamp_percent,
        lamp_min_keep=lamp_min_keep,
        lamp_daily_min=lamp_daily_min,
        opened_today=opened_today,
        initial_count=initial_count,
        on_progress=on_progress,
        should_abort=should_abort,
        device_id=ip or None,
    )

    if lamp_daily_min > 0 and ip and result.get("opened", 0) > 0:
        record_json(ip, "ws_lamp_daily_opened", {
            "date": today_str,
            "count": opened_today + result["opened"],
        })

    return result


def _run_mining(client, tracker: mining.InventoryTracker, *,
                mining_config: Optional[dict],
                device: Optional[str] = None,
                should_abort: Optional[Callable[[], bool]] = None) -> dict:
    """挖礦 opt-in: 用 0x0401 seed + 0x0402 庫存現量，一步一刷新，鎬子用完即停。

    planner 預設維持 v1；`planner_version` / `shadow_planner_version` 由 ws_phase
    自裝置層設定注入 mining 子設定。
    """
    cfg = mining_config or {}
    return mining_supervised.mine_until_pickaxe_empty(
        client,
        tracker,
        allow_bomb=bool(cfg.get("allow_bomb")),
        allow_drill=bool(cfg.get("allow_drill")),
        should_abort=should_abort,
        max_steps=int(cfg.get("max_steps") or 200),
        timeout=cfg.get("timeout"),
        max_depth=cfg.get("max_depth"),
        device_id=device,
        planner_version=str(cfg.get("planner_version") or "v1").strip().lower(),
        shadow_planner_version=str(cfg.get("shadow_planner_version") or "").strip().lower(),
    )


def _run_guild(client, *, spend: bool) -> dict:
    """help_all (free); donate (spend); treasure open only with an active round.

    Guild treasure (尋寶) is event-gated: when no round is running the server
    does not answer guild_treasure_info at all, which surfaces as a
    :class:`WSTimeoutError`. So the treasure read is (a) only done under
    ``spend`` (we would never open it otherwise) and (b) wrapped so a dormant
    event is skipped without failing the whole guild task — help / donate still
    count. A short probe timeout keeps a dormant event from stalling the run.
    """
    summary: dict = {"help": None, "donate": None, "treasure": None}
    summary["help"] = guild.help_all(client)
    if not spend:
        return summary
    summary["donate"] = guild.donate_until_capped(client)
    try:
        info = guild.list_treasure(client, timeout=_TREASURE_PROBE_S)
    except WSTimeoutError:
        summary["treasure"] = "unavailable (dormant event)"
        logger.info("ws_token runner: guild treasure dormant (no response), skipped")
        return summary
    if getattr(info, "round", 0) and getattr(info, "box_list", None):
        summary["treasure"] = guild.open_all_treasure(client)
    else:
        summary["treasure"] = "no active round"
    return summary


def _run_steward(client, *, spend: bool, serv_time: int,
                 sweep_list: Sequence[Sequence[int]],
                 device: str = "", state_dir=None,
                 inventory_counts: Optional[dict[int, int]] = None) -> dict:
    """Read state; shopping once/day when spend; dungeon sweep every wake.

    購物管家受 ``spend`` 與每日日期閘控制；副本管家每次喚醒都嘗試。
    ``spend=False`` 時不自動續費，但已在有效期內仍會正常掃蕩。
    """
    summary: dict = {
        "info": None, "shopping": None, "sweep": None,
        "shopping_active": False, "dungeon_active": False,
    }
    summary["info"] = steward.read_info(client)

    # --- 購物管家: once per day ---
    if spend:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        kw = {"state_dir": state_dir} if state_dir is not None else {}
        st = ws_state.load_state(device, **kw) if device else {}
        steward_st = st.get("steward") or {}

        if steward_st.get("shopping_date") == today:
            summary["shopping_skipped"] = f"already shopped {today}"
        else:
            shopping_active = steward.ensure_active(
                client, steward.SERVICE_SHOPPING, serv_time=serv_time, renew=True)
            summary["shopping_active"] = shopping_active
            if shopping_active:
                summary["shopping"] = steward.run_shopping(client)
                if device:
                    st["steward"] = {**steward_st, "shopping_date": today}
                    ws_state.save_state(device, st, **kw)

    # caller 沒給 sweep_list 時，從遊戲內「開啟掃蕩」設定 + dungeon_list
    # 自動推導；設定回覆的內層 key 不是 level/times，不能直接拿來組封包。
    effective_sweeps: Sequence[Sequence[int]] = sweep_list
    if not effective_sweeps:
        try:
            setting = steward.read_dungeon_setting(client)
            levels = steward.read_dungeon_levels(client)
            effective_sweeps = steward.derive_sweep_list(
                setting,
                levels,
                inventory_counts=inventory_counts,
            )
        except Exception as exc:  # noqa: BLE001
            # 讀不到副本清單時只跳過副本管家，不能讓其它 WS 日任務整批失敗。
            logger.warning("ws_token steward: auto derive sweep list failed: %s", exc)
            effective_sweeps = ()
            summary["sweep_derived_error"] = f"{type(exc).__name__}: {exc}"
        summary["sweep_derived"] = list(effective_sweeps)
    if effective_sweeps:
        dungeon_active = steward.ensure_active(
            client, steward.SERVICE_DUNGEON, serv_time=serv_time, renew=spend)
        summary["dungeon_active"] = dungeon_active
        if dungeon_active:
            logger.info(
                "ws_token steward: 每輪副本掃蕩送出 entries=%s",
                list(effective_sweeps),
            )
            summary["sweep"] = steward.run_dungeon_sweep(client, effective_sweeps)
            logger.info(
                "ws_token steward: 每輪副本掃蕩完成 results=%d",
                len(getattr(summary["sweep"], "results", ())),
            )
    else:
        logger.info("ws_token steward: 本輪沒有可送出的副本掃蕩項目")
    return summary


def _run_relic(client, tracker: mining.InventoryTracker, *, enabled: bool,
               max_steps: int, fragment_floor: int = 0) -> dict:
    """遺物 平均強化: opt-in, default off; SPENDS 遺物碎片 → bounded by max_steps.

    Only reached when ``enabled`` (gated by run_device). 遺物碎片 (item 100022) is
    a grind currency the server deducts authoritatively on each ``relic_up`` step,
    so this is a SPEND task and stays OFF by default + hard-capped by ``max_steps``.

    Strategy (matches relic.plan_balanced_upgrades 平均 distribution): always
    upgrade the LOWEST-level EQUIPPED relic. The live per-level cost lives in
    configRelic (not exposed client-side) — the server alone deducts it — so the
    plan uses a flat unit cost purely to ORDER/cap the steps; the real budget gate
    is the live 遺物碎片 count from the 0x0402 inventory snapshot (``tracker``): we
    re-check it after each step and STOP when it drops below ``fragment_floor`` or
    when the server rejects with 0x0201 (out of fragments / level cap). ``max_steps``
    caps total upgrades regardless.

    The fragment count is only known when the login-time 0x0402 snapshot carried
    item 100022; if it never did we skip the floor gate (the server-side 0x0201
    rejection still bounds spend) but keep the ``max_steps`` cap. Returns
    ``{upgraded, steps, planned, stopped_reason, fragments?}`` or ``{skipped}``.
    """
    if not enabled:
        return {"skipped": "relic disabled (set ws_token.relic_upgrade=True)"}

    info = relic.read_relics(client)
    if not info.success:
        return {"upgraded": 0, "steps": [], "planned": 0,
                "stopped_reason": f"read failed (cmd=0x{info.response_cmd:04x})",
                "error_code": info.error_code}

    equipped = [(r.uid, r.level) for r in info.equipped]
    fragments = tracker.counts.get(relic.RELIC_FRAGMENT_ITEM)
    floor = max(0, int(fragment_floor))
    # Flat unit cost: the budget here only ORDERS the balanced plan + caps it to
    # at most ``fragments`` (when known) or ``max_steps``. The server deducts the
    # real cost; we re-read the live count each step for the floor gate.
    budget = int(fragments) if fragments is not None else max_steps
    planned = relic.plan_balanced_upgrades(
        equipped, budget, cost_at=lambda _lv: 1, max_steps=max_steps)

    upgraded = 0
    steps: list[int] = []
    stopped_reason = "plan_exhausted"
    for uid in planned:
        if fragments is not None and fragments < floor:
            stopped_reason = f"fragments<{floor}"
            break
        res = relic.upgrade_relic(client, uid)
        if not res.success:
            stopped_reason = f"error_code={res.error_code}"
            break
        upgraded += 1
        steps.append(uid)
        # Re-read the live fragment count from the inventory tracker (the server
        # pushes a 0x0402 consume after each relic_up; the tracker tees it).
        fragments = tracker.counts.get(relic.RELIC_FRAGMENT_ITEM, fragments)

    out: dict = {"upgraded": upgraded, "steps": steps, "planned": len(planned),
                 "stopped_reason": stopped_reason}
    if fragments is not None:
        out["fragments"] = fragments
    return out


def _run_relic_sprint(client, tracker: mining.InventoryTracker, *, enabled: bool,
                      target_spend: int) -> dict:
    """遺物碎片衝刺 (衝刺榜): opt-in, default off; SPENDS 遺物碎片 to claim the rounds.

    Only reached when ``enabled`` (gated by run_device). The 衝刺榜 counts the
    cumulative 遺物碎片 spent server-side, so this SPENDS fragments (by levelling
    relics through :func:`ws_token.relic_sprint.run_relic_sprint`) up to
    ``target_spend`` and then claims every CanGet round — hence OFF by default and
    sharing the SAME inventory ``tracker`` as 遺物 平均強化 (the live 遺物碎片 count
    gate reads ``tracker.counts``). When the activity is not the current rotation
    the orchestrator bails with ``{"skipped": "no active sprint"}``. Returns
    relic_sprint.run_relic_sprint's summary or ``{skipped}``.
    """
    if not enabled:
        return {"skipped": "relic_sprint disabled (set ws_token.relic_sprint.enabled=True)"}
    return relic_sprint.run_relic_sprint(
        client, tracker, target_spend=target_spend, enabled=True)


def _run_mount_sprint(client, *, device: str, enabled: bool,
                      quantity: int, now=None) -> dict:
    """坐騎衝刺：用 mount_levup 一次送出自訂數量的發條。"""
    return mount_sprint.run(
        client,
        device,
        enabled=enabled,
        quantity=quantity,
        now=now,
    )


def _run_dragon_realm(client, tracker: mining.InventoryTracker, *,
                      device: str) -> dict:
    """龍骸聖域 pure-WS: explore + collect keys + tier transition.

    Triweekly gate (Wed/Thu/Fri 10-22) checked here; skip outside window.

    本活動週期完成標記（cycle-bound）：到達三樓門後真人自己上三樓、bot 門前不
    再花體力，本週期已無事可做；若不標記完成，之後每輪 WS 都會重跑並 deadloop
    空轉。故：
      - reached_tier_three_gate → 標記完成，本活動週期後續 WS 跳過（下一週期自動重跑）。
      - out_of_stamina → 不標記（龍骸每 7 分 +1 體力，下一輪 WS 要能繼續花體力）。
      - deadloop / budget_exhausted → 不標記（失敗症狀 / 步數上限，都要能重跑）。
    """
    from game_actions.dragon_realm_scheduler import (
        _is_dragon_week, _within_open_window, _cycle_completed, _mark_done,
    )
    import datetime
    now = datetime.datetime.now()
    if not _is_dragon_week(now.date()):
        return {"skipped": "not dragon week"}
    if not _within_open_window(now):
        return {"skipped": "outside 10-22 window"}
    if _cycle_completed(device, now):
        logger.info("ws_token runner: %s 龍骸本活動週期已到三樓門，跳過", device)
        return {"skipped": "already reached tier-3 gate this cycle"}
    reason = dragon_realm.run(client, tracker, device=device)
    if reason == "reached_tier_three_gate":
        _mark_done(device)
    return {"stop_reason": reason, "keys": tracker.counts.get(dragon_realm.KEY_ITEM, 0)}


def _run_xwar_idle(client, *, device: str, state_dir=None, now=None) -> dict:
    """跨服戰 放置獎勵 純-WS 自動領取（每 ≤4h，只在 biweekly 開放窗口內）.

    Thin wrapper over ``xwar_idle.claim_if_due``: the 4h cadence throttle, the
    open-window gate (act_list 0x180c → cross-war type 33 state==Open, server-
    authoritative so no hardcoded biweekly date drifts) and the ws_state ledger
    all live in the module. Dormant event (no frame) / 0x0201 → benign skip.
    """
    return xwar_idle.claim_if_due(client, device=device, state_dir=state_dir, now=now)


def _run_sea_season(client, *, device: str, sea_config: Optional[dict],
                    inventory_tracker=None) -> dict:
    """航海/賽季 pure WS: claim income + tasks + dispatch + repair + tactic."""
    from ws_token import sea_season
    cfg = sea_config or {}
    wood = 0
    if inventory_tracker:
        wood = int(inventory_tracker.counts.get(sea_season.ITEM_WOOD, 0))
    hg = cfg.get("home_grid")
    if isinstance(hg, (list, tuple)) and len(hg) == 2:
        hg = (int(hg[0]), int(hg[1]))
    else:
        hg = None
    rg = cfg.get("relic_grid")
    if isinstance(rg, (list, tuple)) and len(rg) == 2:
        rg = (int(rg[0]), int(rg[1]))
    else:
        rg = None
    gg = cfg.get("garrison_grid")
    if isinstance(gg, (list, tuple)) and len(gg) == 2:
        gg = (int(gg[0]), int(gg[1]))
    else:
        gg = None
    try:
        adm = int(cfg.get("attack_daily_max", 4))
    except (TypeError, ValueError):
        adm = 4
    return sea_season.run_sea_season(
        client,
        device=device,
        do_dispatch=bool(cfg.get("dispatch", True)),
        do_repair=bool(cfg.get("repair", True)),
        tactic_nodes=cfg.get("tactic_nodes"),
        wood_amount=wood,
        home_grid=hg,
        relic_grid=rg,
        garrison_grid=gg,
        attack_daily_max=adm,
    )


def run_device(device: str, *, spend: bool = False,
               sweep_list: Optional[Iterable[Sequence[int]]] = None,
               open_lamp: bool = False,
               lamp_percent: float = 0.0,
               lamp_min_keep: int = 0,
               lamp_daily_min: int = 0,
               farm_config: Optional[dict] = None,
               dungeon_sweeps: Optional[Iterable[Sequence[int]]] = None,
               carpark_target: Optional[int] = None,
               carpark_auto: bool = False,
               carpark_plan: Optional[dict] = None,
               carpark_state_dir=None,
               carpark_now=None,
               couple_gifts: bool = True,
               forge_ring: bool = False,
               workshop_rotate: bool = True,
               kungfu_guess: bool = False,
               kungfu_worship: bool = False,
               mail_claim: bool = False,
               mail_gem_threshold: Optional[int] = None,
               mail_skill_threshold: Optional[int] = None,
               relic_upgrade: bool = False,
               relic_max_steps: int = 10,
               relic_fragment_floor: int = 0,
               relic_sprint_enabled: bool = False,
               relic_sprint_target: int = relic_sprint.SPRINT_TOTAL,
               mount_sprint_enabled: Optional[bool] = None,
               mount_sprint_quantity: int = mount_sprint.DEFAULT_QUANTITY,
               tycoon: bool = False,
               tycoon_max_rolls: int = 50,
               ad_reward_config_ids: Optional[Iterable[int]] = None,
                gacha_config: Optional[dict] = None,
                secret_jewel_config: Optional[dict] = None,
                spirit_draw_free: bool = True,
                mining_config: Optional[dict] = None,
                sea_config: Optional[dict] = None,
               arena_config: Optional[dict] = None,
               escort_config: Optional[dict] = None,
               hellgate_config: Optional[dict] = None,
                cloud_ladder_enabled: bool = False,
                ladder_reward_enabled: bool = False,
               seven_login_enabled: bool = False,
                main_chapter_kills_config: Optional[dict] = None,
                dragon_realm_enabled: bool = True,
                xwar_idle_enabled: bool = False,
                statue_amount: int = 7000,
                progress=None,
                should_abort: Optional[Callable[[], bool]] = None,
                skip_tasks: Optional[Iterable[str]] = None,
                only_tasks: Optional[Iterable[str]] = None) -> RunReport:
    """Run every ws_token daily task for ``device`` over one logged-in client.

    Builds a single WSGameClient (with a TaskCollector mounted as push_handler
    so the login-time main-task frames are captured), connects once, runs each
    task with its own error boundary, then closes the client. Returns a
    :class:`RunReport` summarising per-task results and errors. ``spend=False``
    (default) sends no cost action.

    ``sweep_list`` is the 副本管家 chapter list used on every wake
    ``[(id, level, times[, use_ad]), ...]``. With none configured, the steward
    reads the in-game sweep switches and available dungeon levels, then derives
    a conservative one-entry-per-enabled-chapter list.

    ``open_lamp`` (default False) is an independent opt-in for 開神燈. When True
    the runner opens one bounded batch of 神燈 boxes (REAL, not dry_run) and
    auto-equips/sells the drops — it consumes 神燈 items, so it is gated behind
    its own flag rather than ``spend`` and is OFF by default (legacy behaviour is
    unchanged: only the free redpack step is added for non-lamp devices).
    ``lamp_percent`` (>0 = 依當前神燈總數百分比決定本輪目標) / ``lamp_min_keep``
    (>0 = 剩餘神燈硬地板) 設定百分比/最低保留；兩者皆 0（預設）= 開到沒燈。本輪
    開燈進度 (opened/target) 透過 ``progress(\"lamp\", \"progress\", ...)`` 回報。

    The other new daily tasks split by safety:
      - idle_reward (掛機/離線獎勵) + turntable (轉盤免費次數) + farm harvest run
        unconditionally — they only ever take free / already-earned rewards.
      - ``farm_config`` ``{seed_id?, team_cfg_id?}`` enables farm planting / 打工
        (live-confirm config values; empty = 用免費種子, no seed purchase).
      - ``dungeon_sweeps`` ``[(type, dungeon_id, num), ...]`` enables 掃蕩 only —
        battle is never auto-run (anti-cheat). Skipped with none configured.
      - ``carpark_target`` (cross lot master_id) enables 跨界停車 (只停不收);
        skipped when unset (cross-parking is event-gated and the id is per-event).
      - spirit (守護靈免費召喚) always runs (free draws only); ``workshop_rotate``
        (default True) enables the 加工坊 12h recipe rotation; ``couple_gifts``
        (default True) sends 奶茶+玫瑰 to the partner and ``forge_ring`` (default
        False) opts in to 戒指錘鍊 (consumes all 真愛之石).
      - ``mining_config`` ``{enabled, allow_bomb, allow_drill, max_steps}``
        enables pure-WS mining. It consumes mining tools and therefore stays
        OFF by default. The pickaxe count comes from the 0x0402 consume push
        (no reliable login snapshot), so mining seeds + adopts the real count.
      - ``mail_claim`` (default False) enables 每日自動領取郵件附件 (一鍵領取,
        once-daily gated). Free (only takes earned attachments); skipped without
        the flag. ``mail_gem_threshold`` / ``mail_skill_threshold`` (optional)
        add a best-effort 神器附魔寶石 / 武魂 "full" advisory log before claiming —
        they NEVER block the claim (client has no hard cap; server is authoritative).
      - ``relic_upgrade`` (default False) enables 遺物 平均強化 — it SPENDS 遺物碎片
        (server-authoritative deduct), so it is OFF by default and hard-capped by
        ``relic_max_steps`` (default 10). ``relic_fragment_floor`` (default 0) stops
        upgrading once the live 遺物碎片 count drops below it. Always upgrades the
        lowest-level equipped relic; stops on 0x0201 / floor / cap.
      - ``relic_sprint_enabled`` (default False) enables 遺物碎片衝刺 (衝刺榜) — it
        SPENDS 遺物碎片 up to ``relic_sprint_target`` (default 900000) then claims
        every CanGet round, so it is OFF by default and shares the SAME inventory
        tracker as 遺物 平均強化. A closed / wrong-rotation activity is a safe no-op
        ({"skipped": "no active sprint"}).
      - ``tycoon`` (default False) enables 傳奇大亨 (大富翁) auto-dice. The dice are
        FREE (regen from a timer) so this is pure gain, but stays opt-in; bounded
        by ``tycoon_max_rolls`` (default 50). A closed activity makes the first
        roll return 0x0201 and auto_play stops (safe no-op).
      - ``ad_reward_config_ids`` (default None) enables 看廣告獎勵自動領取 (鑽石/種子)
        purely over WS (is_free=1; the account bought 免廣告). None / empty = the
        task self-skips. Reads today's per-config counts once and claims only up to
        each config's daily cap, skipping during cooldown (no packet sent) — the
        same read-then-deficit discipline as the farm shop.

    ``progress`` (optional) is a ``(task_name, status, detail)`` callback fired
    per task: ``("xxx", "start", "")`` before, ``("xxx", "ok", "")`` /
    ``("xxx", "error", "<err>")`` after — callers wire it to the dashboard
    state and the per-device log. A raising callback is swallowed; it can
    never abort the run.

    ``should_abort`` (optional) is polled at every task boundary (and threaded
    into the 開神燈/挖礦 loops); once it returns True the run stops ASAP, the
    in-progress + remaining tasks are left pending (absent from both ``tasks``
    and ``errors``) and the report carries ``aborted=True``. Used by the WS
    phase to yield to a pending「開啟瀏覽器」request. ``skip_tasks`` (optional)
    is a set of task names already done in a prior interrupted run that are
    bypassed this pass (resume). Both default None -> behaviour unchanged.
    """
    tasks: dict[str, Any] = {}
    errors: dict[str, str] = {}
    skip_set = set(skip_tasks or ())   # 續做：本輪要跳過（先前已完成）的任務
    only_set = set(only_tasks) if only_tasks else None  # 白名單：只跑這些任務
    aborted = False                    # 被 should_abort 中斷 → 剩餘任務留 pending
    sweep: tuple[Sequence[int], ...] = tuple(sweep_list or ())
    dsweeps: tuple[Sequence[int], ...] = tuple(dungeon_sweeps or ())
    role_id_hint = 0

    creds = load_creds(device)
    # The collector must be mounted BEFORE connect so the login-time task PUSH
    # frames (task_all / daily_point / weekly_box) land in it. The OFFLINE idle
    # reward is ALSO a login-time PUSH (reward_info_s2c{type:2}), so the same
    # handler tees those bodies into ``idle_offline`` for _run_idle_reward.
    collector = main_tasks.TaskCollector()
    inventory_tracker = mining.InventoryTracker()
    idle_offline: list[bytes] = []

    # 開神燈百分比/最低保留需要「登入快照的神燈現量」。只在 open_lamp 時 lazy import
    # lamp（非開燈裝置不該載入開燈相依），並在 _push 內把 0x0402 帶 1001 的 frame
    # tee 進 holder。lamp 是最後一個任務，open_lamp 會在開第一批前接管 push_handler，
    # 所以登入時的 0x0402 快照（若有）必在 handler 被換掉之前先被這裡攔到。
    lamp = _load_lamp() if open_lamp else None
    lamp_count_holder: dict[str, Optional[int]] = {"count": None}

    def _push(cmd: int, body: bytes) -> None:
        collector(cmd, body)
        try:
            inventory_tracker.on_push(cmd, body)
        except Exception:  # noqa: BLE001 — 庫存 push 壞掉不該中斷登入
            logger.debug("ws_token runner: %s inventory push parse failed",
                         device, exc_info=True)
        if cmd == idle_reward.CMD_REWARD_INFO:
            try:
                info = idle_reward.parse_reward_info(body)
                if info.type == idle_reward.TYPE_OFFLINE:
                    idle_offline.append(bytes(body))
            except Exception:  # noqa: BLE001 — a malformed push must not break login
                logger.debug("ws_token runner: %s idle offline push parse failed",
                             device, exc_info=True)
        if lamp is not None and cmd == 0x0402:
            try:
                c = lamp.extract_lamp_count(body)
                if c is not None:
                    lamp_count_holder["count"] = c
            except Exception:  # noqa: BLE001 — 壞掉的神燈快照不該中斷登入
                logger.debug("ws_token runner: %s lamp count push parse failed",
                             device, exc_info=True)

    client = _make_client(creds, push_handler=_push)

    try:
        login = client.connect()
    except (WSLoginError, WSError, OSError) as exc:
        logger.error("ws_token runner: %s login failed: %s", device, exc)
        try:
            kicked = _client_is_kicked(client)
        except Exception:  # noqa: BLE001 — diagnostic metadata never masks login error
            kicked = False
        try:
            kick_reason = _client_kick_reason(client)
        except Exception:  # noqa: BLE001 — diagnostic metadata never masks login error
            kick_reason = None
        try:
            client.close()
        except Exception:  # noqa: BLE001 — close must never mask the login error
            logger.debug("ws_token runner: %s close after login failure raised", device,
                         exc_info=True)
        close_reason, close_detail = _client_close_metadata(client)
        return RunReport(device=device, login_ok=False, spend=spend,
                         tasks=tasks, errors={LOGIN_TASK: str(exc)},
                         kicked=kicked, kick_reason=kick_reason,
                         close_reason=close_reason, close_detail=close_detail)

    serv_time = int(login.get("serv_time") or creds.login_time or 0)
    role_id_hint = int(login.get("role_id") or creds.role_id or 0)
    logger.info("ws_token runner: %s login ok role_id=%s spend=%s",
                device, role_id_hint, spend)

    # 登入後補完整庫存快照：0x0402 只推「變動」，未變動的素材/閒置鎬子不會出現，
    # 害 workshop 看不到原料而空轉、mining 得猜鎬子數。0x0401 (req/resp，空 body)
    # 回完整庫存，seed 一次即可（之後 0x0402 增量照常更新）。best-effort：撈不到
    # 不中斷後續任務（workshop 退回防呆 idle、mining 退回猜 seed）。
    try:
        _seeded = inventory_tracker.seed_from_query(
            client, timeout=_FARM_HOME_TIMEOUT_S)
        logger.info("ws_token runner: %s 庫存快照 seed %d items (pickaxe=%s)",
                    device, _seeded,
                    inventory_tracker.counts.get(mining.GOODS_PICKAXE))
    except Exception:  # noqa: BLE001 — 庫存快照失敗不可中斷登入後任務
        logger.debug("ws_token runner: %s 庫存快照 seed 失敗", device, exc_info=True)

    def _notify(name: str, status: str, detail: str = "") -> None:
        if progress is None:
            return
        try:
            progress(name, status, detail)
        except Exception:  # noqa: BLE001 — 回報壞掉不可中斷任務
            logger.debug("ws_token runner: %s progress callback raised", device,
                         exc_info=True)

    def _step(name: str, fn) -> None:
        nonlocal aborted
        if aborted:
            return                       # 已中斷 → 後續任務全部不跑（留 pending）
        if _client_is_kicked(client):
            # 明確踢人與傳輸斷線都停止本輪 WS 任務；上層再依 reason 分流。
            return
        if should_abort is not None and should_abort():
            aborted = True
            _notify(name, "aborted", "pending web launch")
            return                       # 任務邊界中斷：本任務未跑 → pending
        if only_set is not None and name not in only_set:
            return                       # 白名單模式：不在清單內 → 跳過
        if name in skip_set:
            _notify(name, "skip", "resume: already done")
            return                       # 續做：已完成 → 不重跑、不記錄
        try:
            _safe(tasks, errors, name, fn, notify=_notify)
        except WSRunAborted:
            # 長任務（開神燈/挖礦）迴圈內中斷：不記為完成/錯誤 → 留 pending。
            aborted = True
            _notify(name, "aborted", "in-task")

    def _lamp_progress(opened: int, target: int) -> None:
        """逐批開燈進度 → 走既有 _notify（runner 不直接依賴 bot_state）。"""
        _notify("lamp", "progress", f"{opened}/{target}")

    try:
        # 跨界車位要搶 — 登入後最先跑（plan 未啟用時內部立即 skip）。
        _step("carpark",
              lambda: _run_carpark(client, target=carpark_target,
                                   auto=carpark_auto, plan_cfg=carpark_plan,
                                   device=device, state_dir=carpark_state_dir,
                                   now=carpark_now,
                                   cluster_server_id=int(
                                       login.get("server_id") or 0) or None,
                                   decision_log=lambda detail: _notify(
                                       "carpark", "progress", detail)))
        if mount_sprint_enabled:
            _step("mount_sprint",
                  lambda: _run_mount_sprint(
                      client, device=device, enabled=True,
                      quantity=mount_sprint_quantity))
        _step("main_tasks",
              lambda: _run_main_tasks(client, collector))
        _step("league_solo", lambda: _run_league_solo(client))
        _step("redpack", lambda: _run_redpack(client))
        if mail_claim:
            _step("mail",
                  lambda: _run_mail(client, device=device,
                                    gem_threshold=mail_gem_threshold,
                                    skill_threshold=mail_skill_threshold))
        _step("idle_reward", lambda: _run_idle_reward(client, idle_offline))
        _ad_ids = tuple(ad_reward_config_ids or ())
        if _ad_ids:
            _step("ad_rewards",
                  lambda: _run_ad_rewards(client, config_ids=_ad_ids,
                                          enabled=True, device=device))
        _step("turntable", lambda: _run_turntable(client))
        _step("tycoon",
              lambda: _run_tycoon(client, enabled=tycoon,
                                  max_rolls=tycoon_max_rolls))
        _step("farm",
              lambda: _run_farm(client, role_id=role_id_hint, farm_config=farm_config,
                                inventory_tracker=inventory_tracker, device=device))
        _step("harvest_card",
              lambda: _run_harvest_card(
                  client, role_id=role_id_hint, farm_config=farm_config,
                  inventory_tracker=inventory_tracker, device=device))
        _step("dungeon", lambda: _run_dungeon(client, sweeps=dsweeps))
        if hellgate_config and hellgate_config.get("enabled"):
            _step("hellgate", lambda: _run_hellgate(
                client,
                hellgate_config=hellgate_config,
                should_abort=should_abort,
            ))
        _step("rogue", lambda: _run_rogue(client, device=device))
        # 5558 保留 H5 挑戰/助戰流程；其他裝置每週挑戰與獎勵皆走同一 WS。
        if device != cloud_ladder.EXCLUDED_DEVICE:
            if ladder_reward_enabled:
                _step(
                    "ladder_reward",
                    lambda: _run_ladder_reward(client, device=device),
                )
            if seven_login_enabled:
                _step(
                    "seven_login",
                    lambda: _run_seven_login(client, device=device),
                )
            if cloud_ladder_enabled:
                def _cloud_progress(fights: int, level: int, max_level: int) -> None:
                    _notify(
                        "cloud_ladder",
                        "progress",
                        f"{fights} 場，關卡 {level}/{max_level}",
                    )

                _step(
                    "cloud_ladder",
                    lambda: _run_cloud_ladder(
                        client,
                        device=device,
                        should_abort=should_abort,
                        on_progress=_cloud_progress,
                    ),
                )
        if arena_config and arena_config.get("enabled"):
            _step("arena",
                  lambda: _run_arena(client, arena_config=arena_config,
                                     should_abort=should_abort, device=device))
        if escort_config and escort_config.get("enabled"):
            _step("escort",
                  lambda: _run_escort(client, device=device,
                                      escort_config=escort_config,
                                      should_abort=should_abort))
        _step("statue", lambda: _run_statue(client, device=device, amount=statue_amount))
        _step("guild", lambda: _run_guild(client, spend=spend))
        _step("steward",
              lambda: _run_steward(client, spend=spend, serv_time=serv_time,
                                   sweep_list=sweep, device=device,
                                   inventory_counts=inventory_tracker.counts))
        _step("relic",
              lambda: _run_relic(client, inventory_tracker,
                                 enabled=relic_upgrade,
                                 max_steps=relic_max_steps,
                                 fragment_floor=relic_fragment_floor))
        _step("relic_sprint",
              lambda: _run_relic_sprint(client, inventory_tracker,
                                        enabled=relic_sprint_enabled,
                                        target_spend=relic_sprint_target))
        if gacha_config and gacha_config.get("enabled"):
            _step("gacha",
                  lambda: _run_gacha(client, inventory_tracker,
                                     gacha_config=gacha_config,
                                     device=device))
        if gacha_config and gacha_config.get("free_daily"):
            _step("gacha_free",
                  lambda: _run_gacha_free(client, device=device))
        if kungfu_guess:
            _step("kungfu_store", lambda: _run_kungfu_store(client))
        if kungfu_worship:
            _step("kungfu_worship", lambda: _run_kungfu_worship(client))
        _step("pay_mall", lambda: _run_pay_mall(client))
        if spirit_draw_free:
            _step("spirit", lambda: _run_spirit(client))
        _sj_cfg = secret_jewel_config or {}
        if _sj_cfg.get("draw_free") or _sj_cfg.get("buy_daily"):
            _step("secret_jewel",
                  lambda: _run_secret_jewel(
                      client,
                      draw_free=bool(_sj_cfg.get("draw_free")),
                      buy_daily=bool(_sj_cfg.get("buy_daily"))))
        if workshop_rotate:
            _step("workshop",
                  lambda: _run_workshop(client, inventory_tracker, device=device))
        _step("couple",
              lambda: _run_couple(client, gifts=couple_gifts,
                                  forge_ring=forge_ring, device=device))
        if dragon_realm_enabled:
            _step("dragon_realm",
                  lambda: _run_dragon_realm(client, inventory_tracker,
                                            device=device))
        if xwar_idle_enabled:
            _step("xwar_idle",
                  lambda: _run_xwar_idle(client, device=device))
        if sea_config:
            _step("sea_season",
                  lambda: _run_sea_season(client, device=device,
                                          sea_config=sea_config,
                                          inventory_tracker=inventory_tracker))
        if mining_config and mining_config.get("enabled"):
            _step("mining",
                  lambda: _run_mining(client, inventory_tracker,
                                      mining_config=mining_config,
                                      device=device,
                                      should_abort=should_abort))
        if open_lamp:
            _step("lamp",
                  lambda: _run_lamp(client, ip=device,
                                    lamp_percent=lamp_percent,
                                    lamp_min_keep=lamp_min_keep,
                                    lamp_daily_min=lamp_daily_min,
                                    initial_count=lamp_count_holder["count"],
                                    on_progress=_lamp_progress,
                                    should_abort=should_abort))
        # 尾端二次領取：本輪 mining/lamp/arena 等完成後才變可領的每日任務與
        # 活躍度寶箱，在此補領一次，避免當天最後一輪產生的可領項被午夜重置吃掉。
        _step("main_tasks_late", lambda: _run_main_tasks(client, collector))
    finally:
        # Read the kick flag BEFORE close() — a deliberate close never sets it,
        # so this captures only a real 異地登入 / server-drop during the run.
        try:
            kicked = bool(client.is_kicked())
        except Exception:  # noqa: BLE001 — defensive: a fake/odd client w/o the method
            kicked = False
        try:
            kick_reason = _client_kick_reason(client)
        except Exception:  # noqa: BLE001 — reason is diagnostic, never mask cleanup
            kick_reason = None
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("ws_token runner: %s close raised", device, exc_info=True)
        close_reason, close_detail = _client_close_metadata(client)

    # 主線擊殺需要自己的 A 端 WS，且 B 端會啟動免登入 H5 runtime；務必等既有
    # 每日任務主連線關閉後才跑，避免同帳號兩條 WS 互踢。排在最後也避免星期五
    # 3000 隻長任務阻塞停車、領獎等高優先工作。
    kill_cfg = (
        main_chapter_kills_config
        if isinstance(main_chapter_kills_config, dict)
        else {}
    )
    if bool(kill_cfg.get("enabled")) and not kicked and not aborted:
        name = "main_chapter_kills"
        if only_set is None or name in only_set:
            if name in skip_set:
                _notify(name, "skip", "resume: already done")
            elif should_abort is not None and should_abort():
                aborted = True
                _notify(name, "aborted", "pending web launch")
            else:
                def _kill_progress(sent: int, target: int) -> None:
                    _notify(name, "progress", f"{sent}/{target}")

                try:
                    from ws_token import main_chapter_kills

                    _safe(
                        tasks,
                        errors,
                        name,
                        lambda: main_chapter_kills.run_daily(
                            device,
                            interval_sec=kill_cfg.get("interval_sec", 3.0),
                            persist_every=kill_cfg.get("persist_every", 10),
                            should_abort=should_abort,
                            progress=_kill_progress,
                        ),
                        notify=_notify,
                    )
                except WSRunAborted:
                    aborted = True
                    _notify(name, "aborted", "interrupted")

    if kicked:
        logger.warning(
            "ws_token runner: %s 連線中斷 reason=%s kick_reason=%s detail=%s",
            device, close_reason, kick_reason, close_detail or "",
        )
    logger.info(
        "ws_token runner: %s done — %d task(s) ok, %d error(s), kicked=%s "
        "kick_reason=%s close_reason=%s aborted=%s",
        device, len(tasks), len(errors), kicked, kick_reason, close_reason,
        aborted)
    return RunReport(device=device, login_ok=True, spend=spend,
                     tasks=tasks, errors=errors, kicked=kicked,
                     kick_reason=kick_reason, aborted=aborted,
                     close_reason=close_reason, close_detail=close_detail)


def _ok_summary(result) -> str:
    """Compact one-liner for the 'ok' log so a task's outcome reaches main.log.

    Only surfaces the few self-describing keys WS tasks return (stop_reason /
    skipped / keys); other result shapes log nothing (empty string).
    """
    if not isinstance(result, dict):
        return ""
    parts = [f"{k}={result[k]}" for k in ("stop_reason", "skipped")
             if result.get(k)]
    if "keys" in result:
        parts.append(f"keys={result['keys']}")
    daily_progress = result.get("daily_progress")
    if isinstance(daily_progress, dict) and daily_progress.get("detail"):
        parts.append(str(daily_progress["detail"]))
    return ", ".join(parts)


def _safe(tasks: dict, errors: dict, name: str, fn, notify=None) -> None:
    """Run one task with its own error boundary; record result OR error.

    Any exception (WSTimeoutError, parse errors, etc.) is caught so the next
    task still runs. The error is summarised onto ``errors[name]``. ``notify``
    (already exception-safe at the caller) reports start / ok / error.
    """
    if notify:
        notify(name, "start", "")
    try:
        result = fn()
        tasks[name] = result
        if notify:
            notify(name, "ok", _ok_summary(result))
    except WSRunAborted:
        # 中斷不是任務錯誤：往上拋給 _step，讓該任務維持 pending（不記 errors）。
        raise
    except Exception as exc:  # noqa: BLE001 — per-task isolation is the whole point
        errors[name] = f"{type(exc).__name__}: {exc}"
        logger.warning("ws_token runner: task %s failed: %s", name, exc, exc_info=True)
        if notify:
            notify(name, "error", f"{type(exc).__name__}: {exc}")


# --- CLI --------------------------------------------------------------------

def _format_report(rep: RunReport) -> str:
    lines = [
        f"[runner] device={rep.device} login_ok={rep.login_ok} spend={rep.spend}",
        f"[runner] tasks_ok={list(rep.tasks)} errors={list(rep.errors)}",
    ]
    for name, summary in rep.tasks.items():
        lines.append(f"  {name}: {summary}")
    for name, err in rep.errors.items():
        lines.append(f"  ERROR {name}: {err}")
    return "\n".join(lines)


def _parse_sweep_arg(items: list[str]) -> list[tuple[int, ...]]:
    """Parse --sweep id:level:times[:use_ad] tokens (same as steward_smoke)."""
    out: list[tuple[int, ...]] = []
    for tok in items:
        parts = [int(p) for p in tok.split(":")]
        if len(parts) < 3:
            raise SystemExit(f"--sweep entry {tok!r} needs id:level:times[:use_ad]")
        out.append(tuple(parts[:4]))
    return out


def _parse_dungeon_sweep_arg(items: list[str]) -> list[tuple[int, int, int]]:
    """Parse --dungeon-sweep type:dungeon_id:num tokens (same as dungeon_smoke)."""
    out: list[tuple[int, int, int]] = []
    for tok in items:
        parts = [int(p) for p in tok.split(":")]
        if len(parts) != 3:
            raise SystemExit(f"--dungeon-sweep entry {tok!r} needs type:dungeon_id:num")
        out.append((parts[0], parts[1], parts[2]))
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", required=True)
    ap.add_argument("--spend", action="store_true",
                    help="also send cost actions (guild donate / steward shopping "
                         "+ sweep / service renew). Default: free reads/claims only.")
    ap.add_argument("--sweep", action="append", default=[],
                    metavar="id:level:times[:use_ad]",
                    help="副本管家 sweep chapter (repeatable; runs every wake)")
    ap.add_argument("--open-lamp", dest="open_lamp", action="store_true",
                    help="also 開神燈 (REAL): opens one bounded batch and "
                         "auto-equips/sells drops. Consumes 神燈 items. Default: off.")
    ap.add_argument("--lamp-percent", type=float, default=0.0, metavar="PCT",
                    help="開神燈: 依當前神燈總數的百分比決定本輪目標 (0=不依百分比，開到沒燈)")
    ap.add_argument("--lamp-min-keep", type=int, default=0, metavar="N",
                    help="開神燈: 剩餘神燈硬地板 (0=無下限)")
    ap.add_argument("--lamp-daily-min", type=int, default=0, metavar="N",
                    help="開神燈: 每日最少開啟數量 (0=不限制; 不受百分比約束)")
    ap.add_argument("--farm-seed", type=int, default=None, metavar="SEED_ID",
                    help="農場: plant this seed_id on empty lands (live-confirm value)")
    ap.add_argument("--farm-team", type=int, default=None, metavar="TEAM_CFG_ID",
                    help="農場: start 打工 with this team_cfg_id (live-confirm value)")
    ap.add_argument("--dungeon-sweep", action="append", default=[],
                    metavar="type:dungeon_id:num",
                    help="深淵/萬神 掃蕩 (repeatable; 掃蕩 only, never battle)")
    ap.add_argument("--carpark-target", type=int, default=None, metavar="MASTER_ID",
                    help="跨界停車: cross lot master_id to park into (只停不收)")
    ap.add_argument("--carpark-auto", action="store_true",
                    help="跨界停車: auto-pick a parkable cross lot via search "
                         "(只停不收; ignored when --carpark-target is set)")
    ap.add_argument("--no-couple-gifts", dest="couple_gifts", action="store_false",
                    help="伴侶送禮 (奶茶+玫瑰送光) 預設開; 此旗標關閉")
    ap.add_argument("--forge-ring", action="store_true",
                    help="戒指錘鍊: 消耗全部真愛之石 (預設關)")
    ap.add_argument("--no-workshop", dest="workshop_rotate", action="store_false",
                    help="加工坊 12h 配方輪換預設開; 此旗標關閉")
    ap.add_argument("--kungfu-guess", dest="kungfu_guess", action="store_true",
                    help="菇菇武道會 競猜商店: 用粉鑽把競猜幣 4 檔位買到上限 "
                         "(活動沒開時伺服器擋下，安全 no-op; 預設關)")
    ap.add_argument("--mail", dest="mail_claim", action="store_true",
                    help="每日自動領取全部郵件附件 (一鍵領取, 每日一次; 預設關)")
    ap.add_argument("--mail-gem-threshold", type=int, default=None, metavar="N",
                    help="郵件: 神器附魔寶石 best-effort 滿門檻 (僅 log 警告, 不擋領取)")
    ap.add_argument("--mail-skill-threshold", type=int, default=None, metavar="N",
                    help="郵件: 武魂 best-effort 滿門檻 (僅 log 警告, 不擋領取)")
    ap.add_argument("--relic-upgrade", dest="relic_upgrade", action="store_true",
                    help="遺物 平均強化: 消耗遺物碎片強化最低等級的已裝備遺物 (預設關, "
                         "max-steps 上限)")
    ap.add_argument("--relic-max-steps", type=int, default=10, metavar="N",
                    help="遺物強化最多送出的步數上限 (預設 10)")
    ap.add_argument("--relic-fragment-floor", type=int, default=0, metavar="N",
                    help="遺物強化: 剩餘遺物碎片低於此值即停 (0=無下限)")
    ap.add_argument("--tycoon", dest="tycoon", action="store_true",
                    help="傳奇大亨 (大富翁) 自動擲骰: 免費骰子純收益 (活動沒開時 "
                         "首擲被擋=no-op; 預設關)")
    ap.add_argument("--tycoon-max-rolls", type=int, default=50, metavar="N",
                    help="傳奇大亨最多擲骰次數上限 (預設 50)")
    ap.add_argument("--mine", action="store_true",
                    help="挖礦 opt-in: 依 0x0402 庫存現量挖到鎬子用完")
    ap.add_argument("--mine-allow-bomb", action="store_true",
                    help="挖礦允許使用炸彈 4003 (預設關)")
    ap.add_argument("--mine-allow-drill", action="store_true",
                    help="挖礦允許使用鑽頭 4002 (預設關)")
    ap.add_argument("--mine-max-steps", type=int, default=200, metavar="N",
                    help="挖礦最多送出的步數上限 (預設 200)")
    ap.add_argument("--mine-max-depth", type=int, default=None, metavar="DEPTH",
                    help="挖礦 planner max_depth；未設則使用 adapter 預設")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    sweep_list = _parse_sweep_arg(args.sweep) or None
    dungeon_sweeps = _parse_dungeon_sweep_arg(args.dungeon_sweep) or None
    farm_config = None
    if args.farm_seed is not None or args.farm_team is not None:
        farm_config = {"seed_id": args.farm_seed, "team_cfg_id": args.farm_team}
    mining_config = None
    if args.mine:
        mining_config = {
            "enabled": True,
            "allow_bomb": args.mine_allow_bomb,
            "allow_drill": args.mine_allow_drill,
            "max_steps": args.mine_max_steps,
            "max_depth": args.mine_max_depth,
        }
    print(f"[runner] starting device={args.device} spend={args.spend} "
          f"open_lamp={args.open_lamp} farm_config={farm_config} "
          f"dungeon_sweeps={dungeon_sweeps} carpark_target={args.carpark_target} "
          f"mining_config={mining_config}",
          flush=True)
    rep = run_device(args.device, spend=args.spend, sweep_list=sweep_list,
                     open_lamp=args.open_lamp, lamp_percent=args.lamp_percent,
                     lamp_min_keep=args.lamp_min_keep,
                     lamp_daily_min=args.lamp_daily_min, farm_config=farm_config,
                     dungeon_sweeps=dungeon_sweeps,
                     carpark_target=args.carpark_target,
                     carpark_auto=args.carpark_auto,
                     couple_gifts=args.couple_gifts, forge_ring=args.forge_ring,
                     workshop_rotate=args.workshop_rotate,
                     kungfu_guess=args.kungfu_guess,
                     mail_claim=args.mail_claim,
                     mail_gem_threshold=args.mail_gem_threshold,
                     mail_skill_threshold=args.mail_skill_threshold,
                     relic_upgrade=args.relic_upgrade,
                     relic_max_steps=args.relic_max_steps,
                     relic_fragment_floor=args.relic_fragment_floor,
                     tycoon=args.tycoon,
                     tycoon_max_rolls=args.tycoon_max_rolls,
                     mining_config=mining_config)
    print(_format_report(rep), flush=True)
    return 0 if rep.login_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
