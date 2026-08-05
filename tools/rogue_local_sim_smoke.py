# -*- coding: utf-8 -*-
"""萬神試煉 local_sim 冒煙測試 — 跑一局，印出每關 sim ms / 勝敗 / 耗時，並估算耗時節省。

用法：
    python tools/rogue_local_sim_smoke.py [--port 9226] [--rounds 1]

連接小寶（7fe98fc6）的 CDP port 9226，attach 既有已登入頁面，
走 rogue_h5.run_rounds(page, rounds=N, mode='local_sim') 實測。
不動 bot_config.json，不會影響正在跑的 bot。
"""
from __future__ import annotations

import argparse
import io
import msvcrt
import os
import sys
import tempfile
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Add repo root to path so `battle`, `battle_calc` etc. are importable
import os as _os
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

GAME_HOST = "mushroomh5.acenetgame.com"


def acquire_run_lock(port: int):
    """同一 CDP port 僅允許一個 smoke，程序退出時 Windows 會自動釋放鎖。"""
    path = os.path.join(tempfile.gettempdir(), f"rogue-local-sim-{port}.lock")
    handle = open(path, "a+b")
    if os.path.getsize(path) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def connect(port: int):
    """連接既有 CDP 瀏覽器，取回 (pw, page)。"""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    page = next(
        p
        for ctx in browser.contexts
        for p in ctx.pages
        if GAME_HOST in (p.url or "") and "pwa-sw" not in (p.url or "")
    )
    return pw, page


