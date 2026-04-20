"""Playwright screenshot benchmark — measures latency of each approach.

Usage:
    python benchmark_screenshot.py [--rounds 20] [--url URL]

Logs per-call timing to logs/screenshot_benchmark.log and prints summary.
URL defaults to a local data: URI with a canvas element.
"""
import argparse
import logging
import os
import statistics
import time

LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "screenshot_benchmark.log")
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bench")

CANVAS_HTML = """data:text/html,<!DOCTYPE html><html><body style='margin:0'>
<canvas id='c' width='540' height='960'></canvas>
<script>
var c=document.getElementById('c');var ctx=c.getContext('2d');
ctx.fillStyle='#1a1a2e';ctx.fillRect(0,0,540,960);
ctx.fillStyle='#e94560';ctx.font='40px sans-serif';
ctx.fillText('benchmark',80,480);
</script></body></html>"""


def _measure(label: str, fn, rounds: int) -> list:
    times = []
    log.info(f"--- {label}: {rounds} rounds ---")
    for i in range(rounds):
        t0 = time.perf_counter()
        fn()
        ms = (time.perf_counter() - t0) * 1000
        times.append(ms)
        log.info(f"  [{label}] round {i+1:02d}: {ms:.1f} ms")
    return times


def _summarize(label: str, times: list) -> None:
    log.info(
        f"[{label}] avg={statistics.mean(times):.1f}ms  "
        f"median={statistics.median(times):.1f}ms  "
        f"min={min(times):.1f}ms  max={max(times):.1f}ms  "
        f"stdev={statistics.stdev(times) if len(times)>1 else 0:.1f}ms"
    )


def run(rounds: int, url: str) -> None:
    from playwright.sync_api import sync_playwright

    log.info(f"=== Playwright Screenshot Benchmark  rounds={rounds}  url={url[:80]} ===")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 540, "height": 960})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        # warm-up
        page.screenshot()
        time.sleep(0.1)

        # ── 1. bugged approach (simulated: sleep on attempt==0) ────────────
        def method_bugged():
            time.sleep(5)        # the inverted condition that ran on EVERY first attempt
            page.locator("canvas").first.screenshot(timeout=5000)

        bugged = _measure("1_bugged_with_5s_sleep", method_bugged, min(rounds, 3))
        _summarize("1_bugged_with_5s_sleep", bugged)

        # ── 2. page.screenshot() — full page ──────────────────────────────
        def method_full_page():
            page.screenshot()

        t_full = _measure("2_full_page_screenshot", method_full_page, rounds)
        _summarize("2_full_page_screenshot", t_full)

        # ── 3. locator.screenshot() on canvas ─────────────────────────────
        canvas_locator = page.locator("canvas").first
        def method_canvas_locator():
            canvas_locator.screenshot(timeout=5000)

        t_canvas = _measure("3_canvas_locator_screenshot", method_canvas_locator, rounds)
        _summarize("3_canvas_locator_screenshot", t_canvas)

        # ── 4. evaluate() → canvas.toDataURL (canvas_capture method) ──────
        script = """() => {
            const canvas = document.querySelector('canvas');
            return canvas ? canvas.toDataURL('image/jpeg', 0.85) : null;
        }"""
        def method_canvas_capture():
            import base64, numpy as np, cv2
            result = page.evaluate(script)
            if result and result.startswith("data:"):
                b64 = result.split(",", 1)[1]
                data = base64.b64decode(b64)
                arr = np.frombuffer(data, dtype=np.uint8)
                cv2.imdecode(arr, cv2.IMREAD_COLOR)

        t_capture = _measure("4_canvas_toDataURL_JPEG", method_canvas_capture, rounds)
        _summarize("4_canvas_toDataURL_JPEG", t_capture)

        # ── 5. evaluate() → toDataURL PNG (current canvas_capture impl) ───
        script_png = """() => {
            const canvas = document.querySelector('canvas');
            return canvas ? canvas.toDataURL('image/png') : null;
        }"""
        def method_canvas_capture_png():
            import base64, numpy as np, cv2
            result = page.evaluate(script_png)
            if result and result.startswith("data:"):
                b64 = result.split(",", 1)[1]
                data = base64.b64decode(b64)
                arr = np.frombuffer(data, dtype=np.uint8)
                cv2.imdecode(arr, cv2.IMREAD_COLOR)

        t_png = _measure("5_canvas_toDataURL_PNG", method_canvas_capture_png, rounds)
        _summarize("5_canvas_toDataURL_PNG", t_png)

        # ── 6. fixed approach: locator cached + no sleep ──────────────────
        def method_fixed():
            # This is what the code should look like after the fix:
            # no sleep, locator cached, single _sync check skipped when page is valid
            canvas_locator.screenshot(timeout=5000)

        t_fixed = _measure("6_fixed_no_sleep_cached_locator", method_fixed, rounds)
        _summarize("6_fixed_no_sleep_cached_locator", t_fixed)

        context.close()
        browser.close()

    log.info("=== Benchmark complete ===")
    log.info(f"Full log at: {LOG_PATH}")

    print("\n====== SUMMARY ======")
    for label, times in [
        ("bugged (3 rounds)", bugged),
        ("full_page", t_full),
        ("canvas_locator", t_canvas),
        ("canvas_toDataURL JPEG", t_capture),
        ("canvas_toDataURL PNG", t_png),
        ("fixed (no sleep, cached)", t_fixed),
    ]:
        avg = statistics.mean(times)
        print(f"  {label:<34} avg={avg:6.1f}ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--url", default=CANVAS_HTML)
    args = parser.parse_args()
    run(args.rounds, args.url)
