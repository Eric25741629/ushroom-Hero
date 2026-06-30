"""TDD for miner.core.ws_inventory: read mining prop counts from WS 0x0401.

web_h5 mining was reading bomb count via screenshot OCR (check_boom_num), which
mis-reads 0 on the browser render -> bomb blacklisted -> planner never uses the
drill->bomb combo. The fix sources pickaxe/drill/bomb from the WS full-bag
snapshot (0x0401: item_id -> count; 4001/4002/4003).
"""
from ws_token import codec
from miner.core import ws_inventory as wsi


def _entry(item_id: int, count: int, uid: int = 0) -> bytes:
    sub = codec.pb_uint(1, item_id) + codec.pb_uint(2, uid) + codec.pb_uint(3, count)
    return codec.pb_msg(1, sub)


def _bag(*pairs) -> bytes:
    return b"".join(_entry(i, c) for i, c in pairs)


class _FakeApi:
    def __init__(self, page):
        self._page = page

    def call_raw(self, cmd_id, body, timeout_sec=5.0, net_wait_ms=5000):
        assert cmd_id == 0x0401
        assert body == b""
        return self._page.bag


class _Inner:
    def __init__(self, backend_kind, page):
        self.backend_kind = backend_kind
        self._page = page


class _Dev:
    def __init__(self, inner):
        self._d = inner


class _Page:
    def __init__(self, bag):
        self.bag = bag


# --- parse_inventory_bag (the real decode logic) ---------------------------

def test_parse_bag_extracts_three_props_and_ignores_noise():
    raw = _bag((4001, 22), (4002, 78), (4003, 930), (9999, 5))
    assert wsi.parse_inventory_bag(raw) == {"pickaxe": 22, "drill": 78, "bomb": 930}


def test_parse_bag_present_but_zero_is_kept():
    raw = _bag((4003, 0))
    assert wsi.parse_inventory_bag(raw) == {"bomb": 0}


def test_parse_empty_bag_is_empty_dict():
    assert wsi.parse_inventory_bag(b"") == {}


# --- read_ws_prop_counts gating + happy path -------------------------------

def test_adb_backend_returns_none_so_caller_falls_back_to_ocr():
    dev = _Dev(_Inner("adb", _Page(b"")))
    assert wsi.read_ws_prop_counts(dev, _api_factory=_FakeApi) is None


def test_web_h5_without_page_returns_none():
    dev = _Dev(_Inner("web_h5", None))
    assert wsi.read_ws_prop_counts(dev, _api_factory=_FakeApi) is None


def test_web_h5_reads_counts_via_ws_with_absent_defaulting_to_zero():
    dev = _Dev(_Inner("web_h5", _Page(_bag((4001, 22), (4003, 930)))))
    out = wsi.read_ws_prop_counts(dev, _api_factory=_FakeApi)
    assert out == {"pickaxe": 22, "drill": 0, "bomb": 930}


def test_web_h5_empty_bag_returns_none_to_force_ocr_fallback():
    dev = _Dev(_Inner("web_h5", _Page(b"")))
    assert wsi.read_ws_prop_counts(dev, _api_factory=_FakeApi) is None


def test_ws_error_returns_none():
    class _BoomApi:
        def __init__(self, page):
            pass

        def call_raw(self, *a, **k):
            raise RuntimeError("net down")

    dev = _Dev(_Inner("web_h5", _Page(b"x")))
    assert wsi.read_ws_prop_counts(dev, _api_factory=_BoomApi) is None
