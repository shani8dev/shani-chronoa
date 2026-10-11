"""The OpenStreetMap cache, and the two properties that make it worth having.

`maps.py` hits Nominatim, FOSSGIS's OSRM and Overpass. All three ask for the
same thing in their usage policies - a valid User-Agent, one request per second,
**no heavy use** - and the first two were already handled (`netjson`'s named UA,
`_polite`'s cross-process lock). The third was missing: every identical "where
is Shaniwar Wada" was a fresh request, held a second by the lock, and the answer
was the same answer.

The design is open-intelligent-ui's Wikipedia proxy route, and the part worth
copying is one line of its comment: **"A missing article is a real answer
(cached); any other failure is retried next time."** `netjson.get_json` already
raises on a transport failure and returns a body otherwise, so this only has to
respect the distinction rather than invent it.

The other property is that a cache is not a memoised outage. A 500 from OSRM
must not sit on disk for a day.
"""

from __future__ import annotations

import json
import stat
import sys
import time

sys.path.insert(0, "usr/lib/shani-chronoa")

import pytest  # noqa: E402

from shani_chronoa.skills import maps  # noqa: E402


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """A cache in a temp state dir, so no test writes a real one."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setattr(maps, "_CACHE_MAX", 4)
    monkeypatch.setattr(maps, "_CACHE_TTL", 60.0)
    directory = maps._cache_dir()
    return directory


class _StubConfig:
    """The two methods `maps.py` asks of a config.

    **The same shape `tests/test_maps_skill.py` uses**, rather than a keyfile
    gsettings dance: the consent key is not what these tests are about, and a
    stub that answers it lets the network behaviour be the only variable.
    """

    def sense_allowed(self, sense, default=False):
        return True

    def sense_allowed_reason(self, sense):
        return ""

    def get_bool(self, key, default=False):
        return True

    def get_string(self, key, default=""):
        return ""


def _consent_granted(monkeypatch):
    monkeypatch.setattr(maps, "ChronoaConfig", _StubConfig, raising=True)


def _patch_get(monkeypatch, calls, body=None, raises=None):
    """`_get` replaced, counting calls. `raises` simulates a transport failure."""

    def _fake(url, params=None):
        calls.append((url, params))
        if raises is not None:
            raise raises
        return body

    monkeypatch.setattr(maps, "_get", _fake)


class _Response:
    """A canned `httpx.Response`, enough for `netjson.get_json`.

    Only the attributes that function touches: `status_code` for
    `raise_for_status`, `content` for the size limit, `json()` for the body.
    """

    def __init__(self, url, body=None):
        self.status_code = 200
        self.content = b"[]"
        self._body = [] if body is None else body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


class TestARepeatIsAnsweredFromDisk:
    def test_the_second_call_does_not_touch_the_network(self, cache_dir, monkeypatch):
        calls = []
        _patch_get(monkeypatch, calls, body=[{"lat": "18.5", "lon": "73.8",
                                              "display_name": "Shaniwar Wada"}])
        first = maps._cached_get("https://nominatim.test/search", {"q": "wada"})
        second = maps._cached_get("https://nominatim.test/search", {"q": "wada"})
        assert first == second
        assert len(calls) == 1, (
            f"the repeat went to the network again ({len(calls)} calls), so the "
            "cache saves nothing and the upstream's no-heavy-use policy is "
            "still being paid")

    def test_a_different_query_is_a_different_entry(self, cache_dir, monkeypatch):
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        maps._cached_get("https://nominatim.test/search", {"q": "b"})
        assert len(calls) == 2

    def test_param_order_does_not_change_the_key(self, cache_dir, monkeypatch):
        """`?q=x&format=y` and `?format=y&q=x` are one request, not two entries."""
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a", "format": "jsonv2"})
        maps._cached_get("https://nominatim.test/search", {"format": "jsonv2", "q": "a"})
        assert len(calls) == 1
        assert len(list(cache_dir.iterdir())) == 1

    def test_a_hit_leaves_no_egress_record(self, cache_dir, monkeypatch):
        """Nothing left the machine, so the panel must not say it did.

        **Patched at `httpx`, which is the only level where this is observable.**
        `egress.record` lives *inside* `netjson.get_json`, so patching
        `get_json` - or `_get`, one level further out - removes the record along
        with the network and the test cannot tell "not recorded because cached"
        from "not recorded because it never ran". A canned HTTP response leaves
        the real `get_json` in place: it parses, and it records.
        """
        from shani_chronoa import egress
        recorded = []
        monkeypatch.setattr(egress, "record", lambda *a, **k: recorded.append(a))
        monkeypatch.setattr("httpx.Client.get", lambda self, url, **k: _Response(url))
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        assert recorded, "the fetch itself was not recorded"
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        assert len(recorded) == 1, (
            "the cached hit was recorded as network activity; it was a local "
            "read of the user's own disk")


class TestAFailureIsNeverCached:
    def test_a_raising_fetch_is_not_memoised(self, cache_dir, monkeypatch):
        """A cache that stored a failure would answer the same outage for a day.

        The exception must propagate and the next call must retry.
        """
        calls = []
        _patch_get(monkeypatch, calls, raises=OSError("503 from OSRM"))
        with pytest.raises(OSError):
            maps._cached_get("https://router.test/route", {"coords": "0,0;1,1"})
        assert list(cache_dir.iterdir()) == [], (
            "a failed request left an entry behind, so the next identical call "
            "would be answered from the failure instead of retrying")
        with pytest.raises(OSError):
            maps._cached_get("https://router.test/route", {"coords": "0,0;1,1"})
        assert len(calls) == 2, "the retry did not happen"

    def test_an_entry_with_a_garbage_timestamp_is_a_miss(self, cache_dir, monkeypatch):
        """`at` is read off disk and used in arithmetic.

        A hand-edited or truncated entry could hold a string or a list there,
        and `time.time() - "yesterday"` raises `TypeError` - which would surface
        as the skill failing rather than as a cache miss.
        """
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        path = next(cache_dir.iterdir())
        entry = json.loads(path.read_text())
        entry["at"] = "yesterday"
        path.write_text(json.dumps(entry))
        _patch_get(monkeypatch, calls, body=[{"lat": "1"}])
        assert maps._cached_get("https://nominatim.test/search",
                                {"q": "a"}) == [{"lat": "1"}]
        assert len(calls) == 2

    def test_an_empty_body_is_cached(self, cache_dir, monkeypatch):
        """Nominatim's "does not know this place" is `[]` - a real answer.

        Asking about a typo twice should cost one request, and the reply
        ("OpenStreetMap does not know a place called ...") must be identical.
        """
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        assert maps._cached_get("https://nominatim.test/search", {"q": "zzz"}) == []
        assert maps._cached_get("https://nominatim.test/search", {"q": "zzz"}) == []
        assert len(calls) == 1

    def test_after_a_success_the_failure_path_is_still_live(self, cache_dir, monkeypatch):
        """A *new* request after a failure still retries, and a cached answer
        does not hide a later failure from a request it never answered.

        **The second half is not what the first version of this test assumed.**
        It expected the cached request to re-fetch when the transport started
        failing; it does not, and should not - a cache hit is a local read and
        the network is never consulted. What matters is that the cached entry
        expires on its TTL (asserted in `TestTheCacheIsSafeOnDisk`) so a day-old
        answer cannot outlive a day-old outage forever, and that a *failed*
        request writes nothing (asserted above).
        """
        calls = []
        _patch_get(monkeypatch, calls, raises=OSError("down"))
        with pytest.raises(OSError):
            maps._cached_get("https://router.test/route", {"coords": "0,0;1,1"})
        assert list(cache_dir.iterdir()) == []
        # A different request is still a live one, so it is attempted and fails.
        with pytest.raises(OSError):
            maps._cached_get("https://router.test/route", {"coords": "2,2;3,3"})
        assert len(calls) == 2, "the second, genuinely different request was not attempted"
        # And a cached answer is served from disk with no network at all.
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        _patch_get(monkeypatch, calls, raises=OSError("down again"))
        assert maps._cached_get("https://nominatim.test/search", {"q": "a"}) == []
        assert len(calls) == 3, "the cache hit went to the network"


class TestTheCacheIsBounded:
    def test_it_stops_growing(self, cache_dir, monkeypatch):
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        for i in range(12):
            maps._cached_get("https://nominatim.test/search", {"q": str(i)})
            time.sleep(0.01)          # mtime resolution: the oldest must be oldest
        assert len(list(cache_dir.iterdir())) <= maps._CACHE_MAX, (
            f"{len(list(cache_dir.iterdir()))} entries survived a cap of "
            f"{maps._CACHE_MAX}; an unbounded cache is a disk leak that nobody "
            "sees until it is large")

    def test_the_oldest_is_the_one_evicted(self, cache_dir, monkeypatch):
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        for i in range(5):
            maps._cached_get("https://nominatim.test/search", {"q": str(i)})
            time.sleep(0.01)
        # "0" is the oldest by mtime and should be gone; "4" should remain.
        maps._cached_get("https://nominatim.test/search", {"q": "4"})
        assert len(calls) == 5, (
            "evicting the wrong entry re-fetched a live one; the oldest is the "
            "one to drop")


class TestTheCacheIsSafeOnDisk:
    def test_the_directory_is_owner_only(self, cache_dir):
        mode = stat.S_IMODE(cache_dir.stat().st_mode)
        assert mode == 0o700, (
            f"the cache is {mode:o}; it holds the places somebody searched for, "
            "so another account must not be able to list them")

    def test_a_corrupt_entry_is_a_miss_not_a_crash(self, cache_dir, monkeypatch):
        """A truncated write must not make the skill fail on every later call."""
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        path = next(cache_dir.iterdir())
        path.write_text('{"v": 1, "at": ', encoding="utf-8")
        _patch_get(monkeypatch, calls, body=[{"lat": "1", "lon": "2"}])
        assert maps._cached_get("https://nominatim.test/search", {"q": "a"}) == [
            {"lat": "1", "lon": "2"}]
        assert len(calls) == 2

    def test_an_entry_from_another_version_is_ignored(self, cache_dir, monkeypatch):
        """The version field is what invalidates a bad format without deleting
        a directory a running process may be reading."""
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        path = next(cache_dir.iterdir())
        entry = json.loads(path.read_text())
        entry["v"] = -1
        path.write_text(json.dumps(entry))
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        assert len(calls) == 2

    def test_an_expired_entry_is_refetched(self, cache_dir, monkeypatch):
        calls = []
        _patch_get(monkeypatch, calls, body=[])
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        monkeypatch.setattr(maps, "_CACHE_TTL", -1.0)     # everything is stale
        maps._cached_get("https://nominatim.test/search", {"q": "a"})
        assert len(calls) == 2

    def test_a_cache_that_cannot_be_written_is_not_an_error(self, cache_dir, monkeypatch):
        """The answer still comes back. A cache is an optimisation, and one that
        turns a read-only disk into a failed search is worse than no cache.

        Patching `json.dumps`'s consumer would not do: the write is
        `path.write_text(json.dumps(...))`, so the failure has to come from the
        write itself.
        """
        calls = []
        _patch_get(monkeypatch, calls, body=[{"lat": "1", "lon": "2"}])
        import pathlib
        real_write = pathlib.Path.write_text
        monkeypatch.setattr(pathlib.Path, "write_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(
                                OSError("read-only file system")))
        try:
            assert maps._cached_get("https://nominatim.test/search", {"q": "a"}) == [
                {"lat": "1", "lon": "2"}]
        finally:
            monkeypatch.setattr(pathlib.Path, "write_text", real_write)

    def test_a_key_does_not_contain_the_query(self, cache_dir):
        """A filename made of somebody's search words would be both long and
        visible in a directory listing."""
        key = maps._cache_key("https://nominatim.test/search", {"q": "Shaniwar Wada Pune"})
        assert "Shaniwar" not in key and "Pune" not in key
        assert len(key) <= 64


class TestTheSkillStillAnswers:
    """The cache is invisible to the caller, which is the point of wrapping
    `_get` rather than changing any of the three call sites' logic.

    Every test here grants the web sense through `_StubConfig`, because that is
    `tests/test_maps_skill.py`'s own pattern and because the consent key is not
    what any of these assert.
    """

    def test_a_geocode_goes_through_the_cache(self, cache_dir, monkeypatch):
        _consent_granted(monkeypatch)
        calls = []
        monkeypatch.setattr(maps, "_get",
                            lambda url, params=None: (calls.append(url), [
                                {"lat": "18.5186", "lon": "73.8553",
                                 "display_name": "Shaniwar Wada, Pune"}])[1])
        monkeypatch.setattr(maps, "_polite", lambda host, gap=1.1: None)
        out = maps._run({"action": "find", "place": "Shaniwar Wada"})
        assert "Shaniwar Wada" in out
        assert len(calls) == 1

    def test_a_second_geocode_costs_no_request(self, cache_dir, monkeypatch):
        _consent_granted(monkeypatch)
        monkeypatch.setattr(maps, "_polite", lambda host, gap=1.1: None)
        calls = []
        monkeypatch.setattr(maps, "_get",
                            lambda url, params=None: (calls.append(url), [
                                {"lat": "18.5186", "lon": "73.8553",
                                 "display_name": "Shaniwar Wada, Pune"}])[1])
        maps._run({"action": "find", "place": "Shaniwar Wada"})
        maps._run({"action": "find", "place": "Shaniwar Wada"})
        assert len(calls) == 1, (
            f"the second identical lookup made {len(calls)} requests; the "
            "upstream policies ask for no heavy use and this is heavy use")

    def _route_answer(self, monkeypatch, calls):
        """Drive `directions` with the network and location stubbed out.

        The action is `directions` (not `route`) and the destination is
        `destination` or `place`. **Both were guessed wrong by the first
        version of these tests**, which came back "Where to? No destination was
        given." and read as a broken cache. Reading the schema's own enum
        settled it, which is why it is spelled out here.

        **The stub is two-faced, and that is the second thing the first version
        got wrong.** `_endpoints` geocodes the destination *before* routing, so
        one canned body answers a place lookup with a route and the reply is
        "OpenStreetMap does not know a place called ..." - which reads exactly
        like a broken cache. The host decides.
        """
        place = [{"lat": "18.5186", "lon": "73.8553",
                  "display_name": "Shaniwar Wada, Pune, India"}]
        route = {"code": "Ok",
                 "routes": [{"distance": 146805.5, "duration": 7019.2, "legs": []}]}

        def _fake(url, params=None):
            calls.append((url, params))
            return place if "nominatim" in url else route

        monkeypatch.setattr(maps, "_get", _fake, raising=True)
        monkeypatch.setattr(maps, "_polite", lambda host, gap=1.1: None, raising=True)
        monkeypatch.setattr(maps, "_here", lambda: (18.5, 73.8, "your location"),
                            raising=True)

    def test_a_route_goes_through_the_cache(self, cache_dir, monkeypatch):
        """Two requests, not one: `_endpoints` geocodes before it routes.

        **The first version of this asserted `len(calls) == 1` and was wrong**
        - "route to X" is a geocode plus a route, and the count is two. That is
        worth stating because the wrong number reads as a cache that fires twice.
        """
        _consent_granted(monkeypatch)
        calls = []
        self._route_answer(monkeypatch, calls)
        out = maps._run({"action": "directions", "destination": "Shaniwar Wada"})
        assert "Driving" in out, f"the route answer did not come back: {out!r}"
        hosts = {url.split("/")[2] for url, _p in calls}
        assert hosts == {"nominatim.openstreetmap.org",
                         "routing.openstreetmap.de"}, hosts
        assert len(calls) == 2, len(calls)

    def test_a_second_route_costs_no_request(self, cache_dir, monkeypatch):
        _consent_granted(monkeypatch)
        calls = []
        self._route_answer(monkeypatch, calls)
        maps._run({"action": "directions", "destination": "Shaniwar Wada"})
        first = len(calls)
        maps._run({"action": "directions", "destination": "Shaniwar Wada"})
        assert len(calls) == first, (
            f"the second identical route made {len(calls) - first} more requests "
            f"(first route: {first}); the upstream policies ask for no heavy use")

    def test_the_no_traffic_caveat_survives_the_cache(self, cache_dir, monkeypatch):
        """A cached route must still say it is not live traffic.

        The answer's honesty is not the cache's to alter, and the module's own
        docstring says this sentence is always there.
        """
        _consent_granted(monkeypatch)
        calls = []
        self._route_answer(monkeypatch, calls)
        out = maps._run({"action": "directions", "destination": "Shaniwar Wada"})
        assert maps.NO_TRAFFIC in out, (
            "a route answer no longer carries the no-live-traffic caveat")
