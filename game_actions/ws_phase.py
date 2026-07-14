"""WS-first 階段：喚醒後、Playwright 瀏覽器啟動前，先跑純 WS 任務。

run_ws_phase(ip) 讀裝置的 ws_token config，in-thread 跑 ws_token.runner.run_device
（不需瀏覽器/App；WS 登入會踢同帳號其他 session，所以必須在開瀏覽器之前跑），
再把 RunReport 轉成本輪 daily_pipeline 可跳過的任務名集合。

任何失敗（creds 缺/登入失敗/例外）→ frozenset()，Playwright 階段全跑 —— WS 階段
只會替 pipeline 減工作，永遠不會讓它漏工作（天然降級）。

Skip 對照（spec §3.3；含使用者確認的等價：Store==管家代購、好友每日禮物==伴侶送禮）。
farm / dungeon 採條件式 skip：沒配 seed_id / dungeon_sweeps 就不跳（WS 只做了部分）。
"""
from __future__ import annotations

import logging
import time

import config_manager

logger = logging.getLogger(__name__)

# RunReport 任務鍵 → 該鍵成功時 daily_pipeline 可跳過的任務名（無條件部分）。
WS_TO_PIPELINE_SKIPS: dict[str, tuple[str, ...]] = {
    "redpack": ("紅包檢查",),
    "idle_reward": ("點擊寶箱",),
    "guild": ("家族任務",),
    "spirit": ("領取守護靈",),
    "steward": ("商店購買",),       # 使用者確認 Store == 管家代購
    "main_tasks": ("所有日常任務",),
    "couple": ("好友每日禮物",),     # 使用者確認 == 伴侶送禮
    "lamp": ("開神燈",),
    "turntable": ("轉盤金幣",),
    "mining": ("挖礦/Oracle",),
    "gacha": ("抽技能夥伴",),
    "sea_season": ("航海任務 (Sea)",),
}

# pipeline skip 名 → dashboard /api/daily_progress 追蹤的 JsonDataManager 當日 key。
# WS 做完 → ADB/Playwright 跳過 → 舊實作不會寫紀錄 → 徽章永遠 ⏳，
# 所以 WS 成功時要替它回寫。只列 record-dict schema 的 key；
# `mission_timestamp`（每日任務）是 flat scalar schema（Mission.py），
# time_recording 會把它巢狀化破壞讀側，刻意不回寫。
SKIP_TO_DAILY_RECORD: dict[str, tuple[str, ...]] = {
    "商店購買": ("Store",),
    "家族任務": ("donate_family",),
    "挖礦/Oracle": ("挖礦",),
    "萬神試煉": ("萬神試煉",),
}


def _record_daily_done(ip: str, skips: set[str], log) -> None:
    """WS 成功替代掉的 pipeline 任務 → 回寫 dashboard 追蹤的當日紀錄。
    best-effort：寫入失敗只記 log，不影響 skip-set。"""
    import json_manager
    for skip_name, record_keys in SKIP_TO_DAILY_RECORD.items():
        if skip_name not in skips:
            continue
        for key in record_keys:
            try:
                json_manager.time_recording(ip, name=key)
            except Exception:  # noqa: BLE001 — 紀錄失敗不能影響 WS 階段
                log.warning("[%s] WS 回寫當日紀錄失敗: %s", ip, key, exc_info=True)


# --- WS 專屬 daily-progress 徽章回寫（schema 與 time_recording 不同）-----------
# 「每日任務」(mission_timestamp) 與「農場種植」(farm_plant_click) 兩個徽章的
# on-disk schema 與 SKIP_TO_DAILY_RECORD 那批不同，故獨立處理：
#   - mission_timestamp：flat scalar float（Mission.py 直接存數字）。time_recording
#     會巢狀成 dict，破壞 Mission.py 讀側 → 必須直接寫 flat scalar。
#   - farm_plant_click：dict {timestamp,date,datetime,count}（舊視覺 farm_v2 寫）。
#     用 record_timestamp 維持 dict schema，dashboard 的 is_same_day 才認得。
# 觸發來源是 report.tasks 的原始鍵（main_tasks / ad_rewards），不是 pipeline skip 名。

# ad_reward config_id：農場種子廣告（使用者澄清「農場種植」== 每日領種子廣告）。
_AD_FARM_SEED_CONFIG_ID = 15
# 莊園商店 種子 shop_id（farm.buy 407）：對應 dashboard「農場買種」徽章。
_FARM_SEED_SHOP_ID = 407


def _mark_mission_done(ip: str, log) -> None:
    """寫「每日任務」flat scalar mission_timestamp（不可巢狀化）。

    沿用 Mission.py 的 flat schema：保留既有 mission_num，只刷新 timestamp。
    """
    import time as _time

    import json_manager
    mgr = json_manager.JsonDataManager(
        ip.split(":")[-1] if ":" in ip else ip)
    data = mgr.load_data(default_data={"mission_timestamp": 0, "mission_num": 0})
    data["mission_timestamp"] = _time.time()
    mgr.save_data(data)


