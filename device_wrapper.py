import asyncio
import os
import time
import threading
import bot_state
import logging
import cv2
import numpy as np
from typing import Any, Dict, Iterable, Optional
from PIL import Image
from utils.action_tracker import ActionTraceRecorder
from utils.ws_listener import WSFrameTracker
from runtime_services.device_runtime_service import ForceSleepRequested, WakeLoopInterrupted

logger = logging.getLogger(__name__)
_WEB_DEVICE_LOCK = threading.RLock()
_WEB_DEVICE_REGISTRY: Dict[str, "PlaywrightGameDevice"] = {}

# Per-call screenshot duration above which we emit a per-device WARNING into
# logs/<device>/main.log. Tuneable here; do not pepper magic numbers across
# call sites. 500 ms is the operator-set ceiling for "something's wrong".
_SLOW_SCREENSHOT_MS = 500


def _reset_thread_event_loop() -> None:
    # Playwright sync API rejects start() when the calling thread's event loop
    # reports is_running()==True. A previous _playwright.stop() whose dispatcher
    # fiber didn't fully shut down (e.g. Chrome was force-killed) can leave that
    # flag stuck, which then poisons every subsequent restart in the same thread
    # — exactly the 5554 failure mode in logs/emulator-5554/main.log (~210
    # "Sync API inside the asyncio loop" errors). Installing a fresh loop here
    # guarantees the next sync_playwright().start() sees a clean slate.
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
    except Exception as e:
        logger.debug(f"asyncio.set_event_loop fresh-loop install failed: {e}")


DEFAULT_PLAYWRIGHT_CONTEXT_OPTIONS: Dict[str, Any] = {
    "viewport": {"width": 960, "height": 540},
}


class PlaywrightContextConfig:
    """Playwright `browser.new_context(...)` 的設定容器。

    先把預設值集中管理，後續若要擴充 cookies、locale、user_agent、
    storage_state 等選項，直接從這裡延伸即可。
    """

    def __init__(
        self,
        viewport: Optional[Dict[str, int]] = None,
        device_scale_factor: float = 1.0,
        **extra_options: Any,
    ):
        self.viewport = dict(viewport or DEFAULT_PLAYWRIGHT_CONTEXT_OPTIONS["viewport"])
        self.extra_options = dict(extra_options)

    def to_kwargs(self) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "viewport": dict(self.viewport),
        }
        options.update(self.extra_options)
        return options


class PlaywrightContextAdapter:
    """Playwright context 抽象層。

    目的：
    1. 把 `browser.new_context(viewport={...})` 集中封裝。
    2. 預留 cookies / storage_state / new_page 等操作介面。
    3. 讓上層程式未來可無痛切換到 Playwright。
    """

    def __init__(
        self,
        browser: Any = None,
        context: Any = None,
        config: Optional[PlaywrightContextConfig] = None,
    ):
        self._browser = browser
        self._context = context
        self._config = config or PlaywrightContextConfig()

    @property
    def browser(self) -> Any:
        return self._browser

    @property
    def context(self) -> Any:
        return self._context

    @property
    def config(self) -> PlaywrightContextConfig:
        return self._config

    def bind_browser(self, browser: Any) -> "PlaywrightContextAdapter":
        self._browser = browser
        return self

    def set_context(self, context: Any) -> Any:
        self._context = context
        return context

    def create_context(self, browser: Any = None, **overrides: Any) -> Any:
        browser = browser or self._browser
        if browser is None:
            raise ValueError("Playwright browser 尚未綁定，無法建立 context")

        options = self._config.to_kwargs()
        options.update(overrides)
        context = browser.new_context(**options)
        self._browser = browser
        self._context = context
        return context

    def get_context(self) -> Any:
        if self._context is None:
            raise RuntimeError("Playwright context 尚未初始化")
        return self._context

    def new_page(self) -> Any:
        return self.get_context().new_page()

    def cookies(self) -> Any:
        return self.get_context().cookies()

    def add_cookies(self, cookies: Iterable[Dict[str, Any]]) -> Any:
        return self.get_context().add_cookies(list(cookies))

    def clear_cookies(self) -> Any:
        return self.get_context().clear_cookies()

    def storage_state(self, path: Optional[str] = None) -> Any:
        context = self.get_context()
        if path:
            return context.storage_state(path=path)
        return context.storage_state()

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None


