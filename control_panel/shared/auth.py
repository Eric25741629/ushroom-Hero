"""飛寵 dashboard 的 session 認證 decorator。"""
import functools
import os

from flask import jsonify, redirect, request, session

# Dashboard creds from env (MUSHROOM_DASHBOARD_USER / MUSHROOM_DASHBOARD_PASS) so
# they need not live in source; falls back to the legacy literal when unset
# (zero-disruption). SECURITY: set the env vars and rotate the leaked value —
# see docs/BACKEND_ARCH_AUDIT_2026-06-21.md §3.5.
_env_user = os.environ.get("MUSHROOM_DASHBOARD_USER")
_env_pass = os.environ.get("MUSHROOM_DASHBOARD_PASS")
_FLY_PET_USERS = (
    {_env_user: _env_pass} if _env_user and _env_pass
    else {"infinite": "infiniteroot"}
)


def _fly_pet_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("fly_pet_auth"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "unauthorized"}), 401
            return redirect("/fly-pet/login")
        return f(*args, **kwargs)
    return wrapper
