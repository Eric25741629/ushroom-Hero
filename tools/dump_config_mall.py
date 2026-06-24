"""One-shot: dump configMall shop_type=11 (parking decoration frags) to JSON.

Usage:
    python tools/dump_config_mall.py [debug_port]

Default port: 9226 (小寶). Connects via CDP, evaluates JS, saves result to
ws_token/data/mall_parking_frag.json.
"""
import json
import sys
import time
import urllib.request

ROOT = __file__.rsplit("tools", 1)[0]


def find_game_target(port: int) -> str | None:
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            entries = json.loads(r.read())
    except Exception as e:
        print(f"Cannot reach CDP on port {port}: {e}")
        return None
    for e in entries:
        if e.get("type") == "page" and "mushroomh5" in e.get("url", ""):
            return e.get("webSocketDebuggerUrl")
    for e in entries:
        if e.get("type") == "page":
            return e.get("webSocketDebuggerUrl")
    return None


def cdp_eval(ws_url: str, expr: str, timeout: int = 10) -> dict | None:
    import websocket
    ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    payload = json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": False},
    })
    ws.send(payload)
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = ws.recv()
        msg = json.loads(raw)
        if msg.get("id") == 1:
            ws.close()
            return msg.get("result", {})
    ws.close()
    return None


JS = r"""
(function(){
  if (typeof configMall === 'undefined') return JSON.stringify({error:'no_configMall'});
  var out = {};
  var rows = configMall.getDatas ? configMall.getDatas() : {};
  var vals = Array.isArray(rows) ? rows : Object.values(rows || {});
  for (var i = 0; i < vals.length; i++) {
    var row = vals[i];
    var d = row._data || row;
    if (!Array.isArray(d)) continue;
    if (d[1] !== 11) continue;            // shop_type 11 only
    if (!Array.isArray(d[2])) continue;
    var frag_goods = d[2][0];
    var shop_id = d[0];
    var price_cur = (d[3] && d[3][0]) || 0;
    var price = (d[3] && d[3][1]) || 0;
    var cap = d[8] || 0;
    out[frag_goods] = {shop_id: shop_id, price_cur: price_cur, price: price, cap: cap};
  }
  return JSON.stringify(out);
})()
"""


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9226
    print(f"Connecting to CDP port {port}...")
    ws_url = find_game_target(port)
    if not ws_url:
        sys.exit(f"No game page target on port {port}")
    print(f"Target: {ws_url}")
    result = cdp_eval(ws_url, JS)
    if not result:
        sys.exit("CDP eval returned nothing")
    inner = result.get("result", {})
    if inner.get("type") == "string":
        data = json.loads(inner["value"])
        if "error" in data:
            sys.exit(f"JS error: {data['error']}")
        out_path = ROOT + "ws_token/data/mall_parking_frag.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(data)} entries to {out_path}")
        for k, v in list(data.items())[:3]:
            print(f"  goods {k}: shop_id={v['shop_id']}, price={v['price']}, cap={v['cap']}")
    else:
        print(f"Unexpected result: {inner}")


if __name__ == "__main__":
    main()
