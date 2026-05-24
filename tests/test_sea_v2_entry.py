"""Entry-point dispatch for sea_v2.sea().

The live H5 path is verified live; here we only cover the backend guard and the
default-off feature flag (so the runtime is never silently switched).
"""
import sea_v2


class _AdbDevice:
    """A device wrapper with no Playwright page (adb backend)."""
    _page = None


class _NoPageAttr:
    pass


def test_sea_aborts_on_adb_backend_without_crashing():
    report = sea_v2.sea("adb-fc65396d", _AdbDevice())
    assert report.aborted_reason is not None
    assert "h5" in report.aborted_reason.lower() or "backend" in report.aborted_reason.lower()


def test_sea_aborts_when_device_has_no_page_attr():
    report = sea_v2.sea("adb-fc65396d", _NoPageAttr())
    assert report.aborted_reason is not None


def test_use_sea_v2_defaults_off_when_unconfigured():
    assert sea_v2.use_sea_v2("emulator-5554", config={}) is False


def test_use_sea_v2_reads_per_device_then_global_flag():
    cfg = {"global": {"sea_v2_enabled": True}}
    assert sea_v2.use_sea_v2("emulator-5554", config=cfg) is True
    cfg2 = {"devices": {"emulator-5554": {"sea_v2_enabled": False}}, "global": {"sea_v2_enabled": True}}
    # per-device override wins
    assert sea_v2.use_sea_v2("emulator-5554", config=cfg2) is False