class MonitoredDevice:
    """
    裝置操作包裝類別。
    透過單一介面封裝點擊、滑動、截圖、XPath 點擊等動作，
    方便後續替換不同平台的底層實作。
    """

    def __init__(self, original_d, ip: str):
        self._d = original_d
        self._ip = ip
        self._tracker = ActionTraceRecorder()
        # device_id wires the tracker into auto-body-capture mode so that
        # interesting cmds get sample bodies persisted to
        # logs/_archive/ws_capture/auto/<ip>/ during normal bot runs.
        self._ws_frame_tracker = WSFrameTracker(device_id=ip)

    def _trace(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        meaning: str = "",
    ):
        enriched: Dict[str, Any] = dict(payload or {})
        ws_frames = self._collect_ws_frames()
        if ws_frames:
            enriched["ws_frames"] = ws_frames
            enriched["ws_frames_count"] = len(ws_frames)
        try:
            self._tracker.log(
                device_id=self._ip,
                event_type=event_type,
                source="MonitoredDevice",
                meaning=meaning,
                actor=type(self._d).__name__,
                payload=enriched,
            )
        except Exception as e:
            logger.debug(f"[{self._ip}] action_tracker.log 失敗: {e}")

    def _collect_ws_frames(self) -> list:
        """Drain WS frames captured since the previous _trace.

        Only meaningful for web_h5 backend (PlaywrightGameDevice). For adb
        devices and any error path, returns []. Each entry is
        `{"cmd": int, "dir": "tx"|"rx", "ts": int_ms, "len": int}`.
        """
        d = self._d
        if getattr(d, "backend_kind", None) != "web_h5":
            return []
        page = getattr(d, "_page", None)
        if page is None:
            return []
        try:
            if d.owner_thread_id != threading.get_ident():
                return []
        except Exception:
            return []
        try:
            return self._ws_frame_tracker.drain(page, max_n=50)
        except Exception as e:
            logger.debug(f"[{self._ip}] ws_frame_tracker.drain 失敗: {e}")
            return []

    def _auto_meaning(
        self,
        event_type: str,
        explicit_meaning: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        text = str(explicit_meaning or "").strip()
        if text:
            return text
        try:
            st = (bot_state.get_all_states() or {}).get(self._ip, {})
        except Exception:
            st = {}
        task = str(st.get("task", "") or "").strip()
        step = str(st.get("step", "") or "").strip()
        base = " | ".join([x for x in [task, step] if x]) or "未標註任務"
        p = payload or {}
        if event_type == "xpath_click":
            xpath = str(p.get("xpath", "") or "").strip()
            return f"{base} | XPath點擊: {xpath}" if xpath else f"{base} | XPath點擊"
        if event_type in {"tap", "click"}:
            if "x" in p and "y" in p:
                return f"{base} | 座標點擊: ({p.get('x')}, {p.get('y')})"
            return f"{base} | 座標點擊"
        if event_type == "swipe":
            return f"{base} | 滑動: ({p.get('x0')}, {p.get('y0')}) -> ({p.get('x1')}, {p.get('y1')})"
        if event_type == "screenshot":
            fmt = str(p.get("format", "") or "").strip()
            return f"{base} | 截圖{(' format=' + fmt) if fmt else ''}"
        return f"{base} | {event_type}"

    def _pause_guard(self):
        if bot_state.check_force_sleep(self._ip):
            raise ForceSleepRequested(
                f"[{self._ip}] force sleep requested before device action"
            )
        # check_pause() blocks while paused, but returns truthy (instead of
        # blocking) when a manual web-launch request is pending — its carve-out
        # so live-view takeover can break through a paused device. That request
        # is only consumed at the top of the wake loop, which is unreachable
        # mid-pipeline. If we merely let the action through here, pause is
        # silently defeated (dashboard says "paused" but the bot keeps acting).
        # Instead, unwind to the wake-loop top so handle_pending_web_launch()
        # consumes it; the device then re-blocks on the still-paused state.
        if bot_state.check_pause(self._ip):
            raise WakeLoopInterrupted(
                f"[{self._ip}] web-launch request received while paused; "
                "unwinding to wake loop"
            )
        if bot_state.check_force_sleep(self._ip):
            raise ForceSleepRequested(
                f"[{self._ip}] force sleep requested after pause check"
            )

    def _screen_size(self):
        info = getattr(self._d, "info", {}) or {}
        width = info.get("displayWidth") or info.get("screenWidth") or info.get("width")
        height = (
            info.get("displayHeight") or info.get("screenHeight") or info.get("height")
        )
        return width, height

    def _to_px(self, x, y):
        """支援座標比率或絕對座標。

        若 $0 \le x,y < 1$，視為螢幕比例；否則視為絕對座標。
        """
        if (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and 0 <= x < 1
            and 0 <= y < 1
        ):
            width, height = self._screen_size()
            if width and height:
                return int(width * x), int(height * y)
        return int(x), int(y)

    def tap(self, x, y, *args, **kwargs):
        """統一的點擊入口。"""
        self._pause_guard()
        meaning = str(
            kwargs.pop("trace_meaning", "") or kwargs.pop("trace_purpose", "") or ""
        )
        x, y = self._to_px(x, y)
        payload = {"x": x, "y": y}
        self._trace("tap", payload, meaning=self._auto_meaning("tap", meaning, payload))
        tap_fn = getattr(self._d, "tap", None)
        if callable(tap_fn):
            return tap_fn(x, y, *args, **kwargs)
        return self._d.click(x, y, *args, **kwargs)

    def click(self, x, y, *args, **kwargs):
        """點擊前先檢查是否暫停"""
        meaning = str(
            kwargs.pop("trace_meaning", "") or kwargs.pop("trace_purpose", "") or ""
        )
        payload = {"x": x, "y": y}
        self._trace(
            "click", payload, meaning=self._auto_meaning("click", meaning, payload)
        )
        kwargs["trace_meaning"] = meaning
        return self.tap(x, y, *args, **kwargs)

    def click_pct(self, x_ratio: float, y_ratio: float, *args, **kwargs):
        """以螢幕比例點擊。"""
        return self.tap(x_ratio, y_ratio, *args, **kwargs)

    def gesture_swipe(self, *args, **kwargs):
        self._pause_guard()
        meaning = str(
            kwargs.pop("trace_meaning", "") or kwargs.pop("trace_purpose", "") or ""
        )
        payload = {"args_len": len(args)}
        self._trace(
            "gesture_swipe",
            payload,
            meaning=self._auto_meaning("gesture_swipe", meaning, payload),
        )
        gesture_swipe_fn = getattr(self._d, "gesture_swipe", None)
        if callable(gesture_swipe_fn):
            return gesture_swipe_fn(*args, **kwargs)
        return self._d.swipe(*args, **kwargs)

    def swipe(self, *args, **kwargs):
        self._pause_guard()
        meaning = str(
            kwargs.get("trace_meaning", "") or kwargs.get("trace_purpose", "") or ""
        )

        if len(args) >= 4:
            x0, y0 = self._to_px(args[0], args[1])
            x1, y1 = self._to_px(args[2], args[3])
            rest = args[4:]
            args = (x0, y0, x1, y1, *rest)
            payload = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
            self._trace(
                "swipe", payload, meaning=self._auto_meaning("swipe", meaning, payload)
            )
        return self.gesture_swipe(*args, **kwargs)

    def swipe_pct(self, x0_ratio, y0_ratio, x1_ratio, y1_ratio, *args, **kwargs):
        """以螢幕比例滑動。"""
        return self.swipe(x0_ratio, y0_ratio, x1_ratio, y1_ratio, *args, **kwargs)

    def screenshot(self, *args, **kwargs):
        """截圖前也檢查暫停，確保不會在暫停時瘋狂截圖"""
        self._pause_guard()
        fmt = kwargs.get("format")
        meaning = str(
            kwargs.pop("trace_meaning", "") or kwargs.pop("trace_purpose", "") or ""
        )
        payload = {"format": str(fmt) if fmt is not None else ""}
        self._trace(
            "screenshot",
            payload,
            meaning=self._auto_meaning("screenshot", meaning, payload),
        )
        t0 = time.time()
        result = self._d.screenshot(*args, **kwargs)
        elapsed_ms = (time.time() - t0) * 1000
        try:
            bot_state.record_screenshot_time(self._ip, elapsed_ms)
        except Exception as e:
            logger.debug(f"[{self._ip}] record_screenshot_time 失敗: {e}")
        if elapsed_ms > _SLOW_SCREENSHOT_MS:
            try:
                from utils.logging_utils import get_thread_logger
                get_thread_logger().warning(
                    f"[{self._ip}] slow screenshot: {elapsed_ms:.0f}ms "
                    f"(threshold={_SLOW_SCREENSHOT_MS}ms) | {self._auto_meaning('screenshot', meaning, payload)}"
                )
            except Exception as e:
                logger.debug(f"[{self._ip}] slow-screenshot warning log failed: {e}")
        return result

    def xpath_click(self, xpath_expr, *args, **kwargs):
        self._pause_guard()
        meaning = str(
            kwargs.pop("trace_meaning", "") or kwargs.pop("trace_purpose", "") or ""
        )
        payload = {"xpath": str(xpath_expr)}
        self._trace(
            "xpath_click",
            payload,
            meaning=self._auto_meaning("xpath_click", meaning, payload),
        )
        node = self._d.xpath(xpath_expr)
        click_fn = getattr(node, "click", None)
        if callable(click_fn):
            return click_fn(*args, **kwargs)
        raise AttributeError("XPath node does not support click")

    def press(self, key, *args, **kwargs):
        self._pause_guard()
        press_fn = getattr(self._d, "press", None)
        if callable(press_fn):
            return press_fn(key, *args, **kwargs)
        raise AttributeError("Underlying device does not support press")

    def home(self):
        return self.press("home")

    def back(self):
        return self.press("back")

    def app_stop(self, pkg_name=None, *args, **kwargs):
        """Stop an app. Accepts either positional `pkg_name` or keyword `package_name`."""
        if pkg_name is None:
            pkg_name = kwargs.pop("package_name", None)
        if pkg_name is None:
            raise TypeError(
                "app_stop() missing 1 required positional argument: 'pkg_name'"
            )
        self._trace("app_stop", {"package": str(pkg_name)})
        # Call underlying device; prefer keyword for compatibility
        try:
            return self._d.app_stop(pkg_name, *args, **kwargs)
        except TypeError:
            return self._d.app_stop(package_name=pkg_name, *args, **kwargs)

    def app_start(self, pkg_name=None, *args, **kwargs):
        """Start an app. Accepts either positional `pkg_name` or keyword `package_name`."""
        if pkg_name is None:
            pkg_name = kwargs.pop("package_name", None)
        if pkg_name is None:
            raise TypeError(
                "app_start() missing 1 required positional argument: 'pkg_name'"
            )
        self._trace("app_start", {"package": str(pkg_name)})
        try:
            return self._d.app_start(pkg_name, *args, **kwargs)
        except TypeError:
            return self._d.app_start(package_name=pkg_name, *args, **kwargs)

    def __getattr__(self, name):
        """
        委派 (Delegation):
        如果呼叫的方法在本類別中沒定義 (例如 .xpath, .info, .press)，
        則自動轉發給原始的 u2 設備物件。
        """
        return getattr(self._d, name)

    def __call__(self, *args, **kwargs):
        """
        支援 d(text="...") 這種選擇器語法。
        直接轉發給原始 device 物件。
        """
        # 這裡也可以選擇檢查暫停，視需求而定
        # bot_state.check_pause(self._ip)
        return self._d(*args, **kwargs)

    def sleep(self, seconds: float):
        """
        自定義的可打斷休眠。
        將長時間休眠拆解成小段，每段都檢查暫停標誌。
        """
        end_time = time.time() + seconds

        # 只有大於 5 秒的休眠才記錄 Log，避免洗版
        if seconds > 5:
            bot_state.update_state(self._ip, log=f"休眠 {seconds} 秒...")

        while time.time() < end_time:
            # 隨時檢查暫停
            bot_state.check_pause(self._ip)

            # 檢查是否收到跳過指令
            if bot_state.check_skip_sleep(self._ip):
                bot_state.update_state(self._ip, log="休眠已跳過")
                break

            # 每次休眠一小段，提高反應速度
            time.sleep(0.5)
            if time.time() >= end_time:
                break


class _NoopXPathQuery:
    def __init__(self):
        self.exists = False

    @property
    def info(self):
        return {}

    def click(self, *args, **kwargs):
        return False


def _coerce_jpeg_quality(value: Any) -> Optional[int]:
    """Normalize ``web_screenshot_jpeg_quality`` to an int in [1,100], or None.

    None / 0 / unparseable => None, meaning "keep PNG" (the lossless default).
    Out-of-range values are clamped into [1,100].
    """
    if value is None:
        return None
    try:
        q = int(value)
    except (TypeError, ValueError):
        return None
    if q <= 0:
        return None
    return max(1, min(100, q))


class PlaywrightGameDevice:
    """Sync Playwright wrapper that mimics the minimal uiautomator2 API used by this project."""

    backend_kind = "web_h5"

    def __init__(
        self,
        device_id: str,
        cfg: Optional[Dict[str, Any]] = None,
        logger_obj: Optional[logging.Logger] = None,
    ):
        self.device_id = device_id
        self.cfg = cfg or {}
        self.logger = logger_obj or logger
        self.owner_thread_id = threading.get_ident()

        self.web_url = str(self.cfg.get("web_url") or "").strip()
        self.canvas_selector = (
            str(self.cfg.get("web_canvas_selector") or "canvas").strip() or "canvas"
        )
        self.viewport_width = int(self.cfg.get("web_viewport_width") or 540)
        self.viewport_height = int(self.cfg.get("web_viewport_height") or 960)
        # 截圖方法: 'playwright' (預設) 或 'canvas_capture'
        self.screenshot_method = (
            str(self.cfg.get("web_screenshot_method") or "playwright").strip().lower()
        )
        if self.screenshot_method not in {"playwright", "canvas_capture"}:
            self.screenshot_method = "playwright"
        # 截圖格式: web_screenshot_jpeg_quality 設 1..100 時改用 JPEG 擷取
        # (較快較小), None/0/未設定維持 PNG 預設, 不改變 OCR/CNN 輸入。
        self.screenshot_jpeg_quality = _coerce_jpeg_quality(
            self.cfg.get("web_screenshot_jpeg_quality")
        )
        self.info = {
            "displayWidth": self.viewport_width,
            "displayHeight": self.viewport_height,
            "screenOn": True,
            "backend": "web_h5",
            "serial": self.device_id,
        }
        self._in_game = True

        self._playwright = None
        self._context = None
        self._page = None
        self._closed_by_stop = False
        self._headless_override: Optional[bool] = None
        self._start()

    def _configured_headless(self) -> bool:
        return bool(self.cfg.get("web_headless", False))

    def _effective_headless(self) -> bool:
        if self._headless_override is not None:
            return bool(self._headless_override)
        return self._configured_headless()

    def _start(self):
        _reset_thread_event_loop()
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Playwright is required for web_h5 backend. Please install playwright."
            ) from exc

        profile_dir = (
            str(
                self.cfg.get("web_profile_dir") or "playwright_profile/{device_id}"
            ).strip()
            or "playwright_profile/{device_id}"
        )
        # 每個裝置都使用自己的 profile/cookies 資料夾，避免共享登入狀態。
        # 若設定值包含 {device_id} / {ip}，會先做字串替換；否則會自動在尾端附加 device_id。
        profile_dir = profile_dir.format(device_id=self.device_id, ip=self.device_id)
        if not os.path.normpath(profile_dir).endswith(self.device_id):
            profile_dir = os.path.join(profile_dir, self.device_id)
        if not os.path.isabs(profile_dir):
            profile_dir = os.path.join(os.getcwd(), profile_dir)
        os.makedirs(profile_dir, exist_ok=True)

        channel = str(self.cfg.get("web_channel") or "chrome").strip()
        state_file_raw = (
            str(self.cfg.get("web_state_file") or "auth_state/{device_id}.json").strip()
            or "auth_state/{device_id}.json"
        )
        state_file = state_file_raw.format(device_id=self.device_id, ip=self.device_id)
        if "{device_id}" not in state_file_raw and "{ip}" not in state_file_raw:
            if os.path.basename(state_file).lower() == "auth_state.json":
                state_file = os.path.join(
                    os.path.dirname(state_file), "auth_state", f"{self.device_id}.json"
                )
        if state_file and not os.path.isabs(state_file):
            state_file = os.path.join(os.getcwd(), state_file)

        def _clear_chrome_singleton_locks(target_dir: str) -> None:
            # Stale singleton files can make Chrome exit immediately with profile-in-use errors.
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                fp = os.path.join(target_dir, name)
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception as e:
                    logger.debug(f"chrome singleton-lock cleanup failed (path={fp}): {e}")

        def _build_launch_kwargs(
            target_profile: str, use_channel: bool
        ) -> Dict[str, Any]:
            extra_args = [
                "--disable-blink-features=AutomationControlled",
                "--force-device-scale-factor=1",
                "--high-dpi-support=1",
            ]
            debug_port = self.cfg.get("web_debug_port")
            if debug_port:
                extra_args.append(f"--remote-debugging-port={int(debug_port)}")
            kwargs: Dict[str, Any] = {
                "user_data_dir": target_profile,
                "headless": self._effective_headless(),
                "viewport": {
                    "width": self.viewport_width,
                    "height": self.viewport_height,
                },
                "device_scale_factor": 1.0,
                "ignore_default_args": ["--enable-automation"],
                "args": extra_args,
            }
            if use_channel and channel:
                kwargs["channel"] = channel
            return kwargs

        local_app_data = os.environ.get("LOCALAPPDATA", "")
        fallback_profile_dir = (
            os.path.join(local_app_data, "mushroom_playwright_profiles", self.device_id)
            if local_app_data
            else ""
        )

        self._playwright = sync_playwright().start()
        last_err: Optional[Exception] = None
        launch_attempts = [(profile_dir, True)]
        if fallback_profile_dir and os.path.abspath(
            fallback_profile_dir
        ) != os.path.abspath(profile_dir):
            launch_attempts.append((fallback_profile_dir, True))
        # Last fallback: same profile, but no channel ("chrome") to avoid channel-specific startup issues.
        launch_attempts.append((profile_dir, False))

        for target_profile, use_channel in launch_attempts:
            try:
                os.makedirs(target_profile, exist_ok=True)
                _clear_chrome_singleton_locks(target_profile)
                launch_kwargs = _build_launch_kwargs(
                    target_profile, use_channel=use_channel
                )
                self._context = self._playwright.chromium.launch_persistent_context(
                    **launch_kwargs
                )
                if target_profile != profile_dir:
                    self.logger.warning(
                        f"[{self.device_id}] web_h5 switched profile dir to fallback: {target_profile}"
                    )
                break
            except Exception as exc:
                last_err = exc
                self.logger.warning(
                    f"[{self.device_id}] web_h5 launch failed "
                    f"(profile={target_profile}, use_channel={use_channel}): {exc}"
                )
                self._context = None

        if self._context is None:
            # Ensure Playwright process is cleaned if all attempts fail.
            try:
                self._playwright.stop()
            except Exception as e:
                logger.warning(f"[{self.device_id}] playwright.stop() after init-failure cleanup raised: {e}")
            self._playwright = None
            if last_err is not None:
                raise last_err
            raise RuntimeError(
                f"[{self.device_id}] web_h5 launch failed without detailed exception"
            )

        # Deprecated behavior:
        # web_clear_cookies_on_start used to clear on every startup and could wipe session repeatedly.
        # Cookie clearing should now be requested explicitly via one-shot control-panel action.

        self._page = (
            self._context.pages[0] if self._context.pages else self._context.new_page()
        )

        if self.web_url:
            opened = self._open_game_url()
            self._in_game = bool(opened)

        try:
            self._page.wait_for_selector(self.canvas_selector, timeout=15000)
        except Exception:
            self.logger.warning(
                f"[{self.device_id}] web_h5 backend did not find selector: {self.canvas_selector}"
            )

        if state_file:
            try:
                self._context.storage_state(path=state_file)
            except Exception as e:
                logger.warning(f"[{self.device_id}] storage_state save to {state_file} failed: {e}")

    @staticmethod
    def _is_target_closed_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return ("target page, context or browser has been closed" in text) or (
            "target closed" in text
        )

    def _is_session_unavailable(self) -> bool:
        """Return True when Playwright session/page is missing or closed."""
        try:
            if self._context is None or self._page is None:
                return True
            return bool(self._page.is_closed())
        except Exception:
            return True

    def _page_has_canvas(self, page) -> bool:
        # Short timeout: page is already loaded, canvas should resolve in <200ms.
        # The old 1200ms was adding up to 1.2s on every screenshot health-check.
        try:
            page.wait_for_selector(self.canvas_selector, timeout=200)
            return True
        except Exception:
            return False

    def _sync_active_page(self) -> bool:
        """Try to bind self._page to a valid in-game tab and close obvious blank popups."""
        if self._context is None:
            return False
        try:
            pages = [
                p
                for p in (self._context.pages or [])
                if p is not None and not p.is_closed()
            ]
        except Exception:
            return False
        if not pages:
            return False

        # 1) Keep current page if still valid.
        if self._page is not None:
            try:
                if (not self._page.is_closed()) and self._page in pages:
                    if self._page_has_canvas(self._page):
                        return True
            except Exception as e:
                logger.debug(f"[{self.device_id}] current-page validity probe raced/failed: {e}")

        # 2) Prefer page containing game canvas.
        for p in pages:
            if self._page_has_canvas(p):
                self._page = p
                return True

        # 3) Close obvious about:blank popup pages to avoid accidentally binding to white tabs.
        for p in pages:
            try:
                if p is self._page:
                    continue
                url = str(p.url or "").strip().lower()
                if url in {"", "about:blank"}:
                    p.close()
            except Exception as e:
                logger.debug(f"[{self.device_id}] blank-page reaper failed for one page: {e}")

        # 4) Fallback to first non-blank page.
        try:
            pages = [
                p
                for p in (self._context.pages or [])
                if p is not None and not p.is_closed()
            ]
            for p in pages:
                url = str(p.url or "").strip().lower()
                if url not in {"", "about:blank"}:
                    self._page = p
                    return True
        except Exception as e:
            logger.debug(f"[{self.device_id}] non-blank-page fallback iter failed: {e}")
        return False

    def _ensure_browser_session(self, reason: str = "") -> None:
        """Best-effort self-heal for transient web_h5 browser/page loss."""
        if (not self._is_session_unavailable()) and self._sync_active_page():
            return
        suffix = f" ({reason})" if reason else ""
        self.logger.warning(
            f"[{self.device_id}] web_h5 session unavailable{suffix}, restarting browser session..."
        )
        self._restart_browser_session()
        self._sync_active_page()

    def _restart_browser_session(self) -> None:
        """Recreate playwright context/page in-place after browser/page was closed."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception as e:
            logger.warning(f"[{self.device_id}] context.close() during restart raised: {e}")
        self._context = None

        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception as e:
            logger.warning(f"[{self.device_id}] playwright.stop() during restart raised: {e}")
        self._playwright = None
        self._page = None

        self._start()

    def restart_with_headful(self, reason: str = "") -> None:
        """Temporarily force a visible browser window for manual takeover."""
        if self._effective_headless() is False:
            return
        self._headless_override = False
        try:
            self._restart_browser_session()
            suffix = f" ({reason})" if reason else ""
            self.logger.info(
                f"[{self.device_id}] web_h5 restarted in headful mode{suffix}"
            )
        except Exception:
            self._headless_override = None
            raise

    def restore_configured_headless_session(self, reason: str = "") -> None:
        """Restore browser mode to the device's configured web_headless value."""
        configured_headless = self._configured_headless()
        # Only restore if we are currently in an overridden mode.
        if self._headless_override is None:
            return
        # If configured mode is already headful, dropping override is enough.
        if not configured_headless:
            self._headless_override = None
            return
        self._headless_override = None
        self._restart_browser_session()
        suffix = f" ({reason})" if reason else ""
        self.logger.info(
            f"[{self.device_id}] web_h5 restored configured headless mode{suffix}"
        )

    def _open_game_url(self) -> bool:
        """Navigate to game URL, optionally reloading when configured.

        Returns:
            bool: True when at least one navigation attempt succeeded.
        """
        if not self.web_url or self._page is None:
            return False
        nav_timeout_ms = int(self.cfg.get("web_nav_timeout_ms") or 30000)
        headless_mode = self._effective_headless()
        self.logger.info(
            f"[{self.device_id}] web_h5 opening game url: {self.web_url} "
            f"(headless={headless_mode}, timeout={nav_timeout_ms}ms)"
        )

        goto_t0 = time.time()
        try:
            self._page.goto(
                self.web_url, wait_until="domcontentloaded", timeout=nav_timeout_ms
            )
            self.logger.info(
                f"[{self.device_id}] web_h5 goto domcontentloaded ok in "
                f"{(time.time() - goto_t0) * 1000:.0f}ms"
            )
        except Exception as nav_err:
            self.logger.warning(
                f"[{self.device_id}] web_h5 goto timeout/fail after "
                f"{(time.time() - goto_t0) * 1000:.0f}ms: {nav_err}"
            )
            fallback_t0 = time.time()
            try:
                # Fallback: wait for response commit only, to avoid hard-failing whole startup.
                self._page.goto(self.web_url, wait_until="commit", timeout=10000)
                self.logger.info(
                    f"[{self.device_id}] web_h5 goto commit-only fallback ok in "
                    f"{(time.time() - fallback_t0) * 1000:.0f}ms"
                )
            except Exception as fallback_err:
                self.logger.warning(
                    f"[{self.device_id}] web_h5 fallback goto failed after "
                    f"{(time.time() - fallback_t0) * 1000:.0f}ms: {fallback_err}"
                )
                return False

        if bool(self.cfg.get("web_reload_after_goto", False)):
            # Pass an explicit timeout to reload — the historical no-timeout call could
            # block indefinitely in headless mode when the page never reaches DCL.
            reload_t0 = time.time()
            try:
                self._page.reload(
                    wait_until="domcontentloaded", timeout=nav_timeout_ms
                )
                self.logger.info(
                    f"[{self.device_id}] web_h5 reload ok in "
                    f"{(time.time() - reload_t0) * 1000:.0f}ms"
                )
            except Exception as reload_err:
                # Some pages may transiently reject reload; keep the first successful navigation.
                self.logger.warning(
                    f"[{self.device_id}] web_h5 reload skipped after "
                    f"{(time.time() - reload_t0) * 1000:.0f}ms: {reload_err}"
                )
        return True

    def is_alive(self) -> bool:
        try:
            if threading.get_ident() != self.owner_thread_id:
                return False
            if self._context is None or self._page is None:
                return False
            # Manual-hold mode should end once there is no usable game page left.
            # Reuse the same active-page sync logic so blank tabs / stale pages do not
            # keep the device falsely marked as still open.
            if not self._sync_active_page():
                return False
            return not self._page.is_closed()
        except Exception:
            return False

    def _canvas_box(self) -> Dict[str, float]:
        self._ensure_browser_session("canvas_box")
        self._sync_active_page()
        try:
            box = self._page.locator(self.canvas_selector).first.bounding_box()
            if box:
                return box
        except Exception as e:
            logger.debug(f"[{self.device_id}] canvas bounding_box probe failed, using fallback: {e}")
        return {
            "x": 0.0,
            "y": 0.0,
            "width": float(self.viewport_width),
            "height": float(self.viewport_height),
        }

    def _normalize_xy(self, x: float, y: float) -> tuple[float, float]:
        if (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and 0 <= x < 1
            and 0 <= y < 1
        ):
            x = float(x) * self.viewport_width
            y = float(y) * self.viewport_height
        return float(x), float(y)

    def _to_canvas_xy(self, x: float, y: float) -> tuple[float, float]:
        px, py = self._normalize_xy(x, y)
        box = self._canvas_box()
        cx = max(0.0, min(px, box["width"] - 1)) + box["x"]
        cy = max(0.0, min(py, box["height"] - 1)) + box["y"]
        return cx, cy

    def tap(self, x, y, *args, **kwargs):
        self._ensure_browser_session("tap")
        self._sync_active_page()
        cx, cy = self._to_canvas_xy(x, y)
        self._page.mouse.click(cx, cy)
        return True

    def click(self, x, y, *args, **kwargs):
        return self.tap(x, y, *args, **kwargs)

    def swipe(self, x0, y0, x1, y1, duration: float = 0.2, *args, **kwargs):
        self._ensure_browser_session("swipe")
        self._sync_active_page()
        sx, sy = self._to_canvas_xy(x0, y0)
        ex, ey = self._to_canvas_xy(x1, y1)
        self._page.mouse.move(sx, sy)
        self._page.mouse.down()
        self._page.mouse.move(ex, ey, steps=max(3, int(max(duration, 0.05) * 30)))
        self._page.mouse.up()
        return True

    def gesture_swipe(self, *args, **kwargs):
        return self.swipe(*args, **kwargs)

    def _screenshot_via_canvas_capture(self) -> Optional[bytes]:
        """使用 Canvas Capture API 從 canvas 元素獲取圖像資料。

        通過執行 JavaScript 調用 canvas.toDataURL() 獲取 base64 圖像，
        適用於某些 Playwright 原生截圖無法正確捕捉 canvas 內容的情境。
        """
        try:
            # 執行 JavaScript 獲取 canvas 的 base64 圖像資料
            script = """
                () => {
                    const canvas = document.querySelector('canvas');
                    if (!canvas) return null;
                    try {
                        return canvas.toDataURL('image/png');
                    } catch (e) {
                        return null;
                    }
                }
            """
            result = self._page.evaluate(script)
            if (
                result
                and isinstance(result, str)
                and result.startswith("data:image/png;base64,")
            ):
                # 解碼 base64 資料
                import base64

                base64_data = result.split(",")[1]
                return base64.b64decode(base64_data)
            return None
        except Exception as exc:
            self.logger.debug(f"[{self.device_id}] Canvas capture failed: {exc}")
            return None

    def _playwright_screenshot_kwargs(self) -> Dict[str, Any]:
        """Extra kwargs for Playwright screenshot: JPEG when configured, else PNG default."""
        if self.screenshot_jpeg_quality:
            return {"type": "jpeg", "quality": int(self.screenshot_jpeg_quality)}
        return {}

    def _capture_via_playwright(self) -> Optional[bytes]:
        """Capture the canvas via Playwright, falling back to full-page on locator failure."""
        shot_kwargs = self._playwright_screenshot_kwargs()
        try:
            return self._page.locator(self.canvas_selector).first.screenshot(
                timeout=5000, **shot_kwargs
            )
        except Exception:
            return self._page.screenshot(**shot_kwargs)

    def screenshot(self, format=None, *args, **kwargs):
        data: Optional[bytes] = None
        last_exc: Optional[Exception] = None

        for attempt in range(2):
            if attempt > 0:
                # Only sleep on retry (session was lost and just restarted).
                # The original code had `if attempt == 0` which caused a mandatory
                # 5-second sleep on every single screenshot call.
                time.sleep(1)
            # _ensure_browser_session already calls _sync_active_page internally;
            # calling it again right after was redundant and added extra canvas-check
            # overhead on every screenshot.
            self._ensure_browser_session("screenshot")
            try:
                # 根據設定的截圖方法選擇實現
                if self.screenshot_method == "canvas_capture":
                    # 使用 Canvas Capture API
                    data = self._screenshot_via_canvas_capture()
                    if data is None:
                        self.logger.warning(
                            f"[{self.device_id}] Canvas capture returned None, falling back to playwright screenshot"
                        )
                        # 回退到 Playwright 原生截圖
                        data = self._capture_via_playwright()
                else:
                    # 預設使用 Playwright 原生截圖
                    data = self._capture_via_playwright()
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and self._is_target_closed_error(exc):
                    self.logger.warning(
                        f"[{self.device_id}] web_h5 page/context closed during screenshot, restarting browser session..."
                    )
                    self._restart_browser_session()
                    continue
                raise

        if data is None:
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("web_h5 screenshot failed without exception")

        img_array = np.frombuffer(data, dtype=np.uint8)
        img_bgr = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img_bgr is None:
            raise RuntimeError("Failed to decode screenshot from web_h5 backend")

        fmt = (format or "pillow").lower() if isinstance(format, str) else format
        if fmt in {"opencv", "cv2"}:
            return img_bgr

        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)

    def capture_screenshot(self):
        return self.screenshot(format="opencv")

    def xpath(self, expr):
        return _NoopXPathQuery()

    def xpath_click(self, xpath_expr, *args, **kwargs):
        return False

    def app_current(self):
        if self._in_game:
            return {"package": "com.mxdzz.tw.and"}
        return {"package": "browser.idle"}

    def app_start(self, *args, **kwargs):
        force_headful = bool(kwargs.pop("force_headful", False))
        if force_headful:
            self.restart_with_headful(reason="manual web launch")
        self._ensure_browser_session("app_start")

        if self.web_url and self._page is not None:
            try:
                self._open_game_url()
            except Exception as e:
                logger.warning(f"[{self.device_id}] app_start: _open_game_url failed: {e}")
        self._in_game = True
        self._closed_by_stop = False
        return True

    def clear_cookies(self):
        self._ensure_browser_session("clear_cookies")
        if self._context is None:
            raise RuntimeError(
                f"[{self.device_id}] web_h5 context unavailable for clear_cookies"
            )
        self._context.clear_cookies()
        self.logger.info(
            f"[{self.device_id}] web_h5 cookies cleared by one-shot request"
        )
        return True

    def app_stop(self, *args, **kwargs):
        stop_mode = str(self.cfg.get("web_stop_mode", "close")).strip().lower()
        if stop_mode in {"close", "close_page", "close_browser"}:
            try:
                self.close()
            except Exception as exc:
                if self._is_target_closed_error(exc):
                    self.logger.info(
                        f"[{self.device_id}] web_h5 app_stop ignored already-closed target"
                    )
                else:
                    raise
            self._closed_by_stop = True
        elif stop_mode == "blank" and self._page is not None:
            try:
                # Optional behavior for users who still want "hard stop" by leaving the game page.
                self._page.goto("about:blank", wait_until="domcontentloaded")
            except Exception as e:
                logger.debug(f"[{self.device_id}] stop_mode=blank goto about:blank failed: {e}")
            self._closed_by_stop = False
        else:
            self._closed_by_stop = False
        self._in_game = False
        return True

    def press(self, key, *args, **kwargs):
        key_str = str(key).lower()
        if key_str in {"back", "escape"}:
            self._page.keyboard.press("Escape")
            return True
        if key_str == "home":
            return True
        return False

    def home(self):
        return self.press("home")

    def back(self):
        return self.press("back")

    def unlock(self):
        return True

    def screen_off(self):
        return True

    def open_quick_settings(self):
        return True

    def close(self):
        # Both `_context.close()` and `_playwright.stop()` are independent
        # cleanup steps — a failure in the first must NOT short-circuit
        # the second, because that's how the Chrome subprocess gets
        # orphaned: context.close() throws (page hung, target unresponsive),
        # caller catches → playwright.stop() never runs → Chrome stays.
        # Stash any non-trivial error and re-raise after cleanup is done.
        deferred_err: Optional[Exception] = None
        try:
            if self._context is not None:
                try:
                    self._context.close()
                except Exception as exc:
                    if self._is_target_closed_error(exc):
                        self.logger.info(
                            f"[{self.device_id}] web_h5 close ignored already-closed browser context"
                        )
                    else:
                        self.logger.warning(
                            f"[{self.device_id}] web_h5 context close failed (continuing to playwright.stop): {exc}"
                        )
                        deferred_err = exc
                finally:
                    self._context = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception as exc:
                    text = str(exc or "").lower()
                    if "event loop is closed" in text or self._is_target_closed_error(
                        exc
                    ):
                        self.logger.info(
                            f"[{self.device_id}] web_h5 close ignored already-stopped playwright"
                        )
                    else:
                        self.logger.warning(
                            f"[{self.device_id}] web_h5 playwright stop failed: {exc}"
                        )
                        if deferred_err is None:
                            deferred_err = exc
                finally:
                    self._playwright = None
                    _reset_thread_event_loop()
            self._page = None
            self._in_game = False
        finally:
            with _WEB_DEVICE_LOCK:
                current = _WEB_DEVICE_REGISTRY.get(self.device_id)
                if current is self:
                    _WEB_DEVICE_REGISTRY.pop(self.device_id, None)
        if deferred_err is not None:
            raise deferred_err


