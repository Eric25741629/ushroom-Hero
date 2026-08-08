"""Protocol and orchestration tests for WorldBoss pure WS."""
from __future__ import annotations

from ws_token import codec
from ws_token import hellgate
from ws_token.client import WSGameClient
from tests.fakes.ws_fakes import CREDS, FakeTransport, factory_for, login_responder, s2c


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    client = WSGameClient(
        CREDS,
        transport_factory=factory_for(fake),
        heartbeat_enabled=False,
    )
    client.connect()
    return client, fake


def _kv(key: int, value: int) -> bytes:
    return codec.pb_uint(1, key) + codec.pb_uint(2, value)


def test_worldboss_constants_are_not_daily_abyss_constants():
    assert hellgate.TYPE_WORLD_BOSS == 13
    assert hellgate.LEVEL_WORLD_BOSS == 1
    assert hellgate.CMD_WORLD_BOSS_INFO == 3594
    assert hellgate.CMD_RESULT == 3592
    assert hellgate.CMD_GENERIC_RESULT == 3587
    assert hellgate.CMD_BATTLE_MORE_START == 3597
    assert hellgate.CMD_FINISH_WORLD_BOSS == 6593


def test_build_start_body_is_type_then_level():
    assert hellgate.build_start_body() == bytes.fromhex("080d1001")


def test_build_result_body_contains_hp_and_last_hurt_args():
    body = hellgate.build_result_body(
        hellgate.TYPE_WORLD_BOSS,
        1,
        result=0,
        args=((1, 123), (4, 456)),
    )
    fields = codec.walk_dict(body)
    assert fields[1] == 13
    assert fields[2] == 1
    assert fields[3] == 0
    assert fields[4] == 0
    args = [codec.walk_dict(bytes(v)) for n, v in codec.walk(body) if n == 6]
    assert args == [{1: 1, 2: 123}, {1: 4, 2: 456}]
    assert all(n != 5 for n, _raw in codec.walk(body))


def test_wait_for_session_handoff_only_sleeps_remaining_time(monkeypatch):
    class Client:
        connection_started_at = 95.0

    sleeps = []
    monkeypatch.setattr(hellgate.time, "time", lambda: 100.0)
    monkeypatch.setattr(hellgate.time, "sleep", sleeps.append)

    hellgate._wait_for_session_handoff(Client(), 8.0)

    assert sleeps == [3.0]


def test_wait_for_settlement_delay_only_sleeps_until_five_minutes(monkeypatch):
    sleeps = []
    monkeypatch.setattr(hellgate.time, "monotonic", lambda: 125.0)
    monkeypatch.setattr(hellgate.time, "sleep", sleeps.append)

    waited = hellgate._wait_for_settlement_delay(100.0, 300.0)

    assert waited == 275.0
    assert sleeps == [275.0]


def test_wait_for_settlement_delay_does_not_add_another_five_minutes(monkeypatch):
    sleeps = []
    monkeypatch.setattr(hellgate.time, "monotonic", lambda: 405.0)
    monkeypatch.setattr(hellgate.time, "sleep", sleeps.append)

    waited = hellgate._wait_for_settlement_delay(100.0, 300.0)

    assert waited == 0.0
    assert sleeps == []


def test_parse_worldboss_info_decodes_gate_and_string_counters():
    body = (
        codec.pb_uint(1, 1)
        + codec.pb_uint(2, 100)
        + codec.pb_uint(3, 200)
        + codec.pb_str(4, "冠軍")
        + codec.pb_uint(6, 7)
        + codec.pb_str(7, "12345678901234567890")
        + codec.pb_uint(8, 9)
        + codec.pb_str(9, "1234")
        + codec.pb_uint(10, 1)
        + codec.pb_uint(11, 0)
        + codec.pb_uint(12, 1)
    )
    info = hellgate.parse_info(hellgate.CMD_WORLD_BOSS_INFO, body)
    assert info.success is True
    assert (info.is_open, info.times, info.my_rank) == (1, 1, 9)
    assert info.role_name == "冠軍"
    assert info.total_hurt == "12345678901234567890"
    assert info.my_hurt == "1234"
    assert info.pending_result == 1


def test_parse_start_counts_roles_and_keeps_raw_body():
    role = codec.pb_uint(1, 123456789)
    body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(4, 1)
        + codec.pb_uint(5, 42)
        + codec.pb_msg(6, role)
        + codec.pb_msg(6, role)
        + codec.pb_msg(8, codec.pb_str(1, "rank"))
    )
    started = hellgate.parse_start(hellgate.CMD_BATTLE_MORE_START, body)
    assert started.success is True
    assert (started.type, started.dungeon_id, started.random_seed) == (13, 1, 42)
    assert started.roles == 2
    assert started.body == body