def _mark_farm_plant_done(ip: str, log) -> None:
    """寫「農場種植」farm_plant_click（dict schema，含 count）。

    使用者澄清：「農場種植」現等同每日領種子廣告（WS ad_reward config15）。
    沿用舊視覺 farm_v2 的 dict schema，dashboard 讀側（is_same_day）才認得。
    """
    import json_manager
    json_manager.create_time_manager(
        ip.split(":")[-1] if ":" in ip else ip
    ).record_timestamp("farm_plant_click", {"count": 1})


def _ad_seed_claimed(report) -> bool:
    """report.tasks['ad_rewards'] 是否代表「今天農場種子廣告已處理完」。

    完成 = config15 那筆 ``claimed > 0``（真的領到至少一次）或 ``skipped`` 以
    "maxed" 開頭（今天已領滿）。``claimed == 0``（送了請求但被 error_code/cmd 擋
    下，即冷卻/失敗）、``cooldown`` / unknown / 缺項都不算（今天還沒領完，留 ⏳）。

    修正：原本只要有 ``claimed`` key 就算完成，會把「claimed=0 + error」的失敗也
    誤標成當日完成（claim_ad 失敗時仍回 ``{"claimed": 0, "stopped": ...}``）。
    """
    from ws_token import ad_reward
    res = (report.tasks.get("ad_rewards") or {})
    if not isinstance(res, dict):
        return False
    seed_name = ad_reward.AD_NAMES.get(_AD_FARM_SEED_CONFIG_ID)
    entry = (res.get("results") or {}).get(seed_name)
    if not isinstance(entry, dict):
        return False
    if isinstance(entry.get("claimed"), int) and entry["claimed"] > 0:
        return True
    skipped = entry.get("skipped")
    return isinstance(skipped, str) and skipped.startswith("maxed")


def _mark_farm_seed_done(ip: str, log) -> None:
    """寫「農場買種」farm_seed_purchase（比照 farm_v2 的 record_time schema）。"""
    import json_manager
    json_manager.create_time_manager(
        ip.split(":")[-1] if ":" in ip else ip
    ).record_time("farm_seed_purchase")


def _farm_seed_bought(report) -> bool:
    """report.tasks['farm'] 是否代表今天「莊園買種子(407)」已處理完。

    完成 = farm.buy 結果有 shop_id==407 那筆且 ``ok==True`` 且 ``target>0``。
    含「今天已買滿」(``need==0``/``bought==0`` 但 ``ok==True``)，比照 ad-seed 的
    maxed 視為當日完成。買種被 server 擋下 (``ok==False``，如 code=25 冷卻) 不算。
    WS farm 路徑不寫此徽章（只有舊視覺 farm_v2 寫），故由 WS 階段在這裡回寫。
    """
    res = (report.tasks.get("farm") or {})
    if not isinstance(res, dict):
        return False
    buy = res.get("buy")
    if not isinstance(buy, list):
        return False
    for entry in buy:
        if (isinstance(entry, dict)
                and entry.get("shop_id") == _FARM_SEED_SHOP_ID
                and entry.get("ok") is True):
            try:
                if int(entry.get("target") or 0) > 0:
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _record_ws_daily_progress(ip: str, report, effective_done: set[str],
                              log) -> None:
    """回寫「每日任務」「農場種植」徽章（schema 與 SKIP_TO_DAILY_RECORD 不同）。

    best-effort：任一寫入失敗只記 log，不影響 skip-set / WS 階段。
    """
    if "main_tasks" in effective_done:
        try:
            _mark_mission_done(ip, log)
        except Exception:  # noqa: BLE001 — 紀錄失敗不能影響 WS 階段
            log.warning("[%s] WS 回寫每日任務紀錄失敗", ip, exc_info=True)
    if _ad_seed_claimed(report):
        try:
            _mark_farm_plant_done(ip, log)
        except Exception:  # noqa: BLE001
            log.warning("[%s] WS 回寫農場種植紀錄失敗", ip, exc_info=True)
    if _farm_seed_bought(report):
        try:
            _mark_farm_seed_done(ip, log)
        except Exception:  # noqa: BLE001
            log.warning("[%s] WS 回寫農場買種紀錄失敗", ip, exc_info=True)


