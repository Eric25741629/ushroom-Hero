"""坐騎追蹤器 (mount tracker) dashboard blueprint — 頁面 + targets/results/toggle API。

資料源與開關由服務層提供，該分支已 merge，本 branch 已具備：
- ``runtime_services.mount_tracker_service.get_store()`` — 追蹤器狀態儲存
- ``utils.dashboard_settings.get_mount_tracker_enabled`` / ``set_mount_tracker_enabled``

本模組對兩者仍一律走**延遲 import**（包在 module-level 間接函式內）以維持解耦，確保
(a) 服務層暫時缺席時本模組仍能 import 成功、
(b) 測試可 monkeypatch ``_store`` / ``_get_enabled`` / ``_set_enabled`` 這些間接口。

回應一律 ``{"status":"ok"|"error", ...}`` 信封。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from control_panel.shared.auth import require_admin

logger = logging.getLogger(__name__)

bp = Blueprint("mount_tracker", __name__)


# --- 服務層 / 設定開關的延遲 import 間接口（測試 monkeypatch 這幾個） -----------

def _store():
    """取得坐騎追蹤器狀態 store（延遲 import，服務層缺席時本模組仍可 import）。"""
    from runtime_services.mount_tracker_service import get_store
    return get_store()


def _get_enabled() -> bool:
    """讀取坐騎追蹤器是否啟用（延遲 import dashboard_settings）。"""
    from utils.dashboard_settings import get_mount_tracker_enabled
    return get_mount_tracker_enabled()


def _set_enabled(v: bool) -> None:
    """寫入坐騎追蹤器啟用開關（延遲 import dashboard_settings）。"""
    from utils.dashboard_settings import set_mount_tracker_enabled
    set_mount_tracker_enabled(bool(v))


def _progress() -> dict:
    """讀取掃描即時進度（延遲 import 服務層；in-memory 計數，無檔案 I/O）。"""
    from runtime_services.mount_tracker_service import get_progress
    return get_progress()


def _wake_scan() -> None:
    """催醒坐騎追蹤器 daemon 立刻掃一輪（延遲 import 服務層；服務層缺席時容錯 no-op）。"""
    try:
        from runtime_services.mount_tracker_service import wake_scan
    except Exception:  # noqa: BLE001 — 催掃失敗不可讓 toggle 回 500
        return
    wake_scan()


# --- UID 還原（roleId → 顯示用 UID）------------------------------------------

def _uid_of(role_id: int) -> str:
    """由 roleId 還原玩家可見 UID（已驗證的位元擾亂：取低 20 bit 的 5 位 hex 重排）。

    低 20 bit → 5 位大寫 hex（zero-pad），再依 d2 d0 d3 d1 d4 順序重排。合服後
    高位不同、低 20 bit 相同的 roleId 會對到同一 UID（多對一）。
    """
    low = role_id & 0xFFFFF
    s = format(low, "05X")
    return s[2] + s[0] + s[3] + s[1] + s[4]


def _known_name(info: object) -> str | None:
    """從 ``get_known()`` 的 value 取出玩家名稱（value 可能是 dict 或直接是名稱字串）。"""
    if isinstance(info, dict):
        return info.get("name")
    if info is None:
        return None
    return str(info)


def _resolve_offline(by: str, value: str):
    """對已知玩家（``get_known()``）做離線比對後新增 target。

    ``by`` 為 ``"uid"``（比對 ``_uid_of``，大小寫不敏感）或 ``"name"``（名稱完全相等）。
    0 筆 → error；1 筆 → add_target + ok；>1 筆 → error + candidates。
    """
    known = _store().get_known() or {}
    matches: list[dict] = []
    for key, info in known.items():
        rid = int(key)
        if by == "uid":
            hit = _uid_of(rid).lower() == value.lower()
        else:  # name
            hit = _known_name(info) == value
        if hit:
            matches.append({"role_id": rid, "name": _known_name(info)})

    if not matches:
        msg = ("尚未發現此 UID 的玩家，請改用 roleId 或等掃描發現"
               if by == "uid" else
               "尚未發現此名稱的玩家，請改用 roleId 或等掃描發現")
        return jsonify({"status": "error", "message": msg}), 404

    if len(matches) > 1:
        msg = ("UID 合服後對到多個玩家，請選一個"
               if by == "uid" else
               "此名稱對到多個玩家，請選一個")
        return jsonify({"status": "error", "message": msg, "candidates": matches}), 409

    m = matches[0]
    rid = m["role_id"]
    _store().add_target({
        "role_id": rid,
        "name": m["name"],
        "uid": value if by == "uid" else _uid_of(rid),
    })
    return jsonify({"status": "ok"})


# --- routes -----------------------------------------------------------------

@bp.route("/mount-tracker")
def mount_tracker_page():  # 刻意公開（PUBLIC_PATHS）：路人不登入亦可檢視/編輯
    """坐騎追蹤器頁面（掃描發現的玩家坐騎 + 追蹤名單管理）。"""
    from control_panel.routes_pages import _get_frontend_version

    return render_template("mount_tracker.html", frontend_version=_get_frontend_version())


@bp.route("/api/mount_tracker/results", methods=["GET"])
def mount_tracker_results():  # 刻意公開（PUBLIC_PATHS）
    """回傳追蹤器快照 + 啟用狀態 + 即時進度。

    snapshot() 內含 targets/results/known_count/last_run/running；``progress`` 為純記憶體
    即時掃描進度（phase/scanned/found/known/guilds/members），供頁面輪詢即時跳動。
    """
    s = _store().snapshot()
    return jsonify({"status": "ok", "enabled": _get_enabled(), "progress": _progress(), **s})


@bp.route("/api/mount_tracker/targets", methods=["POST"])
def mount_tracker_targets():  # 刻意公開（PUBLIC_PATHS）：路人可增刪目標
    """新增/移除追蹤 target。

    body 型態（依序判斷）：
      - ``{"remove": role_id}``            → 移除該 target。
      - ``{"role_id": rid, name?, uid?}``  → 直接以 roleId 新增。
      - ``{"uid": u}``（無 role_id）        → 對已知玩家離線比對 UID 後新增。
      - ``{"name": n}``（無 role_id/uid）    → 對已知玩家離線比對名稱後新增。
    """
    body = request.get_json(silent=True) or {}

    try:
        # 移除
        if body.get("remove") is not None:
            _store().remove_target(int(body["remove"]))
            return jsonify({"status": "ok"})

        # 直接以 roleId 新增
        if body.get("role_id") is not None:
            rid = int(body["role_id"])
            _store().add_target({
                "role_id": rid,
                "name": body.get("name"),
                "uid": body.get("uid"),
            })
            return jsonify({"status": "ok"})
    except (ValueError, TypeError):
        # 非數字的 remove / role_id → 400 而非 500。
        return jsonify({"status": "error", "message": "參數格式錯誤"}), 400

    # UID 離線解析
    uid = body.get("uid")
    if uid:
        return _resolve_offline("uid", str(uid))

    # 名稱離線解析
    name = body.get("name")
    if name:
        return _resolve_offline("name", str(name))

    return jsonify({"status": "error", "message": "缺少 role_id / uid / name"}), 400


@bp.route("/api/mount_tracker/mark", methods=["POST"])
def mount_tracker_mark():  # 刻意公開（PUBLIC_PATHS）：路人可標記已打掉
    """標記 / 取消標記某台坐騎為「已打掉」（軟標記，跨掃描保留）。

    body：``{target_role_id, owner_role_id, start_time, on?}``（``on`` 預設 True）。
    實例唯一鍵 = ``f"{owner_role_id}:{start_time}"``。三個 id 缺一即回 error。
    """
    body = request.get_json(silent=True) or {}
    target_role_id = body.get("target_role_id")
    owner_role_id = body.get("owner_role_id")
    start_time = body.get("start_time")
    if target_role_id is None or owner_role_id is None or start_time is None:
        return jsonify({"status": "error",
                        "message": "缺少 target_role_id / owner_role_id / start_time"}), 400
    on = body.get("on")
    try:
        _store().mark_attacked(
            int(target_role_id), int(owner_role_id), int(start_time),
            bool(on if on is not None else True))
    except (ValueError, TypeError):
        # 非數字的 target_role_id / owner_role_id / start_time → 400 而非 500。
        return jsonify({"status": "error", "message": "參數格式錯誤"}), 400
    return jsonify({"status": "ok"})


@bp.route("/api/mount_tracker/toggle", methods=["POST"])
@require_admin
def mount_tracker_toggle():
    """啟用/停用坐騎追蹤器（限管理員）。"""
    enabled = bool((request.get_json(silent=True) or {}).get("enabled"))
    _set_enabled(enabled)
    if enabled:
        _wake_scan()  # 開啟時立即催掃一輪，不必等下一個整點間隔。
    return jsonify({"status": "ok", "enabled": enabled})


@bp.route("/api/mount_tracker/rebootstrap", methods=["POST"])
@require_admin
def mount_tracker_rebootstrap():
    """重建玩家庫（限管理員）：清除 bootstrap 完成戳並催掃。

    下一輪 daemon 見 ``bootstrap_done is None`` 會重跑一次性 guild-scan，把 level>=5
    公會的全體成員重新灌進 known_players。
    """
    _store().set_bootstrap_done(None)
    _wake_scan()  # 立即催醒 daemon 跑下一輪（會先做 bootstrap）。
    return jsonify({"status": "ok"})
