"""Tests for ws_token.farm — 互動農場 (home module 12) + 打工 (worker module 73).

Field numbers are the live-exported truth:
  HOME (module 12, docs/protocol/HOME_PROTO_SCHEMA.json):
    home_farm_info_c2s   3077 { role_id#1:uint64 }
    home_farm_info_s2c        { role_id#1, name#2, level#3, exp#4,
                                land_list#5:repeated p_farm_land, ... }
    home_farm_plant_c2s  3078 { seed_id#1:uint32, land_id#2:uint32 }
    home_farm_plant_s2c       { code#1, new_land#2:p_farm_land }
    home_farm_harvest_c2s 3081 { land_id#1:uint32 }
    home_farm_harvest_s2c     { code#1, new_land#2, level#3, exp#4,
                                reward_list#5:repeated p_reward }
  TYPE (docs/protocol/TYPE_PROTO_SCHEMA.json):
    p_farm_land { id#1, crop#2:p_farm_crop }   (crop absent = empty land)
    p_farm_crop { id#1, role_id#2, seed_id#3, cfg_id#4, state#5,
                  start_time#6, acc_time#7, end_time#8, ... }
    p_reward    { gtid#1:int32, num#2:int64 }
  WORKER_COMMON (module 73, WORKER_COMMON_PROTO_SCHEMA.json):
    worker_common_farm_worker_setting_c2s 18689
      { team_cfg_id#1, fertilizer_list#2:repeated uint32,
        fertilizer_time_rest#3, seed_used_seq_list#4:repeated p_key_value }
    ..._s2c { worker_info#1:p_worker }   ; p_worker.worker_status#3
  SHOP (module 27, SHOP_PROTO_SCHEMA.json):
    shop_buy_c2s 6914 { shop_type#1, shop_id#2, num#3 }

crop.state: 0=空地 NOT_EXIT / 1=GROWING / 2=MATURE(可收) / 3..7 其他.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token import farm as farm_module  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.farm import (  # noqa: E402
    CMD_ERROR,
    CMD_FERTILIZE,
    CMD_GET_OTHER_WORKER,
    CMD_HARVEST,
    CMD_INFO,
    CMD_PLANT,
    CMD_SHOP_BUY,
    CMD_SHOP_INFO,
    CMD_WORKER_SETTING,
    CMD_WORKER_CANCEL,
    FARM_SHOP_TYPE,
    FARM_WORKER_TEAM_CFG_ID,
    FERTILIZER_ID_HIGH_YIELD,
    SEED_ID_FREE,
    STATE_GROWING,
    STATE_MATURE,
    STATE_NOT_EXIT,
    FarmInfo,
    FarmLand,
    build_fertilize_body,
    build_get_other_worker_body,
    build_harvest_body,
    build_plant_body,
    build_shop_buy_body,
    build_shop_info_body,
    build_worker_setting_body,
    buy_farm_shop,
    buy_to_daily_target,
    fertilize_lands,
    harvest_ready,
    parse_farm_info,
    parse_shop_counts,
    plant_empty,
    read_farm,
    read_shop_counts,
    read_work_status,
    start_work,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)

# --- wire helpers (build server bodies the parser must decode) --------------


def _crop(id_, role_id, seed_id, cfg_id, state, start=0, acc=0, end=0):
    return (codec.pb_uint(1, id_) + codec.pb_uint(2, role_id) + codec.pb_uint(3, seed_id)
            + codec.pb_uint(4, cfg_id) + codec.pb_uint(5, state) + codec.pb_uint(6, start)
            + codec.pb_uint(7, acc) + codec.pb_uint(8, end))


def _land(land_id, crop=None):
    out = codec.pb_uint(1, land_id)
    if crop is not None:
        out += codec.pb_msg(2, crop)
    return out


def _info_body(role_id, name, level, exp, lands):
    out = (codec.pb_uint(1, role_id) + codec.pb_str(2, name)
           + codec.pb_uint(3, level) + codec.pb_uint(4, exp))
    for land in lands:
        out += codec.pb_msg(5, land)
    return out


# --- build_plant_body: seed_id#1, land_id#2 --------------------------------

def test_build_plant_body_wire_order():
    # home_farm_plant_c2s {seed_id#1, land_id#2}: (seed=2, land=1) -> 08 02 10 01
    assert build_plant_body(2, 1) == bytes.fromhex("08021001")


def test_build_plant_body_matches_codec():
    assert build_plant_body(5001, 7) == codec.pb_uint(1, 5001) + codec.pb_uint(2, 7)


# --- build_harvest_body: land_id#1 -----------------------------------------

def test_build_harvest_body_single_field():
    assert build_harvest_body(3) == codec.pb_uint(1, 3)
    assert build_harvest_body(3) == bytes.fromhex("0803")


# --- build_worker_setting_body ---------------------------------------------

def test_build_worker_setting_body_minimal():
    # {team_cfg_id#1} only; empty seed_used_seq = free seeds = no buying.
    assert build_worker_setting_body(101) == codec.pb_uint(1, 101)


def test_build_worker_setting_body_with_fertilizer_and_rest():
    body = build_worker_setting_body(101, fertilizer_list=(11, 22), fertilizer_time_rest=3)
    d = codec.walk(body)
    assert (1, 101) in d
    # fertilizer_list#2 is repeated uint32 (unpacked)
    assert [v for fn, v in d if fn == 2] == [11, 22]
    assert (3, 3) in d


def test_build_worker_setting_body_seed_used_seq_as_kv():
    # seed_used_seq#4 repeated p_key_value{k#1, v#2}
    body = build_worker_setting_body(101, seed_used_seq=((5001, 2),))
    sub = [v for fn, v in codec.walk(body) if fn == 4]
    assert len(sub) == 1
    assert codec.walk_dict(bytes(sub[0])) == {1: 5001, 2: 2}


# --- build_shop_buy_body: shop_type#1, shop_id#2, num#3 --------------------

def test_build_shop_buy_body_wire():
    assert build_shop_buy_body(1, 9001, 3) == (
        codec.pb_uint(1, 1) + codec.pb_uint(2, 9001) + codec.pb_uint(3, 3))


# --- parse_farm_info: empty / growing / mature tri-state -------------------

def test_parse_farm_info_reads_role_and_level():
    body = _info_body(555, "農夫", 7, 120, lands=[_land(1)])
    info = parse_farm_info(body)
    assert isinstance(info, FarmInfo)
    assert info.role_id == 555
    assert info.name == "農夫"
    assert info.level == 7


def test_parse_farm_info_empty_land_has_no_crop():
    # land with no crop sub-message -> empty
    info = parse_farm_info(_info_body(1, "x", 1, 0, lands=[_land(10)]))
    land = info.lands[0]
    assert isinstance(land, FarmLand)
    assert land.id == 10
    assert land.has_crop is False
    assert land.is_empty is True
    assert land.is_ready is False


def test_parse_farm_info_state_zero_crop_is_empty():
    # crop present but state==0 (NOT_EXIT) also counts as empty land
    crop = _crop(0, 1, 0, 0, STATE_NOT_EXIT)
    info = parse_farm_info(_info_body(1, "x", 1, 0, lands=[_land(11, crop)]))
    land = info.lands[0]
    assert land.state == STATE_NOT_EXIT
    assert land.is_empty is True
    assert land.is_ready is False


def test_parse_farm_info_growing_crop_not_empty_not_ready():
    crop = _crop(99, 1, 5001, 6001, STATE_GROWING, start=100, end=9_999_999_999)
    info = parse_farm_info(_info_body(1, "x", 1, 0, lands=[_land(12, crop)]))
    land = info.lands[0]
    assert land.has_crop is True
    assert land.state == STATE_GROWING
    assert land.end_time == 9_999_999_999
    assert land.is_empty is False
    assert land.is_ready is False


def test_parse_farm_info_mature_crop_is_ready():
    crop = _crop(99, 1, 5001, 6001, STATE_MATURE, end=12345)
    info = parse_farm_info(_info_body(1, "x", 1, 0, lands=[_land(13, crop)]))
    land = info.lands[0]
    assert land.state == STATE_MATURE
    assert land.is_ready is True
    assert land.is_empty is False


def test_parse_farm_info_growing_but_end_time_passed_is_ready():
    # state still GROWING but end_time <= now -> ready (fallback rule)
    crop = _crop(99, 1, 5001, 6001, STATE_GROWING, end=1000)
    info = parse_farm_info(_info_body(1, "x", 1, 0, lands=[_land(14, crop)]))
    land = info.lands[0]
    assert land.is_ready_at(now=2000) is True
    assert land.is_ready_at(now=500) is False


def test_parse_farm_info_repeated_lands_preserved_in_order():
    lands = [_land(1), _land(2, _crop(1, 1, 5001, 6001, STATE_GROWING, end=9e9)),
             _land(3, _crop(2, 1, 5001, 6001, STATE_MATURE, end=1))]
    info = parse_farm_info(_info_body(9, "n", 2, 5, lands=lands))
    assert [l.id for l in info.lands] == [1, 2, 3]
    assert [l.is_empty for l in info.lands] == [True, False, False]
    assert info.empty_lands == (info.lands[0],)
    assert info.ready_lands == (info.lands[2],)


# --- orchestrators against the fake transport ------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_read_farm_sends_role_id_and_parses():
    lands = [_land(1), _land(2, _crop(1, 1, 5001, 6001, STATE_MATURE, end=1))]
    body = _info_body(777, "me", 3, 9, lands=lands)
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        info = read_farm(c, role_id=777)
        assert info.role_id == 777
        assert len(info.lands) == 2
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_INFO]
        assert codec.walk_dict(sent[0]) == {1: 777}  # role_id#1
    finally:
        c.close()


def test_plant_empty_only_targets_empty_lands():
    lands = [_land(1),  # empty
             _land(2, _crop(1, 1, 5001, 6001, STATE_GROWING, end=9e9)),  # busy
             _land(3)]  # empty
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    plant_ok = codec.pb_uint(1, 0) + codec.pb_msg(2, _land(1, _crop(1, 1, 5001, 6001, STATE_GROWING, end=9e9)))
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_PLANT: lambda _b: [s2c(CMD_PLANT, plant_ok)],
    })
    try:
        summary = plant_empty(c, role_id=1, seed_id=5001)
        assert summary["attempted"] == 2  # only lands 1 and 3
        assert summary["planted"] == 2
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_PLANT]
        # each plant: {seed_id#1=5001, land_id#2 in {1,3}}
        assert {d[2] for d in sent} == {1, 3}
        assert all(d[1] == 5001 for d in sent)
    finally:
        c.close()


def test_plant_empty_counts_code_nonzero_as_failure():
    lands = [_land(1)]
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    fail = codec.pb_uint(1, 7)  # code=7 -> failure
    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_PLANT: lambda _b: [s2c(CMD_PLANT, fail)],
    })
    try:
        summary = plant_empty(c, role_id=1, seed_id=5001)
        assert summary["attempted"] == 1
        assert summary["planted"] == 0
    finally:
        c.close()


def test_plant_empty_handles_0x0201_rejection_without_crashing():
    # Live (小寶 2026-06-09): a rejected plant replies on 0x0201 (code 173, e.g.
    # 種子不足), NOT on CMD_PLANT. The old client.call(CMD_PLANT) timed out and
    # crashed. plant_empty must record ok=False with the error code instead.
    lands = [_land(1)]
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_PLANT: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))],
    })
    try:
        summary = plant_empty(c, role_id=1, seed_id=5001)
        assert summary["attempted"] == 1
        assert summary["planted"] == 0
        assert summary["results"][0]["ok"] is False
        assert summary["results"][0]["code"] == 173
    finally:
        c.close()


def test_harvest_ready_only_targets_mature_lands():
    lands = [_land(1, _crop(1, 1, 5001, 6001, STATE_GROWING, end=9e9)),  # not ready
             _land(2, _crop(2, 1, 5001, 6001, STATE_MATURE, end=1)),     # ready
             _land(3, _crop(3, 1, 5001, 6001, STATE_MATURE, end=1))]     # ready
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    reward = codec.pb_uint(1, 1001) + codec.pb_uint(2, 5)
    harvest_ok = (codec.pb_uint(1, 0) + codec.pb_msg(2, _land(2))
                  + codec.pb_uint(3, 4) + codec.pb_uint(4, 10) + codec.pb_msg(5, reward))
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_HARVEST: lambda _b: [s2c(CMD_HARVEST, harvest_ok)],
    })
    try:
        summary = harvest_ready(c, role_id=1)
        assert summary["attempted"] == 2  # lands 2 and 3 only
        assert summary["harvested"] == 2
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_HARVEST]
        assert {d[1] for d in sent} == {2, 3}  # land_id#1
        # rewards collected and surfaced
        assert summary["rewards"] == {1001: 10}  # 5 per harvest * 2
    finally:
        c.close()


def test_harvest_ready_code_nonzero_is_failure():
    lands = [_land(1, _crop(1, 1, 5001, 6001, STATE_MATURE, end=1))]
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    fail = codec.pb_uint(1, 3)  # code=3
    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_HARVEST: lambda _b: [s2c(CMD_HARVEST, fail)],
    })
    try:
        summary = harvest_ready(c, role_id=1)
        assert summary["attempted"] == 1
        assert summary["harvested"] == 0
        assert summary["rewards"] == {}
    finally:
        c.close()


def test_harvest_ready_handles_0x0201_rejection_without_crashing():
    # A rejected harvest replies on 0x0201 (not CMD_HARVEST) -> ok=False, no crash.
    lands = [_land(1, _crop(1, 1, 5001, 6001, STATE_MATURE, end=1))]
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_HARVEST: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))],
    })
    try:
        summary = harvest_ready(c, role_id=1)
        assert summary["attempted"] == 1
        assert summary["harvested"] == 0
        assert summary["rewards"] == {}
        assert summary["results"][0]["ok"] is False
        assert summary["results"][0]["code"] == 173
    finally:
        c.close()


def test_start_work_sends_worker_setting_and_reports_status():
    # s2c worker_info#1 = p_worker{team_cfg_id#1, worker_status#3>0 = running}
    worker = codec.pb_uint(1, 101) + codec.pb_uint(3, 1)
    s2c_body = codec.pb_msg(1, worker)
    c, fake = _client({CMD_WORKER_SETTING: lambda _b: [s2c(CMD_WORKER_SETTING, s2c_body)]})
    try:
        result = start_work(c, 101)
        assert result["running"] is True
        assert result["worker_status"] == 1
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_WORKER_SETTING]
        # empty seed_used_seq -> body is just team_cfg_id#1
        assert codec.walk_dict(sent[0]) == {1: 101}
    finally:
        c.close()


def test_start_work_status_zero_not_running():
    worker = codec.pb_uint(1, 101) + codec.pb_uint(3, 0)
    s2c_body = codec.pb_msg(1, worker)
    c, _ = _client({CMD_WORKER_SETTING: lambda _b: [s2c(CMD_WORKER_SETTING, s2c_body)]})
    try:
        result = start_work(c, 101)
        assert result["running"] is False
        assert result["worker_status"] == 0
    finally:
        c.close()


def test_start_work_handles_0x0201_rejection_without_crashing():
    # A rejected worker_setting replies on 0x0201 (not CMD_WORKER_SETTING) ->
    # running=False with the error code, no crash/timeout.
    c, _ = _client({CMD_WORKER_SETTING: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))]})
    try:
        result = start_work(c, 101)
        assert result["running"] is False
        assert result["worker_status"] == 0
        assert result["error_code"] == 173
    finally:
        c.close()


# --- build_fertilize_body: role_id#1, land_id#2, fertilizer_id#3, num#4 -----

def test_build_fertilize_body_wire_order():
    # role_id#1=0, land_id#2=3, fertilizer_id#3=111, num#4=1 (live capture)
    body = build_fertilize_body(3, 111)
    assert codec.walk(body) == [(1, 0), (2, 3), (3, 111), (4, 1)]


def test_build_fertilize_body_respects_num_and_role():
    body = build_fertilize_body(5, 111, num=2, role_id=777)
    assert codec.walk_dict(body) == {1: 777, 2: 5, 3: 111, 4: 2}


# --- build_get_other_worker_body: role_id#1, team_cfg_id#2:repeated ---------

def test_build_get_other_worker_body_repeats_team_ids():
    body = build_get_other_worker_body(777, (7001,))
    assert codec.walk(body) == [(1, 777), (2, 7001)]
    multi = build_get_other_worker_body(1, (7001, 7002))
    assert [v for fn, v in codec.walk(multi) if fn == 2] == [7001, 7002]


# --- fertilize_lands: per-land, only non-empty lands ------------------------

def test_fertilize_lands_only_targets_non_empty():
    lands = [_land(1),  # empty -> skip
             _land(2, _crop(1, 1, SEED_ID_FREE, 6011, STATE_GROWING, end=9e9)),
             _land(3, _crop(2, 1, SEED_ID_FREE, 6012, STATE_MATURE, end=1))]
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    fert_ok = codec.pb_uint(1, 0) + codec.pb_uint(2, 1) + codec.pb_msg(3, _land(2))
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_FERTILIZE: lambda _b: [s2c(CMD_FERTILIZE, fert_ok)],
    })
    try:
        summary = fertilize_lands(c, role_id=1, fertilizer_id=FERTILIZER_ID_HIGH_YIELD)
        assert summary["attempted"] == 2  # lands 2 and 3 (not the empty land 1)
        assert summary["fertilized"] == 2
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_FERTILIZE]
        assert {d[2] for d in sent} == {2, 3}            # land_id#2
        assert all(d[3] == FERTILIZER_ID_HIGH_YIELD for d in sent)  # fertilizer_id#3
        assert all(d[1] == 0 and d[4] == 1 for d in sent)          # role_id#1=0, num#4=1
    finally:
        c.close()


def test_fertilize_lands_handles_0x0201_rejection():
    lands = [_land(2, _crop(1, 1, SEED_ID_FREE, 6011, STATE_GROWING, end=9e9))]
    info_body = _info_body(1, "x", 1, 0, lands=lands)
    c, _ = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, info_body)],
        CMD_FERTILIZE: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 159))],
    })
    try:
        summary = fertilize_lands(c, role_id=1, fertilizer_id=111)
        assert summary["attempted"] == 1
        assert summary["fertilized"] == 0
        assert summary["results"][0]["ok"] is False
        assert summary["results"][0]["code"] == 159
    finally:
        c.close()


# --- read_work_status: 打工偵測 via get_other_role_info (pure read) ----------

def _other_worker(role_id, team_cfg_id, worker_status):
    # p_other_worker {role_id#1, team_cfg_id#2, pet_info#3, worker_status#4, ...}
    return (codec.pb_uint(1, role_id) + codec.pb_uint(2, team_cfg_id)
            + codec.pb_uint(4, worker_status))


def test_read_work_status_running():
    # team_list#1: p_other_worker[] ; worker_status#4 = 1 -> running
    body = codec.pb_msg(1, _other_worker(777, FARM_WORKER_TEAM_CFG_ID, 1))
    c, fake = _client({CMD_GET_OTHER_WORKER: lambda _b: [s2c(CMD_GET_OTHER_WORKER, body)]})
    try:
        result = read_work_status(c, role_id=777)
        assert result["running"] is True
        assert result["worker_status"] == 1
        assert result["found"] is True
        assert result["team_cfg_id"] == FARM_WORKER_TEAM_CFG_ID
        # c2s carries our own role_id#1 + team_cfg_id#2 = 7001
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_GET_OTHER_WORKER]
        assert codec.walk(sent[0]) == [(1, 777), (2, FARM_WORKER_TEAM_CFG_ID)]
    finally:
        c.close()


def test_read_work_status_stopped():
    body = codec.pb_msg(1, _other_worker(777, FARM_WORKER_TEAM_CFG_ID, 0))
    c, _ = _client({CMD_GET_OTHER_WORKER: lambda _b: [s2c(CMD_GET_OTHER_WORKER, body)]})
    try:
        result = read_work_status(c, role_id=777)
        assert result["running"] is False
        assert result["worker_status"] == 0
        assert result["found"] is True
    finally:
        c.close()


def test_read_work_status_empty_team_list_not_found():
    # empty team_list -> found=False, not running (mirrors live: wrong/empty id)
    c, _ = _client({CMD_GET_OTHER_WORKER: lambda _b: [s2c(CMD_GET_OTHER_WORKER, b"")]})
    try:
        result = read_work_status(c, role_id=777)
        assert result["found"] is False
        assert result["running"] is False
    finally:
        c.close()


def test_stop_work_does_not_send_cancel_when_worker_status_not_found():
    """查不到工作隊伍時，禁止盲送 18178 取消封包。"""
    body = b""
    c, fake = _client({CMD_GET_OTHER_WORKER: lambda _b: [s2c(CMD_GET_OTHER_WORKER, body)]})
    try:
        result = farm_module.stop_work(c, role_id=777)
        assert result["ok"] is False
        assert result["reason"] == "worker_status_not_found"
        assert CMD_WORKER_CANCEL not in fake.sent_cmds()
    finally:
        c.close()


# --- shop_info / buy-to-daily-target (莊園購買 4/4) -------------------------

def _shop_info_body(shop_type, counts):
    # shop_info_s2c {shop_type#1, item#2:repeated {shop_id#1, count#2}}
    out = codec.pb_uint(1, shop_type)
    for sid, cnt in counts.items():
        out += codec.pb_msg(2, codec.pb_uint(1, sid) + codec.pb_uint(2, cnt))
    return out


def test_build_shop_info_body():
    assert build_shop_info_body(4) == codec.pb_uint(1, 4)


def test_parse_shop_counts_maps_shop_id_to_count():
    body = _shop_info_body(4, {407: 4, 408: 1})
    assert parse_shop_counts(body) == {407: 4, 408: 1}
    # absent shop_ids simply don't appear (caller defaults to 0)
    assert parse_shop_counts(_shop_info_body(4, {})) == {}


def test_read_shop_counts_sends_shop_type():
    body = _shop_info_body(4, {407: 2})
    c, fake = _client({CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, body)]})
    try:
        counts = read_shop_counts(c, FARM_SHOP_TYPE)
        assert counts == {407: 2}
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_SHOP_INFO]
        assert codec.walk_dict(sent[0]) == {1: 4}  # shop_type#1
    finally:
        c.close()


def test_buy_to_daily_target_buys_remainder_only():
    # already bought 1 today, target 4 -> buy num=3 in one call
    info = _shop_info_body(4, {407: 1})
    buy_ok = codec.pb_uint(1, 407) + codec.pb_uint(2, 3)  # shop_buy_s2c {shop_id, num}
    c, fake = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, info)],
        CMD_SHOP_BUY: lambda _b: [s2c(CMD_SHOP_BUY, buy_ok)],
    })
    try:
        r = buy_to_daily_target(c, 407, 4)
        assert r["before"] == 1 and r["need"] == 3 and r["bought"] == 3 and r["ok"] is True
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_SHOP_BUY]
        assert sent == [{1: 4, 2: 407, 3: 3}]  # shop_type, shop_id, num=remainder
    finally:
        c.close()


def test_buy_to_daily_target_skips_when_already_at_target():
    # GUI 已買滿 4/4 -> need=0, NO shop_buy sent (respects manual purchase)
    info = _shop_info_body(4, {407: 4})
    c, fake = _client({CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, info)]})
    try:
        r = buy_to_daily_target(c, 407, 4)
        assert r["before"] == 4 and r["need"] == 0 and r["bought"] == 0 and r["ok"] is True
        assert [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_SHOP_BUY] == []
    finally:
        c.close()


def test_buy_to_daily_target_zero_bought_buys_full_target():
    info = _shop_info_body(4, {})  # 408 absent -> 0 bought
    buy_ok = codec.pb_uint(1, 408) + codec.pb_uint(2, 4)
    c, fake = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, info)],
        CMD_SHOP_BUY: lambda _b: [s2c(CMD_SHOP_BUY, buy_ok)],
    })
    try:
        r = buy_to_daily_target(c, 408, 4)
        assert r["before"] == 0 and r["need"] == 4 and r["bought"] == 4
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_SHOP_BUY]
        assert sent == [{1: 4, 2: 408, 3: 4}]
    finally:
        c.close()


def test_buy_to_daily_target_handles_0x0201():
    info = _shop_info_body(4, {})
    c, _ = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, info)],
        CMD_SHOP_BUY: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 90))],
    })
    try:
        r = buy_to_daily_target(c, 407, 4)
        assert r["ok"] is False and r["code"] == 90 and r["bought"] == 0
    finally:
        c.close()


def test_buy_farm_shop_one_info_read_both_items():
    # seed 407 at 4/4 (skip), fertilizer 408 at 0 -> buy 4
    info = _shop_info_body(4, {407: 4})
    buy_ok = codec.pb_uint(1, 408) + codec.pb_uint(2, 4)
    c, fake = _client({
        CMD_SHOP_INFO: lambda _b: [s2c(CMD_SHOP_INFO, info)],
        CMD_SHOP_BUY: lambda _b: [s2c(CMD_SHOP_BUY, buy_ok)],
    })
    try:
        results = buy_farm_shop(c, [{"shop_id": 407, "target": 4},
                                    {"shop_id": 408, "target": 4}])
        assert results[0]["shop_id"] == 407 and results[0]["bought"] == 0  # skipped
        assert results[1]["shop_id"] == 408 and results[1]["bought"] == 4
        # only ONE shop_info read for both items
        assert len([1 for _s, cmd, _b in fake.framed_sent() if cmd == CMD_SHOP_INFO]) == 1
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_SHOP_BUY]
        assert sent == [{1: 4, 2: 408, 3: 4}]  # only the fertilizer buy fired
    finally:
        c.close()


def test_cmd_constants_match_recon():
    assert CMD_INFO == 3077
    assert CMD_PLANT == 3078
    assert CMD_FERTILIZE == 3079
    assert CMD_HARVEST == 3081
    assert CMD_WORKER_SETTING == 18689
    assert CMD_GET_OTHER_WORKER == 18690
    assert CMD_SHOP_INFO == 6913
    assert CMD_SHOP_BUY == 6914


# --- 4-stage 豐收卡 helpers (live-reverse-engineered on 5554, 2026-06-22) ----

from ws_token.farm import (  # noqa: E402
    CMD_PICK,
    fertilize_until_mature,
    harvest_lands,
    pick_lands,
)


def test_fertilize_until_mature_loops_until_state_mature_and_sends_num3():
    # fertilize reply: GROWING on the 1st pass, MATURE on the 2nd → matures in 2 passes.
    n = {"c": 0}

    def fert(_b):
        n["c"] += 1
        state = STATE_MATURE if n["c"] >= 2 else STATE_GROWING
        new_land = _land(1, _crop(1, 0, 103, 6013, state, end=1))
        body = codec.pb_uint(1, 0) + codec.pb_uint(2, 0) + codec.pb_msg(3, new_land)
        return [s2c(CMD_FERTILIZE, body)]

    c, fake = _client({CMD_FERTILIZE: fert})
    try:
        res = fertilize_until_mature(c, [1], num=3, spacing=0)
        assert res["mature"] == [1]
        assert n["c"] == 2  # stopped fertilizing the moment it read state==MATURE
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_FERTILIZE]
        assert all(d.get(4) == 3 for d in sent)  # num#4 = 3 (the live 一鍵施肥 amount)
        assert all(d.get(1) == 0 for d in sent)  # role_id#1 = 0 (self)
    finally:
        c.close()


def test_pick_lands_sends_pick_3080_with_role0_and_landid():
    c, fake = _client({CMD_PICK: lambda _b: [s2c(CMD_PICK, codec.pb_uint(1, 0))]})
    try:
        res = pick_lands(c, [2, 5], spacing=0)
        assert res["picked"] == 2
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_PICK]
        assert [d.get(2) for d in sent] == [2, 5]  # land_id#2
        assert all(d.get(1) == 0 for d in sent)    # role_id#1 = 0 (self)
    finally:
        c.close()


def test_pick_lands_code_nonzero_is_failure():
    # 收成 reply on 3080 carries code#1; code!=0 (e.g. 122 未熟) = failure, not crash.
    c, _ = _client({CMD_PICK: lambda _b: [s2c(CMD_PICK, codec.pb_uint(1, 122))]})
    try:
        res = pick_lands(c, [1], spacing=0)
        assert res["picked"] == 0
        assert res["results"][0]["code"] == 122
    finally:
        c.close()


def test_harvest_lands_counts_and_sums_rewards():
    reward = codec.pb_msg(5, codec.pb_uint(1, 6013) + codec.pb_uint(2, 225))
    harv_ok = codec.pb_uint(1, 0) + reward
    c, fake = _client({CMD_HARVEST: lambda _b: [s2c(CMD_HARVEST, harv_ok)]})
    try:
        res = harvest_lands(c, [1, 2, 3], spacing=0)
        assert res["harvested"] == 3
        assert res["rewards"] == {6013: 225 * 3}
        sent = [codec.walk_dict(b) for _s, cmd, b in fake.framed_sent() if cmd == CMD_HARVEST]
        assert [d.get(1) for d in sent] == [1, 2, 3]  # land_id#1
    finally:
        c.close()


def _empty_farm_info(role_id=1):
    lands = tuple(
        FarmLand(id=land_id, has_crop=False, state=STATE_NOT_EXIT, end_time=0)
        for land_id in range(1, 7)
    )
    return FarmInfo(role_id=role_id, name="test", level=1, exp=0, lands=lands)


def test_harvest_cycle_aborts_before_purchase_when_cancel_not_verified(monkeypatch):
    """WS 未讀到 stopped 時，禁止買肥料、買卡或種特級種子。"""
    monkeypatch.setattr(
        farm_module, "stop_work", lambda *_a, **_k: {"ok": False, "verified": False},
    )
    monkeypatch.setattr(
        farm_module, "start_work_simple",
        lambda *_a, **_k: {"ok": True, "verified": True},
    )
    purchases = []
    monkeypatch.setattr(
        farm_module, "buy_to_daily_target",
        lambda *_a, **_k: purchases.append(True),
    )

    result = farm_module.run_harvest_card_cycle(object(), 1, spacing=0)

    assert result["ok"] is False
    assert result["failure"] == "work_cancel_not_verified"
    assert purchases == []


def test_stop_work_re_reads_status_and_requires_stopped(monkeypatch):
    """18178 有回覆仍不夠，必須再讀到 worker_status=0。"""
    client = type("Client", (), {
        "call_for": lambda self, *_a, **_k: (farm_module.CMD_WORKER_STATE, b""),
    })()
    statuses = iter([
        {"found": True, "running": True, "worker_status": 1},
        {"found": True, "running": False, "worker_status": 0},
    ])
    monkeypatch.setattr(
        farm_module, "read_work_status", lambda *_a, **_k: next(statuses),
    )

    result = farm_module.stop_work(client, role_id=1)

    assert result["ok"] is True
    assert result["verified"] is True


def test_harvest_cycle_no_card_means_already_executed(monkeypatch):
    """肥料步驟完成但卡買不到時，恢復打工並成功略過。"""
    monkeypatch.setattr(
        farm_module, "stop_work", lambda *_a, **_k: {"ok": True, "verified": True},
    )
    monkeypatch.setattr(
        farm_module, "start_work_simple",
        lambda *_a, **_k: {"ok": True, "verified": True},
    )
    monkeypatch.setattr(farm_module, "read_farm", lambda *_a, **_k: _empty_farm_info())
    purchases = iter([
        {"ok": True, "bought": 0},  # 高產肥料已達每日目標
        {"ok": True, "bought": 0},  # 豐收卡買不到 = 已執行
    ])
    monkeypatch.setattr(
        farm_module, "buy_to_daily_target", lambda *_a, **_k: next(purchases),
    )
    planted = []
    monkeypatch.setattr(
        farm_module, "plant_lands", lambda *_a, **_k: planted.append(True),
    )

    result = farm_module.run_harvest_card_cycle(object(), 1, spacing=0)

    assert result["ok"] is True
    assert result["already_executed"] is True
    assert result["restarted_work"] is True
    assert planted == []


def test_harvest_cycle_requires_every_premium_seed_to_plant(monkeypatch):
    """買到卡後只種成 5/6 塊時，不得繼續施肥或宣告 buff 用完。"""
    monkeypatch.setattr(
        farm_module, "stop_work", lambda *_a, **_k: {"ok": True, "verified": True},
    )
    monkeypatch.setattr(
        farm_module, "start_work_simple",
        lambda *_a, **_k: {"ok": True, "verified": True},
    )
    monkeypatch.setattr(farm_module, "read_farm", lambda *_a, **_k: _empty_farm_info())
    purchases = iter([
        {"ok": True, "bought": 4},
        {"ok": True, "bought": 1},
    ])
    monkeypatch.setattr(
        farm_module, "buy_to_daily_target", lambda *_a, **_k: next(purchases),
    )
    monkeypatch.setattr(
        farm_module, "plant_lands", lambda *_a, **_k: {"planted": 5},
    )
    fertilized = []
    monkeypatch.setattr(
        farm_module, "fertilize_until_mature",
        lambda *_a, **_k: fertilized.append(True),
    )

    result = farm_module.run_harvest_card_cycle(object(), 1, spacing=0)

    assert result["ok"] is False
    assert result["failure"] == "premium_seed_plant_incomplete"
    assert result["buff_exhausted"] is False
    assert fertilized == []
