"""車位裝飾升級 dashboard blueprint — pure WS, no browser.

最佳升級車位裝飾 — reads + buys + upgrades via ``ws_token.carpark_decoration_ws``
(car_park_info 12801 / shop_info 6913 / shop_buy 6914 / car_park_skin_up 12817);
brain = ``ws_token.carpark_decoration``.

Long-running work runs in background threads tracked by the shared job registry
in ``control_panel.tools_optimize_jobs``; the frontend polls
``/api/carpark/job/<job_id>`` (owned by this blueprint — it is the shared poll
endpoint for the gacha / dragon tools too).
"""
import time

from flask import Blueprint, jsonify, render_template, request

from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from control_panel.tools_optimize_jobs import (
    _job_log,
    _job_update,
    _jobs,
    _jobs_lock,
    _spawn,
)
from ws_token import carpark_decoration_ws as deco_ws
from ws_token.carpark_decoration import DecoUpgradeState, plan_upgrades

bp = Blueprint("carpark_decorate_tools", __name__)

# Safety bounds for the auto-spend executor.
_DEFAULT_MAX_STEPS = 30
_HARD_MAX_STEPS = 80
_READ_TIMEOUT = 25      # WS read is ~3-4s, not a 90s cocos walk
_EXEC_TIMEOUT = 45      # one buy(up to 30 frags)+upgrade step
_STEP_GAP_S = 10        # server silently drops skin_up sent too soon after the
                        # previous one (live 2026-07-05: ~1s dropped, 10s ok)


def _ws_client(ip: str):
    """Get or create a WS session client. Returns (client, err)."""
    client = ws_session.get_client(ip)
    if client is not None:
        return client, None
    res = ws_session.ensure(ip)
    if res.get("status") == "error":
        return None, res.get("message", "WS 連線失敗")
    client = ws_session.get_client(ip)
    if client is None:
        return None, "WS 連線未就緒"
    return client, None


def _read_state(ip: str):
    """Read decoration state via pure WS. Returns (state_dict, err)."""
    client, err = _ws_client(ip)
    if err:
        return None, err
    return deco_ws.read_state(client, timeout=_READ_TIMEOUT)


def _build_decos(state: dict):
    """state.decos -> (list[DecoUpgradeState], {id:(shop_id,name,price,level)})."""
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
        meta[d["id"]] = {"shop_id": d.get("shop_id"),
                         "name": d.get("name"), "price": d.get("price"),
                         "level": d.get("level")}
    return decos, meta


def _plan(state: dict, budget: int, max_steps: int):
    decos, meta = _build_decos(state)
    raw_coin = state.get("coin")
    coin = int(raw_coin) if raw_coin is not None else None
    # WS 不一定能讀到菇車幣。讀不到時不能假裝是 0；使用者有手填預算才可規劃。
    if coin is None:
        eff_budget = budget if budget > 0 else 0
    else:
        eff_budget = budget if budget > 0 else coin
        eff_budget = min(eff_budget, coin)
    if coin is None and budget <= 0:
        plan = None
    else:
        plan = plan_upgrades(decos, budget=eff_budget, max_steps=max_steps)
    steps = [{
        "id": s.id, "name": s.name,
        "shop_id": meta.get(s.id, {}).get("shop_id"),
        "from_level": s.from_level,
        "to_level": s.to_level, "frags": s.frags, "coin": s.coin,
        "attr_gain": s.attr_gain,
        "coin_per_attr": round(s.coin_per_attr, 3),
    } for s in (plan.steps if plan else ())]
    return {
        "coin": coin, "budget": eff_budget,
        "coin_source": state.get("coin_source"),
        "coin_error": state.get("coin_error") if coin is None else None,
        "steps": steps, "total_coin": plan.total_coin if plan else 0,
        "total_attr": plan.total_attr if plan else 0,
        "total_frags": plan.total_frags if plan else 0,
        "skipped_reason": (
            "coin_unknown_need_budget" if plan is None else plan.skipped_reason
        ),
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
        coin_text = f"{plan['coin']:,}" if plan["coin"] is not None else "未知"
        coin_note = f"（{plan['coin_error']}）" if plan.get("coin_error") else ""
        _job_log(jid, f"已擁有可升裝飾 {plan['owned_count']} 個，"
                      f"菇車幣 {coin_text}{coin_note}，計畫 {len(plan['steps'])} 步")
        _job_update(jid, status="done", phase="done", result=plan)
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")


def _exec_step(ip: str, step: dict):
    """Execute one buy+upgrade step via pure WS."""
    client, err = _ws_client(ip)
    if err:
        return None, err
    return deco_ws.exec_buy_and_upgrade(
        client,
        shop_id=int(step["shop_id"]),
        skin_id=int(step["id"]),
        frags=int(step["frags"]),
        timeout=_EXEC_TIMEOUT)


def _run_execute_job(jid: str, ip: str, budget: int, max_steps: int) -> None:
    # ws_session.ensure() already pauses the bot loop; no manual pause needed.
    try:
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
            if idx < len(steps):
                time.sleep(_STEP_GAP_S)

        _job_update(jid, status="done", phase="done", result={
            **plan, "executed": executed, "spent": spent,
            "stopped_reason": stopped})
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")


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

@bp.route("/tools-optimize")
@_fly_pet_auth
def tools_optimize_page():
    """「工具 優化類」分頁：車位裝飾升級 + 純 WS 一鍵抽卡。"""
    from control_panel.routes_pages import _get_frontend_version

    return render_template("tools_optimize.html", frontend_version=_get_frontend_version())


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