def create_web_device_if_enabled(
    ip: str,
    cfg: Optional[Dict[str, Any]] = None,
    logger_obj: Optional[logging.Logger] = None,
):
    config = cfg or {}
    backend = str(config.get("backend", "adb")).strip().lower()
    if backend != "web_h5":
        return None
    if not str(config.get("web_url") or "").strip():
        raise ValueError(f"[{ip}] web_h5 backend requires non-empty web_url")

    current_tid = threading.get_ident()
    with _WEB_DEVICE_LOCK:
        existing = _WEB_DEVICE_REGISTRY.get(ip)
        if existing is not None and getattr(existing, "owner_thread_id", None) == current_tid:
            if existing.is_alive():
                return existing
            # Same-thread device is dead but its playwright may still be running.
            # Restart in-place rather than creating a second playwright instance,
            # which would raise "Sync API inside asyncio loop" (the 5554 failure mode).
            log = logger_obj or logger
            log.warning(
                f"[{ip}] existing same-thread web device is dead, restarting in-place"
            )
            try:
                existing._restart_browser_session()
            except Exception as restart_err:
                log.warning(
                    f"[{ip}] in-place restart failed ({restart_err}), creating new device"
                )
                try:
                    existing.close()
                except Exception as e:
                    logger.warning(f"[{ip}] existing.close() after failed restart raised: {e}")
                device = PlaywrightGameDevice(device_id=ip, cfg=config, logger_obj=logger_obj)
                _WEB_DEVICE_REGISTRY[ip] = device
                return device
            return existing

        # Cross-thread objects must never be reused with sync Playwright.
        # We replace stale/foreign entries and let old thread own its own lifecycle.
        device = PlaywrightGameDevice(device_id=ip, cfg=config, logger_obj=logger_obj)
        _WEB_DEVICE_REGISTRY[ip] = device
        return device