def test_parse_result_preserves_rewards_ext_and_sext():
    reward = codec.pb_uint(1, 9001) + codec.pb_uint(2, 3)
    sext = codec.pb_uint(1, 1) + codec.pb_str(2, "500000")
    body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(4, 0)
        + codec.pb_msg(5, reward)
        + codec.pb_msg(6, _kv(4, 456))
        + codec.pb_msg(7, sext)
    )
    result = hellgate.parse_result(hellgate.CMD_RESULT, body)
    assert result.success is True
    assert result.rewards == {9001: 3}
    assert result.ext == ((4, 456),)
    assert result.sext == ((1, "500000"),)


def test_parse_generic_finish_result_reads_live_field_six_rewards():
    reward = codec.pb_uint(1, 9) + codec.pb_uint(2, 243481)
    sext = codec.pb_uint(1, 1) + codec.pb_str(2, "52202652322156975787")
    body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 100)
        + codec.pb_uint(3, 13)
        + codec.pb_uint(4, 0)
        + codec.pb_msg(6, reward)
        + codec.pb_msg(7, sext)
    )

    result = hellgate.parse_result(hellgate.CMD_GENERIC_RESULT, body)

    assert result.success is True
    assert (result.type, result.dungeon_id, result.result) == (13, 100, 0)
    assert result.rewards == {9: 243481}
    assert result.ext == ()
    assert result.sext == ((1, "52202652322156975787"),)


def test_finish_worldboss_sends_empty_6593_and_waits_for_3587():
    body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 100)
        + codec.pb_uint(3, 13)
        + codec.pb_uint(4, 0)
    )
    client, fake = _client({
        hellgate.CMD_FINISH_WORLD_BOSS: lambda sent: [
            s2c(hellgate.CMD_GENERIC_RESULT, body) if sent == b"" else None
        ],
    })
    try:
        result = hellgate.finish_worldboss(client)
        assert result.success is True
        sent = [b for _sid, cmd, b in fake.framed_sent()
                if cmd == hellgate.CMD_FINISH_WORLD_BOSS]
        assert sent == [b""]
    finally:
        client.close()


def test_run_with_b_skips_before_start_when_event_is_closed(monkeypatch):
    info_body = codec.pb_uint(1, 0) + codec.pb_uint(10, 1)
    client, fake = _client({hellgate.CMD_WORLD_BOSS_INFO: lambda _b: [
        s2c(hellgate.CMD_WORLD_BOSS_INFO, info_body),
    ]})
    monkeypatch.setattr(hellgate, "open_raw_cdp_runtime", lambda _port: (
        None, None, object(), "raw_cdp"
    ))
    monkeypatch.setattr(hellgate, "close_raw_cdp_runtime", lambda *_args: None)
    try:
        report = hellgate.run_with_b(
            client,
            cdp_port=1,
            session_settle_sec=0,
            settlement_delay_sec=0,
        )
        assert report.skipped == "event closed (is_open=0)"
        assert hellgate.CMD_BATTLE_MORE_START not in fake.sent_cmds()
    finally:
        client.close()


def test_run_with_b_falls_back_to_ephemeral_when_cdp_is_unavailable(monkeypatch):
    events = []
    info = hellgate.WorldBossInfo(success=True, is_open=0, times=1)

    def fail_cdp(port):
        events.append(("cdp", port))
        raise OSError("WinError 10061")

    def open_ephemeral(**kwargs):
        events.append(("ephemeral", kwargs))
        return object(), object(), object(), "ephemeral"

    monkeypatch.setattr(hellgate, "open_raw_cdp_runtime", fail_cdp)
    monkeypatch.setattr(hellgate, "open_b_runtime", open_ephemeral)
    monkeypatch.setattr(hellgate, "fetch_info", lambda *_args, **_kwargs: info)
    monkeypatch.setattr(
        hellgate,
        "close_b_runtime",
        lambda *_args, **kwargs: events.append(("close", kwargs)),
    )

    report = hellgate.run_with_b(
        object(),
        cdp_port=9230,
        game_url="https://mushroomh5.acenetgame.com/",
        headless=True,
        session_settle_sec=0,
    )

    assert report.skipped == "event closed (is_open=0)"
    assert events[0] == ("cdp", 9230)
    assert events[1][0] == "ephemeral"
    assert events[1][1]["prefer_ephemeral"] is True
    assert events[1][1]["headless"] is True
    assert events[2] == ("close", {"kind": "ephemeral"})


