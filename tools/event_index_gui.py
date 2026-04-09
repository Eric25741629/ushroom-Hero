#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, Response


REPORT_DIR = Path("reports/event_index")
app = Flask(__name__)


def _latest_jsonl() -> Path | None:
    files = sorted(REPORT_DIR.glob("event_index_*.jsonl"))
    if not files:
        return None
    return files[-1]


def _load_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _filter_rows(rows: list[dict[str, Any]], q: str, device: str, event_type: str) -> list[dict[str, Any]]:
    query = (q or "").strip().lower()
    out: list[dict[str, Any]] = []
    for r in rows:
        if device and r.get("device_id") != device:
            continue
        if event_type and r.get("event_type") != event_type:
            continue
        if query:
            hay = " ".join(
                [
                    str(r.get("meaning", "")),
                    str(r.get("task", "")),
                    str(r.get("step", "")),
                    str(r.get("caller_file", "")),
                    str(r.get("caller_function", "")),
                    str(r.get("screenshot_path", "")),
                ]
            ).lower()
            if query not in hay:
                continue
        out.append(r)
    return out


@app.get("/api/events")
def api_events() -> Response:
    fp = _latest_jsonl()
    if fp is None:
        return jsonify({"ok": False, "error": "No event index found. Run tools/build_event_index.py first."}), 404
    rows = _load_events(fp)
    q = request.args.get("q", "")
    device = request.args.get("device", "")
    event_type = request.args.get("event_type", "")
    limit = int(request.args.get("limit", "300"))
    filtered = _filter_rows(rows, q, device, event_type)
    filtered = filtered[-max(1, min(2000, limit)) :]

    event_counts: dict[str, int] = {}
    for r in filtered:
        k = str(r.get("event_type", ""))
        event_counts[k] = event_counts.get(k, 0) + 1

    devices = sorted({str(r.get("device_id", "")) for r in rows if str(r.get("device_id", "")).strip()})
    event_types = sorted({str(r.get("event_type", "")) for r in rows if str(r.get("event_type", "")).strip()})
    return jsonify(
        {
            "ok": True,
            "file": str(fp),
            "total": len(rows),
            "filtered": len(filtered),
            "devices": devices,
            "event_types": event_types,
            "event_counts": event_counts,
            "rows": filtered,
        }
    )