def get_alive_web_device(ip: str) -> "Optional[PlaywrightGameDevice]":
    """Return the registered web device for ip if it belongs to the current thread and is alive."""
    current_tid = threading.get_ident()
    with _WEB_DEVICE_LOCK:
        existing = _WEB_DEVICE_REGISTRY.get(ip)
        if (
            existing is not None
            and getattr(existing, "owner_thread_id", None) == current_tid
            and existing.is_alive()
        ):
            return existing
    return None


def get_same_thread_web_device(ip: str) -> "Optional[PlaywrightGameDevice]":
    """Return the same-thread registered web device for ip, or None.

    Unlike get_alive_web_device(), this does NOT call is_alive(), which
    probes canvas with a 200ms timeout that returns false negatives on tabs
    throttled by background-tab policies (a tab waking from multi-minute
    sleep can fail the probe even though the session is structurally fine).

    Returning the device reference (not a boolean) lets callers reuse the
    session directly and avoid calling create_web_device_if_enabled() —
    which itself runs is_alive() on the existing entry and would re-trigger
    the same canvas-probe false-negatives, restarting the session even
    when the caller knows it owns a usable handle.
    """
    current_tid = threading.get_ident()
    with _WEB_DEVICE_LOCK:
        existing = _WEB_DEVICE_REGISTRY.get(ip)
        if (
            existing is not None
            and getattr(existing, "owner_thread_id", None) == current_tid
        ):
            return existing
    return None


