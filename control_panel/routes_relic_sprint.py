"""遺物碎片均勻升級(衝刺) dashboard blueprint — 純 WS 兩階段(規劃→確認執行).

資料源與「看廣告獎勵 / 神器附魔 / 庫存」面板完全一致：走
``control_panel/ws_session.py`` 的持久純 WS 連線（不開瀏覽器、有連線/閒置/踢線
處理），路由用 ``ws_session.get_client(ip)`` 取得已登入的 ``WSGameClient``，再呼叫
``ws_token.relic_sprint`` / ``ws_token.relic`` 的 Python orchestrator。

兩階段 UX（比照車位裝飾 plan→execute）：
  - 規劃 ``GET /api/relic_sprint/plan/<ip>`` — **只讀**：探測本期活動 act_type、讀
    sprint 進度(已累計/4輪門檻/可領狀態)與遺物碎片現量，回預覽 JSON，不送升級。
  - 執行 ``POST /api/relic_sprint/execute/<ip>`` — 走 ``run_relic_sprint``：從最小
    等級遺物開始升級、把累計消耗推到 900K，再領取所有 CanGet 輪獎。

⚠ tracker / 碎片現量來源（重要缺口，見 module docstring 末段）：
  ``run_relic_sprint`` 需要一個 ``InventoryTracker``（``counts: dict[int,int]`` 由
  0x0402 push 餵養）來量測 100022(遺物碎片) 的即時消耗。dashboard 的持久 client 在
  ``ws_session.ensure`` 建立時**沒有**掛 InventoryTracker，且登入 0x0402 快照本就
  **不保證**帶 item 100022（同挖礦鎬子 4001 的情況）。故：
    * 本模組維護 per-ip 的 InventoryTracker registry，於每次取得 client 時補掛
      ``client.set_push_handler(tracker.on_push)``（不覆蓋 ws_session 的 kick handler，
      兩者是 client 上獨立欄位）。
    * 規劃階段「碎片現量」若 tracker 尚未收到帶 100022 的 push，會回 ``None``（前端顯示
      「未知，將於執行時由消耗推送量測」）。**衝刺進度的權威來源是 server 端
      ``accrued``（已累計消耗），規劃主要靠它**，碎片現量只是輔助參考。
    * 執行階段即使碎片現量未知，``relic.spend_to_target`` 會走 ``frag_unknown``
      fallback：以 server 的 0x0201 拒絕（碎片用盡/滿級）與 sprint accrued 為界仍可
      正常運作，``spent`` 則為 best-effort（量測不到時為 0）。
"""
import logging
import time
from collections import OrderedDict

from flask import Blueprint, jsonify, request

from control_panel import ws_session
from control_panel.shared.auth import _fly_pet_auth
from ws_token import mining as ws_mining
from ws_token import relic as ws_relic
from ws_token import relic_sprint as ws_sprint

logger = logging.getLogger(__name__)

bp = Blueprint("relic_sprint", __name__)

# 4 輪門檻 status 對應中文（p_cross_limited_rank_task.status）。
_STATUS_NAME = {
    ws_sprint.STATUS_NORMAL: "未達",
    ws_sprint.STATUS_CAN_GET: "可領",
    ws_sprint.STATUS_HAD_GET: "已領",
}

# 規劃預覽 note：說明「近似 + 可能小幅超出」(每級成本由 server 決定，client 無成本表)。
_PLAN_NOTE = (
    "執行將從最小等級遺物開始升級，把遺物碎片的累計消耗推到 900,000（衝刺總門檻），"
    "並領取所有可領的輪獎。實際每級成本由伺服器決定（client 端沒有成本表），故為近似；"
    "最後一步可能小幅超出門檻。到上限/已領的輪不會重複領、無活動則安全略過。"
)

# 執行階段需要 tracker（量測 100022 即時消耗）。dashboard 持久 client 沒掛，故本模組
# 自管 per-ip InventoryTracker，並於取得 client 時補掛 push handler（見 module docstring）。
# 以 size-capped LRU 保存，避免 per-ip dict 隨裝置數無限增長（記憶體洩漏）。
_MAX_TRACKERS = 32
_trackers: "OrderedDict[str, ws_mining.InventoryTracker]" = OrderedDict()


def _tracker_for(ip: str) -> ws_mining.InventoryTracker:
    """取得 ``ip`` 的 InventoryTracker（沒有就建一個），LRU 上限 ``_MAX_TRACKERS``。

    取用即移到末尾（most-recently-used）；超過上限時丟掉最舊（least-recently-used）
    的一筆，使 registry 不會無限長。dashboard 操作量極小，cap 32 綽綽有餘。
    """
    tracker = _trackers.get(ip)
    if tracker is None:
        tracker = ws_mining.InventoryTracker()
        _trackers[ip] = tracker
        while len(_trackers) > _MAX_TRACKERS:
            _trackers.popitem(last=False)  # evict least-recently-used
    else:
        _trackers.move_to_end(ip)
    return tracker


def _session_client(ip: str):
    """取得 ``ip`` 的 live 純 WS client；沒有就嘗試 ensure 一條。回 (client, tracker, err)。

    取得 client 後補掛本模組的 InventoryTracker push handler，讓後續 0x0402 消耗推送
    能餵進 ``tracker.counts``（kick handler 是 client 上另一獨立欄位，不受影響）。
    與 routes_ad_reward._session_client 同一套行為（共用 ws_session registry）。
    """
    client = ws_session.get_client(ip)
    if client is None:
        res = ws_session.ensure(ip)
        if res.get("status") == "error":
            return None, None, res.get("message", "WS 連線失敗")
        client = ws_session.get_client(ip)
        if client is None:
            return None, None, "WS 連線未就緒"
    tracker = _tracker_for(ip)
    try:
        client.set_push_handler(tracker.on_push)
    except Exception:  # noqa: BLE001 — 掛 handler 失敗不該擋住唯讀規劃
        logger.debug("relic_sprint 補掛 push handler 失敗 ip=%s", ip, exc_info=True)
    return client, tracker, None


