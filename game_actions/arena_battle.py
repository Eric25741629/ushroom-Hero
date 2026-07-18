# -*- coding: utf-8 -*-
"""競技場每日挑戰：依 arena_battle_mode 走 animation / local_sim / pure_ws。"""
from __future__ import annotations

import time
from typing import Any, Optional

import img_tools
from utils.logging_utils import logger

from battle_calc.config import coerce_arena_gap_sec, coerce_battle_mode
from battle_calc.runner import enforce_gap


def _page(d) -> Any:
    return getattr(d, "_page", None)


def _run_animation_fights(d, ip: str, n: int, gap_sec: float) -> None:
    last = 0.0
    for i in range(n):
        last = enforce_gap(last, gap_sec)
        logger.info(f"[{ip}] 競技場挑戰 {i+1}/{n} (animation)")
        img_tools.click_str_by_server(d, "挑戰", y_range=(592, 674), wait_timeout=5)
        start_time = time.time()
        while True:
            time.sleep(1)
            check_str = img_tools.wait_for_any_text(
                d, ["勝利", "對決", "跳過"], y_range=(100, 800), timeout=3
            )
            if check_str == "跳過":
                time.sleep(1)
            elif check_str in ("勝利", "對決"):
                logger.info(f"[{ip}] 挑戰 {i+1} 完成")
                break
            if time.time() - start_time > 60:
                logger.warning(f"[{ip}] 挑戰 {i+1} 逾時，強制結束")
                break
        last = time.monotonic()


def _run_local_sim_fights(d, ip: str, n: int, gap_sec: float) -> bool:
    """H5：UI 點挑戰 + 本頁 BattleMainServer + 回 result（無動畫等待）。"""
    page = _page(d)
    if page is None:
        logger.warning(f"[{ip}] local_sim 需要 web_h5 page → fallback animation")
        return False
    from battle_calc.page_hooks import clear_combat, install_hooks, set_block_result
    from battle_calc.runner import run_sim_path

    install_hooks(page)
    last = 0.0
    for i in range(n):
        last = enforce_gap(last, gap_sec)
        logger.info(f"[{ip}] 競技場挑戰 {i+1}/{n} (local_sim)")
        clear_combat(page, "arena")
        set_block_result(page, True)
        img_tools.click_str_by_server(d, "挑戰", y_range=(592, 674), wait_timeout=5)
        out = run_sim_path(
            page, "arena", "local_sim", ip=ip, timeout_s=25.0, clear_first=False
        )
        if not out.get("ok"):
            logger.warning(f"[{ip}] local_sim 失敗: {out.get('err')} → 中止改 animation 收尾")
            return False
        try:
            page.evaluate(
                """() => {
                  const c = document.querySelector('canvas');
                  if (!c) return;
                  const r = c.getBoundingClientRect();
                  const x = r.left + r.width/2, y = r.top + r.height*0.7;
                  for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                    c.dispatchEvent(new MouseEvent(t, {bubbles:true, clientX:x, clientY:y}));
                  }
                }"""
            )
        except Exception:
            pass
        time.sleep(0.5)
        last = time.monotonic()
    return True