# --- 續做 ledger（中斷後持久化進度；瀏覽器用完重新上線只跑未完成）-------------
# 中斷時把「本輪已實質完成的任務」寫進 ws_state[device]["ws_resume"]；下一輪 WS
# 階段（resume）讀回來跳過它們，完整跑完即清空。有效視窗 30 分鐘 + 同日，逾時/隔日
# 視為 stale（resume 安全地全跑）。詳見
# docs/superpowers/specs/2026-06-15-ws-phase-interruptible-resume-design.md。
_RESUME_KEY = "ws_resume"
_RESUME_TTL_SEC = 30 * 60
# 時間窗（10:00 搶車位）/ 每輪累積收益類任務即使本輪稍早做過，resume 仍要重跑，
# 不被 ledger 跳過。
_RESUME_EXEMPT = frozenset({"carpark", "idle_reward"})
_WS_TASK_LABELS = {
    "harvest_card": "豐收卡",
}


def _ws_task_label(name: str) -> str:
    """Dashboard-facing WS task label; internal task keys stay English."""
    return _WS_TASK_LABELS.get(name, name)


def _substantive_done(report) -> set[str]:
    """本輪「真的做了事」的任務名：在 tasks 且非 ``{"skipped": ...}`` dict。

    errors 與 tasks 互斥（不在 tasks），所以不需另外排除。回 {"skipped"} 的任務
    （如 couple 無伴侶、dungeon 沒配掃蕩、carpark 沒開窗）視為「本輪沒做事」，
    不進 ledger，resume 時照常重跑（便宜且結果相同）。
    """
    done: set[str] = set()
    for name, result in report.tasks.items():
        if isinstance(result, dict) and "skipped" in result:
            continue
        done.add(name)
    return done


def _resume_today(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(now))


def _load_resume_done(ip: str, now: float, log, state_dir=None) -> set[str]:
    """有效（同日 + TTL 內 + 非空）ledger 的已完成任務集合；否則空集合。best-effort。"""
    try:
        from ws_token import state as ws_state
        kw = {"state_dir": state_dir} if state_dir is not None else {}
        led = (ws_state.load_state(ip, **kw).get(_RESUME_KEY)) or {}
    except Exception:  # noqa: BLE001 — 讀 ledger 壞掉 → 不續做、全跑
        log.debug("[%s] WS resume ledger 讀取失敗（忽略，全跑）", ip, exc_info=True)
        return set()
    done = set(led.get("done") or ())
    if (led.get("date") != _resume_today(now)
            or (now - float(led.get("ts") or 0)) >= _RESUME_TTL_SEC
            or not done):
        return set()
    return done


def _save_resume(ip: str, done: set[str], now: float, log, state_dir=None) -> None:
    """中斷時寫入續做 ledger（best-effort：失敗只記 log，不影響 skip-set）。"""
    try:
        from ws_token import state as ws_state
        kw = {"state_dir": state_dir} if state_dir is not None else {}
        st = ws_state.load_state(ip, **kw)
        st[_RESUME_KEY] = {"date": _resume_today(now), "ts": now,
                           "done": sorted(done)}
        ws_state.save_state(ip, st, **kw)
    except Exception:  # noqa: BLE001
        log.warning("[%s] WS resume ledger 寫入失敗（忽略）", ip, exc_info=True)


def _clear_resume(ip: str, log, state_dir=None) -> None:
    """完整跑完後清空續做 ledger（best-effort；沒有 key 則不寫）。"""
    try:
        from ws_token import state as ws_state
        kw = {"state_dir": state_dir} if state_dir is not None else {}
        st = ws_state.load_state(ip, **kw)
        if _RESUME_KEY in st:
            st.pop(_RESUME_KEY, None)
            ws_state.save_state(ip, st, **kw)
    except Exception:  # noqa: BLE001
        log.debug("[%s] WS resume ledger 清除失敗（忽略）", ip, exc_info=True)


def _cfg_int(cfg: dict, key: str, default: int) -> int:
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_opt_int(cfg: dict, key: str):
    v = cfg.get(key)
    try:
        return int(v) if v not in (None, "", 0) else None
    except (TypeError, ValueError):
        return None


