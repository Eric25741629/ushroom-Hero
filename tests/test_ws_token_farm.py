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
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.farm import (  # noqa: E402
    CMD_ERROR,
    CMD_HARVEST,
    CMD_INFO,
    CMD_PLANT,
    CMD_SHOP_BUY,
    CMD_WORKER_SETTING,
    STATE_GROWING,
    STATE_MATURE,
    STATE_NOT_EXIT,
    FarmInfo,
    FarmLand,
    build_harvest_body,
    build_plant_body,
    build_shop_buy_body,
    build_worker_setting_body,
    harvest_ready,
    parse_farm_info,
    plant_empty,
    read_farm,
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


def test_cmd_constants_match_recon():
    assert CMD_INFO == 3077
    assert CMD_PLANT == 3078
    assert CMD_HARVEST == 3081
    assert CMD_WORKER_SETTING == 18689
    assert CMD_SHOP_BUY == 6914
