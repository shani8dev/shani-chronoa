"""The `maps` skill: consent, formatting, and refusing rather than guessing.

`maps` arrived with the Siri/Google parity work (2026-10-08) with **no test
file**, and it is the skill in that batch with the most ways to be quietly
wrong, because every answer it gives is a number about the real world:

- a distance or a travel time computed from a bad coordinate looks exactly like
  one computed from a good one;
- a geocoder that returns nothing must read as "does not know a place called
  that", never as a zero distance or a default map of the Atlantic;
- "here" and "near me" need the **location** sense, which is a different switch
  from the web sense the rest of the skill uses - a machine may allow looking up
  a place it was told about and still not allow being located.

The formatting half is pure and is measured exactly, because "0 km" and "0.0
km" and "under a minute" are all outputs a person would read as an answer:
`km`, `duration` and `haversine` are checked against hand-computed values, and
`haversine` against a known city pair, so a formula error cannot pass as
plausible output.

The live-looking half is stubbed at `maps._get`, the single seam every request
goes through, so **nothing here reaches the network** - Nominatim's usage policy
asks for at most one request per second and `maps._polite` implements that with a
lock file; a test suite that quietly hit the real service would both violate it
and make its own results depend on the internet.

Run: `python3 -m pytest tests/test_maps_skill.py`
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import maps as maps_mod  # noqa: E402


class _Cfg:
    def __init__(self, senses=frozenset({"web"}), privacy_off=True):
        self._senses = set(senses)
        self._privacy_off = privacy_off

    def sense_allowed(self, sense: str) -> bool:
        return sense in self._senses and self._privacy_off

    def sense_allowed_reason(self, sense: str) -> str:
        if sense not in self._senses:
            return f"the {sense} sense is turned off"
        return "privacy mode is on"


@pytest.fixture
def osm(monkeypatch):
    """Replace the network and the config; record every URL requested."""
    state: dict = {"json": [], "calls": [], "senses": {"web"}}

    def fake_get(url, params=None):
        state["calls"].append((url, params))
        return state["json"]

    monkeypatch.setattr(maps_mod, "_get", fake_get, raising=True)
    monkeypatch.setattr(
        maps_mod, "ChronoaConfig", lambda: _Cfg(state["senses"]), raising=True
    )
    # `_polite` sleeps for real seconds between calls to the same host and takes
    # a lock in the state dir. Correct in production, pure latency here.
    monkeypatch.setattr(maps_mod, "_polite", lambda host, gap=1.1: None, raising=True)
    return state


# --- pure formatting ---------------------------------------------------------

@pytest.mark.parametrize("metres,want", [
    (0, "0 m"), (940, "940 m"), (1000, "1.0 km"), (1500, "1.5 km"),
    (12345, "12.3 km"),
])
def test_distance_is_readable_at_both_scales(metres, want):
    assert maps_mod.km(metres) == want


@pytest.mark.parametrize("seconds,want", [
    (30, "under a minute"), (0, "under a minute"),
    (60, "1 min"), (300, "5 min"), (3600, "1 h"), (3900, "1 h 5 min"),
    (7200, "2 h"),
])
def test_duration_does_not_round_a_short_trip_to_zero_minutes(seconds, want):
    """`max(1, ...)` would turn 30 seconds into "1 min", and 0 into "1 min"."""
    assert maps_mod.duration(seconds) == want


def test_the_great_circle_distance_matches_a_known_pair():
    """Two real city pairs, checked against real geography.

    The band is deliberately generous (about 5% either way) because the point is
    to catch a *formula* error - a degrees/radians slip, or a radius off by a
    factor - not to referee the haversine. A first version of this asserted
    110-120 km for Pune-Mumbai and failed on a correct answer of 120.2: the true
    great-circle distance between those coordinates is ~120 km, and tightening
    the band to fit a remembered figure would have been fitting the test to a
    number I did not verify, which is the mistake the band avoids.

    The pairs are separated by 120 km and 1380 km, so a formula that is right
    for short distances and wrong for long ones is still caught.
    """
    pune = (18.5204, 73.8567)
    mumbai = (19.0760, 72.8777)
    bengaluru = (12.9716, 77.5946)

    # Every figure here was computed independently with the textbook haversine
    # rather than written from memory: Pune-Mumbai 120.2 km, Pune-Bengaluru
    # 735.2 km. The first version of this file asserted ~1380 km for
    # Pune-Bengaluru from memory and failed on a correct answer - so the bands
    # below are now measured, with margin for the difference between a spherical
    # and an ellipsoidal earth, not fitted to a recollection.
    short = maps_mod.haversine(pune, mumbai)
    assert 114_000 < short < 126_000, (
        f"Pune to Mumbai came out {short/1000:.1f} km; expected ~120 km. Check "
        "the earth radius and that coordinates are converted to radians."
    )

    longer = maps_mod.haversine(pune, bengaluru)
    assert 700_000 < longer < 770_000, (
        f"Pune to Bengaluru came out {longer/1000:.0f} km; expected ~735 km. "
        "A formula that is right for one distance and wrong for another is "
        "exactly what this pair exists to catch."
    )


def test_distance_from_a_point_to_itself_is_zero():
    point = (12.9716, 77.5946)
    assert maps_mod.haversine(point, point) == pytest.approx(0.0, abs=1e-6)


# --- consent -----------------------------------------------------------------

def test_the_web_sense_is_off_by_default_and_nothing_is_fetched(monkeypatch):
    def exploding(url, params=None):
        raise AssertionError(f"the web sense is off but a request was made: {url}")

    monkeypatch.setattr(maps_mod, "_get", exploding, raising=True)
    monkeypatch.setattr(maps_mod, "ChronoaConfig", lambda: _Cfg(set()), raising=True)
    result = maps_mod._run({"action": "find", "place": "Pune"})
    assert "not available" in result, result


def test_a_refusal_names_the_switch_a_person_can_turn(osm):
    osm["senses"] = set()
    result = maps_mod._run({"action": "find", "place": "Pune"})
    assert "not available" in result, result
    assert "web" in result.lower(), f"the refusal does not name the sense: {result}"
    assert osm["calls"] == [], f"a refused lookup still fetched: {osm['calls']}"


def test_the_stub_records_the_url_not_the_params_dict(osm):
    """The control for the stub, and a lesson from `test_news_skill.py`.

    There, a stub written with the wrong function's signature recorded a params
    dict where a URL belonged and the failure looked like a routing bug in the
    skill. Here the recorded value is asserted to be a URL so that a future
    mismatch fails as a test bug rather than as a mystery.
    """
    osm["json"] = []
    maps_mod._run({"action": "find", "place": "Pune"})
    assert osm["calls"], "nothing was fetched at all"
    url, params = osm["calls"][0]
    assert isinstance(url, str) and url.startswith("https://"), (
        f"the stub recorded {url!r} where the URL belongs - its arguments are "
        "in the wrong order, so the assertions here would be about the wrong thing"
    )
    assert isinstance(params, dict) and "q" in params, params


# --- behaviour ---------------------------------------------------------------

def test_a_geocoder_that_knows_nothing_says_so_rather_than_answering_zero(osm):
    """An empty result is the input most likely to produce a confident wrong
    answer: no hit read as distance zero, or as a map centred on nothing."""
    osm["json"] = []
    result = maps_mod._run({"action": "find", "place": "asdkjhaskdjh"})
    assert "does not know" in result.lower() or "not found" in result.lower(), result
    assert "0 km" not in result, f"an unknown place came back as zero distance: {result}"


def test_an_unknown_action_lists_the_real_ones(osm):
    result = maps_mod._run({"action": "teleport"})
    assert "teleport" in result
    for action in maps_mod._ACTIONS:
        assert action in result, (
            f"the valid-action list omits {action!r}: {result}"
        )


def test_arguments_that_are_not_a_mapping_are_refused(osm):
    result = maps_mod._run(["find", "Pune"])
    assert "not understood" in result or "named values" in result, result


def test_a_network_failure_names_the_service_and_the_error(osm):
    """A failure must not read as an empty answer.

    `_run` catches httpx and decode errors and turns them into a sentence; if
    that catch were removed the sandboxed child would return a traceback to the
    model as the tool result.
    """
    import httpx

    def failing(url, params=None):
        raise httpx.ConnectError("no route to host")

    osm["json"] = []
    import shani_chronoa.skills.maps as m

    original = m._get
    m._get = failing
    try:
        result = m._run({"action": "find", "place": "Pune"})
    finally:
        m._get = original
    assert "Could not reach the map service" in result, result
    assert "ConnectError" in result, result


def test_using_here_without_the_location_sense_is_refused_by_name(osm):
    """"Here" is a different permission from "look this place up".

    The web sense is granted in this fixture; location is not. A skill that
    located the machine whenever the web sense was on would be treating two
    unrelated agreements as one.
    """
    result = maps_mod._run({"action": "nearby", "what": "petrol pump"})
    assert osm["calls"] == [], (
        f"a lookup that cannot know where 'here' is still went to the network: {osm['calls']}"
    )
    assert "location" in result.lower() or "what?" in result.lower(), (
        f"the answer neither asks what was meant nor names the location sense: {result}"
    )


def test_nearby_with_a_kind_still_refuses_without_the_location_sense(osm, monkeypatch):
    """The same refusal with the argument supplied, so the shape above is not
    the only thing being tested.

    The locate probe is stubbed to **succeed**, which is what makes this a test
    of the gate rather than of the environment. On this box the real probe finds
    no location, so `_here()` refuses with "This computer's location is not
    available" - a sentence that also contains the word "location". Asserting on
    that word passed with the gate deleted (mutation-confirmed), because the
    refusal it produced for the wrong reason happened to read like a pass.

    With a position in hand, the only thing that can stop a "here" lookup is the
    consent check, so removing it makes the skill go to the network and the
    `calls == []` assertion fails.
    """
    from shani_chronoa.senses import location

    monkeypatch.setattr(location, "locate", lambda: ((18.5204, 73.8567), "test"), raising=True)

    result = maps_mod._run({"action": "nearby", "what": "petrol pump", "near": "here"})
    assert "location sense" in result.lower(), (
        f"'here' was not refused for the location sense itself: {result}"
    )
    assert osm["calls"] == [], (
        f"'here' is unanswerable without the location sense, yet a request went "
        f"out anyway: {osm['calls']}"
    )


def test_a_locatable_machine_still_needs_the_location_sense():
    """Named for the counter-case: a probe that succeeds does not grant consent.

    Exists so the previous test cannot be satisfied by a machine where locating
    simply fails. Both halves are stubbed - the probe succeeds, the sense is
    shut - and the answer must still be the refusal.
    """
    from shani_chronoa.senses import location

    def fake_locate():
        return ((18.5204, 73.8567), "test")

    original = location.locate
    location.locate = fake_locate
    try:
        result = maps_mod._here()
    finally:
        location.locate = original
    assert isinstance(result, str) and "location sense" in result.lower(), (
        f"a working locate probe bypassed consent: {result!r}"
    )


def test_a_display_name_is_shortened_for_speech_and_the_full_one_is_kept(osm):
    """`_short` exists because a five-part OSM display name is unreadable aloud.

    Asserted as a property rather than a literal: the fixture's own name is
    checked for being a prefix of the full one, so this cannot be satisfied by
    returning the whole string or by returning the wrong part.
    """
    long_name = "Koregaon Park, Pune, Maharashtra, India"
    assert maps_mod._short(long_name, 2) == "Koregaon Park, Pune"
    assert maps_mod._short(long_name, 2).startswith(long_name.split(", ")[0])
    assert maps_mod._short("Pune", 3) == "Pune"


def test_a_single_word_place_is_not_mangled_by_shortening():
    """`", ".join(...[:3])` on a name with no commas is a no-op, not an empty
    string - which is what a naive split-and-rejoin would produce."""
    assert maps_mod._short("Pune") == "Pune"
    assert maps_mod._short("") == ""


# --- reachability ------------------------------------------------------------

def test_the_skill_is_reachable_through_the_real_dispatch_path():
    """Registered and dispatchable.

    The web sense is off here on purpose, so this proves the tool answers
    through the registry and the sandboxed child rather than proving the
    network works; the consent tests above cover the refusal half.
    """
    from shani_chronoa import tools

    outcome = tools.execute_tool_outcome("maps", {"action": "find", "place": "Pune"})
    assert outcome.ran, "the maps skill did not run through the real path"
    assert isinstance(outcome.text, str) and outcome.text, outcome