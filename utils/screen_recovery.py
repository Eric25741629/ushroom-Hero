import time
import os
import cv2
from typing import Callable, Optional


def ensure_main_screen(
    d,
    check_fn: Callable[[object], bool],
    app_package: Optional[str] = None,
    max_attempts: int = 3,
    debug: bool = True,
    close_coords: Optional[tuple[int, int]] = None,
):
    """Ensure device `d` is at main screen by calling `check_fn(d)`.

    Attempts lightweight recovery (back, click close_coords) and finally
    restarts app if provided. Saves debug screenshots on each attempt.
    Returns True if main screen is reached, else False.
    """
    os.makedirs("debug_screens", exist_ok=True)
    for attempt in range(1, max_attempts + 1):
        try:
            ok = check_fn(d)
        except Exception as e:
            if debug:
                print(f"ensure_main_screen: check_fn raised: {e}")
            ok = False

        if ok:
            if debug:
                print("ensure_main_screen: already main screen")
            return True

        if debug:
            print(f"ensure_main_screen: not main screen, attempt {attempt}/{max_attempts}")

        # lightweight recovery steps
        try:
            d.press("back")
        except Exception:
            pass
        time.sleep(0.4)

        if close_coords:
            try:
                d.click(*close_coords)
            except Exception:
                pass
            time.sleep(0.4)

        # save screenshot for debugging
        try:
            img = d.screenshot(format="opencv")
            path = f"debug_screens/ensure_main_{int(time.time())}.png"
            # cv2.imwrite(path, img)
            if debug:
                print(f"ensure_main_screen: saved debug screenshot: {path}")
        except Exception:
            pass

        # final attempt: restart app
        if attempt == max_attempts and app_package:
            try:
                if debug:
                    print("ensure_main_screen: restarting app as fallback")
                d.press("home")
                time.sleep(0.3)
                d.app_start(app_package)
                time.sleep(1.5)
            except Exception as e:
                if debug:
                    print(f"ensure_main_screen: restart failed: {e}")

    return False


def ensure_main_decorator(check_fn: Callable[[object], bool], **kwargs):
    """Decorator factory to ensure main screen before running function.

    Usage:
        @ensure_main_decorator(check_points)
        def my_action(d, ...):
            ...
    """
    def decorator(func):
        def wrapper(*args, **inner_kwargs):
            # assume first arg is device if not provided in kwargs
            d = None
            if args:
                d = args[0]
            elif "d" in inner_kwargs:
                d = inner_kwargs["d"]
            else:
                raise RuntimeError("ensure_main_decorator: device argument not found")

            ok = ensure_main_screen(d, check_fn, **kwargs)
            if not ok:
                raise RuntimeError("Cannot recover to main screen")
            return func(*args, **inner_kwargs)

        return wrapper

    return decorator
