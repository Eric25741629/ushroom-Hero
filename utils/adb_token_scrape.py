"""每次 adb 喚醒順手被動撈 ws_token 登入 ticket（Task B）。

即使裝置 `ws_token.enabled=false`，正常 adb wake cycle 在遊戲「進入可操作狀態」後
仍會 best-effort 從 logcat 撈出 SDK 冷啟動時印出的明文登入鏈，寫/更新
auth_state/_auth_capture_<ip>.json。如此日後使用者把裝置切成 adb+ws，token 已就緒。

與 tools/adb_token_login.py 的差異（後者是冷啟動主動撈取工具）：
  - 不重啟 App（bot 已啟動好遊戲），不做 WS verify（會踢掉 live session）。
  - 只跑 `adb logcat -d`（dump 現有 buffer）解析，不輪詢 print。
  - SDK 只在「冷啟動」印登入鏈；若只是 resume，buffer 裡可能沒有 → 靜默回 False。

解析邏輯（regex + build_creds）以 importlib 從 tools/adb_token_login.py 載入重用，
保持單一真相源；那支檔不是 package，無法 `from tools... import`。

一律 best-effort：任何失敗（adb 出錯/timeout/regex miss/build_creds 拋錯/寫檔
OSError）只 log（warm start 找不到屬正常，用 info 級別避免每次喚醒洗 warning）並
回 False，絕不打斷 wake cycle。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import time
from pathlib import Path
from types import ModuleType
from typing import Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1]
AUTH_DIR = _ROOT / "auth_state"
_TOOL_PATH = _ROOT / "tools" / "adb_token_login.py"

# 被動撈取最多嘗試次數（每次 dump 一整份 buffer），總時間受 timeout_sec 夾住。
_MAX_ATTEMPTS = 3
_RETRY_SLEEP_SEC = 3.0

# importlib 載入的 tools/adb_token_login.py，module-level 快取。
_tool_mod: Optional[ModuleType] = None


def _load_tool() -> Optional[ModuleType]:
    """Lazy-load tools/adb_token_login.py（非 package，只能用檔案路徑載入）。

    對應檔：utils/adb_token_scrape.py 重用此檔的 SCENE_LINE/AUTH_URL_RE/
    AUTH_REPLY_RE 與 build_creds()。改動 regex/build_creds 請同步檢查兩處。
    """
    global _tool_mod
    if _tool_mod is not None:
        return _tool_mod
    try:
        spec = importlib.util.spec_from_file_location("adb_token_login_tool", _TOOL_PATH)
        if spec is None or spec.loader is None:
            logger.info("adb token scrape: 無法建立 tool spec (%s)", _TOOL_PATH)
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _tool_mod = mod
        return mod
    except Exception as exc:  # noqa: BLE001 — 載入失敗不能炸 wake cycle
        logger.info("adb token scrape: 載入 adb_token_login 失敗: %s", exc)
        return None


def _logcat_dump(ip: str, timeout_sec: float) -> str:
    """跑一次 `adb -s <ip> logcat -d`，回 buffer 文字（出錯回空字串）。"""
    out = subprocess.run(
        ["adb", "-s", ip, "logcat", "-d"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout_sec,
    )
    return out.stdout or ""


def _scrape_buffer(tool: ModuleType, buf: str) -> dict:
    """套 tool 的 regex，保留每種 pattern 的最後一筆（對應 SDK 重試/舊 session）。"""
    found: dict = {}
    sl = tool.SCENE_LINE.findall(buf)
    au = tool.AUTH_URL_RE.findall(buf)
    ar = tool.AUTH_REPLY_RE.findall(buf)
    if sl:
        found["serverlist"] = sl[-1]
    if au:
        found["auth_url"] = au[-1]
    if ar:
        found["auth_reply"] = ar[-1]
    return found


def refresh_from_adb_device(
    d,
    ip: str,
    *,
    auth_dir: Path = AUTH_DIR,
    timeout_sec: float = 20.0,
) -> bool:
    """被動撈 logcat 登入鏈並寫/更新 capture 檔。成功回 True，否則 False。

    `d` 僅為與 utils.ws_ticket_refresh.refresh_from_device 簽名對稱保留；實際撈取
    走 subprocess `adb -s <ip> logcat -d`。一律 best-effort，絕不拋例外。
    """
    tool = _load_tool()
    if tool is None:
        return False

    deadline = time.monotonic() + max(0.0, timeout_sec)
    found: dict = {}
    for attempt in range(_MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            buf = _logcat_dump(ip, min(remaining, timeout_sec))
        except (subprocess.SubprocessError, OSError) as exc:
            logger.info("[%s] adb token scrape: logcat 失敗: %s", ip, exc)
            return False
        if buf:
            found = _scrape_buffer(tool, buf)
            if "auth_reply" in found and "serverlist" in found:
                break
        # 還有時間就稍候再 dump（SDK 可能還在印登入鏈）。
        if attempt + 1 < _MAX_ATTEMPTS and (deadline - time.monotonic()) > _RETRY_SLEEP_SEC:
            time.sleep(_RETRY_SLEEP_SEC)

    if "auth_reply" not in found or "serverlist" not in found:
        # warm start（App 只是 resume）下屬正常：用 info 級別避免每次喚醒洗 warning。
        logger.info("[%s] adb token scrape: logcat 未見登入鏈（可能非冷啟動），跳過", ip)
        return False

    try:
        creds = tool.build_creds(found, ip)
    except (RuntimeError, KeyError, ValueError, TypeError) as exc:
        logger.info("[%s] adb token scrape: build_creds 失敗: %s", ip, exc)
        return False

    if not creds.get("loginTicket") or not creds.get("uid"):
        logger.info("[%s] adb token scrape: ticket/uid 為空，跳過", ip)
        return False

    creds["_source"] = "adb_logcat_passive"
    path = Path(auth_dir) / f"_auth_capture_{ip}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"creds": creds}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.info("[%s] adb token scrape: 寫檔失敗: %s", ip, exc)
        return False

    logger.info("[%s] adb token scrape: 已被動寫入 ws_token capture (%s)", ip, path.name)
    return True