@app.get("/")
def index() -> str:
    return """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Event Index Observatory</title>
  <style>
    :root {
      --bg-1: #0f172a;
      --bg-2: #111827;
      --card: rgba(255,255,255,0.08);
      --card-strong: rgba(255,255,255,0.14);
      --text: #f8fafc;
      --muted: #cbd5e1;
      --accent: #22d3ee;
      --accent-2: #f59e0b;
      --ok: #34d399;
      --warn: #fb7185;
      --line: rgba(255,255,255,0.12);
      --shadow: 0 16px 50px rgba(0,0,0,0.35);
      --font: "Noto Sans TC", "Segoe UI", "PingFang TC", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--text); font-family: var(--font);
      background:
        radial-gradient(1000px 500px at 90% -10%, rgba(34,211,238,.22), transparent 60%),
        radial-gradient(800px 420px at -10% 120%, rgba(245,158,11,.16), transparent 60%),
        linear-gradient(160deg, var(--bg-1), var(--bg-2));
      min-height: 100vh;
    }
    .wrap { max-width: 1300px; margin: 0 auto; padding: 24px 16px 42px; }
    .hero {
      display: grid; grid-template-columns: 1.3fr .7fr; gap: 14px; align-items: stretch;
      margin-bottom: 16px;
      animation: rise .55s ease-out;
    }
    .panel {
      background: var(--card); border: 1px solid var(--line); border-radius: 18px;
      box-shadow: var(--shadow); backdrop-filter: blur(6px);
    }
    .hero-main { padding: 20px; }
    .title { font-size: 28px; font-weight: 800; letter-spacing: .4px; margin: 0 0 6px; }
    .subtitle { color: var(--muted); font-size: 14px; margin: 0; }
    .hero-stat { padding: 18px; display: grid; gap: 8px; align-content: center; }
    .big { font-size: 30px; font-weight: 800; color: var(--accent); }
    .filters {
      display: grid; grid-template-columns: 1fr 180px 190px 110px; gap: 10px;
      padding: 12px; margin-bottom: 14px;
      animation: rise .7s ease-out;
    }
    input, select, button {
      width: 100%; border-radius: 12px; border: 1px solid var(--line);
      padding: 10px 12px; background: rgba(255,255,255,.08); color: var(--text); font-size: 14px;
    }
    button {
      background: linear-gradient(120deg, var(--accent), #06b6d4); color: #042c3a; font-weight: 700;
      border: none; cursor: pointer; transition: transform .15s ease, opacity .15s ease;
    }
    button:hover { transform: translateY(-1px); opacity: .95; }
    .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
    .kpi { padding: 14px; animation: rise .85s ease-out; }
    .kpi .k { font-size: 12px; color: var(--muted); }
    .kpi .v { margin-top: 6px; font-size: 24px; font-weight: 800; }
    .main { display: grid; grid-template-columns: 340px 1fr; gap: 12px; }
    .chart, .table { padding: 12px; animation: rise 1s ease-out; }
    .bar { height: 22px; border-radius: 999px; background: rgba(255,255,255,.08); margin: 8px 0; position: relative; overflow: hidden; }
    .bar > i { position: absolute; left: 0; top: 0; bottom: 0; background: linear-gradient(90deg, var(--accent), var(--accent-2)); }
    .bar > span { position: relative; z-index: 1; font-size: 12px; display: block; padding: 3px 10px; color: #00121a; font-weight: 700; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { border-bottom: 1px solid var(--line); text-align: left; padding: 8px 7px; vertical-align: top; }
    th { color: var(--muted); font-weight: 700; position: sticky; top: 0; background: rgba(17,24,39,.95); }
    .table-wrap { max-height: 540px; overflow: auto; border-radius: 12px; border: 1px solid var(--line); }
    .mono { font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; font-size: 11px; color: #d1f5ff; }
    .path { color: #93c5fd; }
    @keyframes rise { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
    @media (max-width: 980px) {
      .hero { grid-template-columns: 1fr; }
      .filters { grid-template-columns: 1fr; }
      .grid { grid-template-columns: 1fr 1fr; }
      .main { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="panel hero-main">
        <h1 class="title">Event Index Observatory</h1>
        <p class="subtitle">追蹤每次動作的意圖、觸發行號與截圖路徑，做低成本回顧。</p>
      </div>
      <div class="panel hero-stat">
        <div class="k">當前索引檔</div>
        <div id="file" class="mono">loading...</div>
      </div>
    </section>

    <section class="panel filters">
      <input id="q" placeholder="搜尋 meaning / task / caller / screenshot path" />
      <select id="device"></select>
      <select id="eventType"></select>
      <button id="refreshBtn">刷新</button>
    </section>

    <section class="grid">
      <div class="panel kpi"><div class="k">總事件數</div><div id="total" class="v">0</div></div>
      <div class="panel kpi"><div class="k">篩選後</div><div id="filtered" class="v">0</div></div>
      <div class="panel kpi"><div class="k">裝置數</div><div id="deviceCount" class="v">0</div></div>
      <div class="panel kpi"><div class="k">事件類型數</div><div id="typeCount" class="v">0</div></div>
    </section>

    <section class="main">
      <div class="panel chart">
        <h3 style="margin:4px 0 10px;">事件分布</h3>
        <div id="bars"></div>
      </div>
      <div class="panel table">
        <h3 style="margin:4px 0 10px;">事件明細</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>time</th><th>device</th><th>type</th><th>meaning</th>
                <th>line</th><th>caller</th><th>screenshot</th>
              </tr>
            </thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
      </div>
    </section>
  </div>

  <script>
    const q = document.getElementById("q");
    const device = document.getElementById("device");
    const eventType = document.getElementById("eventType");
    const refreshBtn = document.getElementById("refreshBtn");

    function opt(select, list, allLabel) {
      select.innerHTML = "";
      const a = document.createElement("option");
      a.value = ""; a.textContent = allLabel; select.appendChild(a);
      list.forEach(v => {
        const o = document.createElement("option");
        o.value = v; o.textContent = v; select.appendChild(o);
      });
    }

    function esc(s) {
      return (s ?? "").toString().replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    }

    async function loadData() {
      const params = new URLSearchParams({
        q: q.value || "",
        device: device.value || "",
        event_type: eventType.value || "",
        limit: "500"
      });
      const res = await fetch("/api/events?" + params.toString());
      const data = await res.json();
      if (!data.ok) {
        document.getElementById("rows").innerHTML = `<tr><td colspan="7">${esc(data.error || "error")}</td></tr>`;
        return;
      }

      document.getElementById("file").textContent = data.file || "";
      document.getElementById("total").textContent = data.total || 0;
      document.getElementById("filtered").textContent = data.filtered || 0;
      document.getElementById("deviceCount").textContent = (data.devices || []).length;
      document.getElementById("typeCount").textContent = (data.event_types || []).length;
      if (!device.dataset.ready) {
        opt(device, data.devices || [], "全部裝置");
        opt(eventType, data.event_types || [], "全部類型");
        device.dataset.ready = "1";
      }

      const counts = data.event_counts || {};
      const max = Math.max(1, ...Object.values(counts));
      const bars = Object.entries(counts).sort((a,b) => b[1]-a[1]).slice(0, 12).map(([k,v]) => {
        const w = Math.max(4, Math.round((v / max) * 100));
        return `<div class="bar"><i style="width:${w}%"></i><span>${esc(k)} · ${v}</span></div>`;
      }).join("");
      document.getElementById("bars").innerHTML = bars || "<div class='mono'>no data</div>";

      const rows = (data.rows || []).slice().reverse().map(r => {
        const line = r.caller_line || r.trigger_line || 0;
        const caller = `${r.caller_function || r.trigger_function || ""}`;
        return `<tr>
          <td class="mono">${esc(r.event_time || "")}</td>
          <td>${esc(r.device_id || "")}</td>
          <td>${esc(r.event_type || "")}</td>
          <td>${esc(r.meaning || "")}</td>
          <td class="mono">${line}</td>
          <td class="path">${esc(caller)}</td>
          <td class="path">${esc(r.screenshot_path || "")}</td>
        </tr>`;
      }).join("");
      document.getElementById("rows").innerHTML = rows || "<tr><td colspan='7'>no rows</td></tr>";
    }

    refreshBtn.addEventListener("click", loadData);
    q.addEventListener("keydown", e => { if (e.key === "Enter") loadData(); });
    device.addEventListener("change", loadData);
    eventType.addEventListener("change", loadData);
    loadData();
  </script>
</body>
</html>"""


def main() -> int:
    host = "127.0.0.1"
    port = 5088
    print(f"Event Index GUI: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