def _rounds_view(sprint: dict) -> list[dict]:
    """把 read_sprint 的 stage rounds 攤成 4 輪預覽（round/threshold/status/中文）。

    sprint["rounds"] = [{small_group_id, task_id, status, done_subtasks}]（stage round,
    1..4），由 read_sprint 依 LIVE 結構推導（小心:``tasks`` 是 32 個原始 task,前 28
    是 milestone 子任務、不是輪,**不可**拿 tasks[0..3] 當輪)。門檻取 ``ROUND_THRESHOLDS``;
    缺的輪(關閉/換版 rounds 不足)以 status=未達補滿。
    """
    rounds = sprint.get("rounds") or []
    by_sgid = {int(r.get("small_group_id", 0) or 0): r for r in rounds}
    out: list[dict] = []
    for idx, threshold in enumerate(ws_sprint.ROUND_THRESHOLDS, start=1):
        r = by_sgid.get(idx, {})
        status = int(r.get("status", ws_sprint.STATUS_NORMAL) or 0)
        out.append({
            "round": idx,
            "threshold": threshold,
            "status": status,
            "status_name": _STATUS_NAME.get(status, str(status)),
            "done_subtasks": int(r.get("done_subtasks", 0) or 0),
        })
    return out


@bp.route("/api/relic_sprint/plan/<ip>", methods=["GET"])
@_fly_pet_auth
def relic_sprint_plan(ip):
    """規劃（只讀）：本期活動 / 已累計進度 / 4 輪門檻狀態 / 碎片現量 / 是否足夠。

    無活動回 ``{open:False, msg}``；有活動回完整預覽（不送任何升級封包）。
    """
    client, tracker, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        act_type = ws_sprint.find_active_act_type(client)
        if act_type is None:
            return jsonify({"status": "ok", "data": {
                "open": False, "msg": "本期無遺物衝刺活動"}})
        sprint = ws_sprint.read_sprint(client, act_type)
        relics = ws_relic.read_relics(client)
    except Exception as exc:  # noqa: BLE001
        logger.warning("relic_sprint_plan 讀取失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    # 本期活動結束時間：從 calendar(6576) 取 active window 的 end（Unix 秒，UTC+8 日界）。
    # 6572 不帶時間窗，故額外讀一次 calendar；失敗不致命（end_ts=None，前端顯示進行中）。
    end_ts = None
    try:
        windows = ws_sprint.read_calendar(client)
        win = ws_sprint.active_window(windows, act_type, int(time.time()))
        end_ts = win.end if win else None
    except Exception:  # noqa: BLE001
        logger.debug("relic_sprint_plan read_calendar 失敗 ip=%s", ip, exc_info=True)

    accrued = int(sprint.get("accrued", 0) or 0)
    total = ws_sprint.SPRINT_TOTAL
    remaining = max(0, total - accrued)
    # 碎片現量：tracker 若尚未收到帶 100022 的 0x0402 push 則為 None（未知）。
    fragments_now = tracker.counts.get(ws_relic.RELIC_FRAGMENT_ITEM)
    enough = (fragments_now is not None) and (fragments_now >= remaining)

    return jsonify({"status": "ok", "data": {
        "open": True,
        "act_type": act_type,
        "accrued": accrued,
        "total": total,
        "remaining": remaining,
        "end_ts": end_ts,
        "rounds": _rounds_view(sprint),
        "claimable_rounds": list(sprint.get("claimable_rounds", [])),
        "fragments_now": fragments_now,
        "enough": enough,
        "equipped_count": len(relics.equipped),
        "note": _PLAN_NOTE,
    }})


@bp.route("/api/relic_sprint/execute/<ip>", methods=["POST"])
@_fly_pet_auth
def relic_sprint_execute(ip):
    """執行：升級最小等級遺物把累計消耗推到 900K → 領取所有 CanGet 輪 — 純 WS。

    回 ``{spent, claimed_rounds, act_type, accrued_before, accrued_after, stopped,
    frag_unknown, skipped?}``。無活動時 ``run_relic_sprint`` 回 ``{skipped:...}``。
    """
    client, tracker, err = _session_client(ip)
    if err:
        return jsonify({"status": "error", "message": err}), 502
    try:
        out = ws_sprint.run_relic_sprint(
            client, tracker,
            target_spend=ws_sprint.SPRINT_TOTAL, enabled=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("relic_sprint_execute 失敗 ip=%s: %s", ip, exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    if out.get("skipped"):
        return jsonify({"status": "ok", "data": {
            "open": False, "skipped": out["skipped"],
            "msg": "本期無遺物衝刺活動" if out["skipped"] == "no active sprint"
                   else out["skipped"],
        }})

    before = out.get("sprint_before") or {}
    after = out.get("sprint_after") or {}
    return jsonify({"status": "ok", "data": {
        "open": True,
        "act_type": out.get("act_type"),
        "spent": int(out.get("spent", 0) or 0),
        "claimed_rounds": list(out.get("claimed_rounds", [])),
        "accrued_before": int(before.get("accrued", 0) or 0),
        "accrued_after": int(after.get("accrued", 0) or 0),
        "total": ws_sprint.SPRINT_TOTAL,
        "stopped": out.get("spend_stopped"),
        "frag_unknown": bool(out.get("frag_unknown", False)),
        "rounds": _rounds_view(after),
    }})
