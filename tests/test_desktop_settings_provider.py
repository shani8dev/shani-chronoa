"""The desktop-schema provider: stand-ins, no real GSettings and no GTK.

Every stand-in has the shape the provider actually calls: a schema source with
``lookup(id, recursive)``, a schema with ``list_keys``/``get_key``, a key with
``get_description``/``get_summary``, and a settings object with ``get_boolean``
and ``get_value``. The provider's two seams (``_schema_source`` and
``_new_settings``) are patched, so nothing here reads dconf or compiles a
schema.
"""

import pytest

from shani_chronoa import desktop_settings as ds
from shani_chronoa import automation_overlay
from shani_chronoa.skills import list_shortcuts

SCHEMA = "org.gnome.desktop.interface"
KEY = "enable-animations"


@pytest.fixture(autouse=True)
def _clean_cache():
    ds.clear_cache()
    yield
    ds.clear_cache()


class FakeKey:
    def __init__(self, description="", summary=""):
        self.description, self.summary = description, summary

    def get_description(self):
        return self.description

    def get_summary(self):
        return self.summary


class FakeSchema:
    def __init__(self, key_words):
        self.key_words = key_words  # name -> (description, summary)

    def list_keys(self):
        return tuple(self.key_words)

    def get_key(self, name):
        description, summary = self.key_words[name]
        return FakeKey(description, summary)


class FakeSource:
    def __init__(self, schemas):
        self.schemas = schemas
        self.calls = []

    def lookup(self, schema_id, recursive):
        self.calls.append((schema_id, recursive))
        return self.schemas.get(schema_id)


class FakeVariant:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get_boolean(self, key):
        return bool(self.values[key])

    def get_value(self, key):
        return FakeVariant(self.values[key])


def _present(monkeypatch, source=None, values=None):
    source = (
        source
        if source is not None
        else FakeSource({SCHEMA: FakeSchema({KEY: ("Animate the interface", "")})})
    )
    monkeypatch.setattr(ds, "_schema_source", lambda: source)
    monkeypatch.setattr(
        ds,
        "_new_settings",
        lambda _id: FakeSettings(values if values is not None else {KEY: False}),
    )
    return source


def test_absent_schema_has_the_documented_not_available_values(monkeypatch):
    """Every call keeps its documented value; no Gio.Settings is constructed."""
    monkeypatch.setattr(ds, "_schema_source", lambda: FakeSource({}))

    def no_settings(_id):
        raise AssertionError("schema absent: Gio.Settings must not be built")

    monkeypatch.setattr(ds, "_new_settings", no_settings)
    assert ds.available(SCHEMA) is False
    assert ds.get(SCHEMA, KEY) is None
    assert ds.get(SCHEMA, KEY, "fallback") == "fallback"
    assert ds.get_bool(SCHEMA, KEY) is False
    assert ds.get_bool(SCHEMA, KEY, True) is True
    assert ds.keys(SCHEMA) == ()
    assert ds.describe(SCHEMA) == {}


def test_present_schema_reads_keys_values_and_descriptions(monkeypatch):
    _present(monkeypatch, values={KEY: False})
    assert ds.available(SCHEMA) is True
    assert ds.keys(SCHEMA) == (KEY,)
    assert ds.describe(SCHEMA) == {KEY: "Animate the interface"}
    assert ds.get(SCHEMA, KEY, True) is False
    assert ds.get_bool(SCHEMA, KEY, True) is False


def test_summary_is_used_when_the_description_is_empty(monkeypatch):
    _present(
        monkeypatch,
        source=FakeSource({SCHEMA: FakeSchema({KEY: ("", "Animation summary.")})}),
    )
    assert ds.describe(SCHEMA) == {KEY: "Animation summary."}


def test_lookup_is_cached_and_a_test_can_clear_it(monkeypatch):
    source = _present(
        monkeypatch, source=FakeSource({SCHEMA: FakeSchema({KEY: ("", "")})})
    )
    assert ds.available(SCHEMA) is True
    assert ds.available(SCHEMA) is True
    assert len(source.calls) == 1
    ds.clear_cache()
    assert ds.available(SCHEMA) is True
    assert len(source.calls) == 2


def test_chronoa_s_own_schema_is_never_claimed(monkeypatch):
    """Even a source that offers it must not make this provider own it."""
    monkeypatch.setattr(
        ds,
        "_schema_source",
        lambda: FakeSource({"org.shani.chronoa": FakeSchema({KEY: ("", "")})}),
    )
    monkeypatch.setattr(ds, "_new_settings", lambda _id: FakeSettings({KEY: True}))
    assert "org.shani.chronoa" not in ds.DESKTOP_SCHEMAS
    assert ds.available("org.shani.chronoa") is False
    assert ds.keys("org.shani.chronoa") == ()
    assert ds.describe("org.shani.chronoa") == {}
    assert ds.get("org.shani.chronoa", KEY, "own") == "own"
    assert ds.get_bool("org.shani.chronoa", KEY, True) is True


def test_missing_typelib_or_raising_source_still_answers(monkeypatch):
    monkeypatch.setattr(ds, "_schema_source", lambda: None)
    assert ds.available(SCHEMA) is False
    assert ds.keys(SCHEMA) == ()

    def boom():
        raise RuntimeError("no schema source")

    ds.clear_cache()
    monkeypatch.setattr(ds, "_schema_source", boom)
    assert ds.available(SCHEMA) is False
    assert ds.describe(SCHEMA) == {}
    assert ds.get(SCHEMA, KEY, "safe") == "safe"
    assert ds.get_bool(SCHEMA, KEY, True) is True


def test_a_settings_read_that_fails_returns_the_default(monkeypatch):
    _present(monkeypatch)

    def boom(_id):
        raise RuntimeError("backend refused")

    monkeypatch.setattr(ds, "_new_settings", boom)
    assert ds.get(SCHEMA, KEY, "safe") == "safe"
    assert ds.get_bool(SCHEMA, KEY, True) is True


def test_both_call_sites_reach_the_provider(monkeypatch):
    """The two repointed desktop reads use the provider, with their defaults."""
    monkeypatch.setattr(
        ds,
        "describe",
        lambda _id: {
            "switch-applications": "Switch between applications. Other words."
        },
    )
    list_shortcuts._SUMMARY_CACHE.clear()
    got = list_shortcuts._summary_for(
        {"schema": "org.gnome.desktop.wm.keybindings", "key": "switch-applications"}
    )
    assert got == "Switch between applications"

    calls = []

    def fake_get_bool(schema_id, key, default=False):
        calls.append((schema_id, key, default))
        return True  # animations on

    monkeypatch.setattr(ds, "get_bool", fake_get_bool)
    assert automation_overlay.reduced_motion() is False
    assert calls == [(SCHEMA, KEY, True)], calls  # absent schema stays "no cue"

    calls.clear()
    monkeypatch.setattr(ds, "get_bool", lambda *a, **k: False)
    assert automation_overlay.reduced_motion() is True
