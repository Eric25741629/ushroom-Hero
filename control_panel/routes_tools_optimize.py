"""「工具 優化類」dashboard blueprint — 車位裝飾升級 + 純 WS 一鍵抽卡.

Two tools, both driving the device's live H5 page via the shared raw-CDP JS path
(``control_panel_app._cdp_evaluate``):

1. 最佳升級車位裝飾 — reads + buys + upgrades purely over the game WebSocket
   (car_park_info / shop_info / shop_buy / car_park_skin_up); see
   docs/protocol/CARPARK_DECORATION_SHOP.md §10. Injected JS in
   ``control_panel.carpark_tools_js``; brain = ``ws_token.carpark_decoration``.
2. 一鍵抽卡 (技能/同伴) — PURE WS over the persistent ws_session connection
   (mirrors the 神器附魔/庫存 panel): the job takes the device's
   ``ws_session.get_client(ip)`` and calls ``ws_token.gacha.draw_once`` directly
   (no browser / cocos UI / animation). Pause/resume of the bot loop is owned by
   ``ws_session`` (ensure pauses on connect, disconnect/sweeper resumes), so the
   gacha job does NOT touch ``bot_state.set_pause`` itself.

The carpark-decoration tool still drives the device's live H5 page via raw CDP
(``control_panel_app._cdp_evaluate``) — its read/exec JS reads cocos config tables
that have no headless ws_token reader yet, so it keeps the browser path.

Long-running work (the ~30s read walk and the multi-minute execute) runs in a
background thread tracked by an in-memory job registry; the frontend polls
``/api/carpark/job/<job_id>``. The carpark execute pauses the device's bot loop
(``bot_state.set_pause``) so it doesn't fight the navigation, then resumes.
"""
import json
import threading
import time
import uuid

from flask import Blueprint, jsonify, render_template, request

import bot_state
from control_panel import ws_session
from control_panel.carpark_tools_js import EXEC_STEP_WS_JS, READ_STATE_WS_JS
from control_panel.shared.auth import _fly_pet_auth
from ws_token import gacha as gacha_logic
from ws_token.carpark_decoration import DecoUpgradeState, plan_upgrades

bp = Blueprint("tools_optimize", __name__)

# Safety bounds for the auto-spend executor.
_DEFAULT_MAX_STEPS = 30
_HARD_MAX_STEPS = 80
_READ_TIMEOUT = 25      # WS read is ~3-4s, not a 90s cocos walk
_EXEC_TIMEOUT = 45      # one buy(up to 30 frags)+upgrade step

