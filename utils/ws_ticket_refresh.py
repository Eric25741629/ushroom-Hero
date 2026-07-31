"""Playwright 階段順手回寫 ws_token creds（自癒迴圈, spec §5）。

遊戲頁載入完成後，page 內的 LoginDataCache 持有最新 login payload。
refresh_from_device(d, ip) 用 page.evaluate（in-process，不踢 session、不需 CDP
attach）讀出來，merge 回 auth_state/_auth_capture_<ip>.json。LoginDataCache 同時
提供 uname/plat 等不會顯示在畫面上的欄位，所以無既有 capture 時也能建立
load_creds() 可讀的完整 seed；若目前版本仍讀不到必填欄位，則保留 partial seed
給 online monitor 使用，但不會誤觸發 WS 登入。

一律 best-effort：任何失敗只 log 回 False，絕不打斷 wake cycle。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ws_token.creds import Creds, load_creds

logger = logging.getLogger(__name__)

AUTH_DIR = Path(__file__).resolve().parents[1] / "auth_state"

# 與 tools/_auth_capture_probe.py 同源：LoginDataCache 是 chunks 虛擬模組。
# netManager 的 ws url 帶著當前 session 的 token (AUTH_HANDSHAKE_SPEC §7.1)。
# 屬性名 live-confirm 步驟在計畫 Task 11.1（上線前先用 probe 對小寶驗一次）。
_CAPTURE_JS = """
async () => {
  const mod = await System.import('chunks:///_virtual/LoginDataCache.ts');
  const L = IS(mod.LoginDataCache);
  const gatewayInfo = L.gateWayInfo || {};
  const loginServer = L.loginServer || {};
  let ws = '';
  try { ws = netManager._cnet._socket.url || ''; } catch (e) {}
  return {
    uid: String(L.uid ?? ''),
    uname: String(L.uname ?? ''),
    plat: String(L.plat ?? ''),
    loginGameId: String(L.loginGameId ?? ''),
    roleId: Number(L.roleId ?? 0),
    pKey: String(L.pKey ?? ''),
    loginTicket: String(L.loginTicket ?? ''),
    loginSceneId: Number(L.loginSceneId ?? 0),
    isWhiteIp: Number(L.isWhiteIp ?? 0),
    loginTime: Number(L.loginTime ?? 0),
    gateway: String(gatewayInfo.ip ?? ''),
    game_server: String(loginServer.game_server ?? ''),
    _ws_url: ws,
  };
}
"""

# merge 進 capture 的欄位（LoginDataCache 讀得到，且 ticket 可能變動）
_REFRESH_KEYS = ("uid", "uname", "plat", "loginGameId", "roleId", "pKey",
                 "loginTicket", "loginSceneId", "isWhiteIp", "loginTime",
                 "gateway", "game_server", "_ws_url")


def _is_complete_capture(creds: dict) -> bool:
    """用 Creds 的正規驗證確認 seed 能否直接交給 WS runner。"""
    try:
        Creds.from_dict(creds)
    except Exception:  # noqa: BLE001 — capture 驗證失敗只能保留 fallback
        return False
    return True


def refresh_from_device(d, ip: str, *, auth_dir: Path = AUTH_DIR) -> bool:
    """從 d._page 讀 LoginDataCache 並 merge 回 capture 檔。成功回 True。"""
    page = getattr(d, "_page", None)
    if page is None:
        logger.debug("[%s] ws ticket refresh: no _page on device, skip", ip)
        return False
    path = Path(auth_dir) / f"_auth_capture_{ip}.json"
    seeding = not path.exists()
    try:
        fresh = page.evaluate(_CAPTURE_JS)
    except Exception as exc:  # noqa: BLE001 — page 可能剛好關閉/導航，不能炸 wake cycle
        logger.warning("[%s] ws ticket refresh: page.evaluate 失敗: %s", ip, exc)
        return False
    if not isinstance(fresh, dict) or not fresh.get("loginTicket"):
        logger.warning("[%s] ws ticket refresh: 讀不到 loginTicket，跳過", ip)
        return False
    # 種第一份時 roleId 是這份檔唯一的價值（給 online monitor 認帳號），沒有就別種。
    if seeding and not fresh.get("roleId"):
        logger.info("[%s] ws ticket refresh: seed 讀不到 roleId，跳過建檔", ip)
        return False
    try:
        data = {} if seeding else json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("capture root must be an object")
        raw_creds = data.get("creds") or {}
        if not isinstance(raw_creds, dict):
            raise ValueError("capture creds must be an object")
        creds = dict(raw_creds)
        for k in _REFRESH_KEYS:
            if fresh.get(k) not in (None, "", 0) or k == "isWhiteIp":
                creds[k] = fresh[k]
        complete = _is_complete_capture(creds)
        data["creds"] = creds
        data["_source"] = "playwright_seed" if seeding else "playwright_refresh"
        data["_partial"] = not complete
        data["_captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")

        # 完整 seed 必須真的能被讀回，否則下一輪仍會走 H5 fallback。
        if complete:
            try:
                load_creds(ip, auth_dir=Path(auth_dir))
            except Exception as exc:  # noqa: BLE001 — 讀取失敗不可中斷 H5
                logger.warning("[%s] ws ticket refresh: capture 讀回驗證失敗: %s",
                               ip, exc)
                return False

        try:
            age_h = (time.time() - float(creds.get("loginTime") or 0)) / 3600.0
        except (TypeError, ValueError):
            age_h = 0.0
        mode = "完整 seed" if complete and seeding else (
            "partial seed" if seeding else "已回寫 ticket")
        logger.info("[%s] ws ticket refresh: %s (loginTime age %.1fh)",
                    ip, mode, age_h)
        return True
    except (OSError, TypeError, ValueError, KeyError) as exc:
        logger.warning("[%s] ws ticket refresh: 寫回失敗: %s", ip, exc)
        return False
