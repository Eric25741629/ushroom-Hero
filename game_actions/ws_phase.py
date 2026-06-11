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
    "mining": ("挖礦任務",),
}


def _run_device(ip: str, cfg: dict, progress=None):
    """間接層：lazy import + 參數展開，tests monkeypatch 這裡。"""
    from ws_token.runner import run_device
    return run_device(
        ip,
        progress=progress,
        spend=bool(cfg.get("spend", False)),
        open_lamp=bool(cfg.get("open_lamp", False)),
        farm_config=cfg.get("farm") or None,
        dungeon_sweeps=cfg.get("dungeon_sweeps") or None,
        carpark_target=cfg.get("carpark_target") or None,
        couple_gifts=bool(cfg.get("couple_gifts", True)),
        workshop_rotate=bool(cfg.get("workshop_rotate", True)),
        forge_ring=bool(cfg.get("forge_ring", False)),
        mining_config=cfg.get("mining") or None,
    )


def run_ws_phase(ip: str, logger_obj=None) -> frozenset[str]:
    """跑 WS 階段並回傳本輪 pipeline 的 skip-set；任何失敗回空集合。"""
    log = logger_obj or logger
    cfg = config_manager.get_device_config(ip).get("ws_token") or {}
    if not cfg.get("enabled", False):
        return frozenset()
    started = time.time()

    def _progress(name: str, status: str, detail: str = "") -> None:
        """逐任務回報 dashboard step + 裝置 log（runner 端已保證不會炸 run）。"""
        if status == "start":
            step = f"WS 任務執行中: {name}"
            log.info("[%s] WS 任務開始: %s", ip, name)
        elif status == "ok":
            step = f"WS 任務完成: {name}"
            log.info("[%s] WS 任務完成: %s", ip, name)
        else:
            step = f"WS 任務失敗: {name}"
            log.warning("[%s] WS 任務失敗: %s (%s)", ip, name, detail)
        try:
            import bot_state
            bot_state.update_state(ip, task="WS 階段", step=step)
        except Exception:  # noqa: BLE001 — 狀態回報失敗不影響任務
            log.debug("[%s] WS 階段 update_state 失敗", ip, exc_info=True)

    try:
        report = _run_device(ip, cfg, _progress)
    except Exception as exc:  # noqa: BLE001 — WS 階段失敗必須降級、不能炸 wake loop
        log.warning("[%s] WS 階段失敗，本輪 Playwright 全跑: %s", ip, exc,
                    exc_info=True)
        return frozenset()
    if not report.login_ok:
        log.warning("[%s] WS 登入失敗 (%s)，本輪 Playwright 全跑",
                    ip, report.errors.get("login"))
        return frozenset()

    skips: set[str] = set()
    for key, names in WS_TO_PIPELINE_SKIPS.items():
        result = report.tasks.get(key)
        if key not in report.tasks or key in report.errors:
            continue
        if isinstance(result, dict) and "skipped" in result:
            continue  # 任務自己宣告本輪沒做事（如 couple 無伴侶）→ 不替代 Playwright
        skips.update(names)
    # 條件式：WS farm 沒配種子就只收成 → Playwright 農場照跑補種
    if "farm" in report.tasks and (cfg.get("farm") or {}).get("seed_id"):
        skips.add("農場任務")
    # 條件式：有配掃蕩才算把萬神試煉做完
    if "dungeon" in report.tasks and "dungeon" not in report.errors \
            and cfg.get("dungeon_sweeps"):
        skips.add("萬神試煉")

    log.info(
        "[%s] WS 階段完成 (%.1fs): ok=%s errors=%s kicked=%s skip=%s",
        ip, time.time() - started, list(report.tasks), list(report.errors),
        report.kicked, sorted(skips))
    return frozenset(skips)
