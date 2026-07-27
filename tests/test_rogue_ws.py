# -*- coding: utf-8 -*-
"""萬神試煉 pure WS（Layer 2 AB separation）單元測試。

覆蓋 ws_token/rogue.py 的 body builder/parser 與
ws_token/rogue_fight.py 的 fight_once/run_rogue_run/run_with_b。
不連真實 WS/瀏覽器 — client 與 B page 皆用 mock。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from ws_token import codec
from ws_token import rogue as rogue_mod
from ws_token.rogue_fight import (
    RogueFightOutcome,
    RogueFightReport,
    fight_once,
    run_rogue_run,
)


# ─── body builders ───────────────────────────────────────────────────────────

def test_build_enter_c2s_return_type_1():
    body = rogue_mod.build_enter_c2s(1)
    assert codec.walk_dict(body) == {1: 1}


def test_build_over_c2s_return_type_0():
    body = rogue_mod.build_over_c2s(0)
    assert codec.walk_dict(body) == {1: 0}


def test_build_result_c2s_encodes_result_and_precent():
    body = rogue_mod.build_result_c2s(0, 77)
    assert codec.walk_dict(body) == {1: 0, 2: 77}


# ─── parsers ─────────────────────────────────────────────────────────────────

def test_parse_status_active_run():
    body = codec.pb_uint(1, 1)
    st = rogue_mod.parse_status(body)
    assert st.has_active_run is True
    assert st.raw_status == 1


def test_parse_status_no_active_run():
    body = codec.pb_uint(1, 0)
    st = rogue_mod.parse_status(body)
    assert st.has_active_run is False


def test_parse_enter_success():
    body = codec.pb_uint(1, 0)
    out = rogue_mod.parse_enter(rogue_mod.CMD_ENTER, body)
    assert out.success is True
    assert out.code == 0


def test_parse_enter_server_error():
    body = codec.pb_uint(1, 5)
    out = rogue_mod.parse_enter(rogue_mod.CMD_ERROR, body)
    assert out.success is False
    assert "server error" in out.error


def test_parse_combat_success_keeps_raw_body():
    raw = codec.pb_uint(1, 0) + codec.pb_uint(4, 12345)
    out = rogue_mod.parse_combat(rogue_mod.CMD_COMBAT, raw)
    assert out.success is True
    assert out.body == raw


def test_parse_result_ack_success():
    body = codec.pb_uint(1, 0)
    out = rogue_mod.parse_result_ack(rogue_mod.CMD_RESULT, body)
    assert out.success is True


def test_parse_over_success_ignores_body_fields():
    # rogue_main_over_s2c 依 schema 沒有 code 欄位（field1=rogue_report 訊息）；
    # 任何非 error 的 CMD_OVER 回覆都算成功，body 內容不影響結果。
    body = codec.pb_uint(1, 3)
    out = rogue_mod.parse_over(rogue_mod.CMD_OVER, body)
    assert out.success is True
    assert out.code == 0


def test_parse_start_reward_info_success():
    body = codec.pb_uint(3, 2)  # refresh_times#3
    out = rogue_mod.parse_start_reward(
        rogue_mod.CMD_START_REWARD_INFO, body,
        expect_cmd=rogue_mod.CMD_START_REWARD_INFO,
    )
    assert out.success is True
    assert out.fields.get(3) == 2


def test_parse_start_reward_confirm_success():
    body = b""  # 實測空 body 也算成功（無 code 欄位）
    out = rogue_mod.parse_start_reward(
        rogue_mod.CMD_START_REWARD_CONFIRM, body,
        expect_cmd=rogue_mod.CMD_START_REWARD_CONFIRM,
    )
    assert out.success is True


def test_parse_start_reward_server_error():
    body = codec.pb_uint(1, 2)
    out = rogue_mod.parse_start_reward(
        rogue_mod.CMD_ERROR, body,
        expect_cmd=rogue_mod.CMD_START_REWARD_CONFIRM,
    )
    assert out.success is False
    assert "server error" in out.error


def test_parse_start_reward_unexpected_cmd():
    out = rogue_mod.parse_start_reward(
        rogue_mod.CMD_ENTER, b"",
        expect_cmd=rogue_mod.CMD_START_REWARD_INFO,
    )
    assert out.success is False
    assert "unexpected cmd" in out.error


def test_fetch_start_reward_info_sends_correct_cmd():
    client = MagicMock()
    client.call_for.return_value = (rogue_mod.CMD_START_REWARD_INFO, b"")
    out = rogue_mod.fetch_start_reward_info(client)
    assert out.success is True
    client.call_for.assert_called_once_with(
        rogue_mod.CMD_START_REWARD_INFO, b"",
        expect_cmds=(rogue_mod.CMD_START_REWARD_INFO, rogue_mod.CMD_ERROR),
        timeout=None,
    )


def test_confirm_start_reward_sends_correct_cmd():
    client = MagicMock()
    client.call_for.return_value = (rogue_mod.CMD_START_REWARD_CONFIRM, b"")
    out = rogue_mod.confirm_start_reward(client)
    assert out.success is True
    client.call_for.assert_called_once_with(
        rogue_mod.CMD_START_REWARD_CONFIRM, b"",
        expect_cmds=(rogue_mod.CMD_START_REWARD_CONFIRM, rogue_mod.CMD_ERROR),
        timeout=None,
    )


# ─── fight_once ──────────────────────────────────────────────────────────────

def _mock_client(*, combat_ok=True, result_ok=True, combat_body=b"\x08\x00"):
    client = MagicMock()
    combat_reply = (
        (rogue_mod.CMD_COMBAT, combat_body)
        if combat_ok
        else (rogue_mod.CMD_ERROR, codec.pb_uint(1, 9))
    )
    result_reply = (
        (rogue_mod.CMD_RESULT, codec.pb_uint(1, 0))
        if result_ok
        else (rogue_mod.CMD_ERROR, codec.pb_uint(1, 9))
    )

    def call_for(cmd, body=b"", *, expect_cmds, timeout=None):
        if cmd == rogue_mod.CMD_COMBAT:
            return combat_reply
        if cmd == rogue_mod.CMD_RESULT:
            return result_reply
        raise AssertionError(f"unexpected cmd {cmd}")

    client.call_for.side_effect = call_for
    return client


def test_fight_once_win_calls_sim_and_reports_result():
    client = _mock_client()
    page = object()
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        return_value={"ok": True, "result": 0, "precent": 0, "ms": 12.3},
    ) as sim:
        out = fight_once(client, page, stage=1)
    assert out.ok is True
    assert out.result == 0
    assert out.sim_ms == 12.3
    sim.assert_called_once()
    # result_c2s 應帶 sim 算出的 result/precent
    client.call_for.assert_any_call(
        rogue_mod.CMD_RESULT,
        rogue_mod.build_result_c2s(0, 0),
        expect_cmds=(rogue_mod.CMD_RESULT, rogue_mod.CMD_ERROR),
        timeout=None,
    )


def test_fight_once_loss_still_reports_result():
    client = _mock_client()
    page = object()
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        return_value={"ok": True, "result": 1, "precent": 45, "ms": 8.0},
    ):
        out = fight_once(client, page, stage=6)
    assert out.ok is True
    assert out.result == 1
    assert out.precent == 45


def test_fight_once_combat_fail_returns_not_ok():
    client = _mock_client(combat_ok=False)
    page = object()
    out = fight_once(client, page, stage=1)
    assert out.ok is False
    # combat_ok=False → mock client 回 CMD_ERROR，parse_combat 產生 "server error N"
    assert "server error" in (out.error or "")


def test_fight_once_sim_fail_returns_not_ok():
    client = _mock_client()
    page = object()
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        return_value={"ok": False, "err": "no protoRoot"},
    ):
        out = fight_once(client, page, stage=1)
    assert out.ok is False
    assert "sim failed" in out.error


def test_fight_once_result_ack_fail_returns_not_ok():
    client = _mock_client(result_ok=False)
    page = object()
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        return_value={"ok": True, "result": 0, "precent": 0, "ms": 1.0},
    ):
        out = fight_once(client, page, stage=1)
    assert out.ok is False
    assert out.result == 0  # sim 結果仍保留供除錯


# ─── run_rogue_run ───────────────────────────────────────────────────────────

def _mock_run_client(*, has_active_run, sim_results):
    """sim_results: list of result ints; last stage stops the loop on loss."""
    client = MagicMock()
    status_body = codec.pb_uint(1, 1 if has_active_run else 0)
    enter_body = codec.pb_uint(1, 0)
    over_body = codec.pb_uint(1, 0)

    def call(cmd, body=b"", *, timeout=None):
        if cmd == rogue_mod.CMD_STATUS:
            return status_body
        raise AssertionError(f"unexpected call cmd {cmd}")

    def call_for(cmd, body=b"", *, expect_cmds, timeout=None):
        if cmd == rogue_mod.CMD_ENTER:
            return (rogue_mod.CMD_ENTER, enter_body)
        if cmd == rogue_mod.CMD_START_REWARD_INFO:
            return (rogue_mod.CMD_START_REWARD_INFO, b"")
        if cmd == rogue_mod.CMD_START_REWARD_CONFIRM:
            return (rogue_mod.CMD_START_REWARD_CONFIRM, b"")
        if cmd == rogue_mod.CMD_COMBAT:
            return (rogue_mod.CMD_COMBAT, b"\x08\x00")
        if cmd == rogue_mod.CMD_RESULT:
            return (rogue_mod.CMD_RESULT, codec.pb_uint(1, 0))
        if cmd == rogue_mod.CMD_OVER:
            return (rogue_mod.CMD_OVER, over_body)
        raise AssertionError(f"unexpected cmd {cmd}")

    client.call.side_effect = call
    client.call_for.side_effect = call_for
    return client


def test_run_rogue_run_always_enters_even_when_active_run():
    # live 測試證實 status.raw==1 時跳過 enter 會讓 combat 回 server error 2；
    # 故現在無論 status 為何，每局一律先呼叫 enter。
    client = _mock_run_client(has_active_run=True, sim_results=[0, 1])
    sims = iter([
        {"ok": True, "result": 0, "precent": 0, "ms": 1.0},
        {"ok": True, "result": 1, "precent": 50, "ms": 1.0},
    ])
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        side_effect=lambda page, mode, body: next(sims),
    ):
        report = run_rogue_run(client, object(), stages=80)
    assert report.success is True
    assert report.stages_fought == 2
    assert report.stages_won == 1
    enter_calls = [
        c for c in client.call_for.call_args_list if c.args[0] == rogue_mod.CMD_ENTER
    ]
    assert len(enter_calls) == 1


def test_run_rogue_run_enters_when_no_active_run():
    client = _mock_run_client(has_active_run=False, sim_results=[1])
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        return_value={"ok": True, "result": 1, "precent": 10, "ms": 1.0},
    ):
        report = run_rogue_run(client, object(), stages=80)
    assert report.success is True
    assert report.stages_fought == 1
    enter_calls = [
        c for c in client.call_for.call_args_list if c.args[0] == rogue_mod.CMD_ENTER
    ]
    assert len(enter_calls) == 1


def test_run_rogue_run_stops_on_loss():
    client = _mock_run_client(has_active_run=True, sim_results=[0, 0, 1])
    sims = iter([
        {"ok": True, "result": 0, "precent": 0, "ms": 1.0},
        {"ok": True, "result": 0, "precent": 0, "ms": 1.0},
        {"ok": True, "result": 1, "precent": 77, "ms": 1.0},
    ])
    with patch(
        "ws_token.rogue_fight.simulate_combat_body",
        side_effect=lambda page, mode, body: next(sims),
    ):
        report = run_rogue_run(client, object(), stages=80)
    assert report.stages_fought == 3
    assert report.stages_won == 2
    # over 應被呼叫一次收尾
    over_calls = [
        c for c in client.call_for.call_args_list if c.args[0] == rogue_mod.CMD_OVER
    ]
    assert len(over_calls) == 1


def test_run_rogue_run_calls_over_even_after_combat_failure():
    client = _mock_run_client(has_active_run=True, sim_results=[])
    client.call_for.side_effect = None

    def call_for(cmd, body=b"", *, expect_cmds, timeout=None):
        if cmd == rogue_mod.CMD_ENTER:
            return (rogue_mod.CMD_ENTER, codec.pb_uint(1, 0))
        if cmd == rogue_mod.CMD_START_REWARD_INFO:
            return (rogue_mod.CMD_START_REWARD_INFO, b"")
        if cmd == rogue_mod.CMD_START_REWARD_CONFIRM:
            return (rogue_mod.CMD_START_REWARD_CONFIRM, b"")
        if cmd == rogue_mod.CMD_COMBAT:
            return (rogue_mod.CMD_ERROR, codec.pb_uint(1, 9))
        if cmd == rogue_mod.CMD_OVER:
            return (rogue_mod.CMD_OVER, codec.pb_uint(1, 0))
        raise AssertionError(f"unexpected cmd {cmd}")

    client.call_for.side_effect = call_for
    report = run_rogue_run(client, object(), stages=80)
    assert report.success is False
    assert report.stages_fought == 0
    over_calls = [
        c for c in client.call_for.call_args_list if c.args[0] == rogue_mod.CMD_OVER
    ]
    assert len(over_calls) == 1


# ─── run_with_b ──────────────────────────────────────────────────────────────

def test_run_with_b_opens_and_closes_b_page():
    client = _mock_run_client(has_active_run=True, sim_results=[1])
    from ws_token import rogue_fight as rf

    fake_pw, fake_browser, fake_page = object(), object(), object()
    with patch.object(
        rf, "open_b_runtime",
        return_value=(fake_pw, fake_browser, fake_page, "ephemeral"),
    ) as open_mock, patch.object(rf, "close_b_runtime") as close_mock, patch(
        "ws_token.rogue_fight.simulate_combat_body",
        return_value={"ok": True, "result": 1, "precent": 0, "ms": 1.0},
    ):
        report = rf.run_with_b(client, stages=80)
    assert report.stages_fought == 1
    open_mock.assert_called_once()
    close_mock.assert_called_once_with(fake_pw, fake_browser, kind="ephemeral")


def test_run_with_b_reports_error_when_b_page_open_fails():
    client = _mock_run_client(has_active_run=True, sim_results=[])
    from ws_token import rogue_fight as rf

    with patch.object(rf, "open_b_runtime", side_effect=RuntimeError("no CDP")):
        report = rf.run_with_b(client, stages=80)
    assert report.success is False
    assert "B page" in report.error
