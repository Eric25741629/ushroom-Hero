import os
import time
import threading
import bot_state
import logging
import cv2
import numpy as np
from typing import Any, Dict, Iterable, Optional
from PIL import Image

logger = logging.getLogger(__name__)
_WEB_DEVICE_LOCK = threading.Lock()
_WEB_DEVICE_REGISTRY: Dict[str, "PlaywrightGameDevice"] = {}


DEFAULT_PLAYWRIGHT_CONTEXT_OPTIONS: Dict[str, Any] = {
    "viewport": {"width": 960, "height": 540},
}


class PlaywrightContextConfig:
    """Playwright `browser.new_context(...)` 的設定容器。

    先把預設值集中管理，後續若要擴充 cookies、locale、user_agent、
    storage_state 等選項，直接從這裡延伸即可。
    """

    def __init__(self, viewport: Optional[Dict[str, int]] = None, device_scale_factor: float = 1.0, **extra_options: Any):
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

    def __init__(self, browser: Any = None, context: Any = None, config: Optional[PlaywrightContextConfig] = None):
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

    def _pause_guard(self):
        bot_state.check_pause(self._ip)

    def _screen_size(self):
        info = getattr(self._d, "info", {}) or {}
        width = info.get("displayWidth") or info.get("screenWidth") or info.get("width")
        height = info.get("displayHeight") or info.get("screenHeight") or info.get("height")
        return width, height

    def _to_px(self, x, y):
        """支援座標比率或絕對座標。

        若 $0 \le x,y < 1$，視為螢幕比例；否則視為絕對座標。
        """
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and 0 <= x < 1 and 0 <= y < 1:
            width, height = self._screen_size()
            if width and height:
                return int(width * x), int(height * y)
        return int(x), int(y)

    def tap(self, x, y, *args, **kwargs):
        """統一的點擊入口。"""
        self._pause_guard()
        x, y = self._to_px(x, y)
        tap_fn = getattr(self._d, "tap", None)
        if callable(tap_fn):
            return tap_fn(x, y, *args, **kwargs)
        return self._d.click(x, y, *args, **kwargs)

    def click(self, x, y, *args, **kwargs):
        """點擊前先檢查是否暫停"""
        return self.tap(x, y, *args, **kwargs)

    def click_pct(self, x_ratio: float, y_ratio: float, *args, **kwargs):
        """以螢幕比例點擊。"""
        return self.tap(x_ratio, y_ratio, *args, **kwargs)

    def gesture_swipe(self, *args, **kwargs):
        self._pause_guard()
        gesture_swipe_fn = getattr(self._d, "gesture_swipe", None)
        if callable(gesture_swipe_fn):
            return gesture_swipe_fn(*args, **kwargs)
        return self._d.swipe(*args, **kwargs)

    def swipe(self, *args, **kwargs):
        self._pause_guard()

        if len(args) >= 4:
            x0, y0 = self._to_px(args[0], args[1])
            x1, y1 = self._to_px(args[2], args[3])
            rest = args[4:]
            args = (x0, y0, x1, y1, *rest)
        return self.gesture_swipe(*args, **kwargs)

    def swipe_pct(self, x0_ratio, y0_ratio, x1_ratio, y1_ratio, *args, **kwargs):
        """以螢幕比例滑動。"""
        return self.swipe(x0_ratio, y0_ratio, x1_ratio, y1_ratio, *args, **kwargs)

    def screenshot(self, *args, **kwargs):
        """截圖前也檢查暫停，確保不會在暫停時瘋狂截圖"""
        self._pause_guard()
        return self._d.screenshot(*args, **kwargs)

    def xpath_click(self, xpath_expr, *args, **kwargs):
        self._pause_guard()
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
            pkg_name = kwargs.pop('package_name', None)
        if pkg_name is None:
            raise TypeError("app_stop() missing 1 required positional argument: 'pkg_name'")
        # Call underlying device; prefer keyword for compatibility
        try:
            return self._d.app_stop(pkg_name, *args, **kwargs)
        except TypeError:
            return self._d.app_stop(package_name=pkg_name, *args, **kwargs)

    def app_start(self, pkg_name=None, *args, **kwargs):
        """Start an app. Accepts either positional `pkg_name` or keyword `package_name`."""
        if pkg_name is None:
            pkg_name = kwargs.pop('package_name', None)
        if pkg_name is None:
            raise TypeError("app_start() missing 1 required positional argument: 'pkg_name'")
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


