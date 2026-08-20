# -*- coding: utf-8 -*-
"""競技場每日挑戰：H5 端走 animation / local_sim / remote_calc。

``pure_ws`` 由 ``game_actions.ws_phase`` 的 WS-first 階段負責；本模組只在
WS 未完成競技場時，於 H5 端處理剩餘場次，不再重新建立第二條 WS 連線。
"""
from __future__ import annotations

import time
from typing import Any, Optional

import img_tools
from utils.logging_utils import logger

from battle_calc.config import (
    coerce_arena_daily_fights,
    coerce_arena_gap_sec,
    coerce_battle_mode,
)
from battle_calc.runner import enforce_gap


def _page(d) -> Any:
    return getattr(d, "_page", None)


def _cocos_arena(d):
    page = _page(d)
    if getattr(d, "backend_kind", None) != "web_h5" or page is None:
        return None
    from game_actions.cocos_arena import CocosArena
    return CocosArena(page)


def _is_h5(d) -> bool:
    return getattr(d, "backend_kind", None) == "web_h5"


def _h5_unavailable(ip: str, action: str, reason: str) -> bool:
    """H5 Cocos 狀態不可用時停止，不把它轉成 OCR 未命中。"""
    logger.warning(
        f"[{ip}] H5_STATE_UNAVAILABLE action={action} reason={reason}; "
        "停止競技場，禁止 OCR fallback"
    )
    return False


def _run_animation_fights(d, ip: str, n: int, gap_sec: float, *, use_cocos: bool = True) -> bool:
    cocos = _cocos_arena(d) if use_cocos else None
    cocos_path_active = cocos is not None
    last = 0.0
    for i in range(n):
        last = enforce_gap(last, gap_sec)
        logger.info(f"[{ip}] 競技場挑戰 {i+1}/{n} (animation)")
        if cocos is not None:
            if not cocos.challenge():
                if _is_h5(d):
                    return _h5_unavailable(ip, "challenge", f"fight={i + 1}")
                logger.warning(f"[{ip}] Cocos 挑戰未驗證，該場退回 OCR")
                cocos = None
                cocos_path_active = False
        if cocos is None:
            img_tools.click_str_by_server(d, "挑戰", y_range=(592, 674), wait_timeout=5)
        start_time = time.time()
        while True:
            if cocos is not None:
                check_str = cocos.wait_result(timeout=max(1, 60 - (time.time() - start_time)))
                if check_str is None:
                    if _is_h5(d):
                        return _h5_unavailable(ip, "wait_result", f"fight={i + 1}")
                    logger.warning(f"[{ip}] Cocos 結果未驗證，該場退回 OCR")
                    cocos = None
                    cocos_path_active = False
                    check_str = img_tools.wait_for_any_text(
                        d, ["勝利", "對決", "跳過"], y_range=(100, 800), timeout=3
                    )
            else:
                if _is_h5(d):
                    return _h5_unavailable(ip, "animation", f"fight={i + 1}")
                time.sleep(1)
                check_str = img_tools.wait_for_any_text(
                    d, ["勝利", "對決", "跳過"], y_range=(100, 800), timeout=3
                )
            if check_str == "跳過":
                time.sleep(1)
            elif check_str in ("勝利", "對決", "失敗"):
                # Cocos 結果窗同樣會顯示「失敗」；它代表本場已結束，
                # 不應再等待到逾時或退回 OCR。收尾仍由 CDP 關閉彈窗。
                logger.info(f"[{ip}] 挑戰 {i+1} 完成 (結果={check_str})")
                break
            if time.time() - start_time > 60:
                logger.warning(f"[{ip}] 挑戰 {i+1} 逾時，強制結束")
                break
        last = time.monotonic()
    return cocos_path_active


def _run_local_sim_fights(
    d, ip: str, n: int, gap_sec: float, *, use_cocos: bool = True
) -> bool:
    """H5：UI 點挑戰 + 本頁 BattleMainServer + 回 result（無動畫等待）。"""
    page = _page(d)
    if page is None:
        if _is_h5(d):
            return _h5_unavailable(ip, "local_sim", "page_missing")
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
        cocos = _cocos_arena(d) if use_cocos else None
        if cocos is not None:
            if not cocos.challenge(occurrence=0):
                return False
        else:
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
        fought_before, remaining = af.daily_fight_plan(ip, n)
        if remaining == 0:
            logger.info(f"[{ip}] pure_ws arena 今日已達標 {fought_before}/{n}")
            return True
        creds = load_creds(ip)
        client = WSGameClient(creds)
        client.connect()
        try:
            report = af.run_with_b(
                client,
                fights=remaining,
                gap_sec=gap_sec,
                prefer_ephemeral=prefer_ephemeral,
                cdp_port=cdp,
                game_url=bc.get("game_url"),
                headless=bool(bc.get("headless", True)),
                ready_timeout_sec=float(bc.get("ready_timeout_sec") or 90),
                device=ip,
            )
            logger.info(
                f"[{ip}] pure_ws arena success={report.success} fought={report.fought} "
                f"today={fought_before + report.fought}/{n} wins={report.wins} "
                f"err={report.error}"
            )
            return fought_before + report.fought >= n
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        logger.exception(f"[{ip}] pure_ws 競技場失敗: {e}")
        return False