def close_all_web_devices(logger_obj: Optional[logging.Logger] = None) -> None:
    """Close all registered web_h5 devices and clear registry.

    H8 fix: Playwright sync API 是 thread-affine。greenlet dispatcher 在 owner
    thread 的 event loop 上跑，從外部 thread 呼叫 `device.close()` 會踩到
    "Sync API inside the asyncio loop" 或更隱晦的 dispatcher 殭屍狀態。

    若呼叫端不是 owner thread（典型情境：Ctrl+C handler 從主執行緒 fan-out 給
    所有裝置），這裡只清空 registry 並警告——process 結束時 OS 會回收
    Playwright 子進程，硬 close 帶來的風險大於收益。長期應透過 stop event
    讓 owner thread 自行清理。
    """
    log = logger_obj or logger
    current_tid = threading.get_ident()
    with _WEB_DEVICE_LOCK:
        devices = list(_WEB_DEVICE_REGISTRY.items())
    # 不在這裡 clear registry：被 skip 的非 owner-thread device 仍由 owner thread
    # 的 finally 自行 close 並從 registry 移除。如果在這裡直接 clear，當 owner
    # thread 沒走到 finally（被卡住或其他原因），browser context 就成孤兒。
    closed_or_attempted = []
    for ip, device in devices:
        owner_tid = getattr(device, "owner_thread_id", None)
        if owner_tid is not None and owner_tid != current_tid:
            log.warning(
                f"[{ip}] close_all_web_devices: caller tid={current_tid} != "
                f"device owner tid={owner_tid}; skipping hard close to avoid "
                f"Playwright thread-affinity violation. Owner thread should "
                f"close on its way out; process exit will reap if not."
            )
            continue
        try:
            device.close()
        except Exception as exc:
            log.warning(
                f"[{ip}] close web_h5 device failed during global shutdown: {exc}"
            )
        # close 成功或丟例外都視為「已嘗試」，從 registry 移除
        closed_or_attempted.append(ip)
    if closed_or_attempted:
        with _WEB_DEVICE_LOCK:
            for ip in closed_or_attempted:
                _WEB_DEVICE_REGISTRY.pop(ip, None)