def test_run_with_b_opens_b_before_info_and_start(monkeypatch):
    events = []
    info = hellgate.WorldBossInfo(success=True, is_open=1, times=1)
    started = hellgate.WorldBossStart(
        success=True, dungeon_id=1, body=b"official-start-body"
    )

    monkeypatch.setattr(
        hellgate,
        "open_raw_cdp_runtime",
        lambda _port: (
            events.append("open")
            or (None, None, object(), "raw_cdp")
        ),
    )
    monkeypatch.setattr(
        hellgate,
        "close_raw_cdp_runtime",
        lambda _page: events.append("close"),
    )
    monkeypatch.setattr(
        hellgate,
        "fetch_info",
        lambda _client, **_kw: events.append("info") or info,
    )
    monkeypatch.setattr(
        hellgate,
        "start",
        lambda _client, **_kw: events.append("start") or started,
    )
    monkeypatch.setattr(
        hellgate,
        "_run_after_start",
        lambda *_args, **_kw: events.append("run")
        or hellgate.WorldBossRun(success=True),
    )

    report = hellgate.run_with_b(object(), cdp_port=9225, session_settle_sec=0)

    assert report.success is True
    assert events == ["open", "info", "start", "run", "close"]


def test_run_with_b_simulates_then_reports_server_result(monkeypatch):
    info_body = codec.pb_uint(1, 1) + codec.pb_uint(10, 1)
    after_info_body = (
        codec.pb_uint(1, 1)
        + codec.pb_str(9, "12")
        + codec.pb_uint(10, 0)
    )
    start_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(5, 42)
    )
    result_body = codec.pb_uint(1, 0) + codec.pb_uint(2, 13) + codec.pb_uint(3, 1) + codec.pb_uint(4, 0)
    finish_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 100)
        + codec.pb_uint(3, 13)
        + codec.pb_uint(4, 0)
        + codec.pb_msg(6, codec.pb_uint(1, 9) + codec.pb_uint(2, 88))
    )
    info_replies = iter((info_body, after_info_body))
    client, fake = _client({
        hellgate.CMD_WORLD_BOSS_INFO: lambda _b: [
            s2c(hellgate.CMD_WORLD_BOSS_INFO, next(info_replies))
        ],
        hellgate.CMD_BATTLE_MORE_START: lambda _b: [s2c(hellgate.CMD_BATTLE_MORE_START, start_body)],
        hellgate.CMD_RESULT: lambda _b: [s2c(hellgate.CMD_RESULT, result_body)],
        hellgate.CMD_FINISH_WORLD_BOSS: lambda _b: [
            s2c(hellgate.CMD_GENERIC_RESULT, finish_body)
        ],
    })
    monkeypatch.setattr(hellgate, "open_raw_cdp_runtime", lambda _port: (
        None, None, object(), "raw_cdp"
    ))
    monkeypatch.setattr(hellgate, "close_raw_cdp_runtime", lambda *_args: None)
    monkeypatch.setattr(hellgate, "simulate_start_body", lambda *_args, **_kw: {
        "ok": True,
        "hp_num": "12",
        "last_hurt_num": "3456",
        "hurt_num": "9999",
        "frames": 10,
        "ms": 1.0,
    })
    try:
        report = hellgate.run_with_b(
            client, cdp_port=1, session_settle_sec=0, settlement_delay_sec=0
        )
        assert report.success is True
        assert report.info is not None
        assert report.info.times == 1
        assert report.after_info is not None
        assert report.after_info.times == 0
        assert report.after_info.my_hurt == "12"
        assert report.result is not None
        assert report.result.rewards == {9: 88}
        sent = [body for _sid, cmd, body in fake.framed_sent() if cmd == hellgate.CMD_RESULT]
        args = [codec.walk_dict(bytes(v)) for n, v in codec.walk(sent[0]) if n == 6]
        assert args == [{1: 1, 2: 12}, {1: 4, 2: 3456}]
        assert hellgate.CMD_FINISH_WORLD_BOSS in fake.sent_cmds()
    finally:
        client.close()


def test_failed_calculation_abandons_without_damage_fields(monkeypatch):
    info_body = codec.pb_uint(1, 1) + codec.pb_uint(10, 1)
    start_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(5, 42)
    )
    result_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(4, 1)
    )
    client, fake = _client({
        hellgate.CMD_WORLD_BOSS_INFO: lambda _b: [
            s2c(hellgate.CMD_WORLD_BOSS_INFO, info_body)
        ],
        hellgate.CMD_BATTLE_MORE_START: lambda _b: [
            s2c(hellgate.CMD_BATTLE_MORE_START, start_body)
        ],
        hellgate.CMD_RESULT: lambda _b: [s2c(hellgate.CMD_RESULT, result_body)],
    })
    monkeypatch.setattr(hellgate, "open_raw_cdp_runtime", lambda _port: (
        None, None, object(), "raw_cdp"
    ))
    monkeypatch.setattr(hellgate, "close_raw_cdp_runtime", lambda *_args: None)
    monkeypatch.setattr(hellgate, "simulate_start_body", lambda *_args, **_kw: {
        "ok": False,
        "complete": False,
        "err": "frame limit",
    })
    try:
        report = hellgate.run_with_b(
            client, cdp_port=1, session_settle_sec=0, settlement_delay_sec=0
        )
        assert report.success is False
        sent = [body for _sid, cmd, body in fake.framed_sent() if cmd == hellgate.CMD_RESULT]
        assert len(sent) == 1
        assert codec.walk_dict(sent[0]) == {1: 13, 2: 1, 3: 1, 4: 0}
        assert all(number not in (5, 6) for number, _raw in codec.walk(sent[0]))
    finally:
        client.close()


