"""車位工具 dashboard blueprint — 一鍵最佳升級車位裝飾 (+ 可擴充骨架).

Drives the device's live H5 page via the shared raw-CDP JS path
(``control_panel_app._cdp_evaluate``), running the SAME cocos buy/upgrade UI a
human uses (verified 2026-06-15, see docs/protocol/CARPARK_DECORATION_SHOP.md §9).
No new WS protocol; no Playwright. The injected JS lives in
``control_panel.carpark_tools_js``; the cost-effectiveness brain is the pure
``ws_token.carpark_decoration.plan_upgrades`` (coin-per-attr greedy).

Long-running work (the ~30s read walk and the multi-minute execute) runs in a
background thread tracked by an in-memory job registry; the frontend polls
``/api/carpark/job/<job_id>``. Execute pauses the device's bot loop
(``bot_state.set_pause``) so it doesn't fight the navigation, then resumes.
"""
import json
import threading
import time
import uuid

from flask import Blueprint, jsonify, render_template, request

import bot_state
from control_panel.carpark_tools_js import EXEC_STEP_JS, READ_STATE_JS
from control_panel.shared.auth import _fly_pet_auth
from ws_token.carpark_decoration import DecoUpgradeState, plan_upgrades

bp = Blueprint("carpark_tools", __name__)

# Safety bounds for the auto-spend executor.
_DEFAULT_MAX_STEPS = 30
_HARD_MAX_STEPS = 80
_READ_TIMEOUT = 90      # the walk reads ~16 decorations, ~1-2s each
_EXEC_TIMEOUT = 45      # one buy(up to 30 frags)+upgrade step

# In-memory job registry: job_id -> dict(status, phase, log, result, error).
_jobs: dict = {}
_jobs_lock = threading.Lock()


def _real_ip(ip: str) -> str:
    """Resolve the bot_state device key. A local TCP emulator id keeps its port
    (key == full id); only a remote/worker-prefixed id is stripped to the serial.
    (Blindly splitting on ':' would mis-key a local '127.0.0.1:5555' -> '5555'.)"""
    try:
        if bot_state.is_local_device(ip):
            return ip
    except Exception:  # noqa: BLE001
        pass
    return ip.split(":")[-1] if ":" in ip else ip


def _new_job() -> str:
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {"status": "running", "phase": "starting", "log": [],
                      "result": None, "error": None}
    return jid


def _job_update(jid: str, **kw) -> None:
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kw)


def _job_log(jid: str, msg: str) -> None:
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].setdefault("log", []).append(msg)


def _cdp_json(ip: str, expression: str, timeout: int):
    """Run injected JS that returns a JSON string; parse it. -> (obj, err)."""
    import control_panel_app as _cpa

    result, err = _cpa._cdp_evaluate(ip, expression, await_promise=True,
                                     timeout=timeout)
    if err:
        return None, err
    inner = (result or {}).get("result", {})
    if (result or {}).get("exceptionDetails"):
        return None, str(result["exceptionDetails"])
    if inner.get("type") == "string":
        try:
            return json.loads(inner["value"]), None
        except Exception as exc:  # noqa: BLE001
            return None, f"parse:{exc}"
    return None, "unexpected_result"


def _read_state(ip: str):
    return _cdp_json(ip, f"({READ_STATE_JS})()", _READ_TIMEOUT)


def _build_decos(state: dict):
    """state.decos -> (list[DecoUpgradeState], {id:(cat,cell,name,price,level)})."""
    decos = []
    meta = {}
    for d in state.get("decos", []):
        steps = d.get("steps") or []
        if not steps:
            continue  # free initial / maxed — nothing to plan
        decos.append(DecoUpgradeState(
            id=d["id"], name=d.get("name", str(d["id"])),
            price_per_frag=int(d.get("price", 0)),
            limit_remaining=int(d.get("limit_remaining", 0)),
            steps=tuple(tuple(int(x) for x in s) for s in steps)))
        meta[d["id"]] = {"cat": d.get("cat"), "cell": d.get("cell"),
                         "name": d.get("name"), "price": d.get("price"),
                         "level": d.get("level")}
    return decos, meta


def _plan(state: dict, budget: int, max_steps: int):
    decos, meta = _build_decos(state)
    coin = int(state.get("coin", 0))
    eff_budget = min(budget if budget > 0 else coin, coin)
    plan = plan_upgrades(decos, budget=eff_budget, max_steps=max_steps)
    steps = [{
        "id": s.id, "name": s.name, "cat": meta.get(s.id, {}).get("cat"),
        "cell": meta.get(s.id, {}).get("cell"), "from_level": s.from_level,
        "to_level": s.to_level, "frags": s.frags, "coin": s.coin,
        "attr_gain": s.attr_gain,
        "coin_per_attr": round(s.coin_per_attr, 3),
    } for s in plan.steps]
    return {
        "coin": coin, "budget": eff_budget,
        "steps": steps, "total_coin": plan.total_coin,
        "total_attr": plan.total_attr, "total_frags": plan.total_frags,
        "skipped_reason": plan.skipped_reason,
        "owned_count": len(decos),
    }


def _run_plan_job(jid: str, ip: str, budget: int, max_steps: int) -> None:
    try:
        _job_update(jid, phase="reading")
        _job_log(jid, "讀取裝置車位裝飾狀態…")
        state, err = _read_state(ip)
        if err:
            _job_update(jid, status="error", error=err)
            return
        _job_update(jid, phase="planning")
        plan = _plan(state, budget, max_steps)
        _job_log(jid, f"已擁有可升裝飾 {plan['owned_count']} 個，"
                      f"菇車幣 {plan['coin']:,}，計畫 {len(plan['steps'])} 步")
        _job_update(jid, status="done", phase="done", result=plan)
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")


