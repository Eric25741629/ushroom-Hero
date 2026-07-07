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

from control_panel.shared.auth import current_user, require_admin

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


def _rally(target_role_id: int, owner: int, pos: int) -> dict:
    """把某列坐騎「搶奪車位」卡片送到家族頻道（延遲 import 服務層；缺席時回 error 信封）。"""
    try:
        from runtime_services.mount_tracker_service import rally_to_guild
    except Exception:  # noqa: BLE001 — 服務層缺席不可讓路由回 500
        return {"status": "error", "message": "服務未就緒"}
    return rally_to_guild(target_role_id, owner, pos)


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
    """坐騎追蹤器頁面（掃描發現的玩家坐騎 + 追蹤名單管理）。

    頁面對路人公開，但「重建玩家庫」等管理操作只在登入後才顯示（``logged_in``）；
    後端仍以 ``@require_admin`` 為準，此旗標只控制 UI 露出，不當作授權。
    """
    from control_panel.routes_pages import _get_frontend_version

    return render_template("mount_tracker.html",
                           frontend_version=_get_frontend_version(),
                           logged_in=bool(current_user()))


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
    on = bool(body.get("on") if body.get("on") is not None else True)
    try:
        _store().mark_attacked(
            int(target_role_id), int(owner_role_id), int(start_time), on)
    except (ValueError, TypeError):
        # 非數字的 target_role_id / owner_role_id / start_time → 400 而非 500。
        return jsonify({"status": "error", "message": "參數格式錯誤"}), 400
    if on:
        # 標記已打掉 → mark_attacked 已設 scan_hold_until；催醒 daemon 讓它重排「暫緩後再掃」，
        # 否則它可能還在長間隔睡眠，5 分鐘後不會自己醒來刷新。
        _wake_scan()
    return jsonify({"status": "ok"})


@bp.route("/api/mount_tracker/scan_now", methods=["POST"])
def mount_tracker_scan_now():
    """立即催醒 daemon 全部重掃一輪（需登入——不在 PUBLIC_PATHS，由全站登入牆守門，
    避免路人 spam 觸發掃描借用帳號）。

    停用中則不催掃（daemon 迴圈本就會略過），回 ``enabled=False`` 供前端提示先開啟。
    """
    enabled = _get_enabled()
    if enabled:
        _wake_scan()
    return jsonify({"status": "ok", "enabled": enabled})


@bp.route("/api/mount_tracker/rally", methods=["POST"])
def mount_tracker_rally():  # 刻意公開（PUBLIC_PATHS）：使用者要求路人免登入即可分享
    """把某列坐騎的「搶奪車位」分享卡片送到家族頻道（搖人）。

    對路人公開（在 PUBLIC_PATHS）：使用者要求免登入即可分享。固定由 5554 送出
    （見服務層 ``RALLY_DEVICE``）。

    body：``{target_role_id, owner, pos}``——分別為停在車位的目標 roleId、車位主人
    roleId(master_id)、車位位置。三者缺一 → 400；非數字 → 400。服務結果以信封回傳
    （5554 忙碌 / 送出失敗 → ``status=error`` + 訊息，仍為 200 由前端顯示）。
    """
    body = request.get_json(silent=True) or {}
    target_role_id = body.get("target_role_id")
    owner = body.get("owner")
    pos = body.get("pos")
    if target_role_id is None or owner is None or pos is None:
        return jsonify({"status": "error",
                        "message": "缺少 target_role_id / owner / pos"}), 400
    try:
        res = _rally(int(target_role_id), int(owner), int(pos))
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "參數格式錯誤"}), 400
    return jsonify(res)


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
