"""Pure-WS device loop for the ws_token backend.

Devices with ``use_ws_runner: true`` (or ``backend: "ws_token"``) run here
instead of the ADB/Playwright daily pipeline. The loop:

  - never touches ADB / Playwright (no device init, no game launch),
  - on each wake calls :func:`ws_token.runner.run_device` once and logs the
    :class:`~ws_token.runner.RunReport`,
  - reuses the existing sleep/wake/pause/force-sleep machinery
    (:func:`runtime_services.sleep_service.run_sleep_cycle`,
    :func:`runtime_services.startup_sleep._handle_startup_sleep`,
    ``bot_state`` pause / force-sleep) so schedule parity, minute-offset and
    dashboard controls apply identically to the other backends.

Phone-account flow (2026-06-09) — applies ONLY to devices that declare an
``online_check_target_pid`` (the human player to protect). For those devices:

  1. Token bootstrap: on the first wake (and whenever a token has gone stale and
     we are in "need refresh" mode) :func:`_ensure_token` mints a fresh login
     ticket *iff* the device serial is currently adb-reachable (cold-starts the
     App, ~30s, via ``ws_token.creds.refresh_creds``). When the phone is offline
     the cached ticket is reused as-is. Once minted the phone can be powered
     down; we never touch ADB again until the ticket expires.
  2. Online protection: before every run, :func:`_protected_player_online`
     asks a configured checker (cross-thread mailbox in ``bot_state``) whether
     the protected player is online. If they are online — or the answer is
     undetermined (timeout / no checker) — we **skip this wake entirely** rather
     than risk stomping a live human session.
  3. Token-expiry state machine: a ``login_ok=False`` report means the ticket
     died. The loop drops into "need refresh" mode — it stops calling
     ``run_device`` and instead, each wake, retries ``_ensure_token(force=True)``
     until the phone is adb-reachable again and a fresh ticket is minted, then
     resumes normal operation. Sleep + online-protection keep applying while
     waiting.

Kick cooldown (異地登入) — applies to ALL ws_token devices regardless of the
phone-account gate. When a run reports it was kicked (the account was logged in
elsewhere, or the server dropped the socket — surfaced as ``RunReport.kicked``),
the loop sleeps for :data:`_KICK_COOLDOWN_SEC` (30 min) this wake instead of the
normal aligned window. The cooldown reuses the existing interruptible sleep
(``run_sleep_cycle(forced_wake_ts=...)``) so pause / force-sleep / skip still cut
it short. The next wake's online-protection probe then decides whether to resume
(online → keep waiting, offline → run). Force-sleep takes priority over cooldown.

Devices WITHOUT an ``online_check_target_pid`` keep the original behaviour
byte-for-byte EXCEPT for this kick cooldown: no online protection, no auto token
refresh — just wake → run_device → sleep (this is the path the S0 wiring tests
exercise). A normal (not-kicked) run is byte-for-byte unchanged.

Heavy / runtime-only modules (``ws_token.runner``, ``ws_token.creds``, the
``device`` ADB scanner, the sleep services) are imported lazily inside the
functions so this module stays cheap to import for unit tests — tests
monkeypatch the lazy hooks below.

The cycle is split into :func:`run_ws_device_cycle` (ONE wake → protect → run →
report, no sleep) so it can be unit-tested in isolation, and
:func:`run_ws_device_loop` (the full while-loop with token state machine, sleep
and control handling) which ``new_main_v2.main`` dispatches to.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import bot_state
import config_manager

logger = logging.getLogger(__name__)

# Conservative default wait for a checker to answer a protection probe. Kept
# short relative to the 2h wake cadence: a slow/absent checker should not pin a
# device awake — it just yields a None result and we skip the wake.
_PROTECT_WAIT_SEC_DEFAULT = 60.0

# Cooldown after a kick (異地登入 / server drop). When a run reports it was kicked
# we back off for 30 minutes before the next wake instead of immediately
# reconnecting (which would just race the human who logged in elsewhere). The
# next wake's online-protection probe then decides whether to resume.
_KICK_COOLDOWN_SEC = 1800.0
_WS_TASK_LABELS = {
    "harvest_card": "豐收卡",
}


def _ws_task_label(name: str) -> str:
    """Dashboard-facing WS task label; internal task keys stay English."""
    return _WS_TASK_LABELS.get(name, name)


def _load_run_device():
    """Lazy import of ws_token.runner.run_device (heavy / WS deps)."""
    from ws_token.runner import run_device
    return run_device


def _load_refresh_creds():
    """Lazy import of ws_token.creds.refresh_creds (shells adb_token_login)."""
    from ws_token.creds import refresh_creds
    return refresh_creds


def _load_get_adb_devices():
    """Lazy import of device.get_adb_devices (keeps this module import-light)."""
    from device import get_adb_devices
    return get_adb_devices


def _is_adb_reachable(ip: str) -> bool:
    """True iff ``ip`` appears in the current ``adb devices`` list.

    Any error scanning ADB is treated as "not reachable" — we never want a
    flaky ADB to *block* the cached-token path.
    """
    try:
        get_adb_devices = _load_get_adb_devices()
        return ip in (get_adb_devices() or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("ws_token _is_adb_reachable(%s) failed: %s", ip, exc)
        return False


def _ensure_token(ip: str, cfg: Any, logger_obj, *, force: bool = False) -> bool:
    """Make sure ``ip`` has a usable login token; refresh it when we can.

    Returns True when a usable token is believed available, False otherwise.

      - adb-reachable → ``refresh_creds(ip)`` mints a fresh ticket (cold-starts
        the App). A failure logs a warning and returns False so the caller can
        stay in/enter "need refresh" mode.
      - NOT adb-reachable → the cached ticket is reused (returns True). This only
        asserts "we have *a* token", not that it is unexpired — expiry is
        detected later via ``run_device`` returning ``login_ok=False``.

    ``force`` is accepted for symmetry with the loop's refresh-mode retries; the
    behaviour is identical (we always refresh when reachable), it just documents
    intent at the call site. Refresh/scan deps are imported lazily.
    """
    if _is_adb_reachable(ip):
        try:
            refresh_creds = _load_refresh_creds()
            refresh_creds(ip)
            logger_obj.info(f"[{ip}] ws_token 取得新 token（adb 可達，已冷啟撈 ticket）")
            return True
        except Exception as exc:  # noqa: BLE001
            logger_obj.warning(
                f"[{ip}] ws_token 撈新 token 失敗（adb 可達但 refresh_creds 例外）: {exc}"
            )
            return False
    # Phone is offline: trust the cached ticket until run_device proves it dead.
    logger_obj.info(f"[{ip}] ws_token adb 不可達，沿用快取 token（force={force}）")
    return True


def _protected_player_online(ip: str, cfg: Any, logger_obj) -> Optional[bool]:
    """Is the protected human player currently online? (conservative)

    Returns:
      True  — protected player is online (or the result is undetermined and we
              must assume online to be safe). Caller MUST skip this wake.
      False — there is nothing to protect, or the player is confirmed offline.
              Caller may run.
      None  — never returned to the caller here; folded into True by the helper
              so callers only branch on True/False. (Kept in the signature for
              documentation; see note below.)

    When ``cfg`` has no ``online_check_target_pid`` there is no player to
    protect → returns False (run). Otherwise a cross-thread online-check is
    submitted to ``bot_state`` and any configured checker may serve it. A
    ``status == "done"`` result yields its ``result_busy`` flag; anything else
    (timeout / no checker / error) is treated as **online** — we would rather
    not run than kick a live player.
    """
    # cfg (caller-supplied) wins for the explicit target; resolver adds the
    # creds fallback so this matches get_device_role_id everywhere else.
    target_pid = cfg.get("online_check_target_pid") or config_manager.get_device_role_id(ip)
    if not target_pid:
        return False  # nothing to protect — no creds, no config

    try:
        wait_sec = float(cfg.get("online_check_timeout_sec", _PROTECT_WAIT_SEC_DEFAULT))
    except (TypeError, ValueError):
        wait_sec = _PROTECT_WAIT_SEC_DEFAULT

    try:
        req_id = bot_state.submit_online_check_request(
            requester_ip=ip, target_pid=int(target_pid)
        )
        result = bot_state.wait_online_check_result(req_id, timeout_sec=wait_sec)
    except Exception as exc:  # noqa: BLE001 — conservative on any mailbox error
        logger_obj.warning(
            f"[{ip}] ws_token 在線保護查詢例外: {exc}；保守視為在線（skip）"
        )
        return True

    status = str(result.get("status", "pending"))
    if status == "done":
        is_online = bool(result.get("result_busy", True))
        logger_obj.info(
            f"[{ip}] ws_token 在線保護結果: online={is_online} "
            f"(target_pid={target_pid}, detail={result.get('detail', '')})"
        )
        return is_online

    # Undetermined: timeout / no checker available / error → assume online.
    logger_obj.warning(
        f"[{ip}] ws_token 在線保護未定: status={status}, error={result.get('error', '')}；"
        f"保守視為在線（skip）"
    )
    return True


def run_ws_device_cycle(ip: str, cfg: Any, logger_obj) -> Optional[Any]:
    """Run ONE ws_token pass for ``ip`` and log the report.

    Pure of any sleep/loop concerns so it can be unit-tested directly.

    Before touching ``run_device`` the cycle runs the online-protection probe
    (:func:`_protected_player_online`). If the protected player is online (or the
    result is undetermined) the cycle **skips** — ``run_device`` is NOT called and
    ``None`` is returned. With no ``online_check_target_pid`` the probe is a no-op
    (returns False) so this is byte-for-byte the original behaviour.

    Returns the :class:`~ws_token.runner.RunReport` on a real run, or ``None``
    when the pass was skipped for protection or ``run_device`` raised. A
    ``login_ok=False`` report is logged as a warning and returned as-is so the
    loop can drive its token-refresh state machine. ``cfg`` is the device config
    (a ``DeviceConfig`` or plain dict — only ``.get`` is used).
    """
    # Online protection: never stomp a live human session.
    if _protected_player_online(ip, cfg, logger_obj):
        logger_obj.info(f"[{ip}] ws_token 受保護玩家在線/不確定，跳過本輪（不踢人）")
        bot_state.update_state(ip, task="WS 在線保護", step="受保護玩家在線/不確定，skip 本輪")
        return None

    spend = bool(cfg.get("ws_token_spend", False))
    sweep_list = cfg.get("ws_token_sweep_list") or None
    open_lamp = bool(cfg.get("ws_token_open_lamp", False))
    farm_config = cfg.get("ws_token_farm_config") or None
    dungeon_sweeps = cfg.get("ws_token_dungeon_sweeps") or None
    carpark_target = cfg.get("ws_token_carpark_target") or None
    carpark_auto = bool(cfg.get("ws_token_carpark_auto", False))
    _ws_nested = cfg.get("ws_token") or {}
    if not isinstance(_ws_nested, dict):
        _ws_nested = {}
    carpark_plan = _ws_nested.get("carpark_plan") or None
    couple_gifts = bool(cfg.get("ws_token_couple_gifts", True))
    forge_ring = bool(cfg.get("ws_token_forge_ring", False))
    workshop_rotate = bool(cfg.get("ws_token_workshop_rotate", True))
    kungfu_guess = bool(cfg.get("ws_token_kungfu_guess", False))
    # 每日自動領郵件附件：單一真相在巢狀 ws_token dict（mail_claim 預設關）。
    mail_claim = bool(_ws_nested.get("mail_claim", False))

    def _opt_int(key: str):
        try:
            v = _ws_nested.get(key)
            return int(v) if v not in (None, "", 0) else None
        except (TypeError, ValueError):
            return None
    mail_gem_threshold = _opt_int("mail_gem_threshold")
    mail_skill_threshold = _opt_int("mail_skill_threshold")
    # 遺物 平均強化 (SPENDS 遺物碎片) / 傳奇大亨擲骰：單一真相在巢狀 ws_token dict。
    relic_upgrade = bool(_ws_nested.get("relic_upgrade", False))
    tycoon = bool(_ws_nested.get("tycoon", False))
    # 跨服戰 放置獎勵 純-WS 每 ≤4h 自動領取 (預設關)；只在 act_list 顯示活動 Open 才送。
    xwar_idle_enabled = bool(_ws_nested.get("xwar_idle", False))
    # 抽卡 (技能/同伴) — 巢狀 ws_token.gacha 子設定 (預設關)；消耗抽卡券。
    gacha_config = _ws_nested.get("gacha") or None
    # 秘寶 (塵世尋寶) — 巢狀 ws_token.secret_jewel 子設定 (預設關)。
    # draw_free=免費抽 (免費)；buy_daily=每日買尋寶圖 (SPENDS 粉鑽)。任一開才傳。
    _sj_cfg = _ws_nested.get("secret_jewel")
    secret_jewel_config = None
    if isinstance(_sj_cfg, dict) and (_sj_cfg.get("draw_free") or _sj_cfg.get("buy_daily")):
        secret_jewel_config = _sj_cfg
    # 看廣告獎勵 (鑽石/種子) — 巢狀 ws_token.ad_rewards 子設定 (預設關)；is_free=1 純 WS 領。
    # enabled 且 config_ids 非空才傳 list，否則保持不傳 (run_device 該任務 self-skip)。
    _ad_cfg = _ws_nested.get("ad_rewards")
    ad_reward_config_ids = None
    if isinstance(_ad_cfg, dict) and bool(_ad_cfg.get("enabled")):
        _ids = _ad_cfg.get("config_ids")
        if isinstance(_ids, (list, tuple)):
            clean_ids = [int(x) for x in _ids if isinstance(x, (int, float))]
            ad_reward_config_ids = clean_ids or None

    def _nested_int(key: str, default: int) -> int:
        try:
            return int(_ws_nested.get(key, default))
        except (TypeError, ValueError):
            return default
    relic_max_steps = _nested_int("relic_max_steps", 10)
    relic_fragment_floor = _nested_int("relic_fragment_floor", 0)
    tycoon_max_rolls = _nested_int("tycoon_max_rolls", 50)
    # 遺物碎片衝刺 (衝刺榜，SPENDS 遺物碎片) — 巢狀 ws_token.relic_sprint 子設定 (預設關)。
    # 啟用時才把 enabled/target 透過 extra_kwargs 傳入,未啟用維持既有 wiring 行為。
    _sprint_cfg = _ws_nested.get("relic_sprint")
    relic_sprint_enabled = False
    relic_sprint_target = None
    if isinstance(_sprint_cfg, dict) and bool(_sprint_cfg.get("enabled")):
        relic_sprint_enabled = True
        try:
            _t = int(_sprint_cfg.get("target_spend"))
            relic_sprint_target = _t if _t > 0 else None
        except (TypeError, ValueError):
            relic_sprint_target = None
    mining_config = cfg.get("ws_token_mining") or None
    sea_config = _ws_nested.get("sea_season") or None
    only_tasks = _ws_nested.get("only_tasks") or None
    # 開神燈百分比/最低保留：單一真相在巢狀 ws_token dict（防呆轉型，壞值退回 0）。
    try:
        lamp_percent = max(0.0, float(_ws_nested.get("lamp_percent", 0) or 0))
    except (TypeError, ValueError):
        lamp_percent = 0.0
    try:
        lamp_min_keep = max(0, int(_ws_nested.get("lamp_min_keep", 0) or 0))
    except (TypeError, ValueError):
        lamp_min_keep = 0
    try:
        lamp_daily_min = max(0, int(_ws_nested.get("lamp_daily_min", 0) or 0))
    except (TypeError, ValueError):
        lamp_daily_min = 0

    run_device = _load_run_device()
    # 看廣告獎勵預設關 → 不傳此參數 (run_device 走預設 None)，維持既有 wiring 行為；
    # 只有啟用時才附上，避免對沒有 **kwargs 的測試 fake 多傳一個關鍵字。
    extra_kwargs: dict = {}
    if ad_reward_config_ids is not None:
        extra_kwargs["ad_reward_config_ids"] = ad_reward_config_ids
    # 遺物碎片衝刺預設關 → 不傳這兩個參數 (run_device 走預設 False/SPRINT_TOTAL)，維持既有
    # wiring 行為；只有啟用時才附上,避免對沒有 **kwargs 的測試 fake 多傳關鍵字。
    if relic_sprint_enabled:
        extra_kwargs["relic_sprint_enabled"] = True
        if relic_sprint_target is not None:
            extra_kwargs["relic_sprint_target"] = relic_sprint_target
    # 秘寶預設關 → 只在啟用時才附上 (避免對沒有 **kwargs 的測試 fake 多傳關鍵字)。
    if secret_jewel_config is not None:
        extra_kwargs["secret_jewel_config"] = secret_jewel_config
    # 跨服戰閒置獎勵預設關 → 只在啟用時才附上 (避免對沒有 **kwargs 的測試 fake 多傳關鍵字)。
    if xwar_idle_enabled:
        extra_kwargs["xwar_idle_enabled"] = True
    bot_state.update_state(ip, task="WS 任務", step="正在執行 ws_token 每日任務")

    def _should_abort() -> bool:
        # 強制休眠即時中斷：run_device 在每個任務邊界（含開神燈/挖礦內迴圈）輪詢。
        # 只 peek 不消費——信號留給 run_device 返回後消費並 raise，讓
        # run_ws_device_loop 既有的 except ForceSleepRequested 走 force_sleep 睡眠。
        # 暫停同樣即時中斷：abort 後迴圈跳過休眠、回頂端 block 於 check_pause。
        return bot_state.has_pending_force_sleep(ip) or bot_state.is_paused(ip)

    def _progress(name: str, status: str, detail: str = "") -> None:
        """逐任務回報 dashboard step + 裝置 log（runner 端已保證不會炸 run）。"""
        label = _ws_task_label(name)
        if status == "start":
            step = f"WS 任務執行中: {label}"
            logger_obj.info(f"[{ip}] ws_token 任務開始: {label}")
        elif status == "ok":
            step = f"WS 任務完成: {label}"
            logger_obj.info(f"[{ip}] ws_token 任務完成: {label}")
        elif status == "progress":
            step = f"WS 開神燈 ({detail})"
            logger_obj.info(f"[{ip}] ws_token 開神燈進度: {detail}")
        else:
            step = f"WS 任務失敗: {label}"
            logger_obj.warning(f"[{ip}] ws_token 任務失敗: {label} ({detail})")
        try:
            bot_state.update_state(ip, task="WS 任務", step=step)
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響任務
            logger_obj.debug(f"[{ip}] ws_token update_state 失敗", exc_info=True)

    try:
        report = run_device(ip, spend=spend, sweep_list=sweep_list,
                            progress=_progress,
                            should_abort=_should_abort,
                            open_lamp=open_lamp, lamp_percent=lamp_percent,
                            lamp_min_keep=lamp_min_keep,
                            lamp_daily_min=lamp_daily_min, farm_config=farm_config,
                            dungeon_sweeps=dungeon_sweeps,
                            carpark_target=carpark_target,
                            carpark_auto=carpark_auto,
                            carpark_plan=carpark_plan,
                            couple_gifts=couple_gifts, forge_ring=forge_ring,
                            workshop_rotate=workshop_rotate,
                            kungfu_guess=kungfu_guess,
                            mail_claim=mail_claim,
                            mail_gem_threshold=mail_gem_threshold,
                            mail_skill_threshold=mail_skill_threshold,
                            relic_upgrade=relic_upgrade,
                            relic_max_steps=relic_max_steps,
                            relic_fragment_floor=relic_fragment_floor,
                            tycoon=tycoon,
                            tycoon_max_rolls=tycoon_max_rolls,
                            gacha_config=gacha_config,
                            mining_config=mining_config,
                            sea_config=sea_config,
                            only_tasks=only_tasks,
                            **extra_kwargs)
    except Exception as exc:  # noqa: BLE001 — one bad pass must not kill the thread
        logger_obj.error(f"[{ip}] ws_token run_device 例外: {exc}", exc_info=True)
        bot_state.update_state(ip, task="WS 任務失敗", step=f"run_device 例外: {exc}")
        return None

    # 強制休眠在 run 期間到達 → run_device 已在任務邊界 abort（只 peek 未消費），
    # 這裡消費信號並 raise，由 run_ws_device_loop 轉成 force_sleep 睡眠語意。
    if bot_state.check_force_sleep(ip):
        from runtime_services.device_runtime_service import ForceSleepRequested
        raise ForceSleepRequested(f"[{ip}] force sleep requested during ws cycle")

    login_ok = bool(getattr(report, "login_ok", False))
    tasks = getattr(report, "tasks", {}) or {}
    errors = getattr(report, "errors", {}) or {}
    if not login_ok:
        # ticket missing / login failed — the loop's state machine handles refresh.
        logger_obj.warning(
            f"[{ip}] ws_token 登入失敗 (login_ok=False, errors={list(errors)})；"
            f"token 可能失效，將進入重撈模式"
        )
        bot_state.update_state(ip, task="WS 登入失敗", step=f"errors={list(errors)}")
    else:
        logger_obj.info(
            f"[{ip}] ws_token 完成: spend={spend} tasks_ok={list(tasks)} errors={list(errors)}"
        )
        bot_state.update_state(
            ip,
            task="WS 任務完成",
            step=f"tasks_ok={list(tasks)} errors={list(errors)}",
        )
    return report


def _is_token_invalid(report: Optional[Any]) -> bool:
    """True iff a run actually completed but reported a failed login.

    A skipped/protected cycle or a swallowed exception returns ``None`` and must
    NOT be read as token expiry — only an explicit ``login_ok=False`` report
    means the ticket died.
    """
    return report is not None and not bool(getattr(report, "login_ok", False))


def _report_kicked(report: Optional[Any]) -> bool:
    """True iff a run completed and reported it was kicked (異地登入 / drop).

    A skipped/protected cycle or a swallowed exception returns ``None`` and is
    NOT a kick. Only an explicit ``report.kicked`` truthy value counts, so this
    is safe to call on any cycle result.
    """
    return report is not None and bool(getattr(report, "kicked", False))


def _kick_cooldown_wake_ts() -> float:
    """Absolute wake timestamp for the post-kick cooldown (now + 30 min).

    Indirected (and using ``time.time`` via the module attribute) so tests can
    monkeypatch the duration or the clock deterministically.
    """
    return time.time() + _KICK_COOLDOWN_SEC


def _log_kick_cooldown(ip: str, logger_obj) -> None:
    """Announce the kick cooldown on the logger and the dashboard state."""
    logger_obj.warning(
        f"[{ip}] ws_token 偵測到異地登入被踢，本輪冷卻 "
        f"{_KICK_COOLDOWN_SEC / 60:.0f} 分鐘後再查在線"
    )
    bot_state.update_state(
        ip, task="WS 被踢冷卻",
        step=f"異地登入被踢，冷卻 {_KICK_COOLDOWN_SEC / 60:.0f} 分鐘後再查在線",
    )


def _device_needs_protection(cfg: Any) -> bool:
    """True iff this device opts into the phone-account flow.

    The presence of ``online_check_target_pid`` is the single gate: it enables
    both online protection and automatic token (re)fetching. Devices without it
    keep the original no-protect / no-auto-refresh behaviour.
    """
    return bool(cfg.get("online_check_target_pid"))


def run_ws_device_loop(ip: str, logger_obj) -> None:
    """Full pure-WS device loop for ``ip``.

    Mirrors the control surface of the legacy main loop — startup stagger,
    pause, force-sleep, aligned sleep/wake — but with NO device init and NO
    game launch. For phone-account devices (those with an
    ``online_check_target_pid``) it additionally drives the token bootstrap /
    expiry state machine described in the module docstring.

    Lazy-imports the sleep services so importing this module stays light for
    unit tests.
    """
    from runtime_services.device_runtime_service import ForceSleepRequested
    from runtime_services.sleep_service import run_sleep_cycle
    from runtime_services.startup_sleep import _handle_startup_sleep

    bot_state.init_device(ip)
    # Token state machine flags (only meaningful for phone-account devices):
    #   _token_bootstrapped — have we tried to mint a token at least once?
    #   _need_refresh        — did the last run prove the ticket dead?
    token_bootstrapped = False
    need_refresh = False
    try:
        _handle_startup_sleep(ip, logger_obj)

        while True:
            force_sleep_now = False
            sleep_policy = "aligned_window"
            sleep_reason = "常規對齊喚醒 (ws_token)"
            cooldown_wake_ts = None  # set when a kick triggers the 30-min back-off
            try:
                # Respect dashboard force-sleep before doing any work.
                if bot_state.check_force_sleep(ip):
                    raise ForceSleepRequested("force sleep requested from dashboard")
                # Respect pause: block here until resumed. WS devices have no
                # browser to open, so a pending web-launch request is irrelevant
                # — check_pause already returns on that, we simply loop back.
                bot_state.check_pause(ip)
                if bot_state.check_force_sleep(ip):
                    raise ForceSleepRequested("force sleep requested during pause")

                # Dashboard 純 WS 工具連線使用中 → 等釋放再登入（同帳號互踢防護；
                # pause 可能被入睡清理吃掉，這裡直接查 ws_session registry）。
                from game_actions.ws_phase import wait_for_dashboard_ws_release
                wait_for_dashboard_ws_release(ip, logger_obj)

                # Re-read config each wake so live toggles (spend/sweep) apply.
                cfg = config_manager.get_device_config(ip)
                protect = _device_needs_protection(cfg)

                if protect and need_refresh:
                    # Ticket is dead. Don't run; only retry minting a fresh one,
                    # which can only succeed once the phone is adb-reachable.
                    if _ensure_token(ip, cfg, logger_obj, force=True):
                        if _is_adb_reachable(ip):
                            need_refresh = False
                            token_bootstrapped = True
                            logger_obj.info(
                                f"[{ip}] ws_token 重撈成功，恢復正常 cycle"
                            )
                            report = run_ws_device_cycle(ip, cfg, logger_obj)
                            if _report_kicked(report):
                                cooldown_wake_ts = _kick_cooldown_wake_ts()
                                _log_kick_cooldown(ip, logger_obj)
                        else:
                            # Cached token reused but we already know it's dead —
                            # stay in refresh mode until the phone comes back.
                            logger_obj.info(
                                f"[{ip}] ws_token 仍在重撈模式（adb 不可達），本輪跳過 run"
                            )
                            bot_state.update_state(
                                ip, task="WS 重撈中", step="等待 adb 可達後重撈 token"
                            )
                    else:
                        logger_obj.info(
                            f"[{ip}] ws_token 重撈未成功，維持重撈模式，本輪跳過 run"
                        )
                        bot_state.update_state(
                            ip, task="WS 重撈中", step="重撈 token 未成功，下輪重試"
                        )
                else:
                    if protect and not token_bootstrapped:
                        # First wake for a phone-account device: bootstrap a token.
                        _ensure_token(ip, cfg, logger_obj)
                        token_bootstrapped = True
                    report = run_ws_device_cycle(ip, cfg, logger_obj)
                    if protect and _is_token_invalid(report):
                        need_refresh = True
                        logger_obj.warning(
                            f"[{ip}] ws_token token 失效，進入重撈模式（停跑 cycle，"
                            f"等 adb 可達後重撈）"
                        )
                    if _report_kicked(report):
                        cooldown_wake_ts = _kick_cooldown_wake_ts()
                        _log_kick_cooldown(ip, logger_obj)
            except ForceSleepRequested as e:
                force_sleep_now = True
                sleep_policy = "force_sleep"
                sleep_reason = "強制休眠"
                cooldown_wake_ts = None  # force-sleep takes priority over cooldown
                logger_obj.warning(f"[{ip}] ws_token 迴圈收到強制休眠請求: {e}")

            # 暫停中斷：cycle 被 should_abort 提前結束 → 別進睡眠，直接回頂端 block 於
            # check_pause，恢復後重跑（daily-done 追蹤跳過已完成任務）。force_sleep 優先。
            if bot_state.is_paused(ip) and not force_sleep_now:
                continue

            enable_dungeon_manager = bool(
                config_manager.get_device_config(ip).get("enable_dungeon_manager", True)
            )
            if cooldown_wake_ts is not None:
                #被踢冷卻：睡滿 30 分鐘（forced_wake_ts 走既有可中斷睡眠，
                # pause / force_sleep / skip 仍能提前喚醒），下輪再走在線保護。
                sleep_policy = "kick_cooldown"
                sleep_reason = "異地登入被踢，冷卻 30 分鐘"
            run_sleep_cycle(
                ip,
                logger_obj,
                forced_wake_ts=cooldown_wake_ts,
                force_sleep_now=force_sleep_now,
                sleep_policy=sleep_policy,
                sleep_reason=sleep_reason,
                enable_dungeon_manager=enable_dungeon_manager,
            )
    except Exception as e:  # noqa: BLE001
        logger_obj.error(f"[{ip}] ws_token 迴圈未預期錯誤: {e}", exc_info=True)
        bot_state.update_state(ip, log=f"ws_token 異常中斷: {e}")
    finally:
        bot_state.set_offline(ip, reason="ws_token thread exit")
