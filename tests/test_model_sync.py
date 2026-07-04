"""ensure_local_model(): never promote a truncated NAS read to the cache.

Regression guard: the chunked copy loop stops on the first empty read. If a NAS
read under-delivers (fewer bytes than the reported size), the old code renamed
the truncated temp file over the cache -> a corrupt model. The fix verifies
`copied == remote_size` before the atomic rename and otherwise falls back to the
remote path (leaving the cache untouched).
"""
from utils import model_sync


def _make_remote(tmp_path, data: bytes = b"WEIGHTS" * 4096):
    remote_dir = tmp_path / "nas"
    remote_dir.mkdir()
    p = remote_dir / "model.pt"
    p.write_bytes(data)
    return p


def _point_home_at(monkeypatch, home_dir):
    home_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(model_sync.Path, "home", staticmethod(lambda: home_dir))


def test_truncated_read_is_not_promoted(tmp_path, monkeypatch):
    remote = _make_remote(tmp_path)
    home_dir = tmp_path / "home"
    _point_home_at(monkeypatch, home_dir)

    # Simulate the NAS reporting more bytes than the copy actually delivers.
    real_size = remote.stat().st_size
    monkeypatch.setattr(model_sync.os.path, "getsize", lambda _p: real_size + 8192)

    result = model_sync.ensure_local_model(str(remote), cache_name=".cache")

    # Falls back to the remote path; the corrupt temp/cache must not exist.
    assert result == str(remote)
    cache = home_dir / ".cache"
    assert not (cache / "model.pt").exists()
    assert not (cache / "model.pt.tmp").exists()


def test_complete_copy_is_promoted(tmp_path, monkeypatch):
    payload = b"WEIGHTS" * 4096
    remote = _make_remote(tmp_path, payload)
    home_dir = tmp_path / "home"
    _point_home_at(monkeypatch, home_dir)

    result = model_sync.ensure_local_model(str(remote), cache_name=".cache")

    local = home_dir / ".cache" / "model.pt"
    assert result == str(local)
    assert local.exists()
    assert local.read_bytes() == payload
