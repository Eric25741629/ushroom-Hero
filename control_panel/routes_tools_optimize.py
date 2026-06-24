"""「工具 優化類」dashboard blueprint — 車位裝飾升級 + 純 WS 一鍵抽卡.

Two tools, all pure WS over ``ws_session`` persistent connections (no browser):

1. 最佳升級車位裝飾 — reads + buys + upgrades via
   ``ws_token.carpark_decoration_ws`` (car_park_info 12801 / shop_info 6913 /
   shop_buy 6914 / car_park_skin_up 12817); brain = ``ws_token.carpark_decoration``.
2. 一鍵抽卡 (技能/同伴) — ``ws_token.gacha.draw_once``.
3. 龍骸聖域 — ``ws_token.dragon_realm``.

Long-running work runs in background threads tracked by an in-memory job registry;
the frontend polls ``/api/carpark/job/<job_id>``.
"""
import json
import threading
import time
import uuid

from flask import Blueprint, jsonify, render_template, request

import bot_state
from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from ws_token import carpark_decoration_ws as deco_ws
from ws_token import gacha as gacha_logic
from ws_token import dragon_realm as dr_logic
from ws_token.carpark_decoration import DecoUpgradeState, plan_upgrades
from ws_token.mining import InventoryTracker

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
    coin = int(state.get("coin") or 0)
    # coin=0 (unknown via WS) + budget>0 → use the user-provided budget as-is
    eff_budget = budget if budget > 0 else coin
    if coin > 0:
        eff_budget = min(eff_budget, coin)
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
            time.sleep(0.4)

        _job_update(jid, status="done", phase="done", result={
            **plan, "executed": executed, "spent": spent,
            "stopped_reason": stopped})
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")


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


# ── dragon realm (龍骸聖域) ─────────────────────────────────────────────────

@bp.route("/api/dragon/status/<ip>")
@_fly_pet_auth
def dragon_status(ip):
    ip = _real_ip(ip)
    client = ws_session.get_client(ip)
    if not client:
        return jsonify({"status": "error", "message": "未連線，請先連線裝置"}), 400
    try:
        from ws_token import codec
        body = client.call(dr_logic.CMD_INFO, b"", timeout=5)
        d = codec.walk_dict(body)
        tracker = InventoryTracker()
        try:
            tracker.seed_from_query(client, timeout=5)
        except Exception:
            pass
        keys = tracker.counts.get(dr_logic.KEY_ITEM, 0)
        return jsonify({"status": "ok", "data": {
            "ceng": d.get(2, 0), "hp": d.get(3, 0), "keys": keys,
        }})
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@bp.route("/api/dragon/run/<ip>", methods=["POST"])
@_fly_pet_auth
def dragon_run(ip):
    ip = _real_ip(ip)
    jid = _spawn(_run_dragon_job, ip)
    return jsonify({"status": "ok", "job_id": jid})


def _run_dragon_job(jid: str, ip: str) -> None:
    _job_update(jid, phase="connecting")
    client = ws_session.get_client(ip)
    if not client:
        _job_update(jid, status="error", error="未連線")
        return
    tracker = InventoryTracker()
    old_handler = None
    try:
        old_handler = client._push_handler
        # chain: forward to old handler AND track inventory
        def _chain(cmd, body):
            tracker.on_push(cmd, body)
            if old_handler:
                old_handler(cmd, body)
        client.set_push_handler(_chain)
        try:
            tracker.seed_from_query(client, timeout=5)
        except Exception:
            _job_log(jid, "inventory seed 失敗，鑰匙初始數量可能不準")

        _job_update(jid, phase="exploring")
        _job_log(jid, f"開始：keys={tracker.counts.get(dr_logic.KEY_ITEM, 0)}")

        # wrap dr_logic.run with per-action logging
        import logging
        class _LogHandler(logging.Handler):
            def emit(self, record):
                _job_log(jid, record.getMessage())
        h = _LogHandler()
        h.setLevel(logging.INFO)
        dr_logger = logging.getLogger("ws_token.dragon_realm")
        dr_logger.addHandler(h)
        try:
            reason = dr_logic.run(client, tracker, max_actions=200)
        finally:
            dr_logger.removeHandler(h)

        _job_update(jid, status="done", result=reason,
                    phase=f"完成：{reason}")
        _job_log(jid, f"結束：{reason}")
    except Exception as exc:
        _job_update(jid, status="error", error=str(exc))
        _job_log(jid, f"錯誤：{exc}")
    finally:
        if old_handler is not None:
            client.set_push_handler(old_handler)