def test_result_ack_timeout_uses_after_info_confirmation(monkeypatch):
    info_body = codec.pb_uint(1, 1) + codec.pb_str(9, "10") + codec.pb_uint(10, 1)
    after_info_body = codec.pb_uint(1, 1) + codec.pb_str(9, "42") + codec.pb_uint(10, 0)
    start_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(5, 42)
    )
    info_replies = iter((info_body, after_info_body))
    client, fake = _client({
        hellgate.CMD_WORLD_BOSS_INFO: lambda _b: [
            s2c(hellgate.CMD_WORLD_BOSS_INFO, next(info_replies))
        ],
        hellgate.CMD_BATTLE_MORE_START: lambda _b: [
            s2c(hellgate.CMD_BATTLE_MORE_START, start_body)
        ],
        hellgate.CMD_FINISH_WORLD_BOSS: lambda _b: [
            s2c(
                hellgate.CMD_GENERIC_RESULT,
                codec.pb_uint(1, 0)
                + codec.pb_uint(2, 100)
                + codec.pb_uint(3, 13)
                + codec.pb_uint(4, 0),
            )
        ],
    })
    monkeypatch.setattr(hellgate, "open_raw_cdp_runtime", lambda _port: (
        None, None, object(), "raw_cdp"
    ))
    monkeypatch.setattr(hellgate, "close_raw_cdp_runtime", lambda *_args: None)
    monkeypatch.setattr(hellgate, "simulate_start_body", lambda *_args, **_kw: {
        "ok": True,
        "complete": True,
        "hp_num": "10",
        "last_hurt_num": "32",
    })
    monkeypatch.setattr(hellgate, "_RESULT_ACK_TIMEOUT_SEC", 0.1)
    try:
        report = hellgate.run_with_b(
            client,
            cdp_port=1,
            session_settle_sec=0,
            settlement_delay_sec=0,
            timeout=0.1,
        )
        assert report.success is True
        assert report.after_info is not None
        assert report.after_info.times == 0
        assert report.result is not None and report.result.success is True
        sent = [body for _sid, cmd, body in fake.framed_sent()
                if cmd == hellgate.CMD_RESULT]
        assert len(sent) == 1
    finally:
        client.close()


def test_result_error_cannot_be_promoted_by_stale_info_update(monkeypatch):
    info_body = codec.pb_uint(1, 1) + codec.pb_str(9, "10") + codec.pb_uint(10, 1)
    after_info_body = codec.pb_uint(1, 1) + codec.pb_str(9, "42") + codec.pb_uint(10, 0)
    start_body = (
        codec.pb_uint(1, 0)
        + codec.pb_uint(2, 13)
        + codec.pb_uint(3, 1)
        + codec.pb_uint(5, 42)
    )
    error_body = codec.pb_uint(1, 173)
    info_replies = iter((info_body, after_info_body))
    client, _fake = _client({
        hellgate.CMD_WORLD_BOSS_INFO: lambda _b: [
            s2c(hellgate.CMD_WORLD_BOSS_INFO, next(info_replies))
        ],
        hellgate.CMD_BATTLE_MORE_START: lambda _b: [
            s2c(hellgate.CMD_BATTLE_MORE_START, start_body)
        ],
        hellgate.CMD_RESULT: lambda _b: [s2c(hellgate.CMD_ERROR, error_body)],
    })
    monkeypatch.setattr(hellgate, "open_raw_cdp_runtime", lambda _port: (
        None, None, object(), "raw_cdp"
    ))
    monkeypatch.setattr(hellgate, "close_raw_cdp_runtime", lambda *_args: None)
    monkeypatch.setattr(hellgate, "simulate_start_body", lambda *_args, **_kw: {
        "ok": True,
        "complete": True,
        "hp_num": "10",
        "last_hurt_num": "32",
    })
    try:
        report = hellgate.run_with_b(
            client,
            cdp_port=1,
            session_settle_sec=0,
            settlement_delay_sec=0,
            timeout=0.1,
        )
        assert report.success is False
        assert report.result is not None
        assert report.result.error_code == 173
    finally:
        client.close()
