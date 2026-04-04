from __future__ import annotations

from typing import Optional

from device_wrapper import PlaywrightGameDevice


def connect_web(
    url: str,
    device_id: str = "web-shot",
    *,
    width: int = 540,
    height: int = 960,
    canvas_selector: str = "canvas",
    headless: bool = False,
) -> PlaywrightGameDevice:
    """
    Create a simple web_h5 device that supports `d.screenshot()`.

    Example:
        from web_screenshot_device import connect_web
        d = connect_web("https://example.com")
        img = d.screenshot()
        img.save("web.png")
    """
    cfg = {
        "backend": "web_h5",
        "web_url": str(url).strip(),
        "web_canvas_selector": canvas_selector or "canvas",
        "web_viewport_width": int(width),
        "web_viewport_height": int(height),
        "web_headless": bool(headless),
        "web_stop_mode": "close",
    }
    return PlaywrightGameDevice(device_id=device_id, cfg=cfg)


def screenshot_web(
    url: str,
    save_path: Optional[str] = None,
    *,
    device_id: str = "web-shot",
    width: int = 540,
    height: int = 960,
    canvas_selector: str = "canvas",
    headless: bool = False,
):
    """
    One-call helper for users who only want a screenshot immediately.
    Returns a PIL image by default.
    """
    d = connect_web(
        url,
        device_id=device_id,
        width=width,
        height=height,
        canvas_selector=canvas_selector,
        headless=headless,
    )
    try:
        img = d.screenshot()
        if save_path:
            img.save(save_path)
        return img
    finally:
        d.close()


if __name__ == "__main__":
    demo_url = "https://example.com"
    img = screenshot_web(demo_url, save_path="web_demo.png", headless=True)
    print(f"saved screenshot: web_demo.png ({img.size[0]}x{img.size[1]})")
