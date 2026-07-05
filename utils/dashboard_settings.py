"""dashboard_settings.py — dashboard 帳號/主機角色設定儲存層。

檔案: <project_root>/dashboard_settings.json (gitignored)
結構: {"accounts": [{username, password_hash, is_admin, status, visible_devices}],
       "host_role": {"mode":..., "master_url":...} | None}
"""
import json
import os
import re
import threading

from werkzeug.security import check_password_hash, generate_password_hash

_LOCK = threading.RLock()
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_settings_path = os.path.join(_PROJECT_ROOT, "dashboard_settings.json")

VALID_USERNAME = re.compile(r"^[A-Za-z0-9_]{3,32}$")

_LAST_ADMIN_ERR = "至少需保留一名管理員"


class SettingsCorruptError(Exception):
    pass


def set_settings_path(path):
    global _settings_path
    _settings_path = path


def settings_path():
    return _settings_path


def _migrate_initial():
    """檔案不存在時：由 env（或 legacy fallback）生成第一組管理員。"""
    user = os.environ.get("MUSHROOM_DASHBOARD_USER") or "infinite"
    pw = os.environ.get("MUSHROOM_DASHBOARD_PASS") or "infiniteroot"
    return {
        "accounts": [{
            "username": user,
            "password_hash": generate_password_hash(pw),
            "is_admin": True,
            "status": "active",
            "visible_devices": [],
        }],
        "host_role": None,
    }


def load_settings():
    with _LOCK:
        if not os.path.exists(_settings_path):
            data = _migrate_initial()
            _save(data)
            return data
        try:
            with open(_settings_path, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data.get("accounts"), list):
                raise ValueError("accounts missing")
            return data
        except (ValueError, OSError) as e:
            raise SettingsCorruptError(str(e)) from e


def _save(data):
    tmp = _settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _settings_path)


def _find(data, username):
    for acct in data["accounts"]:
        if acct["username"] == username:
            return acct
    return None


def _active_admin_count(accounts):
    return sum(1 for a in accounts if a.get("is_admin") and a.get("status") == "active")


def verify_login(username, password):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None or acct.get("status") != "active":
            return None
        if not check_password_hash(acct["password_hash"], password):
            return None
        return acct


def create_application(username, password):
    with _LOCK:
        data = load_settings()
        if not VALID_USERNAME.match(username):
            return "帳號名格式錯誤（3-32 字元，僅限英數與底線）"
        if _find(data, username) is not None:
            return "帳號已存在"
        data["accounts"].append({
            "username": username,
            "password_hash": generate_password_hash(password),
            "is_admin": False,
            "status": "pending",
            "visible_devices": [],
        })
        _save(data)
        return None


def approve_account(username, visible_devices):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None:
            return
        acct["status"] = "active"
        acct["visible_devices"] = list(visible_devices)
        _save(data)


def reject_account(username):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None:
            return
        data["accounts"].remove(acct)
        _save(data)


def list_accounts():
    with _LOCK:
        data = load_settings()
        result = []
        for acct in data["accounts"]:
            copy = {k: v for k, v in acct.items() if k != "password_hash"}
            result.append(copy)
        return result


def create_account(username, password, is_admin, visible_devices):
    with _LOCK:
        data = load_settings()
        if not VALID_USERNAME.match(username):
            return "帳號名格式錯誤（3-32 字元，僅限英數與底線）"
        if _find(data, username) is not None:
            return "帳號已存在"
        data["accounts"].append({
            "username": username,
            "password_hash": generate_password_hash(password),
            "is_admin": bool(is_admin),
            "status": "active",
            "visible_devices": list(visible_devices),
        })
        _save(data)
        return None


def delete_account(username):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None:
            return "帳號不存在"
        if acct.get("is_admin") and acct.get("status") == "active" \
                and _active_admin_count(data["accounts"]) <= 1:
            return _LAST_ADMIN_ERR
        data["accounts"].remove(acct)
        _save(data)
        return None


def set_password(username, password):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None:
            return "帳號不存在"
        acct["password_hash"] = generate_password_hash(password)
        _save(data)
        return None


def set_visible_devices(username, devices):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None:
            return "帳號不存在"
        acct["visible_devices"] = list(devices)
        _save(data)
        return None


def set_admin(username, is_admin):
    with _LOCK:
        data = load_settings()
        acct = _find(data, username)
        if acct is None:
            return "帳號不存在"
        if not is_admin and acct.get("is_admin") and acct.get("status") == "active" \
                and _active_admin_count(data["accounts"]) <= 1:
            return _LAST_ADMIN_ERR
        acct["is_admin"] = bool(is_admin)
        _save(data)
        return None


def get_host_role():
    with _LOCK:
        data = load_settings()
        return data.get("host_role")


def set_host_role(mode, master_url):
    with _LOCK:
        data = load_settings()
        if mode is None and master_url is None:
            data["host_role"] = None
        else:
            data["host_role"] = {"mode": mode, "master_url": master_url}
        _save(data)


def get_mount_tracker_enabled():
    """坐騎追蹤器背景掃描開關（預設關）。"""
    with _LOCK:
        data = load_settings()
        return bool(data.get("mount_tracker_enabled", False))


def set_mount_tracker_enabled(enabled):
    """設定坐騎追蹤器背景掃描開關。"""
    with _LOCK:
        data = load_settings()
        data["mount_tracker_enabled"] = bool(enabled)
        _save(data)


def pending_count():
    with _LOCK:
        data = load_settings()
        return sum(1 for a in data["accounts"] if a.get("status") == "pending")