def _ad_reward_ids(cfg: dict):
    """ws_token.ad_rewards → run_device 的 config_ids list；enabled 且非空才回，否則 None。"""
    ad = cfg.get("ad_rewards")
    if not (isinstance(ad, dict) and ad.get("enabled")):
        return None
    ids = ad.get("config_ids")
    if not isinstance(ids, (list, tuple)):
        return None
    clean = [int(x) for x in ids
             if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return clean or None


def _relic_sprint(cfg: dict):
    """ws_token.relic_sprint → (enabled, target|None)。未啟用回 (False, None)。"""
    sp = cfg.get("relic_sprint")
    if not (isinstance(sp, dict) and sp.get("enabled")):
        return False, None
    try:
        t = int(sp.get("target_spend"))
        return True, (t if t > 0 else None)
    except (TypeError, ValueError):
        return True, None


def _run_device(ip: str, cfg: dict, progress=None, *,
                should_abort=None, skip_tasks=None):
    """間接層：lazy import + 參數展開，tests monkeypatch 這裡。

    轉傳的參數必須對齊 ``runtime_services.ws_runner_service``（兩條 caller 共用
    ``run_device``）。歷史上本函式漏接了一整批 — mail/tycoon/kungfu/遺物強化+衝刺/
    看廣告獎勵 —— 導致走 WS-first 階段的 adb 裝置即使 config 開了也靜默不跑。
    ``cfg`` 是裝置的 ws_token 巢狀 dict；``kungfu_guess`` 由 run_ws_phase 折入。
    """
    from ws_token.runner import run_device
    sprint_on, sprint_target = _relic_sprint(cfg)
    kwargs = dict(
        progress=progress,
        spend=bool(cfg.get("spend", False)),
        open_lamp=bool(cfg.get("open_lamp", False)),
        lamp_percent=cfg.get("lamp_percent", 0),
        lamp_min_keep=cfg.get("lamp_min_keep", 0),
        lamp_daily_min=_cfg_int(cfg, "lamp_daily_min", 0),
        farm_config=cfg.get("farm") or None,
        dungeon_sweeps=cfg.get("dungeon_sweeps") or None,
        carpark_target=cfg.get("carpark_target") or None,
        carpark_auto=bool(cfg.get("carpark_auto", False)),
        carpark_plan=cfg.get("carpark_plan") or None,
        couple_gifts=bool(cfg.get("couple_gifts", True)),
        workshop_rotate=bool(cfg.get("workshop_rotate", True)),
        forge_ring=bool(cfg.get("forge_ring", False)),
        kungfu_guess=bool(cfg.get("kungfu_guess", False)),
        mail_claim=bool(cfg.get("mail_claim", False)),
        mail_gem_threshold=_cfg_opt_int(cfg, "mail_gem_threshold"),
        mail_skill_threshold=_cfg_opt_int(cfg, "mail_skill_threshold"),
        relic_upgrade=bool(cfg.get("relic_upgrade", False)),
        relic_max_steps=_cfg_int(cfg, "relic_max_steps", 10),
        relic_fragment_floor=_cfg_int(cfg, "relic_fragment_floor", 0),
        relic_sprint_enabled=sprint_on,
        tycoon=bool(cfg.get("tycoon", False)),
        tycoon_max_rolls=_cfg_int(cfg, "tycoon_max_rolls", 50),
        ad_reward_config_ids=_ad_reward_ids(cfg),
        gacha_config=cfg.get("gacha") or None,
        secret_jewel_config=cfg.get("secret_jewel") or None,
        mining_config=cfg.get("mining") or None,
        sea_config=cfg.get("sea_season") or None,
        dragon_realm_enabled=bool(cfg.get("dragon_realm_enabled", True)),
        xwar_idle_enabled=bool(cfg.get("xwar_idle", False)),
        should_abort=should_abort,
        skip_tasks=skip_tasks,
        only_tasks=cfg.get("only_tasks") or None,
    )
    # target 只在有值時帶，否則讓 run_device 用其預設（避免 None 覆寫 int 預設）。
    if sprint_on and sprint_target is not None:
        kwargs["relic_sprint_target"] = sprint_target
    return run_device(ip, **kwargs)


def _bootstrap_token(ip: str, log, *, force: bool = False) -> bool:
    """間接層：lazy import，tests monkeypatch 這裡。"""
    from ws_token.bootstrap import bootstrap_token
    return bootstrap_token(ip, logger_obj=log, force=force)


# --- 在線閘門：自己的 WS 登入別把真人踢下線（全裝置共用）-----------------------
# WS 登入會「踢同帳號其他 session」(本檔 docstring)。所有帳號都是真人 → 每台裝置
# 在自己的 WS 登入「之前」都先用觀察者(online_monitor 快照,別台好友清單讀來的在線
# 旗標)確認帳號離線才放行。判定零登入成本(只讀快照)。
_HUMAN_WAIT_POLL_SEC = 30
# 觀察者看不到帳號狀態(None)時，非 human_played 裝置最多重查幾輪就 best-effort 放行，
# 避免冷啟動還沒有任何偵測器時整機卡死。human_played(手機主帳號)不設上限。
_UNDETERMINED_MAX_POLLS = 3


def _account_role_id(ip: str):
    """間接層（tests monkeypatch 這裡）：裝置代表的帳號 roleId（target_pid 或 creds）；無則回 None。"""
    return config_manager.get_device_role_id(ip)


def _account_online(role_id):
    """間接層：喚醒閘門可採信 no-idle 讓路前 10 分鐘內的明確離線快照。"""
    try:
        from ws_token.online_monitor import account_online_for_wake_gate
        return account_online_for_wake_gate(role_id)
    except Exception:  # noqa: BLE001 — 讀快照失敗 → None（視為可能在線）
        return None


def _current_detector():
    """間接層（tests monkeypatch 這裡）：當前在線偵測器裝置序號；無則 None。"""
    try:
        from ws_token.online_monitor import current_detector
        return current_detector()
    except Exception:  # noqa: BLE001
        return None


def _web_launch_pending(ip: str) -> bool:
    """間接層（tests monkeypatch 這裡）：使用者按了「開啟網頁」＝最高優先放行。"""
    try:
        import bot_state
        return bool(bot_state.has_pending_web_launch_request(ip))
    except Exception:  # noqa: BLE001
        return False


# --- 帳號 session 閘門：session_registry 是唯一真相來源 -----------------------
# 喚醒週期開跑前取得 SCHEDULER lease；背景借用者可搶回，dashboard 工具則等待釋放。
_DASHBOARD_WS_POLL_SEC = 15


def acquire_scheduler_lease(ip: str, log) -> None:
    """喚醒週期開跑前取得 SCHEDULER lease（Phase 5，取代舊 wait_for_dashboard_ws_release）。

    - 背景借用者（上線偵測/檢查/坐騎追蹤）→ preempt 直接搶回（它們 poll preempted 讓位）。
    - dashboard TOOL（人手動操作）→ 尊重人，15s poll 等待釋放（2026-07-10 使用者定案）。
    - protected（human_played 裝置）→ 不登記 lease 放行，交由既有觀察者閘門保護。
    - 使用者按「開啟網頁」→ 立即放行（明確接管意圖，可能未取得 lease）。
    """
    from runtime_services import session_registry as registry
    waited = 0
    while True:
        result = registry.acquire(ip, registry.Owner.SCHEDULER,
                                  registry.Channel.WS, label="喚醒週期")
        if result.ok:
            if waited:
                log.info("[%s] 佔用已釋放，取得 SCHEDULER lease（等了約 %ds）", ip, waited)
            return
        if result.reason == "protected":
            log.info("[%s] human_played 保護帳號，不登記 SCHEDULER lease", ip)
            return
        conflict = result.conflict
        if conflict is not None and conflict.owner in registry.YIELDING_BORROWERS:
            result = registry.acquire(ip, registry.Owner.SCHEDULER,
                                      registry.Channel.WS, label="喚醒週期",
                                      preempt=True)
            if result.ok:
                log.info("[%s] 已搶回被 %s 借用的帳號", ip, conflict.owner.value)
                return
        if _web_launch_pending(ip):
            log.info("[%s] 偵測到開啟網頁請求，放行 SCHEDULER 閘門（未取得 lease）", ip)
            return
        holder = conflict.owner.value if conflict is not None else "未知"
        if waited == 0:
            log.info("[%s] 帳號被 %s 佔用，喚醒週期等待釋放", ip, holder)
        try:
            import bot_state
            bot_state.update_state(
                ip, task="等待 dashboard 連線釋放",
                step=f"帳號被 {holder} 佔用，{_DASHBOARD_WS_POLL_SEC}s 後重查")
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響等待
            log.debug("[%s] 等待佔用釋放狀態回報失敗", ip, exc_info=True)
        time.sleep(_DASHBOARD_WS_POLL_SEC)
        waited += _DASHBOARD_WS_POLL_SEC


def _wait_until_human_offline(ip: str, log, *, human_played: bool = True) -> None:
    """擋在自己的 WS 登入前，直到觀察者確認帳號離線才放行（所有裝置共用）。

    ``_account_online`` 回：
      - ``False``（確認離線）→ 放行。
      - ``True``（真人在玩）→ 每 ``_HUMAN_WAIT_POLL_SEC`` 秒重查，直到離線（不設上限）。
      - ``None``（觀察者看不到）：``human_played``（手機主帳號，最該保護）比照 True
        無限等（使用者 2026-06-25 指定）；其他（emulator）最多重查
        ``_UNDETERMINED_MAX_POLLS`` 輪就 best-effort 放行，避免冷啟動無偵測器時卡死。

    提前放行的兩個特例：
      - 解不出 roleId（無 creds）→ 直接放行（本來就登不進、踢不了人）。
      - **本裝置正是當前在線偵測器** → 直接放行：monitor 正以本帳號連線＝不可能有真人
        （真人登入早把 monitor 踢了），登入只會踢掉 monitor，monitor 自會交接。
    可中斷：使用者按「開啟網頁」→ 立即放行（web_h5 manual override，符合
    feedback-open-browser-always-responsive）。
    """
    rid = _account_role_id(ip)
    if rid is None:
        return
    if _current_detector() == ip:
        return
    undetermined_polls = 0
    waited = 0
    while True:
        if _web_launch_pending(ip):
            log.info("[%s] 偵測到開啟網頁請求，放行 WS 在線閘門", ip)
            return
        online = _account_online(rid)
        if online is False:
            if waited:
                log.info("[%s] 帳號已離線，恢復腳本（等了約 %ds）", ip, waited)
            return
        if online is None and not human_played:
            undetermined_polls += 1
            if undetermined_polls > _UNDETERMINED_MAX_POLLS:
                log.warning(
                    "[%s] 觀察者連 %d 輪看不到帳號狀態，best-effort 放行（可能冷啟動無偵測器）",
                    ip, _UNDETERMINED_MAX_POLLS)
                return
        reason = "帳號在線(真人在玩)" if online else "觀察者暫時看不到(當作可能在線)"
        try:
            import bot_state
            bot_state.update_state(
                ip, task="等待真人下線",
                step=f"{reason}，{_HUMAN_WAIT_POLL_SEC}s 後重查")
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響等待
            log.debug("[%s] 等待真人下線狀態回報失敗", ip, exc_info=True)
        log.info("[%s] 等待真人下線：%s", ip, reason)
        time.sleep(_HUMAN_WAIT_POLL_SEC)
        waited += _HUMAN_WAIT_POLL_SEC


def _should_bootstrap(backend_kind: str, cfg: dict) -> bool:
    """adb+WS-first：每輪冷啟撈 token（bootstrap_token 內部 has_creds 命中則 no-op）。

    web_h5 不走這條（它有「失敗即短路跳過 WS」語意）；web_h5 的初次種子改走
    best-effort 的 _should_seed_web_h5。
    """
    return backend_kind == "adb" and bool(cfg.get("bootstrap_token", True))


def _has_ws_creds(ip: str) -> bool:
    """間接層（tests monkeypatch 這裡）：是否已有可讀的 WS capture。"""
    from ws_token.bootstrap import has_creds
    return has_creds(ip)


def _adb_reachable(ip: str) -> bool:
    """間接層（tests monkeypatch 這裡）：ip 是否出現在 adb devices 清單。"""
    from runtime_services.ws_runner_service import _is_adb_reachable
    return _is_adb_reachable(ip)


def _should_seed_web_h5(ip: str, backend_kind: str, cfg: dict) -> bool:
    """web_h5 模擬器「缺 capture」時是否該自動冷啟原生 App 撈一次種子。

    Playwright 的頁面回寫（utils.ws_ticket_refresh）只能「刷新既有 capture」、湊不出
    page 讀不到的 uname/plat，無法種第一份。模擬器多半裝有原生 App，故缺檔 + adb 可達
    時冷啟撈一次種子，之後交給頁面回寫維持（has_creds 為真 → 不再冷啟）。adb 不可達的
    純雲端 web 裝置（web-xxx，不在 adb devices）自然被排除，避免每輪空跑 adb_token_login。
    """
    if backend_kind != "web_h5" or not cfg.get("bootstrap_token", True):
        return False
    if _has_ws_creds(ip):
        return False  # 已有種子 → 交給 Playwright refresh，不冷啟
    return _adb_reachable(ip)


def run_ws_phase(ip: str, logger_obj=None, *, now=None,
                 state_dir=None) -> frozenset[str]:
    """跑 WS 階段並回傳本輪 pipeline 的 skip-set；任何失敗回空集合。

    WS 階段可被「開啟瀏覽器」請求即時中斷（``should_abort`` 輪詢
    ``bot_state.has_pending_web_launch_request``）：中斷時把已實質完成的任務寫進
    持久化 ledger，主迴圈讓位去開瀏覽器；使用者用完重新上線，下一輪 WS 階段讀
    ledger 只續做未完成的任務（``skip_tasks``）。完整跑完即清空 ledger。
    ``now`` / ``state_dir`` 供測試注入（預設 ``time.time()`` / 預設 ws_state 路徑）。
    """
    log = logger_obj or logger
    # 交接訊號必須早於設定讀取重置；設定損壞或讀取例外時也不可沿用上一輪 True。
    try:
        import bot_state
        bot_state.set_ws_h5_handoff_ok(ip, False)
    except Exception:  # noqa: BLE001
        log.debug("[%s] 重置 WS→H5 交接訊號失敗（忽略）", ip, exc_info=True)
    device_cfg = config_manager.get_device_config(ip)
    cfg = device_cfg.get("ws_token") or {}
    if not cfg.get("enabled", False):
        return frozenset()
    # 本輪 WS 登入結果訊號（Phase D1 skip-browser 用）：進入「已嘗試的 WS 階段」即先
    # 重置 False，只有稍後確認登入成功才設 True → 任何失敗/例外/提前 return 都留 False，
    # 讀到的永遠是本輪結果（不殘留上一輪）。best-effort，寫失敗不影響 WS 階段。
    try:
        import bot_state
        bot_state.set_ws_login_ok(ip, False)
    except Exception:  # noqa: BLE001
        log.debug("[%s] 重置 WS 登入訊號失敗（忽略）", ip, exc_info=True)
    # kungfu_guess 真相在裝置層 flat ws_token_kungfu_guess；cfg 是 ws_token 巢狀
    # 沒這欄 → 折入,讓 _run_device 統一從 cfg 取（與 ws_runner_service 一致）。
    if "kungfu_guess" not in cfg:
        _kf = device_cfg.get("ws_token_kungfu_guess")
        if _kf is not None:
            cfg = {**cfg, "kungfu_guess": bool(_kf)}
    # 舊裝置設定把 mining 放在頂層 ws_token_mining；WS-first 只讀巢狀 cfg，
    # 導致全地圖礦點導向從未執行。新巢狀 ws_token.mining 若存在則維持優先。
    if "mining" not in cfg:
        _mining = device_cfg.get("ws_token_mining")
        if _mining is not None:
            cfg = {**cfg, "mining": _mining}
    # 裝置層主/shadow planner 設定注入 mining 子設定（copy-on-write：DeviceConfig
    # 與巢狀 dict 可能被 cache，不可就地改）。預設 v1 / shadow 關閉。
    _mining_cfg = dict(cfg.get("mining") or {})
    if _mining_cfg:
        _mining_cfg["planner_version"] = str(
            device_cfg.get("mining_planner_version", "v1") or "v1").strip().lower()
        _mining_cfg["shadow_planner_version"] = str(
            device_cfg.get("mining_shadow_planner_version", "") or "").strip().lower()
        cfg = {**cfg, "mining": _mining_cfg}
    # 所有帳號都是真人 → 每台都在自己的 WS 登入「之前」先等真人下線（WS 登入會異地
    # 登入踢掉真人正在玩的 session）。涵蓋正常 WS 與離線 fallback 兩條路（都走本函式）。
    # human_played(手機主帳號) 的「觀察者看不到」採無限等；其他 best-effort 放行。
    _wait_until_human_offline(
        ip, log, human_played=bool(device_cfg.get("human_played")))

    backend_kind = str(device_cfg.get("backend", "adb")).strip().lower()
    can_bootstrap = _should_bootstrap(backend_kind, cfg)
    started = time.time()
    now = started if now is None else now

    def _should_abort() -> bool:
        """強制休眠 = 最高優先中斷（任何後端；peek 不消費，信號留給主迴圈轉成
        force_sleep 睡眠語意）。暫停次之（任何後端；abort 後主迴圈在開瀏覽器前
        block 於 check_pause，恢復後讀 ledger 續做）。開瀏覽器請求再次之，僅
        web_h5（adb 沒有瀏覽器可開，不中斷）。讀失敗視為不中斷。"""
        try:
            import bot_state
            if bot_state.has_pending_force_sleep(ip):
                return True
            if bot_state.is_paused(ip):
                return True
            if backend_kind != "web_h5":
                return False
            return bool(bot_state.has_pending_web_launch_request(ip))
        except Exception:  # noqa: BLE001
            return False

    # 續做：讀有效 ledger（中斷後 30 分鐘內、同日）→ 跳過已完成的任務。
    prior_done = _load_resume_done(ip, now, log, state_dir)
    skip_tasks = prior_done - _RESUME_EXEMPT
    if skip_tasks:
        log.info("[%s] WS 續做：跳過先前已完成 %s", ip, sorted(skip_tasks))

    def _progress(name: str, status: str, detail: str = "") -> None:
        """逐任務回報 dashboard step + 裝置 log（runner 端已保證不會炸 run）。"""
        label = _ws_task_label(name)
        if status == "start":
            step = f"WS 任務執行中: {label}"
            log.info("[%s] WS 任務開始: %s", ip, label)
        elif status == "ok":
            step = f"WS 任務完成: {label}"
            if detail:
                log.info("[%s] WS 任務完成: %s（%s）", ip, label, detail)
            else:
                log.info("[%s] WS 任務完成: %s", ip, label)
        elif status == "progress":
            step = f"WS 開神燈 ({detail})"
            log.info("[%s] WS 開神燈進度: %s", ip, detail)
        else:
            step = f"WS 任務失敗: {label}"
            log.warning("[%s] WS 任務失敗: %s (%s)", ip, label, detail)
        try:
            import bot_state
            bot_state.update_state(ip, task="WS 階段", step=step)
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響任務
            log.debug("[%s] WS 階段 update_state 失敗", ip, exc_info=True)

    if can_bootstrap and not _bootstrap_token(ip, log, force=False):
        return frozenset()

    # web_h5 模擬器初次種子（best-effort）：缺 capture + adb 可達才冷啟原生 App 撈一次，
    # 種完交給 Playwright 頁面回寫維持，之後 has_creds 為真 → 不再冷啟。失敗只 log，往下
    # 走 → _run_device 的 load_creds 會再失敗 → 自動降級 Playwright（行為同舊）。
    # ponytail: 缺 App 的 adb 可達 web_h5 會每輪重試（adb_token_login ~2min）；模擬器實務
    # 上都有 App，種一次即止，故不加退避；若日後出現空試 hang 再補「種子嘗試退避」。
    if _should_seed_web_h5(ip, backend_kind, cfg):
        if _bootstrap_token(ip, log, force=False):
            log.info("[%s] web_h5 WS 種子成功，本輪續跑 WS", ip)
        else:
            log.info("[%s] web_h5 WS 種子失敗，本輪降級 Playwright", ip)

    def _run_once():
        return _run_device(ip, cfg, _progress,
                           should_abort=_should_abort, skip_tasks=skip_tasks)

    try:
        report = _run_once()
    except Exception as exc:  # noqa: BLE001 — WS 階段失敗必須降級、不能炸 wake loop
        log.warning("[%s] WS 階段失敗，本輪 Playwright 全跑: %s", ip, exc,
                    exc_info=True)
        return frozenset()
    if not report.login_ok:
        if can_bootstrap:
            log.warning("[%s] WS 登入失敗 (%s)，嘗試重撈 token 後重跑一次",
                        ip, report.errors.get("login"))
            if _bootstrap_token(ip, log, force=True):
                try:
                    report = _run_once()
                except Exception as exc:  # noqa: BLE001
                    log.warning("[%s] WS 重撈後仍失敗，本輪 Playwright 全跑: %s",
                                ip, exc, exc_info=True)
                    return frozenset()
        if report.login_ok:
            log.info("[%s] WS 重撈 token 後登入成功", ip)
        else:
            log.warning("[%s] WS 登入失敗 (%s)，本輪 Playwright 全跑",
                        ip, report.errors.get("login"))
            return frozenset()

    # 走到這裡 report.login_ok 必為真（前面所有 not-login_ok 路徑皆已 return）。
    # 記錄本輪 WS 登入成功訊號供 Phase D1 skip-browser 判斷。best-effort。
    try:
        import bot_state
        bot_state.set_ws_login_ok(ip, True)
    except Exception:  # noqa: BLE001
        log.debug("[%s] 設定 WS 登入成功訊號失敗（忽略）", ip, exc_info=True)

    # effective_done = 本輪實質完成 ∪ 先前 ledger 已完成（resume 跳過的那些也算數），
    # 確保續做跳過的任務同樣讓 daily_pipeline 正確略過，不會用瀏覽器重做一次。
    effective_done = prior_done | _substantive_done(report)
    skips: set[str] = set()
    for key, names in WS_TO_PIPELINE_SKIPS.items():
        if key in effective_done:
            skips.update(names)
    # 條件式：WS farm 沒配種子就只收成 → Playwright 農場照跑補種
    if "farm" in effective_done and (cfg.get("farm") or {}).get("seed_id"):
        skips.add("農場任務")
    # 條件式：有配掃蕩才算把萬神試煉做完
    if "dungeon" in effective_done and cfg.get("dungeon_sweeps"):
        skips.add("萬神試煉")

    # 續做 ledger：被中斷 → 寫入已完成清單 + 狀態回報；完整跑完 → 清空（下一個
    # 喚醒週期回到全新狀態，避免跨喚醒誤跳會 regen 的任務）。
    if report.aborted:
        _save_resume(ip, effective_done, now, log, state_dir)
        try:
            import bot_state
            bot_state.update_state(
                ip, task="WS 階段",
                step=f"WS 已中斷(強制休眠/開啟瀏覽器)，已完成 {len(effective_done)} 項，"
                     f"下輪續做")
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響流程
            log.debug("[%s] WS 中斷狀態回報失敗", ip, exc_info=True)
        log.info("[%s] WS 階段被中斷（強制休眠/開啟瀏覽器），已完成 %s，待續",
                 ip, sorted(effective_done))
    else:
        _clear_resume(ip, log, state_dir)

    _record_daily_done(ip, skips, log)
    _record_ws_daily_progress(ip, report, effective_done, log)

    # errors 帶原因（dict 而非只列任務名）→ 一行 summary 即可排查，不必往上捲找 WARNING。
    log.info(
        "[%s] WS 階段完成 (%.1fs): ok=%s errors=%s kicked=%s aborted=%s skip=%s",
        ip, time.time() - started, list(report.tasks), dict(report.errors),
        report.kicked, report.aborted, sorted(skips))
    if not report.kicked and not report.aborted:
        try:
            import bot_state
            bot_state.set_ws_h5_handoff_ok(ip, True)
        except Exception:  # noqa: BLE001
            log.debug("[%s] 設定 WS→H5 健康交接訊號失敗（忽略）", ip, exc_info=True)
    return frozenset(skips)
