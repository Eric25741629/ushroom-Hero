"""管家續費 (buy_service 18693) 回應不是 18693 echo — 不可掛死整個 steward.

Live 2026-07-12 (手機 fc65396d): 服務到期後 ensure_active 走 renew，
client.call 只等 cmd=18693 → WSTimeoutError → 購物+副本整包沒跑。
實際回應通道：成功回 info(18692)、被拒走 0x0201、休眠可能完全無回應。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.steward import (  # noqa: E402
    CMD_BUY_SERVICE,
    CMD_INFO,
    SERVICE_SHOPPING,
    ensure_active,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)

CMD_ERR = 0x0201
SERV_TIME = 1000


def _kv_at(fid, k, v):
    return codec.pb_msg(fid, codec.pb_uint(1, k) + codec.pb_uint(2, v))


def _info_body(pairs):
    return b"".join(_kv_at(1, k, v) for k, v in pairs)


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


EXPIRED = _info_body([(SERVICE_SHOPPING, SERV_TIME - 1)])
ACTIVE = _info_body([(SERVICE_SHOPPING, SERV_TIME + 99999)])


def test_renew_rejected_via_error_channel_returns_false():
    """18693 被拒走 0x0201：不炸、回 False。"""
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, EXPIRED)],
        CMD_BUY_SERVICE: lambda _b: [s2c(CMD_ERR, codec.pb_uint(1, 173))],
    })
    try:
        assert ensure_active(c, SERVICE_SHOPPING, serv_time=SERV_TIME,
                             renew=True, timeout=0.5) is False
        assert CMD_BUY_SERVICE in fake.sent_cmds()
    finally:
        c.close()


def test_renew_success_replies_info_push():
    """18693 成功時回 info(18692)：視為成功、重讀 info 確認 active。"""
    state = {"bought": False}

    def info_resp(_b):
        body = ACTIVE if state["bought"] else EXPIRED
        return [s2c(CMD_INFO, body)]

    def buy_resp(_b):
        state["bought"] = True
        return [s2c(CMD_INFO, ACTIVE)]

    c, fake = _client({CMD_INFO: info_resp, CMD_BUY_SERVICE: buy_resp})
    try:
        assert ensure_active(c, SERVICE_SHOPPING, serv_time=SERV_TIME,
                             renew=True, timeout=0.5) is True
    finally:
        c.close()


def test_renew_no_response_returns_false_without_raise():
    """18693 完全無回應（休眠事件模式）：不 raise、回 False。"""
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, EXPIRED)],
        CMD_BUY_SERVICE: lambda _b: [],
    })
    try:
        assert ensure_active(c, SERVICE_SHOPPING, serv_time=SERV_TIME,
                             renew=True, timeout=0.4) is False
    finally:
        c.close()
