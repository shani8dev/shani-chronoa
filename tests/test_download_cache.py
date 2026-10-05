"""The download cache: never fetch twice, never trust a corrupt entry.

The cache exists because provisioning is per-user by design - a model that needs
root to install is a model nobody installs - which means two accounts on one
machine each pay for the same 639 MB, and a test run that seeds a cache still
paid again because nothing in the product looked there.

Two properties matter and both are checked here with a control that can fail:
a hit must be **verified** before use, and a miss must fall through to the
network rather than silently installing nothing.
"""
import os
import sys

sys.path.insert(0, "/home/shrinivaskumbhar/Documents/shani/shani-chronoa/usr/lib/shani-chronoa")

from shani_chronoa import stt_provision as sp  # noqa: E402


class _Consented:
    """Consent is checked *before* the cache, so these tests have to grant it."""

    def get_bool(self, key, default=False):
        return True


def _config():
    return _Consented()


def _spec(payload: bytes, name="fake.bin"):
    import hashlib
    return sp.ModelSpec(name, name, len(payload), hashlib.sha256(payload).hexdigest(), "")


def test_a_verified_cache_hit_skips_the_network(tmp_path, monkeypatch):
    payload = b"a model, more or less" * 100
    spec = _spec(payload)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / spec.filename).write_bytes(payload)
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(cache))

    def explode(*_a, **_k):
        raise AssertionError("the network was used even though the cache had it")

    monkeypatch.setattr(sp, "_stream", explode)
    user = tmp_path / "user"
    got = sp._install(spec, user_dir=user, system_dir=tmp_path / "system",
                      existing=None, config=_config())
    assert got == user / spec.filename
    assert got.read_bytes() == payload


def test_a_corrupt_cache_entry_is_refused_and_fetched_again(tmp_path, monkeypatch):
    """A cache entry that fails its digest is not a cache entry.

    Treated as a hit it would install a tampered model and report success - the
    exact thing the digest check exists to prevent, laundered through a
    convenience feature. So the assertion is on what actually lands: the real
    bytes, fetched over the transport, never the cache's.
    """
    import hashlib

    import httpx

    payload = b"the real model" * 50
    spec = sp.ModelSpec("fake", "fake.bin", len(payload),
                        hashlib.sha256(payload).hexdigest(), "",
                        "https://example.invalid/models")
    cache = tmp_path / "cache"
    cache.mkdir()
    # Right name, right length, wrong bytes.
    (cache / spec.filename).write_bytes(b"x" * len(payload))
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(cache))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(sp, "MODELS", {"fake": spec})

    def handler(request):
        return httpx.Response(200, headers={"content-length": str(len(payload))},
                              content=payload)

    written = sp.provision("fake", config=_Consented(),
                           transport=httpx.MockTransport(handler))
    assert written.read_bytes() == payload, "the corrupt cache entry was installed"
    assert b"x" * 64 not in written.read_bytes()


def test_a_wrong_length_cache_entry_is_a_miss_without_hashing_it(tmp_path, monkeypatch):
    payload = b"the real model" * 50
    spec = _spec(payload)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / spec.filename).write_bytes(b"short")
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(cache))
    assert sp._from_cache(spec, tmp_path / "dest") is None


def test_no_cache_directory_means_no_cache_not_an_error(tmp_path, monkeypatch):
    """`SHANI_DOWNLOAD_CACHE` pointing somewhere unusable must not break installs."""
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", "/proc/definitely/not/writable")
    assert sp.cache_dir() is None
    assert sp._from_cache(_spec(b"x" * 10), tmp_path / "dest") is None


def test_the_cache_directory_is_read_at_call_time(tmp_path, monkeypatch):
    """A value latched at import time would ignore both the harness's override
    and a test's tmpdir."""
    first, second = tmp_path / "one", tmp_path / "two"
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(first))
    assert sp.cache_dir() == first
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(second))
    assert sp.cache_dir() == second


def test_a_successful_download_leaves_a_copy_behind(tmp_path, monkeypatch):
    """Otherwise the cache only ever fills by accident, and the whole feature is
    a miss every time."""
    import hashlib

    import httpx

    payload = b"downloaded bytes" * 40
    spec = sp.ModelSpec("fake", "fake.bin", len(payload),
                        hashlib.sha256(payload).hexdigest(), "",
                        "https://example.invalid/models")
    cache = tmp_path / "cache"
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(cache))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    def handler(request):
        return httpx.Response(200, headers={"content-length": str(len(payload))},
                              content=payload)

    monkeypatch.setattr(sp, "MODELS", {"fake": spec})
    written = sp.provision("fake", config=_Consented(),
                           transport=httpx.MockTransport(handler))

    assert written.read_bytes() == payload
    cached = cache / spec.filename
    assert cached.is_file(), "the verified download was not cached"
    assert cached.read_bytes() == payload
    # Readable by the next account, or it is not a cache.
    assert os.access(cached, os.R_OK)


def test_the_second_install_is_served_from_the_cache(tmp_path, monkeypatch):
    """The point of the feature, end to end: fetch once, install twice."""
    import hashlib

    import httpx

    payload = b"only fetched once" * 40
    spec = sp.ModelSpec("fake", "fake.bin", len(payload),
                        hashlib.sha256(payload).hexdigest(), "",
                        "https://example.invalid/models")
    cache = tmp_path / "cache"
    monkeypatch.setenv("SHANI_DOWNLOAD_CACHE", str(cache))

    fetches = []

    def handler(request):
        fetches.append(str(request.url))
        return httpx.Response(200, headers={"content-length": str(len(payload))},
                              content=payload)

    monkeypatch.setattr(sp, "MODELS", {"fake": spec})
    transport = httpx.MockTransport(handler)

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share-1"))
    first = sp.provision("fake", config=_Consented(), transport=transport)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share-2"))
    second = sp.provision("fake", config=_Consented(),
                          transport=httpx.MockTransport(
                              lambda request: (_ for _ in ()).throw(
                                  AssertionError("fetched a second time"))))

    assert first.read_bytes() == second.read_bytes() == payload
    assert len(fetches) == 1, f"expected one fetch, made {len(fetches)}"
