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
_STEP_GAP_S = 10        # proven-safe skin_up→skin_up spacing (live 2026-07-05:
                        # ~1s dropped, 10s ok); also the wait before a reconnect
_FAST_GAP_S = 5         # optimistic spacing; exact threshold is unknown between
                        # 1s and 10s — one detected drop backs off to _STEP_GAP_S


def _ws_client(ip: str):
    """Get or create a WS session client. Returns (client, err)."""
    client = ws_session.get_client(ip)
    if client is not None:
        return client, None
    res = ws_session.ensure(ip)
    if res.get("status") != "ok":
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
            steps=tuple(tuple(int(x) for x in s) for s in steps),
            held_frags=int(d.get("held_frags", 0))))
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
        "price": meta.get(s.id, {}).get("price"),
        "from_level": s.from_level,
        "to_level": s.to_level, "frags": s.frags,
        "buy_frags": s.buy_frags,  # 折抵持有後實際購買量(顯示用);frags=總需
        "coin": s.coin,
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


def _exec_step(ip: str, step: dict, gap: float = _STEP_GAP_S):
    """Execute one buy+upgrade step via pure WS.

    ``gap`` = min spacing between skin_up sends; the wait happens INSIDE the
    exec (counted from the previous send), so read/buy time is credited.

    When the decoration's frags were pre-bought as a batch (see ``_prebuy_group``)
    the held-frags accounting in ``exec_buy_and_upgrade`` sees them and buys
    nothing here — this becomes a pure skin_up. Passing the real per-star
    ``frags`` (not 0) keeps the self-heal buy as a safety net if the batch
    estimate ever undershoots.
    """
    client, err = _ws_client(ip)
    if err:
        return None, err
    return deco_ws.exec_buy_and_upgrade(
        client,
        shop_id=int(step["shop_id"]),
        skin_id=int(step["id"]),
        frags=int(step["frags"]),
        target_level=int(step["to_level"]),
        timeout=_EXEC_TIMEOUT,
        skin_up_gap=gap)


def _prebuy_group(ip: str, shop_id: int, skin_id: int, frags: int):
    """Buy a decoration's WHOLE frag batch in ONE 6914 (held-frags aware).

    ``do_upgrade=False`` so no star is consumed. ``exec_buy_and_upgrade`` only
    buys the shortfall over frags already held, so a retry after a dropped
    connection never double-buys. Returns (result, err); result.frags_bought =
    frags actually purchased this call (0 if already fully held).
    """
    client, err = _ws_client(ip)
    if err:
        return None, err
    return deco_ws.exec_buy_and_upgrade(
        client,
        shop_id=int(shop_id),
        skin_id=int(skin_id),
        frags=int(frags),
        do_upgrade=False,
        timeout=_EXEC_TIMEOUT)


# Exception names that mean the WS transport died (vs a game-logic reject).
_CONN_ERR_MARKERS = ("ConnectionClosed", "ConnectionReset", "BrokenPipe",
                     "ConnectionAborted", "socket is already closed")


