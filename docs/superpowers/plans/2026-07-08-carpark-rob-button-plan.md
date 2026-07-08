# Plan: Dashboard「加入搶佔/駐守」按鈕

Spec: `docs/superpowers/specs/2026-07-08-carpark-rob-button-design.md`(已 commit 748342a3)

## Global Constraints(本 repo 必守)

- 不加新套件。JSON 讀取用 `utf-8-sig`。
- pytest 必指定測試檔(hook 擋裸 pytest)。
- 只 stage 有動到的檔(絕不 `git add -A`;repo 有 ~80 WIP 檔 + `auth_state/` secrets)。
- 不 push、不加 attribution footer。commit 不可 `--no-verify`。
- façade 晚綁定:route 內用 `import control_panel_app as _cpa` 後呼叫 `_cpa._cdp_json_response(...)`,
  不可直接 import(測試會 monkeypatch `control_panel_app._cdp_json_response`)。
- 硬限制帳號:route 只接受 `ip == "7fe98fc6"`,其餘 403。
- 無 hot-reload:改完提醒使用者重啟 `new_main_v2.py`。

---

## Task 1 — 後端 route + JS snippet + 單元測試

### 檔案
- 改 `control_panel/routes_control.py`(新增 route + module-level JS 常數)。
- 新增 `tests/test_carpark_rob_route.py`。

### 實作規格

在 `control_panel/routes_control.py` 加一個 module-level 常數(JS 模板,`{pos}`/`{qt}` 之後用
`.format` 以**已驗證的 int** 填入,無注入面):

```python
_SERVER_CAR_JOIN_JS = """
(function(){{
  return new Promise(function(resolve){{
    try {{
      var nm = window.netManager;
      if (!nm || !nm._cnet) {{ resolve(JSON.stringify({{error:'請先開啟網頁並進入遊戲(netManager未就緒)'}})); return; }}
      var sock = nm._cnet;
      var CMD = 12861;
      var done = false;
      var origRecv = sock.reciveMsg.bind(sock);
      var myWrap;
      var finish = function(reply){{
        if (done) return; done = true;
        try {{ if (sock.reciveMsg === myWrap) sock.reciveMsg = origRecv; }} catch(e){{}}
        resolve(JSON.stringify(reply));
      }};
      myWrap = function(cmd, body){{
        try {{
          if ((cmd|0) === CMD && !done) {{
            var b = body instanceof Uint8Array ? body : (body && body.buffer ? new Uint8Array(body.buffer, body.byteOffset||0, body.byteLength) : null);
            var hex=''; if (b) {{ for (var i=0;i<Math.min(b.length,2048);i++) hex += b[i].toString(16).padStart(2,'0'); }}
            finish({{ok:true, reply_hex:hex, len: b?b.length:0}});
          }}
        }} catch(e){{}}
        return origRecv(cmd, body);
      }};
      sock.reciveMsg = myWrap;
      nm.send('car_park.server_car_join', {{pos: {pos}, queue_type: {qt}}});
      setTimeout(function(){{ finish({{ok:true, sent:true, reply_hex:null}}); }}, 3000);
    }} catch(e){{ resolve(JSON.stringify({{error: String(e)}})); }}
  }});
}})()
"""
```

Route:

```python
@bp.route("/api/carpark_rob/<ip>", methods=["POST"])
def carpark_rob(ip):
    require_device_access(ip)
    if ip != "7fe98fc6":
        return jsonify({"status": "error", "message": "此功能僅限小寶"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        pos = int(payload.get("pos"))
        queue_type = int(payload.get("queue_type"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "pos/queue_type 需為整數"}), 400
    if pos < 1:
        return jsonify({"status": "error", "message": "pos 需 >= 1"}), 400
    if queue_type not in (1, 2):
        return jsonify({"status": "error", "message": "queue_type 需為 1(搶佔) 或 2(駐守)"}), 400

    import control_panel_app as _cpa
    js = _SERVER_CAR_JOIN_JS.format(pos=pos, qt=queue_type)
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="reply", timeout=8)
```

注意:route body 用 `.format(pos=pos, qt=queue_type)`,故 JS 模板中所有字面大括號都用 `{{ }}`
跳脫(上面已寫好);`{pos}` / `{qt}` 是唯一兩個佔位符。imports 沿用檔頭已有的
`jsonify, request`(已 import)+ `require_device_access`(已 import)。

### 測試(`tests/test_carpark_rob_route.py`)

用 Flask test client。app 取得方式參照現有 `tests/test_*` 對 control_panel 的建法
(找一個現有 control_panel route 測試檔照抄 app fixture / auth 繞過方式;若無,直接
`from control_panel.routes_control import bp` 建一個 minimal Flask app 註冊 bp,並
monkeypatch `require_device_access` 為 no-op、monkeypatch `control_panel_app._cdp_json_response`)。