def _finish_with_ocr(d, ip: str) -> bool:
    """用既有 OCR 錨點離場，並在 web_h5 上驗證已回到主頁。

    OCR 文字只作為舊版底部離場按鈕的定位錨點；位移點擊不會直接按下
    「記錄」文字本身，避免重新打開對戰記錄彈窗。
    """
    if _is_h5(d):
        return _h5_unavailable(ip, "finish", "cocos_finish_unavailable")
    try:
        refreshed = img_tools.click_str_by_server(
            d, "刷新", y_range=(711, 782), shift_y=60, wait_timeout=5
        )
        time.sleep(1)
        exited = img_tools.click_str_by_server(
            d, "記錄", y_range=(831, 865), x_range=(437, 521),
            shift_y=60, wait_timeout=5,
        )
        time.sleep(1)
        if not (refreshed and exited):
            return False
        page = _page(d)
        if page is None:
            return True
        from utils.cocos_navigator import CocosNavigator

        return CocosNavigator(page).current_view() == "main"
    except Exception:
        return False


def run_arena_challenges(d, ip: str, cfg: Optional[dict] = None) -> bool:
    """進入競技場並打每日目標場次（預設 9，可依裝置覆寫）。"""
    import config_manager

    cfg = cfg or config_manager.get_device_config_dict(ip)
    mode = coerce_battle_mode(cfg.get("arena_battle_mode", "animation"))
    if mode == "pure_ws":
        # pure_ws 是 WS-first 階段的唯一責任。能走到 H5 代表 WS 未完成、
        # 或 WS 階段未啟用；此處只補做剩餘競技場，避免同帳號再次登入 WS。
        logger.info(
            f"[{ip}] 競技場 pure_ws 已由 WS 階段處理，H5 剩餘流程改走 animation"
        )
        mode = "animation"
    gap = coerce_arena_gap_sec(cfg.get("arena_fight_gap_sec", 7))
    n = coerce_arena_daily_fights(cfg.get("arena_daily_fights", 9))
    cocos = None
    cocos_path_active = False

    # H5 只處理 WS 未完成的剩餘競技場；pure_ws 已在上方降級成 animation，
    # 因此這裡一定先進入 H5/Cocos UI，不會再建立 WS client。
    if mode != "pure_ws":
        cocos = _cocos_arena(d)
        entered_cocos = cocos is not None and cocos.enter()
        if not entered_cocos:
            if cocos is not None:
                if _is_h5(d):
                    return _h5_unavailable(ip, "enter", "arena_panel_not_verified")
                logger.warning(f"[{ip}] 競技場 Cocos 進場未驗證，退回 OCR")
            elif _is_h5(d):
                return _h5_unavailable(ip, "enter", "page_missing")
            img_tools.click_str_by_server(d, "競技場", shift_y=-20, x_range=(0, 160))
            time.sleep(0.5)
            img_tools.click_str_by_server(d, "挑戰", wait_timeout=5, y_range=(789, 855))
            cocos = None
        else:
            cocos_path_active = True

    ok = False
    if mode == "pure_ws":
        logger.info(f"[{ip}] 競技場 pure_ws（B=ephemeral 全新瀏覽器，無 profile）")
        ok = _run_pure_ws_fights(ip, n, gap, cfg)
        if ok:
            return True
    elif mode == "local_sim":
        ok = _run_local_sim_fights(d, ip, n, gap, use_cocos=cocos_path_active)
        if not ok:
            cocos_path_active = False
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
                cocos = _cocos_arena(d) if cocos_path_active else None
                if cocos is not None:
                    if not cocos.challenge(occurrence=0):
                        ok = False
                        cocos_path_active = False
                        break
                else:
                    img_tools.click_str_by_server(d, "挑戰", y_range=(592, 674), wait_timeout=5)
                out = run_sim_path(page, "arena", "remote_calc", ip=ip, timeout_s=25.0)
                set_block_result(page, False)
                if not out.get("ok"):
                    ok = False
                    cocos_path_active = False
                    break
                last = time.monotonic()

    if mode == "animation" or not ok:
        if mode != "animation":
            logger.warning(f"[{ip}] 競技場 mode={mode} 失敗 → animation fallback")
            if mode == "pure_ws":
                # pure_ws 失敗後，web_h5 優先重新接回 Cocos/JS UI 路徑。
                # 這裡若直接走 OCR，動畫等待結果會在每場持續輪詢 OCR。
                cocos = _cocos_arena(d)
                entered_cocos = cocos is not None and cocos.enter()
                if entered_cocos:
                    cocos_path_active = True
                else:
                    if cocos is not None:
                        if _is_h5(d):
                            return _h5_unavailable(
                                ip, "fallback_enter", "arena_panel_not_verified"
                            )
                        logger.warning(f"[{ip}] pure_ws fallback 的 Cocos 進場未驗證，退回 OCR")
                    cocos = None
                    cocos_path_active = False
                    img_tools.click_str_by_server(d, "競技場", shift_y=-20, x_range=(0, 160))
                    time.sleep(0.5)
                    img_tools.click_str_by_server(d, "挑戰", wait_timeout=5, y_range=(789, 855))
        cocos_path_active = _run_animation_fights(
            d, ip, n, gap, use_cocos=cocos_path_active
        )

    if _is_h5(d) and not cocos_path_active and cocos is not None:
        # Cocos 失敗也要盡力把已開啟的競技場／popup 收乾淨；回傳值仍是
        # False，讓上層知道本輪不可用，不會把清理成功誤當成戰鬥成功。
        try:
            cocos.finish()
        except Exception:
            logger.debug(f"[{ip}] H5 競技場失敗後收尾例外", exc_info=True)

    # 收尾
    if cocos_path_active and cocos is not None:
        return bool(cocos.finish())
    if _is_h5(d):
        return _h5_unavailable(ip, "finish", "cocos_path_inactive")
    return _finish_with_ocr(d, ip)
