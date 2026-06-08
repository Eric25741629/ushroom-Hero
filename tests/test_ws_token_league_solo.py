"""Tests for ws_token.league_solo — claim 烈焰山洞 (lava) + 魔法劇場 (theatre) boxes.

Schemas are the live-exported truth:
  docs/protocol/DUNGEON_PROTO_SCHEMA.json
    dungeon_league_solo_info     3598 (0x0E0E) c2s {} -> s2c {box_list#1:p_league_solo_box[], record_list#2, reward_status#3}
    dungeon_league_solo_get_reward 3599 (0x0E0F) c2s {type#1:uint32} -> s2c (box update)
    dungeon_league_solo_update_box 3600 (0x0E10) s2c {box_info#1:p_league_solo_box[]}  (the success ack)
    dungeon_league_hard_info     3608 (0x0E18) -> s2c {buff_id#1, state#2, ...}  (NO box_list: not needed)
  docs/protocol/TYPE_PROTO_SCHEMA.json
    p_league_solo_box {type#1, count#2, got_count#3, rare_offer_name#4}

Behaviour:
  - all four box types (1/2 daily lava, 3/4 weekly theatre) live in solo_info.box_list.
  - claimable = count > got_count (server enforces the real chest_limit; over-claim -> error 159).
  - error 159 = already claimed / maxed -> treat as "already done", skip, do NOT abort the batch.
  - a get_reward s2c reply (update_box 0x0E10 or any non-error reply on that cmd) == success.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.league_solo import (  # noqa: E402
    ALL_TYPES,
    CMD_ERROR,
    CMD_GET_REWARD,
    CMD_SOLO_INFO,
    CMD_UPDATE_BOX,
    DAILY_TYPES,
    ERR_ALREADY_CLAIMED,
    WEEKLY_TYPES,
    ClaimResult,
    LeagueSoloBox,
    build_get_reward_body,
    claim_available,
    claim_box,
    parse_claim_result,
    parse_solo_info,
    read_boxes,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


def _box(type_, count, got_count, rare_name=""):
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, count)
            + codec.pb_uint(3, got_count) + codec.pb_str(4, rare_name))


def _solo_info_body(boxes, *, record_entries=(), reward_status=None):
    out = b"".join(codec.pb_msg(1, b) for b in boxes)
    out += b"".join(codec.pb_msg(2, r) for r in record_entries)
    if reward_status is not None:
        out += codec.pb_uint(3, reward_status)
    return out


# --- build_get_reward_body: {type#1:uint32} --------------------------------

def test_build_get_reward_body_wire():
    # get_reward_c2s {type#1}: type=1 -> tag 0x08, value 0x01
    assert build_get_reward_body(1) == bytes.fromhex("0801")


def test_build_get_reward_body_type_four():
    assert build_get_reward_body(4) == codec.pb_uint(1, 4)


def test_build_get_reward_body_matches_walk():
    assert codec.walk_dict(build_get_reward_body(3)) == {1: 3}


# --- parse_solo_info: box_list (field 1, repeated) -------------------------

def test_parse_solo_info_decodes_box_list():
    body = _solo_info_body(
        [_box(1, 1, 0, "稀有甲"), _box(2, 1, 1, "稀有乙"),
         _box(3, 2, 0), _box(4, 1, 1)],
        reward_status=5,
    )
    boxes = parse_solo_info(body)
    assert [b.type for b in boxes] == [1, 2, 3, 4]
    assert (boxes[0].count, boxes[0].got_count) == (1, 0)
    assert boxes[0].rare_offer_name == "稀有甲"
    assert boxes[0].claimable is True            # count 1 > got 0
    assert boxes[1].claimable is False           # count 1 == got 1
    assert boxes[2].claimable is True            # count 2 > got 0
    assert boxes[3].claimable is False           # count 1 == got 1


def test_parse_solo_info_ignores_record_and_status_fields():
    record = codec.pb_uint(1, 999) + codec.pb_str(2, "someone")
    body = _solo_info_body([_box(1, 1, 0)], record_entries=[record], reward_status=7)
    boxes = parse_solo_info(body)
    assert len(boxes) == 1 and boxes[0].type == 1


def test_parse_solo_info_empty():
    assert parse_solo_info(b"") == []


def test_claimable_property():
    assert LeagueSoloBox(type=1, count=3, got_count=1).claimable is True
    assert LeagueSoloBox(type=1, count=1, got_count=1).claimable is False
    assert LeagueSoloBox(type=1, count=0, got_count=0).claimable is False


# --- parse_claim_result: update_box s2c vs 0x0201 error --------------------

def test_parse_claim_result_update_box_is_success():
    body = codec.pb_msg(1, _box(1, 1, 1))   # box now got_count == count
    r = parse_claim_result(CMD_UPDATE_BOX, body, type_=1)
    assert r.success is True
    assert r.error_code is None
    assert r.already_claimed is False


def test_parse_claim_result_get_reward_cmd_is_success():
    # some servers reply on the request cmd itself; any non-error reply == claimed
    r = parse_claim_result(CMD_GET_REWARD, b"", type_=2)
    assert r.success is True


def test_parse_claim_result_error_159_is_already_claimed():
    body = codec.pb_uint(1, ERR_ALREADY_CLAIMED)   # 159 -> \x08\x9f\x01
    r = parse_claim_result(CMD_ERROR, body, type_=1)
    assert r.success is False
    assert r.already_claimed is True
    assert r.error_code == ERR_ALREADY_CLAIMED


def test_parse_claim_result_other_error_is_failure_not_already():
    r = parse_claim_result(CMD_ERROR, codec.pb_uint(1, 2), type_=1)
    assert r.success is False
    assert r.already_claimed is False
    assert r.error_code == 2


def test_err_already_claimed_is_159():
    assert ERR_ALREADY_CLAIMED == 159


def test_type_groups():
    assert DAILY_TYPES == (1, 2)
    assert WEEKLY_TYPES == (3, 4)
    assert ALL_TYPES == (1, 2, 3, 4)


# --- read_boxes / claim_box against the fake transport ---------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_read_boxes_roundtrip():
    body = _solo_info_body([_box(1, 1, 0, "甲"), _box(3, 1, 0, "乙")])
    c, _ = _client({CMD_SOLO_INFO: lambda _b: [s2c(CMD_SOLO_INFO, body)]})
    try:
        boxes = read_boxes(c)
        assert [b.type for b in boxes] == [1, 3]
        assert boxes[0].rare_offer_name == "甲"
    finally:
        c.close()


def test_claim_box_sends_type_and_resolves_update_box():
    ack = codec.pb_msg(1, _box(2, 1, 1))
    c, fake = _client({CMD_GET_REWARD: lambda _b: [s2c(CMD_UPDATE_BOX, ack)]})
    try:
        r = claim_box(c, 2)
        assert isinstance(r, ClaimResult)
        assert r.success is True and r.type == 2
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_GET_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 2}
    finally:
        c.close()


def test_claim_box_error_159_skips_as_already_claimed():
    body = codec.pb_uint(1, ERR_ALREADY_CLAIMED)
    c, _ = _client({CMD_GET_REWARD: lambda _b: [s2c(CMD_ERROR, body)]})
    try:
        r = claim_box(c, 1)
        assert r.success is False and r.already_claimed is True
    finally:
        c.close()


# --- claim_available: only count>got_count, 159 skipped, no abort ----------

def test_claim_available_claims_only_claimable_and_skips_maxed():
    info = _solo_info_body([
        _box(1, 1, 0),   # claimable -> claim
        _box(2, 1, 1),   # maxed -> skip without sending
        _box(3, 2, 0),   # claimable -> claim
        _box(4, 0, 0),   # nothing earned -> skip
    ])

    def get_reward_resp(body):
        type_ = codec.walk_dict(body).get(1)
        return [s2c(CMD_UPDATE_BOX, codec.pb_msg(1, _box(type_, 1, 1)))]

    c, fake = _client({CMD_SOLO_INFO: lambda _b: [s2c(CMD_SOLO_INFO, info)],
                       CMD_GET_REWARD: get_reward_resp})
    try:
        summary = claim_available(c)
        assert summary["attempted"] == 2          # types 1 and 3
        assert summary["claimed"] == 2
        assert summary["skipped_maxed"] == 2      # types 2 and 4
        claimed_types = sorted(codec.walk_dict(b).get(1)
                               for _sid, cmd, b in fake.framed_sent()
                               if cmd == CMD_GET_REWARD)
        assert claimed_types == [1, 3]
    finally:
        c.close()


def test_claim_available_159_counts_as_already_not_failure_and_continues():
    info = _solo_info_body([_box(1, 1, 0), _box(3, 1, 0)])

    def get_reward_resp(body):
        type_ = codec.walk_dict(body).get(1)
        if type_ == 1:   # server says already claimed (race) -> 159
            return [s2c(CMD_ERROR, codec.pb_uint(1, ERR_ALREADY_CLAIMED))]
        return [s2c(CMD_UPDATE_BOX, codec.pb_msg(1, _box(type_, 1, 1)))]

    c, _ = _client({CMD_SOLO_INFO: lambda _b: [s2c(CMD_SOLO_INFO, info)],
                    CMD_GET_REWARD: get_reward_resp})
    try:
        summary = claim_available(c)
        assert summary["attempted"] == 2
        assert summary["claimed"] == 1            # type 3 only
        assert summary["already"] == 1            # type 1 -> 159
        # batch did not abort: both types were attempted
        assert len(summary["results"]) == 2
    finally:
        c.close()


def test_claim_available_types_filter_restricts_to_weekly():
    info = _solo_info_body([_box(1, 1, 0), _box(2, 1, 0), _box(3, 1, 0), _box(4, 1, 0)])

    def get_reward_resp(body):
        type_ = codec.walk_dict(body).get(1)
        return [s2c(CMD_UPDATE_BOX, codec.pb_msg(1, _box(type_, 1, 1)))]

    c, fake = _client({CMD_SOLO_INFO: lambda _b: [s2c(CMD_SOLO_INFO, info)],
                       CMD_GET_REWARD: get_reward_resp})
    try:
        summary = claim_available(c, types=WEEKLY_TYPES)
        assert summary["claimed"] == 2
        claimed_types = sorted(codec.walk_dict(b).get(1)
                               for _sid, cmd, b in fake.framed_sent()
                               if cmd == CMD_GET_REWARD)
        assert claimed_types == [3, 4]            # daily types 1/2 not touched
    finally:
        c.close()
