"""一鍵抽卡 (技能/同伴) dashboard blueprint — pure WS over ``ws_session``.

一鍵抽卡 — ``ws_token.gacha.draw_once`` (cmd 0x0902). Long-running work runs in a
background thread tracked by the shared job registry in
``control_panel.tools_optimize_jobs``; the frontend polls
``/api/carpark/job/<job_id>`` (owned by ``routes_carpark_decorate_tools``).
"""
import logging
import time

from flask import Blueprint, jsonify, request

from control_panel import ws_session
from control_panel.routes_carpark_decorate_tools import _conn_lost
from control_panel.shared.auth import _fly_pet_auth
from control_panel.tools_optimize_jobs import (
    _job_log,
    _job_update,
    _jobs,
    _jobs_lock,
    _spawn,
)
from ws_token import gacha as gacha_logic

logger = logging.getLogger(__name__)

bp = Blueprint("gacha_tools", __name__)

# --- gacha (抽卡) pure-WS; ladder/cost/ids brain lives in ws_token.gacha ---
_DRAW_TYPES = gacha_logic.DRAW_TYPE_NAME           # {1:'技能', 2:'同伴'}
_DRAIN_LADDER = gacha_logic.BUNDLE_LADDER          # (999, 35, 15)
_BUNDLE_COST = gacha_logic.BUNDLE_COST             # {15:15, 35:30, 999:800}
_FIXED_COUNTS = tuple(gacha_logic.BUNDLE_COST)     # (15, 35, 999)
_DRAW_WS_TIMEOUT = 10           # per-draw WS call_for timeout (s)
_PROBE_TIMEOUT = 10             # 0x0401 + 0x0901 read-only confirmation
_DRAW_MIN_INTERVAL_S = 1.0      # 5554 live: 707ms dropped, 804ms accepted
_DRAIN_MAX_ITERS = 8000         # runaway guard for 一鍵抽完 (bundles)
_DRAW_MAX_BATCHES = 2000        # fixed-mode batch cap


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
            if res.get("status") != "ok":
                _job_update(jid, status="error",
                            error=f"WS 連線失敗：{res.get('message', '')}")
                return
            client = ws_session.get_client(ip)
        if client is None:
            _job_update(jid, status="error", error="WS 連線未就緒")
            return

        try:
            probe = gacha_logic.read_probe(
                client, draw_type, timeout=_PROBE_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — 讀不到就不可安全付費抽卡
            logger.warning("gacha 初始狀態讀取失敗 %s: %s", ip, exc)
            probe = None
        if probe is None:
            _job_update(jid, status="error",
                        error="無法讀取抽券與抽池狀態，未開始抽卡")
            return

        _job_update(jid, phase="drawing", result={
            "type": draw_type, "type_name": type_name, "mode": mode,
            "total": 0, "batches_done": 0, "stopped_reason": None})
        total = 0
        batches_done = 0
        stopped = None
        last_draw_started = None

        def _paced_draw(cnt: int):
            """依 request 起點節流；5554 live 證實 1 秒有安全餘裕。"""
            nonlocal last_draw_started
            if last_draw_started is not None:
                wait = (_DRAW_MIN_INTERVAL_S
                        - (time.monotonic() - last_draw_started))
                if wait > 0:
                    time.sleep(wait)
            last_draw_started = time.monotonic()
            return _draw_once(client, draw_type, cnt)

        def _draw_with_reconnect(cnt: int):
            """One draw; on transport loss reconnect once and retry the SAME
            bundle (mirrors the decoration executor). A landed-but-reply-lost
            draw retried = one extra bundle at worst — fine for drain (goal is
            spend-all) and rare enough for fixed."""
            nonlocal client
            res, err = _paced_draw(cnt)
            if err and _conn_lost(ip, err):
                _job_log(jid, f"  ⚠ WS 斷線（{err}），重連後重試…")
                if ws_session.ensure(ip).get("status") == "ok":
                    c2 = ws_session.get_client(ip)
                    if c2 is not None:
                        client = c2
                        res, err = _paced_draw(cnt)
            return res, err

        if mode == "drain":
            _job_log(jid, f"一鍵抽完（{type_name}）：依券餘額選 999/35/15，抽到不足即止")
            remaining = probe.ticket_count
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
                res, err = _draw_with_reconnect(rung)
                if err or not res or res.get("err"):
                    stopped = f"error:{err or (res or {}).get('err')}"
                    _job_log(jid, f"  {rung} 抽：傳輸錯誤（{stopped}）")
                    logger.warning("gacha drain %s 中止: %s", ip, stopped)
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
                # 0x0902 回覆是彙總後的獎勵「疊數」（live 5554: 999 抽回 24 疊），
                # 抽數以 bundle 大小計，疊數只進 log。
                stacks = int(res["drawn"])
                total += rung
                batches_done += 1
                rem = res.get("remaining")
                if rem is not None:
                    remaining = int(rem)
                elif remaining is not None:
                    remaining -= _BUNDLE_COST[rung]
                probe = gacha_logic.GachaProbe(
                    ticket_count=remaining,
                    pool_count=probe.pool_count + rung,
                )
                _job_log(jid, f"  {rung} 抽 → 成功（{stacks} 疊獎勵，累計 {total} 抽"
                              + (f"，券剩 {remaining:,}）" if remaining is not None else "）"))
                _set_gacha_progress(jid, total, batches_done)
        else:  # fixed: count × batches
            _job_log(jid, f"指定抽（{type_name}）：{count} 抽 × {batches} 批…")
            for b in range(batches):
                res, err = _draw_with_reconnect(count)
                if err or not res or res.get("rejected") or not res.get("ok") or int(res.get("drawn", 0)) <= 0:
                    reason = (f"reject code={res.get('error_code')}" if (res and res.get("rejected"))
                              else (res or {}).get("err") if res else err)
                    stopped = f"stopped:{reason or 'drawn=0'}"
                    _job_log(jid, f"  第 {b + 1} 批：停（{reason or 'drawn=0'}）")
                    logger.warning("gacha fixed %s 中止: %s", ip, stopped)
                    break
                stacks = int(res["drawn"])
                total += count
                batches_done += 1
                probe = gacha_logic.GachaProbe(
                    ticket_count=probe.ticket_count - _BUNDLE_COST[count],
                    pool_count=probe.pool_count + count,
                )
                rem = res.get("remaining")
                _job_log(jid, f"  [{b + 1}/{batches}] {count} 抽 → 成功（{stacks} 疊獎勵，累計 {total} 抽"
                              + (f"，券剩 {int(rem):,}）" if rem is not None else "）"))
                _set_gacha_progress(jid, total, batches_done)

        _job_update(jid, status="done", phase="done", result={
            "type": draw_type, "type_name": type_name, "mode": mode,
            "total": total, "batches_done": batches_done, "stopped_reason": stopped})
        _job_log(jid, f"完成：{type_name} 共抽 {total} 次（{batches_done} 批）")
    except Exception as exc:  # noqa: BLE001
        _job_update(jid, status="error", error=f"{type(exc).__name__}: {exc}")


# --- routes -----------------------------------------------------------------

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