def main() -> int:
    ap = argparse.ArgumentParser(description="萬神試煉 local_sim smoke test")
    ap.add_argument("--port", type=int, default=9226, help="CDP port（預設 9226，小寶）")
    ap.add_argument("--rounds", type=int, default=1, help="跑幾局（預設 1）")
    ap.add_argument(
        "--close-loss-only",
        action="store_true",
        help="只關閉目前失敗彈窗並確認消失，不進場、不結算",
    )
    ap.add_argument(
        "--cleanup-active-only",
        action="store_true",
        help="只把未完成局推進到可結算狀態後結束，不開始新局",
    )
    args = ap.parse_args()

    run_lock = acquire_run_lock(args.port)
    if run_lock is None:
        print(f"[smoke] CDP port={args.port} 已有測試執行中，拒絕重複啟動")
        return 3

    print(f"[smoke] connect CDP port={args.port}")
    pw, page = connect(args.port)
    print(f"[smoke] page url: {page.url[:80]}")

    # 把 battle_calc 的詳細 log 掛到 stdout
    from battle import rogue_h5

    # 在 run_rounds 之前 patch battle_loop 來記錄每關耗時
    _orig_battle_loop = rogue_h5.battle_loop
    stage_records: list[dict] = []

    def _timed_battle_loop(page_, shot=None, mode="animation", ip=""):
        nonlocal _orig_battle_loop
        # 包裝成能記錄每關的版本
        # 直接呼叫 _orig_battle_loop 但先計時
        t_start = time.monotonic()
        result = _orig_battle_loop(page_, shot=shot, mode=mode, ip=ip)
        elapsed = time.monotonic() - t_start
        stage_records.append({"fought": result, "elapsed_s": elapsed, "mode": mode})
        return result

    rogue_h5.battle_loop = _timed_battle_loop  # type: ignore[assignment]

    # 確認目前狀態
    try:
        st = rogue_h5.state(page)
        print(f"[smoke] 目前狀態: {st}")
    except Exception as e:
        print(f"[smoke] 讀狀態失敗: {e}")
        pw.stop()
        return 1

    if st == rogue_h5.RESULT_LOSE:
        print("[smoke] 偵測到延遲失敗彈窗，先等待並確認關閉")
        if not rogue_h5.wait_and_close_loss_result(page):
            print("[smoke] 失敗彈窗無法安全關閉，停止測試")
            pw.stop()
            return 4
        st = rogue_h5.state(page)
        print(f"[smoke] 關閉失敗彈窗後狀態: {st}")

    if args.close_loss_only:
        print(f"[smoke] close-loss-only 完成，最終狀態: {st}")
        pw.stop()
        return 0

    if args.cleanup_active_only:
        if st in (rogue_h5.ENTER, rogue_h5.CONFIRM, rogue_h5.REMAKE):
            print(f"[smoke] 未完成局停在 {st}，先推進到可結算 STAGE")
            if not rogue_h5.advance_to_stage(page):
                print("[smoke] 無法安全推進到 STAGE，停止清理")
                pw.stop()
                return 5
            st = rogue_h5.state(page)
        if st == rogue_h5.STAGE:
            print("[smoke] 結束目前未完成局")
            if not rogue_h5.settle_run(page):
                print("[smoke] 未完成局結算失敗")
                pw.stop()
                return 6
            st = rogue_h5.state(page)
        print(f"[smoke] cleanup-active-only 完成，最終狀態: {st}")
        pw.stop()
        return 0

    # 尚未進入萬神狀態時，先清登入後公告、獎勵與 TopView 彈窗，再回主頁。
    # 已在萬神內則不可用通用 popup sweep，避免把 RogueMainView 的結束鈕誤當關閉鈕。
    if st == rogue_h5.UNKNOWN:
        from utils.cocos_navigator import CocosNavigator

        nav = CocosNavigator(page)
        closed = nav.dismiss_blocking_popups()
        nav.goto_main()
        print(f"[smoke] 啟動彈窗清理: closed={closed}, view={nav.current_view()}")
        time.sleep(1.5)
        st = rogue_h5.state(page)
        print(f"[smoke] 清理後狀態: {st}")

    # 若已有進行中的 run（不在 HOME）先結算
    if st not in (rogue_h5.HOME, rogue_h5.UNKNOWN):
        print(f"[smoke] 非 HOME 狀態({st})，先 settle_run + open_home")
        try:
            rogue_h5.settle_run(page)
        except Exception as e:
            print(f"[smoke] settle_run 例外: {e}")
        rogue_h5.open_home(page)
        time.sleep(1.5)
        st = rogue_h5.state(page)
        print(f"[smoke] settle 後狀態: {st}")

    # 若 STAGE 表示開到一半—先直接用 open_home 跳回
    if st != rogue_h5.HOME:
        print(f"[smoke] 嘗試 open_home 強制回到主面板")
        rogue_h5.open_home(page)
        time.sleep(1.5)
        st = rogue_h5.state(page)
        print(f"[smoke] open_home 後狀態: {st}")

    if st != rogue_h5.HOME:
        print(f"[smoke] 仍非 HOME({st})，放棄（請先手動回萬神主面板）")
        pw.stop()
        return 1

    print(f"\n[smoke] === 開始跑 {args.rounds} 局 mode=local_sim ===\n")
    t_total_start = time.monotonic()
    try:
        completed = rogue_h5.run_rounds(
            page,
            rounds=args.rounds,
            mode="local_sim",
            ip="7fe98fc6",
        )
    except Exception as e:
        print(f"[smoke] run_rounds 例外: {e}")
        import traceback
        traceback.print_exc()
        pw.stop()
        return 2
    t_total = time.monotonic() - t_total_start

    print(f"\n[smoke] === 結果 ===")
    print(f"[smoke] 完成局數: {completed}/{args.rounds}")
    print(f"[smoke] 總耗時: {t_total:.1f} s")
    for i, rec in enumerate(stage_records):
        print(
            f"[smoke]   局{i+1}: fought={rec['fought']}關, "
            f"elapsed={rec['elapsed_s']:.1f}s, mode={rec['mode']}"
        )

    # 耗時對比估算
    # animation 模式每關約 2-5s（含動畫），以 _BATTLE_TIMEOUT=90s 為上限
    # local_sim 每關 sim < 0.5s，等關結果窗渲染約 1-2s；實測估算
    anim_per_stage_estimate = 10.0  # 保守估算（秒/關，含動畫轉場）
    if stage_records:
        total_fought = sum(r["fought"] for r in stage_records)
        if total_fought > 0:
            sim_per_stage = t_total / total_fought
            saved_per_stage = max(0, anim_per_stage_estimate - sim_per_stage)
            print(
                f"\n[smoke] 耗時對比估算："
                f"\n  local_sim 平均 {sim_per_stage:.1f}s/關"
                f"\n  animation 估算 ~{anim_per_stage_estimate:.0f}s/關（保守）"
                f"\n  每關節省 ~{saved_per_stage:.1f}s"
                f"\n  {total_fought} 關共省 ~{saved_per_stage * total_fought:.0f}s"
            )
        else:
            print("[smoke] 0 關完成，無法估算耗時")

    pw.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
