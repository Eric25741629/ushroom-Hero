"""飛寵 dashboard 的 session 認證 decorator。"""
import functools

from flask import jsonify, redirect, request, session

_FLY_PET_USERS = {"infinite": "infiniteroot"}


def _fly_pet_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("fly_pet_auth"):
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"status": "error", "message": "unauthorized"}), 401
            return redirect("/fly-pet/login")
        return f(*args, **kwargs)
    return wrapper