class PlaywrightGameDevice:
    """Sync Playwright wrapper that mimics the minimal uiautomator2 API used by this project."""

    backend_kind = "web_h5"

    def __init__(self, device_id: str, cfg: Optional[Dict[str, Any]] = None, logger_obj: Optional[logging.Logger] = None):
        self.device_id = device_id
        self.cfg = cfg or {}
        self.logger = logger_obj or logger
        self.owner_thread_id = threading.get_ident()

        self.web_url = str(self.cfg.get("web_url") or "").strip()
        self.canvas_selector = str(self.cfg.get("web_canvas_selector") or "canvas").strip() or "canvas"
        self.viewport_width = int(self.cfg.get("web_viewport_width") or 540)
        self.viewport_height = int(self.cfg.get("web_viewport_height") or 960)
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
        self._start()

    def _start(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("Playwright is required for web_h5 backend. Please install playwright.") from exc

        profile_dir = str(self.cfg.get("web_profile_dir") or "playwright_profile/{device_id}").strip() or "playwright_profile/{device_id}"
        # 每個裝置都使用自己的 profile/cookies 資料夾，避免共享登入狀態。
        # 若設定值包含 {device_id} / {ip}，會先做字串替換；否則會自動在尾端附加 device_id。
        profile_dir = profile_dir.format(device_id=self.device_id, ip=self.device_id)
        if not os.path.normpath(profile_dir).endswith(self.device_id):
            profile_dir = os.path.join(profile_dir, self.device_id)
        if not os.path.isabs(profile_dir):
            profile_dir = os.path.join(os.getcwd(), profile_dir)
        os.makedirs(profile_dir, exist_ok=True)

        channel = str(self.cfg.get("web_channel") or "chrome").strip()
        state_file_raw = str(self.cfg.get("web_state_file") or "auth_state/{device_id}.json").strip() or "auth_state/{device_id}.json"
        state_file = state_file_raw.format(device_id=self.device_id, ip=self.device_id)
        if "{device_id}" not in state_file_raw and "{ip}" not in state_file_raw:
            if os.path.basename(state_file).lower() == "auth_state.json":
                state_file = os.path.join(os.path.dirname(state_file), "auth_state", f"{self.device_id}.json")
        if state_file and not os.path.isabs(state_file):
            state_file = os.path.join(os.getcwd(), state_file)

        def _clear_chrome_singleton_locks(target_dir: str) -> None:
            # Stale singleton files can make Chrome exit immediately with profile-in-use errors.
            for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                fp = os.path.join(target_dir, name)
                try:
                    if os.path.exists(fp):
                        os.remove(fp)
                except Exception:
                    pass

        def _build_launch_kwargs(target_profile: str, use_channel: bool) -> Dict[str, Any]:
            kwargs: Dict[str, Any] = {
                "user_data_dir": target_profile,
                "headless": bool(self.cfg.get("web_headless", False)),
                "viewport": {"width": self.viewport_width, "height": self.viewport_height},
                "device_scale_factor": 1.0,
                "ignore_default_args": ["--enable-automation"],
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--force-device-scale-factor=1",
                    "--high-dpi-support=1",
                ],
            }
            if use_channel and channel:
                kwargs["channel"] = channel
            return kwargs

        local_app_data = os.environ.get("LOCALAPPDATA", "")
        fallback_profile_dir = os.path.join(local_app_data, "mushroom_playwright_profiles", self.device_id) if local_app_data else ""

        self._playwright = sync_playwright().start()
        last_err: Optional[Exception] = None
        launch_attempts = [(profile_dir, True)]
        if fallback_profile_dir and os.path.abspath(fallback_profile_dir) != os.path.abspath(profile_dir):
            launch_attempts.append((fallback_profile_dir, True))
        # Last fallback: same profile, but no channel ("chrome") to avoid channel-specific startup issues.
        launch_attempts.append((profile_dir, False))

        for target_profile, use_channel in launch_attempts:
            try:
                os.makedirs(target_profile, exist_ok=True)
                _clear_chrome_singleton_locks(target_profile)
                launch_kwargs = _build_launch_kwargs(target_profile, use_channel=use_channel)
                self._context = self._playwright.chromium.launch_persistent_context(**launch_kwargs)
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
            except Exception:
                pass
            self._playwright = None
            if last_err is not None:
                raise last_err
            raise RuntimeError(f"[{self.device_id}] web_h5 launch failed without detailed exception")

        # Deprecated behavior:
        # web_clear_cookies_on_start used to clear on every startup and could wipe session repeatedly.
        # Cookie clearing should now be requested explicitly via one-shot control-panel action.

        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

        if self.web_url:
            opened = self._open_game_url()
            self._in_game = bool(opened)

        try:
            self._page.wait_for_selector(self.canvas_selector, timeout=15000)
        except Exception:
            self.logger.warning(f"[{self.device_id}] web_h5 backend did not find selector: {self.canvas_selector}")

        if state_file:
            try:
                self._context.storage_state(path=state_file)
            except Exception:
                pass

    @staticmethod
    def _is_target_closed_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return ("target page, context or browser has been closed" in text) or ("target closed" in text)

    def _is_session_unavailable(self) -> bool:
        """Return True when Playwright session/page is missing or closed."""
        try:
            if self._context is None or self._page is None:
                return True
            return bool(self._page.is_closed())
        except Exception:
            return True

    def _page_has_canvas(self, page) -> bool:
        try:
            page.wait_for_selector(self.canvas_selector, timeout=1200)
            return True
        except Exception:
            return False

    def _sync_active_page(self) -> bool:
        """Try to bind self._page to a valid in-game tab and close obvious blank popups."""
        if self._context is None:
            return False
        try:
            pages = [p for p in (self._context.pages or []) if p is not None and not p.is_closed()]
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
            except Exception:
                pass

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
            except Exception:
                pass

        # 4) Fallback to first non-blank page.
        try:
            pages = [p for p in (self._context.pages or []) if p is not None and not p.is_closed()]
            for p in pages:
                url = str(p.url or "").strip().lower()
                if url not in {"", "about:blank"}:
                    self._page = p
                    return True
        except Exception:
            pass
        return False

    def _ensure_browser_session(self, reason: str = "") -> None:
        """Best-effort self-heal for transient web_h5 browser/page loss."""
        if (not self._is_session_unavailable()) and self._sync_active_page():
            return
        suffix = f" ({reason})" if reason else ""
        self.logger.warning(f"[{self.device_id}] web_h5 session unavailable{suffix}, restarting browser session...")
        self._restart_browser_session()
        self._sync_active_page()

    def _restart_browser_session(self) -> None:
        """Recreate playwright context/page in-place after browser/page was closed."""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        self._context = None

        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._page = None

        self._start()

    def _open_game_url(self) -> bool:
        """Navigate to game URL then force one refresh for stability.

        Returns:
            bool: True when at least one navigation attempt succeeded.
        """
        if not self.web_url or self._page is None:
            return False
        nav_timeout_ms = int(self.cfg.get("web_nav_timeout_ms") or 30000)
        try:
            self._page.goto(self.web_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
        except Exception as nav_err:
            self.logger.warning(f"[{self.device_id}] web_h5 goto timeout/fail: {nav_err}")
            try:
                # Fallback: wait for response commit only, to avoid hard-failing whole startup.
                self._page.goto(self.web_url, wait_until="commit", timeout=10000)
            except Exception as fallback_err:
                self.logger.warning(f"[{self.device_id}] web_h5 fallback goto failed: {fallback_err}")
                return False
        try:
            self._page.reload(wait_until="domcontentloaded")
        except Exception:
            # Some pages may transiently reject reload; keep the first successful navigation.
            pass
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
        except Exception:
            pass
        return {"x": 0.0, "y": 0.0, "width": float(self.viewport_width), "height": float(self.viewport_height)}

    def _normalize_xy(self, x: float, y: float) -> tuple[float, float]:
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and 0 <= x < 1 and 0 <= y < 1:
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

    def screenshot(self, format=None, *args, **kwargs):
        data: Optional[bytes] = None
        last_exc: Optional[Exception] = None

        for attempt in range(2):
            if attempt == 0:
                time.sleep(5)
            self._ensure_browser_session("screenshot")
            self._sync_active_page()
            try:
                try:
                    data = self._page.locator(self.canvas_selector).first.screenshot(timeout=5000)
                except Exception:
                    data = self._page.screenshot()
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
        self._ensure_browser_session("app_start")

        if self.web_url and self._page is not None:
            try:
                self._open_game_url()
            except Exception:
                pass
        self._in_game = True
        self._closed_by_stop = False
        return True

    def clear_cookies(self):
        self._ensure_browser_session("clear_cookies")
        if self._context is None:
            raise RuntimeError(f"[{self.device_id}] web_h5 context unavailable for clear_cookies")
        self._context.clear_cookies()
        self.logger.info(f"[{self.device_id}] web_h5 cookies cleared by one-shot request")
        return True

    def app_stop(self, *args, **kwargs):
        stop_mode = str(self.cfg.get("web_stop_mode", "close")).strip().lower()
        if stop_mode in {"close", "close_page", "close_browser"}:
            try:
                self.close()
            except Exception as exc:
                if self._is_target_closed_error(exc):
                    self.logger.info(f"[{self.device_id}] web_h5 app_stop ignored already-closed target")
                else:
                    raise
            self._closed_by_stop = True
        elif stop_mode == "blank" and self._page is not None:
            try:
                # Optional behavior for users who still want "hard stop" by leaving the game page.
                self._page.goto("about:blank", wait_until="domcontentloaded")
            except Exception:
                pass
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
        try:
            if self._context is not None:
                try:
                    self._context.close()
                except Exception as exc:
                    if self._is_target_closed_error(exc):
                        self.logger.info(f"[{self.device_id}] web_h5 close ignored already-closed browser context")
                    else:
                        raise
                finally:
                    self._context = None
            if self._playwright is not None:
                try:
                    self._playwright.stop()
                except Exception as exc:
                    text = str(exc or "").lower()
                    if "event loop is closed" in text or self._is_target_closed_error(exc):
                        self.logger.info(f"[{self.device_id}] web_h5 close ignored already-stopped playwright")
                    else:
                        raise
                finally:
                    self._playwright = None
            self._page = None
            self._in_game = False
        finally:
            with _WEB_DEVICE_LOCK:
                current = _WEB_DEVICE_REGISTRY.get(self.device_id)
                if current is self:
                    _WEB_DEVICE_REGISTRY.pop(self.device_id, None)


def create_web_device_if_enabled(ip: str, cfg: Optional[Dict[str, Any]] = None, logger_obj: Optional[logging.Logger] = None):
    config = cfg or {}
    backend = str(config.get("backend", "adb")).strip().lower()
    if backend != "web_h5":
        return None
    if not str(config.get("web_url") or "").strip():
        raise ValueError(f"[{ip}] web_h5 backend requires non-empty web_url")

    current_tid = threading.get_ident()
    with _WEB_DEVICE_LOCK:
        existing = _WEB_DEVICE_REGISTRY.get(ip)
        if (
            existing is not None
            and getattr(existing, "owner_thread_id", None) == current_tid
            and existing.is_alive()
        ):
            return existing

        # Cross-thread objects must never be reused with sync Playwright.
        # We replace stale/foreign entries and let old thread own its own lifecycle.
        device = PlaywrightGameDevice(device_id=ip, cfg=config, logger_obj=logger_obj)
        _WEB_DEVICE_REGISTRY[ip] = device
        return device


def close_all_web_devices(logger_obj: Optional[logging.Logger] = None) -> None:
    """Close all registered web_h5 devices and clear registry."""
    log = logger_obj or logger
    with _WEB_DEVICE_LOCK:
        devices = list(_WEB_DEVICE_REGISTRY.items())
        _WEB_DEVICE_REGISTRY.clear()
    for ip, device in devices:
        try:
            device.close()
        except Exception as exc:
            log.warning(f"[{ip}] close web_h5 device failed during global shutdown: {exc}")
