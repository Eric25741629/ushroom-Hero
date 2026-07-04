"""飛寵 dashboard blueprint — 從 control_panel_app.py 拆出（純 code-motion）。

晚綁定規則（關鍵）：tests 會 monkeypatch
``control_panel_app._cdp_json_response`` 與 ``control_panel_app._FLY_PET_ICON_DIR``。
因此路由內呼叫 ``_cdp_json_response(...)`` / 讀 ``_FLY_PET_ICON_DIR`` 一律改成
``import control_panel_app as _cpa`` 後用 ``_cpa._cdp_json_response(...)`` /
``_cpa._FLY_PET_ICON_DIR``。模組頂部仍定義 ``_FLY_PET_ICON_DIR`` 預設值（façade
re-export 它作初始值）。``_cdp_evaluate`` / ``_FLY_PET_LOCK_JS`` 直接用 shared 的
（同一物件）。
"""
import json
import os

from flask import Blueprint, jsonify, request, send_from_directory

import bot_state
import config_manager

from control_panel.shared.auth import _fly_pet_auth
from control_panel.shared.cdp import _cdp_err_code, _cdp_evaluate, _FLY_PET_LOCK_JS

bp = Blueprint("fly_pet", __name__)


# Real in-game fly-pet icons dumped from the live game by tools/dump_flypet_icons.py
# (one PNG per species config_id). Served to the dashboard card wall.
# 本檔位於 control_panel/ 子目錄，repo 根需取上一層（與原 control_panel_app.py 同路徑）。
_FLY_PET_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "flypet_icons"
)


@bp.route("/api/cdp_evaluate/<ip>", methods=["GET", "POST"])
def cdp_evaluate(ip):
    """Execute JS on a web_h5 device via CDP Runtime.evaluate.

    POST: {"expression": "..."}
    GET:  ?expr=...  (URL-encoded JS expression)
    """
    import control_panel_app as _cpa

    if request.method == "POST":
        expression = (request.json or {}).get("expression", "")
    else:
        expression = request.args.get("expr", "")
    if not expression:
        return jsonify({"status": "error", "message": "no expression"}), 400

    await_promise = False
    if request.method == "POST":
        await_promise = bool((request.json or {}).get("awaitPromise", False))

    return _cpa._cdp_json_response(ip, expression, await_promise=await_promise)


@bp.route("/api/fly_pet_list/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_list(ip):
    """Dump all fly pets + entries for a device (convenience wrapper)."""
    import control_panel_app as _cpa

    js = r"""new Promise((resolve) => {
        let done = false;
        function finish() {
            if (done) return;
            done = true;
            normalEvent.off('FlyPeCollectionBack', finish);
            clearTimeout(timeout);
            resolve(collect());
        }
        const timeout = setTimeout(finish, 2500);
        function collect() {
        try {
            const __extractLockStar = """ + _FLY_PET_LOCK_JS + r""";
            const cache = IS(ISInclude.FlyPetDataCache);
            const pets = cache.pet_list;
            const collectionIds = new Set();
            const collectionList = cache.collection_list || {};
            for (const ck in collectionList) {
                const cv = collectionList[ck];
                if (cv !== null && cv !== undefined && cv !== 0) collectionIds.add(String(cv));
                if (ck !== null && ck !== undefined && ck !== "0") collectionIds.add(String(ck));
            }
            const result = [];
            for (const key in pets) {
                const pet = pets[key];
                if (!pet) continue;
                const cfg = configFly.getDataByKey(pet.config_id);
                const entries = [];
                for (const e of (pet.entry_list || [])) {
                    try {
                        const ec = configFly_entry.getDataByKeys("id", e.k, "level", e.v);
                        entries.push({
                            id: e.k, level: e.v,
                            name: ec ? ec.name : "?",
                            quality: ec ? ec.quality : 0,
                            desc: ec ? String(ec.desc || "") : "",
                            desc_parm: ec ? ec.desc_parm : [],
                            belong_talent: ec ? ec.belong_talent : 0,
                            special_effect: ec ? (ec.special_effect || 0) : 0
                        });
                    } catch(ex) {
                        entries.push({id: e.k, level: e.v, name: "parse_err", quality: 0});
                    }
                }
                const __ls = __extractLockStar(pet);
                const lock = __ls.lock;
                const star = __ls.star;
                result.push({
                    id: pet.id,
                    config_id: pet.config_id,
                    name: pet.name || "",
                    display_name: cfg ? cfg.name : "?",
                    quality: pet.quality,
                    level: pet.level,
                    fight: pet.fight || 0,
                    is_deployed: (pet.fight || 0) === 1,
                    is_collected: collectionIds.has(String(pet.config_id)),
                    generation: pet.generation || 0,
                    growth: pet.growth || 0,
                    step: pet.step || 0,
                    lock: lock,
                    star: star,
                    entries: entries
                });
            }
            result.sort((a,b) => b.quality - a.quality || b.level - a.level);
            return JSON.stringify(result);
        } catch(err) {
            return JSON.stringify({error: err.message, stack: err.stack});
        }
        }
        normalEvent.on('FlyPeCollectionBack', finish);
        try {
            IS(ISInclude.FlyPetControl).send_66_32();
        } catch(ex) {
            finish();
        }
    })"""
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="pets")


