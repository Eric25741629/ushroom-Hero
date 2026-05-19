"""驗證開神燈自動化流程是否正常。

流程：
  attach CDP → handle_game_startup_pages 回主頁 → _navigate_to_lamp_page
  → 執行 N 個 tick（讀 count → LampLoopState.tick → 執行動作）
  → 輸出驗證報告：count 有沒有下降、state machine 動作是否合理

使用方式（bot 需在跑，Chrome 需有 web_debug_port）：
  python tools/verify_lamp_via_playwright.py --device emulator-5554 --ticks 30
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

import cv2
import numpy as np
import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from Open_gold_paddle_ocr import (  # noqa: E402
    LAMP_COUNT_ROI,
    get_remaining_lamp_count,
    _navigate_to_lamp_page,
    click_and_wait,
)
from opengold_v2.lamp_loop_state import LampLoopState, LampLoopAction  # noqa: E402
from game_initialization import handle_game_startup_pages  # noqa: E402
from game_actions.reward_manager import reward as _reward_fn  # noqa: E402

OCR_URL = "http://100.64.0.5:5001"
GAME_URL = "https://mushroomh5.acenetgame.com/"
DEFAULT_DEVICE = "emulator-5554"
SCREEN_W = 540
SCREEN_H = 960

logger = logging.getLogger("verify_lamp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── 裝置 shim ─────────────────────────────────────────────────────────────────

class _PlaywrightDeviceShim:
    """模擬 device 介面，讓現有 helper（screenshot/click/press/app_stop）可直接用。"""

    def __init__(self, page, canvas_locator):
        self._page = page
        self._canvas = canvas_locator

    def screenshot(self, format=None):
        data = self._canvas.first.screenshot(timeout=5000)
        arr = np.frombuffer(data, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)

    def click(self, x: int, y: int):
        self._canvas.first.click(position={"x": x, "y": y})

    def press(self, key: str):
        self._page.keyboard.press("Escape" if key == "back" else key)

    def app_stop(self, package: str):
        self._page.goto(GAME_URL, wait_until='domcontentloaded', timeout=30000)
        time.sleep(5)


# ── OCR（用於截圖存檔旁記）────────────────────────────────────────────────────

def ocr_roi(img: np.ndarray, y1: int, y2: int, x1: int, x2: int) -> list:
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        return []
    _, buf = cv2.imencode('.jpg', roi)
    b64 = base64.b64encode(buf).decode('utf-8')
    try:
        r = requests.post(f"{OCR_URL}/ocr", json={"image": b64}, timeout=10)
        if not r.ok:
            return []
        results = r.json().get('ocr_results') or []
        return [it.get('text', '') if isinstance(it, dict) else str(it) for it in results]
    except Exception as exc:
        logger.warning(f"OCR error: {exc}")
        return []


def save_screenshot(img: np.ndarray, path: str):
    ok, buf = cv2.imencode('.jpg', img)
    if ok:
        with open(path, 'wb') as fp:
            fp.write(buf.tobytes())


# ── Config ────────────────────────────────────────────────────────────────────

def _lookup_debug_port(device: str) -> int | None:
    try:
        with open(os.path.join(ROOT, "bot_config.json"), encoding="utf-8-sig") as f:
            cfg = json.load(f)
        return cfg.get("devices", {}).get(device, {}).get("web_debug_port")
    except Exception:
        return None


# ── 驗證核心 ──────────────────────────────────────────────────────────────────

@dataclass
class TickRecord:
    i: int
    t: float
    count: int | None
    action: str
    streak: int
    raw_ocr: list = field(default_factory=list)
    shot: str = ""


def run_lamp_verification(shim: _PlaywrightDeviceShim, ticks: int, tick_sleep: float,
                          out_dir: str, stable_threshold: int = 2,
                          reengage_after_stable: int = 5) -> list[TickRecord]:
    """
    主驗證迴圈：模擬 bot 的 lamp loop，記錄每個 tick 的狀態和動作。
    """
    state = LampLoopState(
        stable_threshold=stable_threshold,
        reengage_after_stable=reengage_after_stable,
    )
    records: list[TickRecord] = []

    print(f"\n{'tick':>4}  {'count':>10}  {'action':<18}  streak  raw_ocr")
    print("-" * 65)

    for i in range(ticks):
        t = time.time()
        img = shim.screenshot(format='opencv')
        count = get_remaining_lamp_count(shim)
        raw_ocr = ocr_roi(img, *LAMP_COUNT_ROI)

        # 檢查是否有裝備彈窗（技能 ROI OCR 是否有 text_to_skill_code 內容）
        skill_roi_texts = ocr_roi(img, 634, 744, 291, 367)
        has_popup = bool(skill_roi_texts)

        action = state.tick(count, has_popup=has_popup)

        shot_path = os.path.join(out_dir, f"tick_{i:03d}.jpg")
        save_screenshot(img, shot_path)

        rec = TickRecord(i=i, t=t, count=count, action=action.name,
                         streak=state.stable_streak, raw_ocr=raw_ocr, shot=shot_path)
        records.append(rec)

        print(f"{i:>4}  {str(count):>10}  {action.name:<18}  {state.stable_streak:>6}  {raw_ocr}")

        # 執行 state machine 指示的動作
        if action == LampLoopAction.REENGAGE_AUTO:
            logger.info(f"[tick {i}] REENGAGE_AUTO: 點擊自動按鈕 + 開始確認")
            click_and_wait(shim, 370, 826, 1)   # 自動按鈕
            click_and_wait(shim, 271, 576, 5)   # 開始確認

        elif action == LampLoopAction.HANDLE_POPUP:
            logger.info(f"[tick {i}] HANDLE_POPUP: 點擊裝備彈窗 (271, 576)")
            shim.click(271, 576)
            time.sleep(2)

        elif action == LampLoopAction.RENAVIGATE:
            logger.info(f"[tick {i}] RENAVIGATE: 重新導航到神燈頁")
            _navigate_to_lamp_page(shim)
            time.sleep(2)

        # WAIT：不動作，等下一個 tick

        time.sleep(tick_sleep)

    return records


def print_report(records: list[TickRecord]):
    print("\n" + "=" * 65)
    print("驗證報告")
    print("=" * 65)

    counts = [r.count for r in records if r.count is not None]
    none_count = sum(1 for r in records if r.count is None)
    action_counts = {}
    for r in records:
        action_counts[r.action] = action_counts.get(r.action, 0) + 1

    print(f"總 tick 數     : {len(records)}")
    print(f"count=None 次數: {none_count} / {len(records)}"
          + (" ← OCR 問題" if none_count > len(records) * 0.3 else ""))

    if counts:
        print(f"count 範圍     : {min(counts)} ~ {max(counts)}")
        diff = counts[0] - counts[-1] if len(counts) >= 2 else 0
        print(f"count 淨下降   : {diff}"
              + (" ← 神燈有被開" if diff > 0 else " ← 神燈未減少，請檢查自動模式"))

    print(f"動作分布       : {action_counts}")

    transitions = [(a, b) for a, b in zip(records, records[1:]) if a.count != b.count and
                   a.count is not None and b.count is not None]
    if transitions:
        print(f"\ncount 變動 ({len(transitions)} 次):")
        for a, b in transitions:
            direction = "下降(開燈)" if b.count < a.count else "上升(被動生成)"
            print(f"  tick {a.i:03d}→{b.i:03d}: {a.count} → {b.count}  [{direction}]")
    else:
        print("\ncount 無任何變動 ← 可能未進入神燈頁 / 自動模式未啟動 / OCR 全為 None")

    # 問題診斷
    print("\n診斷：")
    if none_count == len(records):
        print("  [!] 全部 count=None：OCR server 不通，或未在神燈頁（ROI 對錯位置）")
    elif none_count > len(records) * 0.5:
        print("  [!] 超過半數 None：動畫遮擋頻繁，或 OCR server 不穩定")
    if action_counts.get("RENAVIGATE", 0) == len(records):
        print("  [!] 全部 RENAVIGATE：unreadable_streak 超標，根因是 count 全 None")
    if not transitions:
        print("  [!] count 沒有下降：神燈自動模式可能沒有啟動")
    if action_counts.get("REENGAGE_AUTO", 0) == 0 and action_counts.get("WAIT", 0) > 0:
        print("  [?] 沒有 REENGAGE_AUTO：可能 stable_threshold 設太高，或 tick 數不足")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="神燈自動化流程驗證")
    parser.add_argument('--device', default=DEFAULT_DEVICE)
    parser.add_argument('--debug-port', type=int, default=None)
    parser.add_argument('--ticks', type=int, default=30, help='執行幾個 tick')
    parser.add_argument('--sleep', type=float, default=1.5, help='每 tick 間隔秒數')
    parser.add_argument('--stable-threshold', type=int, default=2)
    parser.add_argument('--reengage-after', type=int, default=5)
    parser.add_argument('--out-dir', default=os.path.join(ROOT, 'logs', '_archive', 'lamp_verify'))
    parser.add_argument('--skip-home', action='store_true', help='跳過回首頁（已在主頁面時）')
    args = parser.parse_args()

    debug_port = args.debug_port or _lookup_debug_port(args.device)
    if not debug_port:
        print(f"找不到 {args.device} 的 web_debug_port，請設定 bot_config.json 或用 --debug-port。")
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    cdp_url = f"http://localhost:{debug_port}"
    print(f"CDP        : {cdp_url}")
    print(f"OCR server : {OCR_URL}")
    print(f"Ticks      : {args.ticks}  sleep={args.sleep}s")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            print(f"[錯誤] 無法連線 {cdp_url}: {e}")
            print("請先啟動 bot，讓 Chrome 以 debug port 開啟後再試。")
            return 1

        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        canvas = page.locator('canvas')
        shim = _PlaywrightDeviceShim(page, canvas)

        # 步驟 1：回主頁面
        if not args.skip_home:
            print("\n--- 步驟 1：回主頁面 ---")

            def _start_game_fn(d, ip):
                page.goto(GAME_URL, wait_until='domcontentloaded', timeout=30000)
                time.sleep(10)

            ok = handle_game_startup_pages(
                d=shim, ip=args.device,
                start_game_fn=_start_game_fn,
                reward_fn=_reward_fn,
            )
            if not ok:
                print("無法確認在主頁面，中止。")
                browser.close()
                return 1

        # 步驟 2：進神燈頁
        print("\n--- 步驟 2：進入神燈頁 ---")
        _navigate_to_lamp_page(shim)
        time.sleep(2)

        # 步驟 3：驗證迴圈
        print("\n--- 步驟 3：開神燈驗證迴圈 ---")
        records = run_lamp_verification(
            shim, args.ticks, args.sleep, args.out_dir,
            stable_threshold=args.stable_threshold,
            reengage_after_stable=args.reengage_after,
        )

        # 步驟 4：報告
        print_report(records)

        summary_path = os.path.join(args.out_dir, 'summary.json')
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump([vars(r) for r in records], f, ensure_ascii=False, indent=2)
        print(f"\n詳細紀錄：{summary_path}")

        browser.close()
        return 0


if __name__ == '__main__':
    sys.exit(main())
