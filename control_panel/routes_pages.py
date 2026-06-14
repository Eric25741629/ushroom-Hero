"""頁面與雜項路由 blueprint（index / updates / war-room / fly-pet / 版本 / 回饋）。"""
import datetime
import json
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, send_from_directory, session

from control_panel.shared.auth import _FLY_PET_USERS, _fly_pet_auth

bp = Blueprint("pages", __name__)

# 路徑常數：原始檔以 ``Path(__file__).resolve().parent`` 計算 repo root，但本檔位於
# control_panel/ 子目錄，必須改用 ``parents[1]`` 才能維持指向 repo 根目錄的同一路徑。
_WAR_ROOM_DIR = Path(__file__).resolve().parents[1] / "push_project" / "web"
_REDESIGN_DIR = Path(__file__).resolve().parents[1] / "docs" / "dashboard_redesigns"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_README_PATH = _REPO_ROOT / "README.md"
_UPDATE_PATH = _REPO_ROOT / "update.txt"
_BUG_FEEDBACK_PATH = _REPO_ROOT / "reports" / "bug_feedback.jsonl"
_TEMPLATES_DIR = _REPO_ROOT / "templates"


def _load_readme_text() -> str:
    try:
        return _README_PATH.read_text(encoding="utf-8-sig")
    except Exception as e:
        return f"README 讀取失敗: {e}"


def _load_update_text() -> str:
    try:
        return _UPDATE_PATH.read_text(encoding="utf-8-sig")
    except Exception as e:
        return f"update.txt 讀取失敗: {e}"


def _file_mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except Exception:
        return 0


def _get_frontend_version() -> str:
    tracked = [
        _TEMPLATES_DIR / "dashboard.html",
        _TEMPLATES_DIR / "readme_viewer.html",
        _UPDATE_PATH,
        Path(__file__).resolve(),
    ]
    return "-".join(str(_file_mtime(path)) for path in tracked)


@bp.route("/")
def index():
    """主控面板首頁"""
    from control_panel.routes_status import _get_program_info

    return render_template(
        "dashboard.html",
        program_info=_get_program_info(),
        frontend_version=_get_frontend_version(),
    )


@bp.route("/updates")
@bp.route("/updates/")
def updates_page():
    """Serve update.txt content inside the control panel."""
    return render_template(
        "readme_viewer.html",
        page_title="更新公告",
        page_subtitle="目前顯示的是 repo 根目錄的 `update.txt` 內容。",
        page_text=_load_update_text(),
        frontend_version=_get_frontend_version(),
    )


@bp.route("/war-room")
@bp.route("/war-room/")
def war_room_index():
    """Serve the existing cross-server parking battle room inside control panel."""
    return send_from_directory(str(_WAR_ROOM_DIR), "菇勇者.html")


@bp.route("/war-room/<path:filename>")
def war_room_static(filename):
    return send_from_directory(str(_WAR_ROOM_DIR), filename)


@bp.route("/dashboard-redesigns")
@bp.route("/dashboard-redesigns/")
def dashboard_redesigns_index():
    """比較頁：7 個 dashboard 重新設計方案（2026-06-14，待使用者挑選一個做正式主控台）。"""
    return send_from_directory(str(_REDESIGN_DIR), "index.html")


@bp.route("/dashboard-redesigns/<path:filename>")
def dashboard_redesigns_file(filename):
    return send_from_directory(str(_REDESIGN_DIR), filename)


@bp.route("/fly-pet/login", methods=["GET", "POST"])
def fly_pet_login():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        p = request.form.get("password") or ""
        if _FLY_PET_USERS.get(u) == p:
            session["fly_pet_auth"] = True
            return redirect("/fly-pet")
        return render_template("fly_pet_login.html", error="帳號或密碼錯誤")
    return render_template("fly_pet_login.html")


@bp.route("/fly-pet/logout")
def fly_pet_logout():
    session.pop("fly_pet_auth", None)
    return redirect("/fly-pet/login")


@bp.route("/fly-pet")
@_fly_pet_auth
def fly_pet_page():
    return render_template("fly_pet.html")


@bp.route("/api/bug_feedback", methods=["POST"])
def submit_bug_feedback():
    try:
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()
        detail = str(data.get("detail", "")).strip()
        reporter = str(data.get("reporter", "")).strip()
        page = str(data.get("page", "")).strip()
        if not title or not detail:
            return jsonify({"status": "error", "message": "title and detail are required"}), 400

        payload = {
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "title": title,
            "detail": detail,
            "reporter": reporter,
            "page": page,
        }
        _BUG_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _BUG_FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@bp.route("/api/frontend_version", methods=["GET"])
def get_frontend_version():
    return jsonify({"version": _get_frontend_version()})
