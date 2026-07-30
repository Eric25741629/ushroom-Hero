"""Unit tests for ws_token.ladder_reward (no live client)."""
import datetime

import pytest

from ws_token import ladder_reward as lr


def _tuesday() -> datetime.date:
    base = datetime.date(2026, 6, 22)
    return base + datetime.timedelta(days=(lr.TUESDAY - base.isocalendar()[2]) % 7)


@pytest.fixture
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(lr, "_STORE", tmp_path / "ladder_reward.json")
    return tmp_path


def test_missing_device_uses_shared_template_on_tuesday(tmp_store):
    body = lr.encode_picks([(25, 1), (24, 2)])
    lr.save_store({
        lr.TEMPLATE_DEVICE: {
            "body_hex": body.hex(),
            "enabled": True,
        },
    })
    sent = []

    class Client:
        def call_for(self, cmd, payload, *, expect_cmds):
            sent.append((cmd, bytes(payload)))
            return lr.CMD_SELECT, bytes(payload) + b"\x10\x00"

    result = lr.apply_if_due(
        "emulator-5556",
        datetime.date(2026, 7, 28),
        client=Client(),
    )

    assert result["ok"] is True
    assert sent == [(lr.CMD_SELECT, body)]
    stored = lr.load_store()["emulator-5556"]
    assert stored["body_hex"] == body.hex()
    assert stored["last_applied_week"] == "2026-W31"

# 小寶 captured 2026-06-21: 25 picks, difficulties 16-25, echo ended 0x10 0x00.
XIAOBAO_HEX = (
    "0a04081910010a04081910020a04081910040a04081810010a04081810020a040817"
    "10010a04081710020a04081710030a04081610010a04081610020a04081610030a"
    "04081610040a04081510010a04081510030a04081410010a04081410020a040814"
    "10030a04081310030a04081210010a04081110010a04081110030a04081010010a"
    "04081010020a04081110020a0408131001"
)


def test_decode_real_capture_has_25_picks():
    picks = lr.decode_picks(bytes.fromhex(XIAOBAO_HEX))
    assert len(picks) == 25
    # difficulties span 16..25
    assert {d for d, _ in picks} == set(range(16, 26))
    # difficulty 25 picked indices 1,2,4
    assert sorted(i for d, i in picks if d == 25) == [1, 2, 4]


def test_encode_decode_roundtrip():
    picks = [(25, 1), (25, 2), (24, 1), (16, 3)]
    assert lr.decode_picks(lr.encode_picks(picks)) == picks


def test_decode_stops_at_trailing_result():
    # body = one pick {25,1} then a top-level result field 2 = 0 (0x10 0x00)
    body = lr.encode_picks([(25, 1)]) + b"\x10\x00"
    assert lr.decode_picks(body) == [(25, 1)]


def test_merge_fills_missing_from_template():
    base = [(25, 1), (24, 1)]            # device only picked 2
    fill = [(25, 1), (25, 2), (24, 1), (23, 1)]  # 小寶 template
    merged = lr.merge_picks(base, fill)
    assert set(merged) == {(25, 1), (25, 2), (24, 1), (23, 1)}
    # sorted by difficulty desc then index
    assert merged == [(25, 1), (25, 2), (24, 1), (23, 1)]


def test_varint_multibyte():
    # difficulty 200 needs a 2-byte varint; roundtrip must survive
    assert lr.decode_picks(lr.encode_picks([(200, 5)])) == [(200, 5)]


def test_tuesday_helper_is_tuesday():
    assert _tuesday().isocalendar()[2] == lr.TUESDAY


def test_is_due_only_on_tuesday(tmp_store):
    lr.record_device("dev", XIAOBAO_HEX, captured="2026-06-21")
    tue = _tuesday()
    assert lr.is_due("dev", tue)[0] is True
    assert lr.is_due("dev", tue + datetime.timedelta(days=1))[0] is False  # Wed


def test_is_due_skips_unknown_or_disabled(tmp_store):
    tue = _tuesday()
    assert lr.is_due("missing", tue) == (False, "no_record")
    lr.record_device("dev", XIAOBAO_HEX, captured="2026-06-21")
    store = lr.load_store(); store["dev"]["enabled"] = False; lr.save_store(store)
    assert lr.is_due("dev", tue) == (False, "disabled")


def test_apply_marks_week_and_dedupes(tmp_store):
    lr.record_device("dev", XIAOBAO_HEX, captured="2026-06-21")
    tue = _tuesday()
    sent = []

    class FakeClient:
        def call_for(self, cmd, body, *, expect_cmds):
            sent.append((cmd, body))
            return lr.CMD_SELECT, body  # echo back = success

    r1 = lr.apply_if_due("dev", tue, client=FakeClient())
    assert r1["ok"] is True and r1["picks"] == 25
    assert len(sent) == 1
    # second call same week -> skipped, no send
    r2 = lr.apply_if_due("dev", tue, client=FakeClient())
    assert r2 == {"skipped": "already_this_week"}
    assert len(sent) == 1


def test_apply_no_transport(tmp_store):
    lr.record_device("dev", XIAOBAO_HEX, captured="2026-06-21")
    assert lr.apply_if_due("dev", _tuesday()) == {"skipped": "no_transport"}