# --- gacha (抽卡) pure-WS; ladder/cost/ids brain lives in ws_token.gacha ---
_DRAW_TYPES = gacha_logic.DRAW_TYPE_NAME           # {1:'技能', 2:'同伴'}
_DRAIN_LADDER = gacha_logic.BUNDLE_LADDER          # (999, 35, 15)
_BUNDLE_COST = gacha_logic.BUNDLE_COST             # {15:15, 35:30, 999:800}
_FIXED_COUNTS = tuple(gacha_logic.BUNDLE_COST)     # (15, 35, 999)
_DRAW_WS_TIMEOUT = 10           # per-draw WS call_for timeout (s)
_DRAIN_MAX_ITERS = 8000         # runaway guard for 一鍵抽完 (bundles)
_DRAW_MAX_BATCHES = 2000        # fixed-mode batch cap

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
    return _cdp_json(ip, f"({READ_STATE_WS_JS})()", _READ_TIMEOUT)


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
    coin = int(state.get("coin", 0))
    eff_budget = min(budget if budget > 0 else coin, coin)
    plan = plan_upgrades(decos, budget=eff_budget, max_steps=max_steps)
    steps = [{
        "id": s.id, "name": s.name,
        "shop_id": meta.get(s.id, {}).get("shop_id"),
        "from_level": s.from_level,
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
    args = [int(step["shop_id"]), int(step["id"]), int(step["frags"]), True]
    return _cdp_json(ip, f"({EXEC_STEP_WS_JS})({json.dumps(args)})", _EXEC_TIMEOUT)


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


# --- gacha (抽卡) pure-WS ----------------------------------------------------


def _draw_once(client, draw_type: int, count: int):
    """Send one 0x0902 {type, count} over the persistent ws_session client.

    Returns ``(res, err)`` where ``res = {ok, drawn, remaining, rejected,
    error_code}`` — same shape the job loop already consumes. ``remaining`` is
    always ``None`` on the pure-WS path (ticket balance came from the CDP 0x0402
    side-channel; the drain ladder is reject-driven, so it isn't needed).
    ``rejected`` means the server replied 0x0201 (e.g. insufficient tickets)."""
    try:
        r = gacha_logic.draw_once(client, draw_type, count,
                                  timeout=_DRAW_WS_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — surface as transport error
        return None, f"{type(exc).__name__}: {exc}"
    return {
        "ok": bool(r.success),
        "drawn": int(r.drawn),
        "remaining": None,
        "rejected": bool(r.rejected),
        "error_code": r.error_code,
    }, None


def _set_gacha_progress(jid: str, total: int, batches_done: int) -> None:
    with _jobs_lock:
        job = _jobs.get(jid)
        if job and job.get("result"):
            job["result"]["total"] = total
            job["result"]["batches_done"] = batches_done


def _run_gacha_job(jid: str, ip: str, draw_type: int, mode: str,
                   count: int, batches: int) -> None:
    type_name = _DRAW_TYPES.get(draw_type, str(draw_type))
    try:
        # Pure-WS: take the persistent ws_session client (it owns the bot-loop
        # pause — ensure pauses on connect, disconnect/sweeper resumes — so we do
        # NOT touch bot_state.set_pause here, unlike the carpark CDP path).
        _job_update(jid, phase="connecting")
        client = ws_session.get_client(ip)
        if client is None:
            res = ws_session.ensure(ip)
            if res.get("status") == "error":
                _job_update(jid, status="error",
                            error=f"WS 連線失敗：{res.get('message', '')}")
                return
            client = ws_session.get_client(ip)
        if client is None:
            _job_update(jid, status="error", error="WS 連線未就緒")
            return

        _job_update(jid, phase="drawing", result={
            "type": draw_type, "type_name": type_name, "mode": mode,
            "total": 0, "batches_done": 0, "stopped_reason": None})
        total = 0
        batches_done = 0
        stopped = None

        if mode == "drain":
            _job_log(jid, f"一鍵抽完（{type_name}）：依券餘額選 999/35/15，抽到不足即止")
            remaining = None      # learned from each draw's 0x0402 ticket feedback
            fb_idx = 0            # fallback ladder index while remaining unknown
            iters = 0
            while True:
                iters += 1
                if iters > _DRAIN_MAX_ITERS:
                    stopped = "max_iters"
                    break
                if remaining is not None:
                    rung = gacha_logic.largest_affordable(remaining)
                    if rung is None:
                        stopped = "exhausted"
                        break
                else:
                    if fb_idx >= len(_DRAIN_LADDER):
                        stopped = "exhausted"
                        break
                    rung = _DRAIN_LADDER[fb_idx]
                res, err = _draw_once(client, draw_type, rung)
                if err or not res or res.get("err"):
                    stopped = f"error:{err or (res or {}).get('err')}"
                    _job_log(jid, f"  {rung} 抽：傳輸錯誤（{stopped}）")
                    break
                if res.get("rejected") or not res.get("ok") or int(res.get("drawn", 0)) <= 0:
                    reason = (f"reject code={res.get('error_code')}"
                              if res.get("rejected") else "drawn=0")
                    _job_log(jid, f"  {rung} 抽：停（{reason}）→ 換下一階")
                    if remaining is not None:
                        remaining = _BUNDLE_COST[rung] - 1   # correct stale estimate
                    else:
                        fb_idx += 1
                    continue
                drawn = int(res["drawn"])
                total += drawn
                batches_done += 1
                rem = res.get("remaining")
                if rem is not None:
                    remaining = int(rem)
                elif remaining is not None:
                    remaining -= _BUNDLE_COST[rung]
                _job_log(jid, f"  {rung} 抽 → +{drawn}（累計 {total}"
                              + (f"，券剩 {remaining:,}）" if remaining is not None else "）"))
                _set_gacha_progress(jid, total, batches_done)
        else:  # fixed: count × batches
            _job_log(jid, f"指定抽（{type_name}）：{count} 抽 × {batches} 批…")
            for b in range(batches):
                res, err = _draw_once(client, draw_type, count)
                if err or not res or res.get("rejected") or not res.get("ok") or int(res.get("drawn", 0)) <= 0:
                    reason = (f"reject code={res.get('error_code')}" if (res and res.get("rejected"))
                              else (res or {}).get("err") if res else err)
                    stopped = f"stopped:{reason or 'drawn=0'}"
                    _job_log(jid, f"  第 {b + 1} 批：停（{reason or 'drawn=0'}）")
                    break
                drawn = int(res["drawn"])
                total += drawn
                batches_done += 1
                rem = res.get("remaining")
                _job_log(jid, f"  [{b + 1}/{batches}] {count} 抽 → +{drawn}（累計 {total}"
                              + (f"，券剩 {int(rem):,}）" if rem is not None else "）"))
                _set_gacha_progress(jid, total, batches_done)

        _job_update(jid, status="done", phase="done", result={
            "type": draw_type, "type_name": type_name, "mode": mode,
            "total": total, "batches_done": batches_done, "stopped_reason": stopped})
        _job_log(jid, f"完成：{type_name} 共抽 {total} 次（{batches_done} 批）")
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")


# --- routes -----------------------------------------------------------------

@bp.route("/tools-optimize")
@_fly_pet_auth
def tools_optimize_page():
    """「工具 優化類」分頁：車位裝飾升級 + 純 WS 一鍵抽卡。"""
    return render_template("tools_optimize.html")


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


@bp.route("/api/gacha/draw/<ip>", methods=["POST"])
@_fly_pet_auth
def gacha_draw(ip):
    """純 WS 抽卡（cmd 0x0902）。body: {type:1|2, mode:"drain"|"fixed",
    count?:15|35|999, batches?:int}。回 job_id，前端 poll /api/carpark/job。"""
    src = request.get_json(silent=True) or {}
    try:
        draw_type = int(src.get("type", 0))
    except (TypeError, ValueError):
        draw_type = 0
    if draw_type not in _DRAW_TYPES:
        return jsonify({"status": "error", "message": "type 必須是 1(技能) 或 2(同伴)"}), 400
    mode = str(src.get("mode", "drain"))
    if mode not in ("drain", "fixed"):
        return jsonify({"status": "error", "message": "mode 必須是 drain 或 fixed"}), 400
    count, batches = 0, 1
    if mode == "fixed":
        try:
            count = int(src.get("count", 0))
            batches = int(src.get("batches", 1))
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "count/batches 需為整數"}), 400
        if count not in _FIXED_COUNTS:
            return jsonify({"status": "error",
                            "message": f"count 必須是 {list(_FIXED_COUNTS)}"}), 400
        batches = max(1, min(_DRAW_MAX_BATCHES, batches))
    jid = _spawn(_run_gacha_job, ip, draw_type, mode, count, batches)
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
