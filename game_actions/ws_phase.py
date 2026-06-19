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


def _run_device(ip: str, cfg: dict, progress=None, *,
                should_abort=None, skip_tasks=None):
    """間接層：lazy import + 參數展開，tests monkeypatch 這裡。"""
    from ws_token.runner import run_device
    return run_device(
        ip,
        progress=progress,
        spend=bool(cfg.get("spend", False)),
        open_lamp=bool(cfg.get("open_lamp", False)),
        lamp_percent=cfg.get("lamp_percent", 0),
        lamp_min_keep=cfg.get("lamp_min_keep", 0),
        farm_config=cfg.get("farm") or None,
        dungeon_sweeps=cfg.get("dungeon_sweeps") or None,
        carpark_target=cfg.get("carpark_target") or None,
        carpark_auto=bool(cfg.get("carpark_auto", False)),
        carpark_plan=cfg.get("carpark_plan") or None,
        couple_gifts=bool(cfg.get("couple_gifts", True)),
        workshop_rotate=bool(cfg.get("workshop_rotate", True)),
        forge_ring=bool(cfg.get("forge_ring", False)),
        gacha_config=cfg.get("gacha") or None,
        mining_config=cfg.get("mining") or None,
        should_abort=should_abort,
        skip_tasks=skip_tasks,
    )


def _bootstrap_token(ip: str, log, *, force: bool = False) -> bool:
    """間接層：lazy import，tests monkeypatch 這裡。"""
    from ws_token.bootstrap import bootstrap_token
    return bootstrap_token(ip, logger_obj=log, force=force)


def _should_bootstrap(backend_kind: str, cfg: dict) -> bool:
    """只有 adb+WS-first 需要冷啟 App 撈 token；web_h5 走頁面回寫。"""
    return backend_kind == "adb" and bool(cfg.get("bootstrap_token", True))


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
    device_cfg = config_manager.get_device_config(ip)
    cfg = device_cfg.get("ws_token") or {}
    if not cfg.get("enabled", False):
        return frozenset()
    backend_kind = str(device_cfg.get("backend", "adb")).strip().lower()
    can_bootstrap = _should_bootstrap(backend_kind, cfg)
    started = time.time()
    now = started if now is None else now

    def _should_abort() -> bool:
        """開瀏覽器請求 = 最高優先中斷。僅 web_h5（adb 沒有瀏覽器可開，不中斷）；
        讀失敗視為不中斷。"""
        if backend_kind != "web_h5":
            return False
        try:
            import bot_state
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
        if status == "start":
            step = f"WS 任務執行中: {name}"
            log.info("[%s] WS 任務開始: %s", ip, name)
        elif status == "ok":
            step = f"WS 任務完成: {name}"
            log.info("[%s] WS 任務完成: %s", ip, name)
        elif status == "progress":
            step = f"WS 開神燈 ({detail})"
            log.info("[%s] WS 開神燈進度: %s", ip, detail)
        else:
            step = f"WS 任務失敗: {name}"
            log.warning("[%s] WS 任務失敗: %s (%s)", ip, name, detail)
        try:
            import bot_state
            bot_state.update_state(ip, task="WS 階段", step=step)
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響任務
            log.debug("[%s] WS 階段 update_state 失敗", ip, exc_info=True)

    if can_bootstrap and not _bootstrap_token(ip, log, force=False):
        return frozenset()

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
                step=f"WS 已暫停(開啟瀏覽器)，已完成 {len(effective_done)} 項，"
                     f"重新上線後續做")
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響流程
            log.debug("[%s] WS 中斷狀態回報失敗", ip, exc_info=True)
        log.info("[%s] WS 階段被中斷（開啟瀏覽器），已完成 %s，待續",
                 ip, sorted(effective_done))
    else:
        _clear_resume(ip, log, state_dir)

    _record_daily_done(ip, skips, log)
    _record_ws_daily_progress(ip, report, effective_done, log)

    log.info(
        "[%s] WS 階段完成 (%.1fs): ok=%s errors=%s kicked=%s aborted=%s skip=%s",
        ip, time.time() - started, list(report.tasks), list(report.errors),
        report.kicked, report.aborted, sorted(skips))
    return frozenset(skips)