AAA,每測一個行為:
1. `test_rejects_non_xiaobao_device`:POST `/api/carpark_rob/emulator-5554` {pos:1,queue_type:1} → 403。
2. `test_rejects_missing_pos`:POST `/api/carpark_rob/7fe98fc6` {queue_type:1}(無 pos)→ 400。
3. `test_rejects_bad_queue_type`:POST 小寶 {pos:1,queue_type:9} → 400。
4. `test_rejects_pos_below_one`:POST 小寶 {pos:0,queue_type:1} → 400。
5. `test_valid_calls_cdp_once`:monkeypatch `control_panel_app._cdp_json_response` 記錄呼叫並回
   `(jsonify({"status":"ok","reply":{"ok":True}}), 200)` 形式的 Flask response;POST 小寶
   {pos:3,queue_type:2} → 200,且被 patch 的函式呼叫一次、傳入的 `js` 字串含 `"pos: 3"` 與
   `"queue_type: 2"`。

### 驗證指令
```
python -m py_compile control_panel/routes_control.py tests/test_carpark_rob_route.py
python -m pytest tests/test_carpark_rob_route.py -q
```

### commit message
`feat(carpark): dashboard 加入搶佔/駐守 後端 route(server_car_join 12861,CDP live session,限小寶)`

---

## Task 2 — 前端 inline 面板

### 檔案
- 改 `templates/dashboard.html`。

### 實作規格

1. 在裝置卡 action bar 的 `else` 分支(約 `dashboard.html:3268` `showRecover` 那顆按鈕之後、
   template literal 結尾 `` ` `` 之前),用現有慣例注入,gate 小寶:

```js
${ip === '7fe98fc6' ? `
  <span class="carpark-rob-group" style="display:inline-flex;gap:4px;align-items:center;margin-left:6px;">
    <input type="number" min="1" id="carparkRobPos-${ip}" placeholder="pos" style="width:56px;">
    <select id="carparkRobType-${ip}">
      <option value="1">搶佔</option>
      <option value="2">駐守</option>
    </select>
    <button class="btn btn-skip" onclick="carparkRob('${ip}')">加入</button>
  </span>` : ''}
```

2. 在 script 區(參照 `deviceControl` 附近,`dashboard.html:3687` 那支的風格)加 handler:

```js
async function carparkRob(ip) {
  const posEl = document.getElementById(`carparkRobPos-${ip}`);
  const typeEl = document.getElementById(`carparkRobType-${ip}`);
  const pos = parseInt(posEl && posEl.value, 10);
  const queue_type = parseInt(typeEl && typeEl.value, 10);
  if (!Number.isInteger(pos) || pos < 1) { alert('請填正確的 pos'); return; }
  const label = queue_type === 2 ? '駐守' : '搶佔';
  if (!confirm(`確定送出 加入${label} pos=${pos}?(會真的參戰、用掉當日次數)`)) return;
  try {
    const res = await fetch(`/api/carpark_rob/${ip}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pos, queue_type }),
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      const r = data.reply || {};
      if (typeof toast === 'function') toast(`已送出 加入${label} pos=${pos}` + (r.reply_hex ? '(有回應)' : '(已送出)'), 'success');
      else alert(`已送出 加入${label} pos=${pos}`);
    } else {
      const msg = (data && data.message) || `HTTP ${res.status}`;
      if (typeof toast === 'function') toast('失敗: ' + msg, 'error'); else alert('失敗: ' + msg);
    }
  } catch (e) {
    if (typeof toast === 'function') toast('錯誤: ' + e, 'error'); else alert('錯誤: ' + e);
  }
}
```

(若 `toast` 全域名稱與現檔不符,改用現檔實際的 toast helper;先 grep `function toast` / `window.toast` 確認。)

### 驗證
```
python -m py_compile control_panel_app.py   # 確保 template 未破壞 app import(非必要但快篩)
```
主要靠 dashboard-ui-review(Task 2 完成後跑)+ 使用者 live 首點驗證。

### commit message
`feat(carpark): dashboard 小寶裝置卡加入搶佔/駐守 inline 面板 + carparkRob handler`

---

## 完成後

- 派 Opus 跑 `dashboard-ui-review`(前端改動)。
- 最終全分支 Opus review(動到裝置控制/送遊戲指令 → 查權限與帳號 gate)。
- merge 回 main;提醒使用者重啟 `new_main_v2.py`。
- queue_type=2=駐守 首點 live 驗證回應碼。