def _conn_lost(ip: str, err: str) -> bool:
    """True when a step failure is a transport loss worth one reconnect."""
    if any(m in err for m in _CONN_ERR_MARKERS):
        return True
    return ws_session.get_client(ip) is None


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

        # 同類項合併：選中的步驟集合不變（計畫仍最省），但執行時把同一裝飾的
        # 步驟聚在一起 → 該裝飾整批碎片一次 6914 買齊，再連續升星。步驟在 steps
        # 內已是各裝飾的星級遞增（貪心逐階推進），依 id 首次出現排序保留成本序。
        groups: list[tuple[int, list[dict]]] = []
        by_id: dict[int, list[dict]] = {}
        for step in steps:
            bucket = by_id.get(step["id"])
            if bucket is None:
                bucket = by_id[step["id"]] = []
                groups.append((step["id"], bucket))
            bucket.append(step)

        executed = []
        spent = 0
        stopped = None
        gap = _FAST_GAP_S
        step_no = 0
        for _deco_id, gsteps in groups:
            head = gsteps[0]
            name, shop_id = head["name"], head["shop_id"]
            # 每碎片真實單價:coin 已折抵持有碎片,不能再用 coin//frags 反推。
            unit = int(head.get("price") or 0) or (
                head["coin"] // head["frags"] if head["frags"] else 0)
            total_frags = sum(s["frags"] for s in gsteps)
            total_buy = sum(s.get("buy_frags", s["frags"]) for s in gsteps)
            group_coin = sum(s["coin"] for s in gsteps)
            if spent + group_coin > plan["budget"]:
                stopped = "budget_exhausted"
                break

            # ── 整批預買碎片（一次 6914）───────────────────────────────────
            if total_frags > 0:
                held_note = (f"（需 {total_frags}，持有折抵後買 {total_buy}）"
                             if total_buy < total_frags else f" {total_buy} 碎片")
                _job_log(jid, f"{name} 一次買齊{held_note}"
                              f"（升 ★{head['from_level']}→{gsteps[-1]['to_level']}）")
                res, e = _prebuy_group(ip, shop_id, _deco_id, total_frags)
                if e and _conn_lost(ip, e):
                    _job_log(jid, f"   ⚠ WS 斷線（{e}），重連後重試買碎片…")
                    time.sleep(_STEP_GAP_S)
                    if ws_session.ensure(ip).get("status") == "ok":
                        res, e = _prebuy_group(ip, shop_id, _deco_id, total_frags)
                if e or not res or not res.get("ok"):
                    reason = (res or {}).get("err") if res else e
                    fb = (res or {}).get("frags_bought") or 0
                    coin_spent = unit * fb
                    spent += coin_spent
                    _job_log(jid, f"   ✗ 買碎片停止：{reason}"
                                  + (f"（已扣 {coin_spent:,} 菇車幣）" if coin_spent else ""))
                    stopped = f"prebuy_failed:{reason}"
                    break
                pb_frags = res.get("frags_bought") or 0
                spent += unit * pb_frags
                if pb_frags:
                    held_note = (f"（其餘 {total_frags - pb_frags} 持有折抵）"
                                 if pb_frags < total_frags else "")
                    _job_log(jid, f"   ✓ 已買 {pb_frags} 碎片，花 {unit * pb_frags:,}"
                                  + held_note)
                else:
                    _job_log(jid, f"   ✓ 持有碎片已足（{total_frags}），免購")

            # ── 連續升星（碎片已持有 → 純升星）─────────────────────────────
            broke = False
            for step in gsteps:
                step_no += 1
                _job_log(jid, f"[{step_no}/{len(steps)}] {name} "
                              f"★{step['from_level']}→{step['to_level']} 升星")
                res, e = _exec_step(ip, step, gap)
                if e and _conn_lost(ip, e):
                    # 斷線那步的升可能已入帳、也可能晚到：等滿一個冷卻再重連。
                    # target_level 護欄保證重試不多升；碎片已持有不重買。
                    _job_log(jid, f"   ⚠ WS 斷線（{e}），重連後重試…")
                    time.sleep(_STEP_GAP_S)
                    if ws_session.ensure(ip).get("status") == "ok":
                        res, e = _exec_step(ip, step, gap)
                if res and res.get("resent") and gap < _STEP_GAP_S:
                    # skin_up 被冷卻靜默丟棄過：樂觀間隔太短，退回實測安全值。
                    gap = _STEP_GAP_S
                    _job_log(jid, f"   ⚠ 偵測到 skin_up 冷卻丟棄，間隔改回 {gap}s")
                if e or not res or not res.get("ok"):
                    reason = (res or {}).get("err") if res else e
                    # 預買已扣款；升星自我修復若補買了碎片也計入。
                    coin_spent = unit * ((res or {}).get("frags_bought") or 0)
                    spent += coin_spent
                    _job_log(jid, f"   ✗ 停止：{reason}"
                                  + (f"（補買扣 {coin_spent:,}）" if coin_spent else ""))
                    executed.append({**step, "ok": False, "reason": reason,
                                     "coin_spent": coin_spent})
                    stopped = f"step_failed:{reason}"
                    broke = True
                    break
                coin_spent = unit * (res.get("frags_bought") or 0)
                spent += coin_spent
                executed.append({**step, "ok": True, "coin_spent": coin_spent,
                                 "after_level": res.get("after_level")})
                _job_log(jid, f"   ✓ 升到 ★{res.get('after_level')}")
                with _jobs_lock:
                    if jid in _jobs and _jobs[jid].get("result"):
                        _jobs[jid]["result"]["executed"] = list(executed)
            if broke:
                break

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