@bp.route("/api/fly_pet_icon/<int:config_id>", methods=["GET"])
@_fly_pet_auth
def fly_pet_icon(config_id):
    """Serve the cached real in-game icon for a fly-pet species.

    Returns the dumped ``static/flypet_icons/<config_id>.png``. A missing icon is a
    404 (not an error) so the dashboard falls back to its placeholder avatar. The
    ``<int:config_id>`` route converter blocks path traversal / arbitrary filenames.
    """
    import control_panel_app as _cpa

    filename = f"{config_id}.png"
    if not os.path.isfile(os.path.join(_cpa._FLY_PET_ICON_DIR, filename)):
        return ("", 404)
    resp = send_from_directory(_cpa._FLY_PET_ICON_DIR, filename, mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/api/fly_pet_check_connection/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_check_connection(ip):
    """Check if the game WebSocket is connected."""
    import control_panel_app as _cpa

    js = r"""(() => {
        try {
            const cnet = netManager._cnet;
            const sock = cnet._socket;
            const connected = sock && sock.readyState === 1;
            return JSON.stringify({connected});
        } catch(e) {
            return JSON.stringify({connected: false, error: e.message});
        }
    })()"""
    return _cpa._cdp_json_response(ip, js)


@bp.route("/api/fly_pet_browser_status/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_browser_status(ip):
    """Lightweight 'is a browser already launched?' check.

    Hits the device's CDP ``/json/list`` (a local HTTP call via
    ``find_game_page_target``) instead of evaluating JS into the game, so the
    UI can tell 'no browser launched' apart from 'browser up but game still
    loading' without the heavy game-WebSocket probe (``fly_pet_check_connection``).
    """
    from runtime_services.live_view_bridge import find_game_page_target

    cfg = config_manager.get_device_config(ip)
    debug_port = cfg.get("web_debug_port")
    if not debug_port:
        return jsonify({
            "status": "ok",
            "data": {"browser_up": False, "debug_port": None, "no_debug_port": True},
        })
    try:
        ws_url = find_game_page_target(
            int(debug_port), "mushroomh5.acenetgame.com",
            timeout_sec=1.0, poll_interval=0.3,
        )
    except Exception as exc:
        return jsonify({
            "status": "ok",
            "data": {"browser_up": False, "debug_port": int(debug_port), "error": str(exc)},
        })
    return jsonify({
        "status": "ok",
        "data": {"browser_up": bool(ws_url), "debug_port": int(debug_port)},
    })


@bp.route("/api/fly_pet_bot_status/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_bot_status(ip):
    """Check if bot script is actively running on this device."""
    states = bot_state.get_all_states()
    st = states.get(ip, {})
    status = st.get("status", "OFFLINE")
    task = st.get("task", "")
    step = st.get("step", "")
    running = status not in ("OFFLINE", "") and task not in ("待命", "初始化", "休眠中", "強制休眠", "手動操作", "")
    return jsonify({
        "status": "ok",
        "bot_running": running,
        "bot_status": status,
        "bot_task": task,
        "bot_step": step,
    })


@bp.route("/api/fly_pet_resolve/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_resolve(ip):
    """Sell/decompose selected pets. Waits for server ack via OnInfoUpdate event."""
    import control_panel_app as _cpa

    data = request.json or {}
    ids = data.get("ids", [])
    if not ids or not isinstance(ids, list):
        return jsonify({"status": "error", "message": "ids must be a non-empty list"}), 400
    ids_js = json.dumps([int(i) for i in ids])
    js = f"""new Promise((resolve) => {{
    const requestedIds = {ids_js}.map(String);
    const __extractLockStar = {_FLY_PET_LOCK_JS};
    let collectionReady = false;

    function countPets() {{
        return Object.keys(IS(ISInclude.FlyPetDataCache).pet_list).filter(k => IS(ISInclude.FlyPetDataCache).pet_list[k]).length;
    }}
    function petById(cache, id) {{
        if (cache.pet_list[id]) return cache.pet_list[id];
        for (const k in cache.pet_list) {{
            const pet = cache.pet_list[k];
            if (pet && String(pet.id) === String(id)) return pet;
        }}
        return null;
    }}
    function collectionIdSet(cache) {{
        const collectionIds = new Set();
        const collectionList = cache.collection_list || {{}};
        for (const ck in collectionList) {{
            const cv = collectionList[ck];
            if (cv !== null && cv !== undefined && cv !== 0) collectionIds.add(String(cv));
            if (ck !== null && ck !== undefined && ck !== "0") collectionIds.add(String(ck));
        }}
        return collectionIds;
    }}
    function buildSafeIds() {{
        const cache = IS(ISInclude.FlyPetDataCache);
        const collectionIds = collectionIdSet(cache);
        const safeIds = [];
        const skipped = {{locked: 0, collected: 0, deployed: 0, missing: 0}};
        requestedIds.forEach((id) => {{
            const pet = petById(cache, id);
            if (!pet) {{ skipped.missing++; return; }}
            const locked = __extractLockStar(pet).lock || pet.lock;
            if (locked) {{ skipped.locked++; return; }}
            if (collectionIds.has(String(pet.config_id))) {{ skipped.collected++; return; }}
            if (pet.fight === 1) {{ skipped.deployed++; return; }}
            safeIds.push(Number(pet.id));
        }});
        return {{safeIds, skipped, petCount: countPets()}};
    }}
    function startResolve() {{
        const plan = buildSafeIds();
        const safeIds = plan.safeIds;
        if (safeIds.length === 0) {{
            resolve(JSON.stringify({{ok: false, message: 'no safe pets', petCount: plan.petCount, skipped: plan.skipped}}));
            return;
        }}
        let done = false;
        const timeout = setTimeout(() => {{
            if (done) return;
            done = true;
            normalEvent.off('OnInfoUpdate', handler);
            resolve(JSON.stringify({{ok: false, message: 'timeout', petCount: countPets(), skipped: plan.skipped}}));
        }}, 8000);
        function handler() {{
            if (done) return;
            done = true;
            clearTimeout(timeout);
            normalEvent.off('OnInfoUpdate', handler);
            resolve(JSON.stringify({{ok: true, petCount: countPets(), skipped: plan.skipped}}));
        }}
        normalEvent.on('OnInfoUpdate', handler);
        IS(ISInclude.FlyPetControl).send_66_8(safeIds);
    }}
    function onCollectionReady() {{
        if (collectionReady) return;
        collectionReady = true;
        clearTimeout(collectionTimeout);
        normalEvent.off('FlyPeCollectionBack', onCollectionReady);
        startResolve();
    }}
    const collectionTimeout = setTimeout(onCollectionReady, 2500);
    normalEvent.on('FlyPeCollectionBack', onCollectionReady);
    try {{
        IS(ISInclude.FlyPetControl).send_66_32();
    }} catch(ex) {{
        onCollectionReady();
    }}
}})"""
    return _cpa._cdp_json_response(ip, js, await_promise=True)


@bp.route("/api/fly_pet_breed_info/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_breed_info(ip):
    """Get breeding pool status and shelf info."""
    import control_panel_app as _cpa

    js = r"""(() => {
        const cache = IS(ISInclude.FlyPetDataCache);
        const isRealPet = (p) => p && Number(p.id || 0) > 0;
        const homes = [];
        for (const k in cache.home_list) {
            const h = cache.home_list[k];
            if (!h) continue;
            const raw = {};
            for (const fk in h) {
                const v = h[fk];
                if (v === null || v === undefined) { raw[fk] = v; continue; }
                if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') raw[fk] = v;
            }
            const petInfo = (p) => {
                if (!isRealPet(p)) return null;
                const cfg = configFly.getDataByKey(p.config_id);
                return {id: p.id, config_id: p.config_id, name: p.name || '', display_name: cfg ? cfg.name : '?', quality: p.quality || 0, role_id: p.role_id || 0};
            };
            homes.push({
                id: h.id, name: h.name || '', state: h.state,
                end_time: h.end_time || 0,
                fly_a: petInfo(h.fly_a),
                fly_b: petInfo(h.fly_b),
                fly_pet: petInfo(h.fly_pet),
                _raw: raw
            });
        }
        const shelved = [];
        for (const k in cache.use_pet_list) {
            const s = cache.use_pet_list[k];
            if (!s || !s.info) continue;
            const cfg = configFly.getDataByKey(s.info.config_id);
            shelved.push({id: s.info.id, config_id: s.info.config_id, display_name: cfg ? cfg.name : '?', quality: s.info.quality || 0, state: s.state || 0});
        }
        const egg_list = [];
        if (cache.egg_list) {
            for (const ek in cache.egg_list) {
                const eg = cache.egg_list[ek];
                if (!eg) continue;
                const raw = {};
                for (const fk in eg) {
                    const v = eg[fk];
                    if (v === null || v === undefined) { raw[fk] = v; continue; }
                    if (typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean') raw[fk] = v;
                }
                egg_list.push(raw);
            }
        }
        return JSON.stringify({homes, shelved, egg_list});
    })()"""
    return _cpa._cdp_json_response(ip, js)


@bp.route("/api/fly_pet_breed_methods/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_breed_methods(ip):
    """Discover FlyPetControl send_* methods for debugging."""
    import control_panel_app as _cpa

    js = r"""(() => {
        const ctrl = IS(ISInclude.FlyPetControl);
        const proto = Object.getPrototypeOf(ctrl);
        const methods = Object.getOwnPropertyNames(proto).filter(n => n.startsWith('send_66'));
        return JSON.stringify(methods.sort());
    })()"""
    return _cpa._cdp_json_response(ip, js)


@bp.route("/api/fly_pet_shelve/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_shelve(ip):
    """Put a pet on or remove from the breeding shelf."""
    data = request.json or {}
    pet_id = data.get("pet_id")
    action = data.get("action")
    if pet_id is None or action not in ("place", "remove"):
        return jsonify({"status": "error", "message": "pet_id required, action must be 'place' or 'remove'"}), 400
    type_val = 0 if action == "place" else 1
    js = f"IS(ISInclude.FlyPetControl).send_66_23({int(pet_id)}, {type_val}, 0)"
    result, err = _cdp_evaluate(ip, js)
    if err:
        return jsonify({"status": "error", "message": err}), _cdp_err_code(err)
    return jsonify({"status": "ok"})


@bp.route("/api/fly_pet_partner/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_partner(ip):
    """Get a 搭檔 (breeding partner)'s shelved pets, by role_id (async).

    The server replies via the ``RolePetListBack`` event whose payload carries
    the partner's shelved pets under ``data.list`` (array of {info, state, ...}).
    """
    import control_panel_app as _cpa

    role_id = request.args.get("role_id")
    if not role_id:
        return jsonify({"status": "error", "message": "role_id query param required"}), 400
    try:
        role_id_int = int(role_id)
    except (ValueError, TypeError):
        return jsonify({"status": "error", "message": "role_id must be an integer"}), 400
    js = f"""new Promise((resolve) => {{
    const tmr = setTimeout(() => {{
        normalEvent.off('RolePetListBack', handler);
        resolve(JSON.stringify([]));
    }}, 8000);
    function handler(data) {{
        normalEvent.off('RolePetListBack', handler);
        clearTimeout(tmr);
        const pets = [];
        for (const p of (data.list || [])) {{
            if (!p || !p.info) continue;
            const cfg = configFly.getDataByKey(p.info.config_id);
            const entries = [];
            for (const e of (p.info.entry_list || [])) {{
                try {{
                    const ec = configFly_entry.getDataByKeys("id", e.k, "level", e.v);
                    entries.push({{
                        id: e.k, level: e.v,
                        name: ec ? ec.name : "?",
                        quality: ec ? ec.quality : 0
                    }});
                }} catch(ex) {{
                    entries.push({{id: e.k, level: e.v, name: "?", quality: 0}});
                }}
            }}
            pets.push({{
                id: p.info.id, config_id: p.info.config_id,
                name: p.info.name || '', display_name: cfg ? cfg.name : '?',
                quality: p.info.quality, level: p.info.level,
                generation: p.info.generation || 0,
                growth: p.info.growth || 0,
                state: p.state || 0,
                end_time: p.end_time || 0,
                entries: entries
            }});
        }}
        resolve(JSON.stringify(pets));
    }}
    normalEvent.on('RolePetListBack', handler);
    IS(ISInclude.FlyPetControl).send_66_24({role_id_int});
}})"""
    return _cpa._cdp_json_response(ip, js, await_promise=True)


@bp.route("/api/fly_pet_breed_start/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_breed_start(ip):
    """Start breeding."""
    import control_panel_app as _cpa

    data = request.json or {}
    base_id = data.get("base_id")
    fly_a_id = data.get("fly_a_id")
    fly_b_id = data.get("fly_b_id")
    if base_id is None or fly_a_id is None or fly_b_id is None:
        return jsonify({"status": "error", "message": "base_id, fly_a_id, fly_b_id required"}), 400
    base_id_int = int(base_id)
    # Bug 9: no confirming *Back event exists for 66_27, so send the command then
    # poll the breed cache until home_list[base_id].state becomes 1 (breeding),
    # bounded by a ~3000ms timeout. Resolve {ok, state, timed_out}.
    js = f"""new Promise((resolve) => {{
    IS(ISInclude.FlyPetControl).send_66_27({base_id_int}, {int(fly_a_id)}, {int(fly_b_id)});
    const baseId = {base_id_int};
    const deadline = Date.now() + 3000;
    function readState() {{
        try {{
            const home = IS(ISInclude.FlyPetDataCache).home_list[baseId];
            if (!home) return null;
            return home.state;
        }} catch(e) {{ return null; }}
    }}
    const initial = readState();
    if (initial === null) {{
        resolve(JSON.stringify({{ok: true, state: null, timed_out: false}}));
        return;
    }}
    const timer = setInterval(() => {{
        const st = readState();
        if (st === 1) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: false}}));
        }} else if (Date.now() >= deadline) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: true}}));
        }}
    }}, 150);
}})"""
    return _cpa._cdp_json_response(ip, js, await_promise=True)


@bp.route("/api/fly_pet_breed_collect/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_breed_collect(ip):
    """Collect breeding result."""
    import control_panel_app as _cpa

    data = request.json or {}
    base_id = data.get("base_id")
    if base_id is None:
        return jsonify({"status": "error", "message": "base_id required"}), 400
    base_id_int = int(base_id)
    # Bug 9: no confirming *Back event exists for 66_28, so send the command then
    # poll the breed cache until home_list[base_id].state moves AWAY from 2
    # (collectable) -> 0 idle / 3 awaiting-hatch, bounded by a ~3000ms timeout.
    js = f"""new Promise((resolve) => {{
    IS(ISInclude.FlyPetControl).send_66_28({base_id_int});
    const baseId = {base_id_int};
    const deadline = Date.now() + 3000;
    function readState() {{
        try {{
            const home = IS(ISInclude.FlyPetDataCache).home_list[baseId];
            if (!home) return null;
            return home.state;
        }} catch(e) {{ return null; }}
    }}
    const initial = readState();
    if (initial === null) {{
        resolve(JSON.stringify({{ok: true, state: null, timed_out: false}}));
        return;
    }}
    const timer = setInterval(() => {{
        const st = readState();
        if (st !== 2) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: false}}));
        }} else if (Date.now() >= deadline) {{
            clearInterval(timer);
            resolve(JSON.stringify({{ok: true, state: st, timed_out: true}}));
        }}
    }}, 150);
}})"""
    return _cpa._cdp_json_response(ip, js, await_promise=True)


@bp.route("/api/fly_pet_hatch/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_hatch(ip):
    """Hatch an egg in a breeding nest."""
    import control_panel_app as _cpa

    data = request.json or {}
    egg_id = data.get("egg_id")
    if egg_id is None:
        return jsonify({"status": "error", "message": "egg_id required"}), 400
    egg_ids_js = json.dumps([int(egg_id)])
    js = f"""new Promise((resolve) => {{
    let done = false;
    const tmr = setTimeout(() => {{
        if (done) return; done = true;
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({{ok: false, message: 'timeout'}}));
    }}, 8000);
    function handler() {{
        if (done) return; done = true;
        clearTimeout(tmr);
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({{ok: true}}));
    }}
    normalEvent.on('EggListBack', handler);
    IS(ISInclude.FlyPetControl).send_66_3({egg_ids_js});
}})"""
    return _cpa._cdp_json_response(ip, js, await_promise=True)


@bp.route("/api/fly_pet_partners/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_partners(ip):
    """Get the 搭檔 (breeding-partner) list, capped at 30 by the game.

    Partners live in ``FlyPetDataCache.role_list`` (populated by InitHybridData
    from the server's ``partner_list``), NOT the friend list. Each entry exposes
    role_id + name so the frontend can fetch each partner's shelved pets via
    ``/api/fly_pet_partner`` (send_66_24 -> RolePetListBack). If the cache is not
    yet populated we request hybrid data (send_66_1/21/22) and read it back.
    """
    import control_panel_app as _cpa

    js = r"""new Promise((resolve) => {
        function collect() {
            const out = [];
            try {
                const rl = IS(ISInclude.FlyPetDataCache).role_list || [];
                for (const k in rl) {
                    const r = rl[k];
                    if (!r) continue;
                    const rid = Number(r.role_id) || 0;
                    if (!rid) continue;
                    out.push({
                        role_id: rid,
                        name: String(r.name || ""),
                        head: Number(r.head) || 0
                    });
                }
            } catch (ex) {}
            return out;
        }
        const cached = collect();
        if (cached.length > 0) { return resolve(JSON.stringify(cached)); }
        let done = false;
        function handler() {
            if (done) return; done = true;
            clearTimeout(tmr);
            normalEvent.off('EggListBack', handler);
            resolve(JSON.stringify(collect()));
        }
        normalEvent.on('EggListBack', handler);
        const c = IS(ISInclude.FlyPetControl);
        c.send_66_1(); c.send_66_21(); c.send_66_22();
        const tmr = setTimeout(() => {
            if (done) return; done = true;
            normalEvent.off('EggListBack', handler);
            resolve(JSON.stringify(collect()));
        }, 3000);
    })"""
    return _cpa._cdp_json_response(ip, js, await_promise=True, data_key="partners")


@bp.route("/api/fly_pet_refresh_breed/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_refresh_breed(ip):
    """Trigger breed info refresh (egg list + hybrid base + shelves).

    Bug 5: block until the refresh data actually arrives (EggListBack) instead of
    fire-and-forget, so the frontend no longer needs a fixed delay. Bounded by a
    ~1500ms timeout; the listener is always detached on both paths.
    """
    import control_panel_app as _cpa

    js = """new Promise((resolve) => {
    let done = false;
    function handler() {
        if (done) return; done = true;
        clearTimeout(tmr);
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({ok: true, timed_out: false}));
    }
    const tmr = setTimeout(() => {
        if (done) return; done = true;
        normalEvent.off('EggListBack', handler);
        resolve(JSON.stringify({ok: true, timed_out: true}));
    }, 1500);
    normalEvent.on('EggListBack', handler);
    const c = IS(ISInclude.FlyPetControl);
    c.send_66_1(); c.send_66_21(); c.send_66_22();
})"""
    return _cpa._cdp_json_response(ip, js, await_promise=True)


@bp.route("/api/fly_pet_find_pair/<ip>", methods=["POST"])
@_fly_pet_auth
def fly_pet_find_pair(ip):
    """Find best breeding pair matching criteria from available pets."""
    import control_panel_app as _cpa

    data = request.json or {}
    criteria = data.get("criteria", {})
    exclude_ids = data.get("exclude_ids", [])

    mode = criteria.get("mode", "quality_count")
    quality = int(criteria.get("quality", 3))
    min_count = int(criteria.get("min_count", 2))
    min_total = int(criteria.get("min_total_entries", 3))
    prefer_low_gen = bool(criteria.get("prefer_low_gen", True))
    species_whitelist = [int(x) for x in (criteria.get("species_whitelist") or [])]
    entry_whitelist = [int(x) for x in (criteria.get("entry_whitelist") or [])]
    exclude_js = json.dumps([int(i) for i in exclude_ids])
    species_js = json.dumps(species_whitelist)
    entry_js = json.dumps(entry_whitelist)

    js = f"""(() => {{
    try {{
        const __extractLockStar = {_FLY_PET_LOCK_JS};
        const cache = IS(ISInclude.FlyPetDataCache);
        const excludeIds = new Set({exclude_js}.map(String));
        const mode = '{mode}';
        const quality = {quality};
        const minCount = {min_count};
        const minTotal = {min_total};
        const preferLowGen = {str(prefer_low_gen).lower()};
        const speciesWhitelist = new Set({species_js}.map(Number));
        const entryWhitelist = {entry_js}.map(Number);

        const cooldownIds = new Set();
        for (const k in cache.use_pet_list) {{
            const s = cache.use_pet_list[k];
            if (s && s.state > 0 && s.info) cooldownIds.add(String(s.info.id));
        }}

        const breedingIds = new Set();
        for (const k in cache.home_list) {{
            const h = cache.home_list[k];
            if (!h) continue;
            if (h.fly_a) breedingIds.add(String(h.fly_a.id));
            if (h.fly_b) breedingIds.add(String(h.fly_b.id));
        }}

        const candidates = [];
        for (const key in cache.pet_list) {{
            const pet = cache.pet_list[key];
            if (!pet) continue;
            const pid = String(pet.id);
            if (excludeIds.has(pid) || cooldownIds.has(pid) || breedingIds.has(pid)) continue;

            const locked = __extractLockStar(pet).lock;
            if (locked || pet.lock) continue;
            if (pet.fight === 1) continue;

            const entries = [];
            for (const e of (pet.entry_list || [])) {{
                try {{
                    const ec = configFly_entry.getDataByKeys("id", e.k, "level", e.v);
                    entries.push({{id: e.k, level: e.v, name: ec ? ec.name : '?', quality: ec ? ec.quality : 0}});
                }} catch(ex) {{
                    entries.push({{id: e.k, level: e.v, name: '?', quality: 0}});
                }}
            }}

            let matches = false;
            let matchingCount = 0;
            if (mode === 'quality_count') {{
                matchingCount = entries.filter(e => e.quality === quality).length;
                matches = matchingCount >= minCount;
            }} else {{
                matches = entries.length >= minTotal;
                matchingCount = entries.length;
            }}
            if (!matches) continue;

            // 方案: 限制種類 (白名單). 空 = 不限.
            if (speciesWhitelist.size > 0 && !speciesWhitelist.has(Number(pet.config_id))) continue;
            // 方案: 限制詞條 (白名單, AND). 候選須同時包含全部指定 entry id.
            if (entryWhitelist.length > 0) {{
                const entryIds = new Set(entries.map(e => Number(e.id)));
                if (!entryWhitelist.every(id => entryIds.has(id))) continue;
            }}

            const cfg = configFly.getDataByKey(pet.config_id);
            candidates.push({{
                id: pet.id, config_id: pet.config_id,
                name: pet.name || '',
                display_name: cfg ? cfg.name : '?',
                generation: pet.generation || 0,
                quality: pet.quality || 0,
                entry_count: entries.length,
                matching_count: matchingCount,
                entries: entries.map(e => ({{name: e.name, quality: e.quality}}))
            }});
        }}

        if (preferLowGen) {{
            candidates.sort((a, b) => a.generation - b.generation || b.matching_count - a.matching_count || b.entry_count - a.entry_count);
        }} else {{
            candidates.sort((a, b) => b.matching_count - a.matching_count || b.entry_count - a.entry_count || a.generation - b.generation);
        }}

        const pair = candidates.length >= 2
            ? {{fly_a: candidates[0], fly_b: candidates[1]}}
            : null;

        return JSON.stringify({{
            pair: pair,
            candidates_count: candidates.length,
            skipped_cooldown: cooldownIds.size,
            skipped_breeding: breedingIds.size
        }});
    }} catch(err) {{
        return JSON.stringify({{error: err.message, stack: err.stack}});
    }}
}})()"""
    return _cpa._cdp_json_response(ip, js)


@bp.route("/api/fly_pet_catalog/<ip>", methods=["GET"])
@_fly_pet_auth
def fly_pet_catalog(ip):
    """Full flypet catalog: every species (configFly) + every entry (configFly_entry).

    Used to populate the breeding-preset editor's 種類/詞條 whitelists. Enumerates
    the config tables' `.datas` array (not getDataByKey, which is single-row).
    Entries are deduped by id (one row per 詞條, collapsing levels).
    """
    import control_panel_app as _cpa

    js = """(() => {
        try {
            const sp = (typeof configFly !== 'undefined' && configFly && configFly.datas)
                ? configFly.datas.map(d => ({id: d.id, name: d.name})) : [];
            const arr = (typeof configFly_entry !== 'undefined' && configFly_entry && configFly_entry.datas)
                ? configFly_entry.datas : [];
            const seen = {};
            const entries = [];
            for (const e of arr) {
                if (!e || seen[e.id]) continue;
                seen[e.id] = 1;
                entries.push({id: e.id, name: e.name, quality: e.quality});
            }
            return JSON.stringify({species: sp, entries: entries});
        } catch(err) {
            return JSON.stringify({species: [], entries: [], error: err.message});
        }
    })()"""
    return _cpa._cdp_json_response(ip, js, data_key="catalog")
