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
  5. steward     — read_info (free); shopping + dungeon sweep (spend); service
                   renewal only when spend AND the service is expired.
  6. spirit      — free: 守護靈免費召喚 (draw_all_free; only free_times,
                   never buys 招喚貨幣 — item 800003 does not exist).
  7. workshop    — 加工坊 12h 配方輪換 (rotate_team_recipes, parity alternates;
                   cadence persisted in ws_state/<device>.json; gated by
                   ``workshop_rotate``, default on).
  8. couple      — 伴侶: 奶茶+玫瑰送光 (give_all_in_hand, batches of 20;
                   ``couple_gifts`` default on) + 戒指錘鍊 (``forge_ring``
                   opt-in, default off). Skipped without a partner.
  9. mining      — 挖礦: opt-in (``mining_config.enabled=True``) only. Uses the
                   login-time 0x0402 inventory snapshot; skips instead of
                   guessing when the pickaxe count is unknown. Re-plans after
                   each confirmed step and can consume pickaxe/bomb/drill only
                   through explicit config flags.
 10. lamp        — 開神燈: opt-in (``open_lamp=True``) only. Spends 神燈 items and
                   auto-equips/sells drops, so it is gated behind its own flag
                   (NOT ``spend``) and OFF by default. Runs one batch of up to
                   ``batch_num`` boxes (lamp.open_lamp's own cap), never unbounded.

Default ``spend=False`` runs only the free reads + claims (incl. redpack) and
sends NO cost action. ``spend=True`` additionally donates, shops, sweeps, and (if
expired) renews — see the per-task spend gates below. ``open_lamp`` is an
independent opt-in: it is OFF by default and consumes 神燈 items only when True.

CLI:  python -m ws_token.runner --device <dev> [--spend] [--open-lamp] [--mine]
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from ws_token import (
    carpark, couple, dungeon, farm, guild, idle_reward, league_solo, main_tasks,
    mining, mining_supervised, redpack, spirit, steward, turntable, workshop,
)
from ws_token import state as ws_state
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

LOGIN_TASK = "login"
TASK_ORDER: tuple[str, ...] = (
    "main_tasks", "league_solo", "redpack", "idle_reward", "turntable", "farm",
    "dungeon", "guild", "steward", "carpark", "spirit", "workshop", "couple",
    "mining", "lamp")

# 開神燈 API 單次上限是 20；總量靠單線程連續批次累積。
_LAMP_BATCH_NUM: int = 20
_LAMP_MAX_BATCHES: int = 500  # 20 * 500 = 10000
_LAMP_BATCH_DELAY_SEC: float = 0.2

# workshop 12h 配方輪換間隔（使用者 2026-06-10 指定：兩類別 12hr 切一次）
_WORKSHOP_ROTATE_S: float = 12 * 3600.0


@dataclass(frozen=True)
class RunReport:
    """Outcome of one ``run_device`` pass.

    ``tasks`` maps each run task name to its result summary (whatever the task's
    orchestrator returned, or a small dict assembled here). ``errors`` maps a
    task name (or ``"login"``) to a short error string for whatever failed; a
    successful run has an empty ``errors``.

    ``kicked`` is True iff the connection was kicked mid-run — the account was
    logged in elsewhere (異地登入, cmd 259) or the server dropped the socket. The
    in-flight tasks usually fail (connection gone) and land in ``errors``; this
    flag lets the loop tell "kicked" apart from an ordinary task failure so it
    can back off (30-min cooldown) instead of hammering.
    """

    device: str
    login_ok: bool
    spend: bool
    tasks: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    kicked: bool = False


def _make_client(creds, **kwargs) -> WSGameClient:
    """Construct the WSGameClient. Indirected so tests can inject a fake."""
    return WSGameClient(creds, **kwargs)


def _load_lamp():
    """Lazy import 開神燈 deps; runner import should stay pure-WS/light."""
    from ws_token import lamp
    return lamp


# --- per-task runners (each returns a summary; raising is caught by run_device)

def _run_main_tasks(client, collector: main_tasks.TaskCollector) -> dict:
    """Free: snapshot login-push state, then claim every free reward."""
    state = main_tasks.collect_state(client, collector, settle=_PUSH_SETTLE_S)
    daily = main_tasks.claim_daily_tasks(client, state)
    marry = main_tasks.claim_marry_tasks(client, state)  # 默契考驗 好感週任務 (type 6)
    daily_box = main_tasks.claim_daily_box(client, state)
    weekly_box = main_tasks.claim_weekly_box(client, state)
    achievement = main_tasks.claim_achievement(client)
    return {
        "daily_tasks": daily,
        "marry_tasks": marry,
        "daily_box": daily_box,
        "weekly_box": weekly_box,
        "achievement": achievement,
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


def _run_idle_reward(client, offline_pushes: list) -> dict:
    """Free: claim ONLINE accrual (claim{1}) + the OFFLINE reward pushed at login.

    The OFFLINE reward arrives as a reward_info_s2c{type:2} PUSH right after login;
    ``offline_pushes`` collects those bodies (see the composite push handler in
    run_device). claim_online / claim_offline_from_push are no-ops when nothing is
    claimable (returns None), so this is safe to run unconditionally. There is no
    "看廣告加倍" over WS — only the base amount. Returns
    ``{online, offline}`` booleans (True = claimed).
    """
    online = idle_reward.claim_online(client)
    summary: dict = {"online": bool(online and online.success), "offline": None}
    if offline_pushes:
        off = idle_reward.claim_offline_from_push(client, offline_pushes[0])
        summary["offline"] = bool(off and off.success)
    return summary


def _run_turntable(client) -> dict:
    """Free: spin every free / accumulated 轉盤 turn (no ad top-ups over WS).

    spin_all_free reads ``num`` once and spins that many (capped); it only ever
    consumes turns the account already has. Returns ``{spun, results}``.
    """
    return turntable.spin_all_free(client)


def _run_farm(client, *, role_id: int, farm_config: Optional[dict]) -> dict:
    """農場/打工: harvest ready crops (free); plant / 打工 only when configured.

    harvest_ready claims every MATURE land (free reward) and always runs. Planting
    needs a ``seed_id`` and starting 打工 needs a ``team_cfg_id`` — both are
    live-confirm config values, so they run ONLY when ``farm_config`` supplies them
    (empty seed_used_seq = 用免費種子 = 不買種). Returns ``{harvest, plant, work}``.
    """
    summary: dict = {"harvest": None, "plant": None, "work": None, "buy": None}
    # The live server answers home_farm_info only ONCE per session, so read the
    # farm a single time and reuse the snapshot for both harvest and plant.
    info = farm.read_farm(client, role_id)
    summary["harvest"] = farm.harvest_ready(client, role_id, info=info)
    if farm_config:
        seed_id = farm_config.get("seed_id")
        team_cfg_id = farm_config.get("team_cfg_id")
        buy_list = farm_config.get("buy")
        if seed_id:
            summary["plant"] = farm.plant_empty(client, role_id, int(seed_id), info=info)
        if team_cfg_id:
            summary["work"] = farm.start_work(client, int(team_cfg_id))
        # 莊園購買: buy each configured farm-shop item UP TO its daily target.
        # Reads today's count first, so an item already bought in the GUI is
        # respected (buys only the remainder; nothing if already at target).
        if buy_list:
            summary["buy"] = farm.buy_farm_shop(client, buy_list)
    return summary


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


def _run_carpark(client, *, target: Optional[int]) -> dict:
    """跨界停車 (只停不收): auto-park one free CROSS slot when a target is set.

    Cross-parking is event-gated — when no cross event is live the lot read times
    out — and the target lot id is a live-confirm value, so this runs ONLY when
    ``carpark_target`` is configured. Returns auto_park_cross's summary
    (``{parked, reason, pos, mount_id, ...}``) or a skip note.
    """
    if not target:
        return {"skipped": "no carpark_target configured (event-gated)"}
    return carpark.auto_park_cross(client, target_id=int(target))


def _run_spirit(client) -> dict:
    """守護靈免費召喚: draw_all_free 只用 free_times, 不買招喚貨幣 (800003 不存在)."""
    return spirit.draw_all_free(client)


def _run_workshop(client, *, device: str, state_dir=None, now=None) -> dict:
    """加工坊 12h 配方輪換 (spec §2.2; cadence 存 ws_state/<device>.json).

    state {"workshop": {"last_rotate_ts": float, "parity": int}}; 12h 未到 →
    {"rotated": False}; 到了 → rotate_team_recipes(交替 parity)。

    Best-effort 驗收: rotate_team_recipes 不會 raise（伺服器對狀態不符的
    cancel/choose 靜默不回，switch_recipe 內吞 WSTimeoutError），所以只有
    ``switched`` 內至少一個 ``chosen.ok == True``（真的換成一個配方）才算有效
    輪換並回寫 state；全失敗/timeout 則不寫 state，下一輪再試。
    """
    import time as _time
    now = _time.time() if now is None else now
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    st = ws_state.load_state(device, **kw)
    wst = st.get("workshop") or {}
    last_ts = float(wst.get("last_rotate_ts") or 0)
    if now - last_ts < _WORKSHOP_ROTATE_S:
        hours = (now - last_ts) / 3600.0
        return {"rotated": False, "reason": f"rotated {hours:.1f}h ago (<12h)"}
    parity = (int(wst.get("parity") or 0) + 1) % 2 if last_ts else 0
    out = workshop.rotate_team_recipes(client, parity=parity)
    any_ok = any((s.get("chosen") or {}).get("ok") for s in out.get("switched", ()))
    if not any_ok:
        logger.warning(
            "ws_token runner: %s workshop rotate had no successful switch "
            "(server silent / no team workshops) — state not written, will retry",
            device)
        return {"rotated": False, "reason": "all switches failed/timeout", **out}
    st["workshop"] = {"last_rotate_ts": now, "parity": parity}
    ws_state.save_state(device, st, **kw)
    return {"rotated": True, **out}


def _run_couple(client, *, gifts: bool, forge_ring: bool) -> dict:
    """伴侶: 奶茶+玫瑰送光 (give_all_in_hand, 每批20封頂) + 戒指錘鍊 (spend 類).

    默契考驗 (Marry type 6) 已由 _run_main_tasks 的 claim_marry_tasks 領取。
    無伴侶 (favor list 空且 lover_id=0) → skip。
    """
    partners = couple.read_favor_info(client)
    friend_id = partners[0].role_id if partners else couple.read_partner(client)
    summary: dict = {"partner": friend_id, "milk_tea": None, "flower": None,
                     "ring": None}
    if not friend_id:
        return {**summary, "skipped": "no partner"}
    if gifts:
        summary["milk_tea"] = couple.give_all_in_hand(
            client, friend_id=friend_id, flower_id=couple.MILK_TEA)
        summary["flower"] = couple.give_all_in_hand(
            client, friend_id=friend_id, flower_id=couple.FLOWER)
    if forge_ring:
        summary["ring"] = couple.forge_ring_until_empty(client)
    return summary


def _run_lamp(client) -> dict:
    """開神燈: sequentially open up to 10000 boxes and auto-equip/sell drops.

    Only reached when ``open_lamp=True`` (gated by run_device); it consumes 神燈
    items. The server accepts at most 20 per OPEN_ALL call, so this runs a
    single-threaded 20-item batch loop capped at 500 batches (10000 total) and
    stops early when the server reports no lamps left. Returns
    lamp.open_lamp's summary ``{opened, equipped, sold, left, dry_run}``.
    """
    return _load_lamp().open_lamp(
        client,
        dry_run=False,
        batch_num=_LAMP_BATCH_NUM,
        max_batches=_LAMP_MAX_BATCHES,
        batch_delay=_LAMP_BATCH_DELAY_SEC,
    )


def _run_mining(client, tracker: mining.InventoryTracker, *,
                mining_config: Optional[dict]) -> dict:
    """挖礦 opt-in: 用 0x0402 庫存現量，一步一刷新，鎬子用完即停。"""
    cfg = mining_config or {}
    return mining_supervised.mine_until_pickaxe_empty(
        client,
        tracker,
        allow_bomb=bool(cfg.get("allow_bomb")),
        allow_drill=bool(cfg.get("allow_drill")),
        max_steps=int(cfg.get("max_steps") or 200),
        timeout=cfg.get("timeout"),
        max_depth=cfg.get("max_depth"),
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
                 sweep_list: Sequence[Sequence[int]]) -> dict:
    """read_info (free); shopping + dungeon sweep + renew only when spend.

    The dungeon sweep needs a caller-supplied chapter list (``sweep_list`` —
    steward does NOT auto-derive level/times); with no chapters configured the
    sweep is skipped even on spend, matching the live wiring.
    """
    summary: dict = {
        "info": None, "shopping": None, "sweep": None,
        "shopping_active": False, "dungeon_active": False,
    }
    summary["info"] = steward.read_info(client)
    if not spend:
        return summary

    # renew=True spends 家園幣 only when the selected service is expired.
    shopping_active = steward.ensure_active(
        client, steward.SERVICE_SHOPPING, serv_time=serv_time, renew=True)
    summary["shopping_active"] = shopping_active
    if shopping_active:
        summary["shopping"] = steward.run_shopping(client)

    if sweep_list:
        dungeon_active = steward.ensure_active(
            client, steward.SERVICE_DUNGEON, serv_time=serv_time, renew=True)
        summary["dungeon_active"] = dungeon_active
        if dungeon_active:
            summary["sweep"] = steward.run_dungeon_sweep(client, sweep_list)
    return summary


def run_device(device: str, *, spend: bool = False,
               sweep_list: Optional[Iterable[Sequence[int]]] = None,
               open_lamp: bool = False,
               farm_config: Optional[dict] = None,
               dungeon_sweeps: Optional[Iterable[Sequence[int]]] = None,
               carpark_target: Optional[int] = None,
               couple_gifts: bool = True,
               forge_ring: bool = False,
               workshop_rotate: bool = True,
               mining_config: Optional[dict] = None,
               progress=None) -> RunReport:
    """Run every ws_token daily task for ``device`` over one logged-in client.

    Builds a single WSGameClient (with a TaskCollector mounted as push_handler
    so the login-time main-task frames are captured), connects once, runs each
    task with its own error boundary, then closes the client. Returns a
    :class:`RunReport` summarising per-task results and errors. ``spend=False``
    (default) sends no cost action.

    ``sweep_list`` (only used when ``spend``) is the 副本管家 chapter list
    ``[(id, level, times[, use_ad]), ...]``; with none configured the sweep is
    skipped (steward does not auto-derive level/times).

    ``open_lamp`` (default False) is an independent opt-in for 開神燈. When True
    the runner opens one bounded batch of 神燈 boxes (REAL, not dry_run) and
    auto-equips/sells the drops — it consumes 神燈 items, so it is gated behind
    its own flag rather than ``spend`` and is OFF by default (legacy behaviour is
    unchanged: only the free redpack step is added for non-lamp devices).

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
        OFF by default. Missing 0x0402 inventory snapshot -> skip, not guess.

    ``progress`` (optional) is a ``(task_name, status, detail)`` callback fired
    per task: ``("xxx", "start", "")`` before, ``("xxx", "ok", "")`` /
    ``("xxx", "error", "<err>")`` after — callers wire it to the dashboard
    state and the per-device log. A raising callback is swallowed; it can
    never abort the run.
    """
    tasks: dict[str, Any] = {}
    errors: dict[str, str] = {}
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

    client = _make_client(creds, push_handler=_push)

    try:
        login = client.connect()
    except (WSLoginError, WSError, OSError) as exc:
        logger.error("ws_token runner: %s login failed: %s", device, exc)
        try:
            client.close()
        except Exception:  # noqa: BLE001 — close must never mask the login error
            logger.debug("ws_token runner: %s close after login failure raised", device,
                         exc_info=True)
        return RunReport(device=device, login_ok=False, spend=spend,
                         tasks=tasks, errors={LOGIN_TASK: str(exc)})

    serv_time = int(login.get("serv_time") or creds.login_time or 0)
    role_id_hint = int(login.get("role_id") or creds.role_id or 0)
    logger.info("ws_token runner: %s login ok role_id=%s spend=%s",
                device, role_id_hint, spend)

    def _notify(name: str, status: str, detail: str = "") -> None:
        if progress is None:
            return
        try:
            progress(name, status, detail)
        except Exception:  # noqa: BLE001 — 回報壞掉不可中斷任務
            logger.debug("ws_token runner: %s progress callback raised", device,
                         exc_info=True)

    def _step(name: str, fn) -> None:
        _safe(tasks, errors, name, fn, notify=_notify)

    try:
        _step("main_tasks", lambda: _run_main_tasks(client, collector))
        _step("league_solo", lambda: _run_league_solo(client))
        _step("redpack", lambda: _run_redpack(client))
        _step("idle_reward", lambda: _run_idle_reward(client, idle_offline))
        _step("turntable", lambda: _run_turntable(client))
        _step("farm",
              lambda: _run_farm(client, role_id=role_id_hint, farm_config=farm_config))
        _step("dungeon", lambda: _run_dungeon(client, sweeps=dsweeps))
        _step("guild", lambda: _run_guild(client, spend=spend))
        _step("steward",
              lambda: _run_steward(client, spend=spend, serv_time=serv_time,
                                   sweep_list=sweep))
        _step("carpark",
              lambda: _run_carpark(client, target=carpark_target))
        _step("spirit", lambda: _run_spirit(client))
        if workshop_rotate:
            _step("workshop",
                  lambda: _run_workshop(client, device=device))
        _step("couple",
              lambda: _run_couple(client, gifts=couple_gifts,
                                  forge_ring=forge_ring))
        if mining_config and mining_config.get("enabled"):
            _step("mining",
                  lambda: _run_mining(client, inventory_tracker,
                                      mining_config=mining_config))
        if open_lamp:
            _step("lamp", lambda: _run_lamp(client))
    finally:
        # Read the kick flag BEFORE close() — a deliberate close never sets it,
        # so this captures only a real 異地登入 / server-drop during the run.
        try:
            kicked = bool(client.is_kicked())
        except Exception:  # noqa: BLE001 — defensive: a fake/odd client w/o the method
            kicked = False
        try:
            client.close()
        except Exception:  # noqa: BLE001
            logger.debug("ws_token runner: %s close raised", device, exc_info=True)

    if kicked:
        logger.warning("ws_token runner: %s 連線在執行中被踢（異地登入/伺服器斷線）",
                       device)
    logger.info("ws_token runner: %s done — %d task(s) ok, %d error(s), kicked=%s",
                device, len(tasks), len(errors), kicked)
    return RunReport(device=device, login_ok=True, spend=spend,
                     tasks=tasks, errors=errors, kicked=kicked)


def _safe(tasks: dict, errors: dict, name: str, fn, notify=None) -> None:
    """Run one task with its own error boundary; record result OR error.

    Any exception (WSTimeoutError, parse errors, etc.) is caught so the next
    task still runs. The error is summarised onto ``errors[name]``. ``notify``
    (already exception-safe at the caller) reports start / ok / error.
    """
    if notify:
        notify(name, "start", "")
    try:
        tasks[name] = fn()
        if notify:
            notify(name, "ok", "")
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
                    help="副本管家 sweep chapter (repeatable; only used with --spend)")
    ap.add_argument("--open-lamp", dest="open_lamp", action="store_true",
                    help="also 開神燈 (REAL): opens one bounded batch and "
                         "auto-equips/sells drops. Consumes 神燈 items. Default: off.")
    ap.add_argument("--farm-seed", type=int, default=None, metavar="SEED_ID",
                    help="農場: plant this seed_id on empty lands (live-confirm value)")
    ap.add_argument("--farm-team", type=int, default=None, metavar="TEAM_CFG_ID",
                    help="農場: start 打工 with this team_cfg_id (live-confirm value)")
    ap.add_argument("--dungeon-sweep", action="append", default=[],
                    metavar="type:dungeon_id:num",
                    help="深淵/萬神 掃蕩 (repeatable; 掃蕩 only, never battle)")
    ap.add_argument("--carpark-target", type=int, default=None, metavar="MASTER_ID",
                    help="跨界停車: cross lot master_id to park into (只停不收)")
    ap.add_argument("--no-couple-gifts", dest="couple_gifts", action="store_false",
                    help="伴侶送禮 (奶茶+玫瑰送光) 預設開; 此旗標關閉")
    ap.add_argument("--forge-ring", action="store_true",
                    help="戒指錘鍊: 消耗全部真愛之石 (預設關)")
    ap.add_argument("--no-workshop", dest="workshop_rotate", action="store_false",
                    help="加工坊 12h 配方輪換預設開; 此旗標關閉")
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
                     open_lamp=args.open_lamp, farm_config=farm_config,
                     dungeon_sweeps=dungeon_sweeps,
                     carpark_target=args.carpark_target,
                     couple_gifts=args.couple_gifts, forge_ring=args.forge_ring,
                     workshop_rotate=args.workshop_rotate,
                     mining_config=mining_config)
    print(_format_report(rep), flush=True)
    return 0 if rep.login_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