def _run_pure_ws_fights(ip: str, n: int, gap_sec: float, cfg: Optional[dict] = None) -> bool:
    """純 WS 開戰 + B 秒算（預設全新無 profile 瀏覽器）。"""
    try:
        from battle_calc.config import get_battle_calc_global
        from ws_token.arena_fight import resolve_b_cdp_port
        from ws_token.client import WSGameClient
        from ws_token.creds import load_creds
        from ws_token import arena_fight as af

        bc = get_battle_calc_global()
        b_mode = str(bc.get("mode") or "ephemeral").strip().lower()
        prefer_ephemeral = b_mode != "cdp"
        cdp = resolve_b_cdp_port(
            device_cdp=(cfg or {}).get("web_debug_port"),
            calc_cdp=bc.get("cdp_port") if bc.get("enabled") else None,
        )
        if not prefer_ephemeral and not cdp:
            logger.warning(f"[{ip}] pure_ws b_mode=cdp 但無 CDP port")
            return False
        creds = load_creds(ip)
        client = WSGameClient(creds)
        client.connect()
        try:
            report = af.run_with_b(
                client,
                fights=n,
                gap_sec=gap_sec,
                prefer_ephemeral=prefer_ephemeral,
                cdp_port=cdp,
                game_url=bc.get("game_url"),
                headless=bool(bc.get("headless", True)),
                ready_timeout_sec=float(bc.get("ready_timeout_sec") or 90),
            )
            logger.info(
                f"[{ip}] pure_ws arena success={report.success} fought={report.fought} "
                f"wins={report.wins} err={report.error}"
            )
            return bool(report.success)
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"[{ip}] pure_ws 競技場失敗: {e}")
        return False


def run_arena_challenges(d, ip: str, cfg: Optional[dict] = None) -> None:
    """進入競技場並打 3 場（模式由 cfg.arena_battle_mode 決定）。"""
    import config_manager

    cfg = cfg or config_manager.get_device_config_dict(ip)
    mode = coerce_battle_mode(cfg.get("arena_battle_mode", "animation"))
    gap = coerce_arena_gap_sec(cfg.get("arena_fight_gap_sec", 7))
    n = 3

    # 進競技場 UI（pure_ws 不需 UI，但進場可刷新對手；pure_ws 直接協議）
    if mode != "pure_ws":
        img_tools.click_str_by_server(d, "競技場", shift_y=-20, x_range=(0, 160))
        time.sleep(0.5)
        img_tools.click_str_by_server(d, "挑戰", wait_timeout=5, y_range=(789, 855))

    ok = False
    if mode == "pure_ws":
        logger.info(f"[{ip}] 競技場 pure_ws（B=ephemeral 全新瀏覽器，無 profile）")
        ok = _run_pure_ws_fights(ip, n, gap, cfg)
        if ok:
            return
    elif mode == "local_sim":
        ok = _run_local_sim_fights(d, ip, n, gap)
    elif mode == "remote_calc":
        # 與 local_sim 相同入口，sim 走 remote；失敗 fallback local
        page = _page(d)
        if page is None:
            ok = False
        else:
            from battle_calc.page_hooks import install_hooks, set_block_result
            from battle_calc.runner import run_sim_path

            install_hooks(page)
            last = 0.0
            ok = True
            for i in range(n):
                last = enforce_gap(last, gap)
                logger.info(f"[{ip}] 競技場挑戰 {i+1}/{n} (remote_calc)")
                set_block_result(page, True)
                img_tools.click_str_by_server(d, "挑戰", y_range=(592, 674), wait_timeout=5)
                out = run_sim_path(page, "arena", "remote_calc", ip=ip, timeout_s=25.0)
                set_block_result(page, False)
                if not out.get("ok"):
                    ok = False
                    break
                last = time.monotonic()

    if mode == "animation" or not ok:
        if mode != "animation":
            logger.warning(f"[{ip}] 競技場 mode={mode} 失敗 → animation fallback")
            if mode == "pure_ws":
                # pure_ws 失敗時嘗試進 UI
                try:
                    img_tools.click_str_by_server(d, "競技場", shift_y=-20, x_range=(0, 160))
                    time.sleep(0.5)
                    img_tools.click_str_by_server(d, "挑戰", wait_timeout=5, y_range=(789, 855))
                except Exception:
                    pass
        _run_animation_fights(d, ip, n, gap)

    # 收尾
    try:
        img_tools.click_str_by_server(d, "刷新", y_range=(711, 782), shift_y=60)
        time.sleep(1)
        img_tools.click_str_by_server(
            d, "記錄", y_range=(831, 865), x_range=(437, 521), shift_y=60, wait_timeout=5
        )
        time.sleep(1)
    except Exception:
        pass
