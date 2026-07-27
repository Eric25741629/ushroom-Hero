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
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Add repo root to path so `battle`, `battle_calc` etc. are importable
import os as _os
_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

GAME_HOST = "mushroomh5.acenetgame.com"


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
    args = ap.parse_args()

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
