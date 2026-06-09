"""Playwright 階段順手回寫 ws_token creds（自癒迴圈, spec §5）。

遊戲頁載入完成後，page 內的 LoginDataCache 持有最新 login ticket。
refresh_from_device(d, ip) 用 page.evaluate（in-process，不踢 session、不需 CDP
attach）讀出來，merge 回 auth_state/_auth_capture_<ip>.json —— 只更新會過期的
欄位（loginTicket/pKey/loginTime/...），保留 page 上讀不到的 uname/plat。
下一輪 WS 階段永遠拿到 <1 cycle 舊的 ticket。

一律 best-effort：任何失敗只 log 回 False，絕不打斷 wake cycle。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AUTH_DIR = Path(__file__).resolve().parents[1] / "auth_state"

# 與 tools/_auth_capture_probe.py 同源：LoginDataCache 是 chunks 虛擬模組。
# netManager 的 ws url 帶著當前 session 的 token (AUTH_HANDSHAKE_SPEC §7.1)。
# 屬性名 live-confirm 步驟在計畫 Task 11.1（上線前先用 probe 對小寶驗一次）。
_CAPTURE_JS = """
async () => {
  const mod = await System.import('chunks:///_virtual/LoginDataCache.ts');
  const L = mod.LoginDataCache;
  let ws = '';
  try { ws = netManager._cnet._socket.url || ''; } catch (e) {}
  return {
    uid: String(L.uid ?? ''),
    loginGameId: String(L.loginGameId ?? ''),
    roleId: Number(L.roleId ?? 0),
    pKey: String(L.pKey ?? ''),
    loginTicket: String(L.loginTicket ?? ''),
    loginSceneId: Number(L.loginSceneId ?? 0),
    isWhiteIp: Number(L.isWhiteIp ?? 0),
    loginTime: Number(L.loginTime ?? 0),
    _ws_url: ws,
  };
}
"""

# merge 進 capture 的欄位（page 讀得到、且會過期/變動的）
_REFRESH_KEYS = ("uid", "loginGameId", "roleId", "pKey", "loginTicket",
                 "loginSceneId", "isWhiteIp", "loginTime", "_ws_url")


def refresh_from_device(d, ip: str, *, auth_dir: Path = AUTH_DIR) -> bool:
    """從 d._page 讀 LoginDataCache 並 merge 回 capture 檔。成功回 True。"""
    page = getattr(d, "_page", None)
    if page is None:
        logger.debug("[%s] ws ticket refresh: no _page on device, skip", ip)
        return False
    path = Path(auth_dir) / f"_auth_capture_{ip}.json"
    if not path.exists():
        logger.info("[%s] ws ticket refresh: 無既有 capture 檔 (%s)，跳過", ip, path)
        return False
    try:
        fresh = page.evaluate(_CAPTURE_JS)
    except Exception as exc:  # noqa: BLE001 — page 可能剛好關閉/導航，不能炸 wake cycle
        logger.warning("[%s] ws ticket refresh: page.evaluate 失敗: %s", ip, exc)
        return False
    if not isinstance(fresh, dict) or not fresh.get("loginTicket"):
        logger.warning("[%s] ws ticket refresh: 讀不到 loginTicket，跳過", ip)
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        creds = dict(data.get("creds") or {})
        for k in _REFRESH_KEYS:
            if fresh.get(k) not in (None, "", 0) or k == "isWhiteIp":
                creds[k] = fresh[k]
        data["creds"] = creds
        data["_source"] = "playwright_refresh"
        data["_captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        age_h = (time.time() - float(creds.get("loginTime") or 0)) / 3600.0
        logger.info("[%s] ws ticket refresh: 已回寫 ticket (loginTime age %.1fh)",
                    ip, age_h)
        return True
    except (OSError, ValueError) as exc:
        logger.warning("[%s] ws ticket refresh: 寫回失敗: %s", ip, exc)
        return False