def _exec_step(ip: str, step: dict):
    args = [int(step["cat"]), int(step["cell"]), int(step["frags"]), True,
            step.get("name")]
    return _cdp_json(ip, f"({EXEC_STEP_JS})({json.dumps(args)})", _EXEC_TIMEOUT)


def _run_execute_job(jid: str, ip: str, budget: int, max_steps: int) -> None:
    rip = _real_ip(ip)
    paused = False
    try:
        _job_update(jid, phase="pausing")
        _job_log(jid, f"暫停裝置 {rip} 的 bot loop…")
        try:
            bot_state.set_pause(rip, True)
            paused = True
            # set_pause no-ops (only warns) for an untracked device key — confirm
            # it actually took so we don't drive a still-running bot loop blindly.
            if bot_state.get_pause_event(rip) is None:
                _job_log(jid, f"⚠ 裝置 {rip} 未在 bot 追蹤中，未實際暫停（續行）")
        except Exception as exc:  # noqa: BLE001
            _job_log(jid, f"暫停失敗（續行）：{exc}")

        _job_update(jid, phase="reading")
        state, err = _read_state(ip)
        if err:
            _job_update(jid, status="error", error=f"read:{err}")
            return
        plan = _plan(state, budget, max_steps)
        steps = plan["steps"]
        _job_update(jid, phase="executing", result={**plan, "executed": [],
                                                    "stopped_reason": None})
        if not steps:
            _job_update(jid, status="done", phase="done",
                        result={**plan, "executed": [],
                                "stopped_reason": plan.get("skipped_reason")})
            return

        executed = []
        spent = 0
        stopped = None
        for idx, step in enumerate(steps, 1):
            if spent + step["coin"] > plan["budget"]:
                stopped = "budget_exhausted"
                break
            _job_log(jid, f"[{idx}/{len(steps)}] {step['name']} "
                          f"★{step['from_level']}→{step['to_level']} "
                          f"買{step['frags']}碎片 花{step['coin']:,}")
            res, e = _exec_step(ip, step)
            if e or not res or not res.get("ok"):
                reason = (res or {}).get("err") if res else e
                # if the buy committed (frags purchased) but the upgrade failed,
                # the coin WAS spent — count it so budget/report stay honest.
                coin_spent = step["coin"] if (res and res.get("bought")) else 0
                spent += coin_spent
                _job_log(jid, f"   ✗ 停止：{reason}"
                              + (f"（已扣 {coin_spent:,} 菇車幣）" if coin_spent else ""))
                executed.append({**step, "ok": False, "reason": reason,
                                 "coin_spent": coin_spent})
                stopped = f"step_failed:{reason}"
                break
            spent += step["coin"]
            executed.append({**step, "ok": True, "coin_spent": step["coin"],
                             "after_level": res.get("after_level")})
            _job_log(jid, f"   ✓ 升到 ★{res.get('after_level')}")
            with _jobs_lock:
                if jid in _jobs and _jobs[jid].get("result"):
                    _jobs[jid]["result"]["executed"] = list(executed)
            time.sleep(0.4)

        _job_update(jid, status="done", phase="done", result={
            **plan, "executed": executed, "spent": spent,
            "stopped_reason": stopped})
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        if paused:
            try:
                bot_state.set_pause(rip, False)
                _job_log(jid, f"已恢復裝置 {rip} 的 bot loop")
            except Exception:  # noqa: BLE001
                pass


def _spawn(target, *args) -> str:
    jid = _new_job()
    threading.Thread(target=target, args=(jid, *args), daemon=True).start()
    return jid


def _parse_params():
    src = request.get_json(silent=True) or request.args or {}
    try:
        budget = int(src.get("budget", 0) or 0)
    except (TypeError, ValueError):
        budget = 0
    try:
        max_steps = int(src.get("max_steps", _DEFAULT_MAX_STEPS) or _DEFAULT_MAX_STEPS)
    except (TypeError, ValueError):
        max_steps = _DEFAULT_MAX_STEPS
    max_steps = max(1, min(_HARD_MAX_STEPS, max_steps))
    return budget, max_steps


# --- routes -----------------------------------------------------------------

@bp.route("/carpark-tools")
@_fly_pet_auth
def carpark_tools_page():
    """車位工具分頁（最佳升級車位裝飾；未來可擴充更多按鈕）。"""
    return render_template("carpark_tools.html")


@bp.route("/api/carpark/plan/<ip>", methods=["POST", "GET"])
@_fly_pet_auth
def carpark_plan(ip):
    """讀狀態 + 算最佳升級計畫（預覽，不花費）。回 job_id，前端 poll。"""
    budget, max_steps = _parse_params()
    jid = _spawn(_run_plan_job, ip, budget, max_steps)
    return jsonify({"status": "ok", "job_id": jid})


@bp.route("/api/carpark/execute/<ip>", methods=["POST"])
@_fly_pet_auth
def carpark_execute(ip):
    """暫停裝置 → 讀 → 算 → 逐步買碎片+升級 → 恢復。回 job_id，前端 poll。"""
    budget, max_steps = _parse_params()
    jid = _spawn(_run_execute_job, ip, budget, max_steps)
    return jsonify({"status": "ok", "job_id": jid})


@bp.route("/api/carpark/job/<job_id>", methods=["GET"])
@_fly_pet_auth
def carpark_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        snap = dict(job) if job else None
    if snap is None:
        return jsonify({"status": "error", "message": "unknown job"}), 404
    return jsonify({"status": "ok", "job": snap})
