"""Read desktop-owned GSettings schemas without ever owning their settings.

Chronoa's own settings live in ``settings_file.py``; this module is only for
schemas that belong to the desktop (``org.gnome.*``). It is the one place that
looks a desktop schema up before asking ``Gio.Settings`` to read it, because
``Gio.Settings.new()`` on an uninstalled schema aborts the process rather than
returning a handle, and a non-GNOME desktop must get an honest answer instead.

Missing schema, missing typelib, unreadable key: every call returns its
documented "not available" value and never raises:

===================  ==========================
call                 value when not available
===================  ==========================
``available(id)``    ``False``
``get(id, key, d)``  ``d`` (default ``None``)
``get_bool(...)``    ``d`` (default ``False``)
``keys(id)``         ``()``
``describe(id)``     ``{}``
===================  ==========================

Lookup is cached per process. ``clear_cache()`` exists for tests that install
stand-in schema sources; production never needs it. No GTK is imported.
"""

from __future__ import annotations

#: The desktop schemas this tree actually reads. An allowlist, not a guess: a
#: schema outside this set is "not available" here even if some desktop happens
#: to install it. ``org.shani.chronoa`` is deliberately absent - that is
#: Chronoa's own setting, owned by ``settings_file.py``, not a desktop schema.
DESKTOP_SCHEMAS = (
    "org.gnome.desktop.a11y.applications",
    "org.gnome.desktop.a11y.interface",
    "org.gnome.desktop.a11y.keyboard",
    "org.gnome.desktop.background",
    "org.gnome.desktop.interface",
    "org.gnome.desktop.notifications",
    "org.gnome.desktop.screensaver",
    "org.gnome.desktop.session",
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.desktop.peripherals.touchpad",
    "org.gnome.settings-daemon.plugins.color",
    "org.gnome.settings-daemon.plugins.media-keys",
)

_CACHE: dict = {}


def clear_cache() -> None:
    """Forget cached schema lookups. For tests that swap the schema source."""
    _CACHE.clear()


def _schema_source():
    """The process-wide schema source, or None when Gio is unavailable."""
    try:
        from gi.repository import Gio
    except (ImportError, ValueError):
        return None
    return Gio.SettingsSchemaSource.get_default()


def _new_settings(schema_id: str):
    """A live settings object. Called only after the schema was found."""
    from gi.repository import Gio

    return Gio.Settings.new(schema_id)


def _schema(schema_id: str):
    """The cached Gio.SettingsSchema, or None. Never raises."""
    if schema_id not in DESKTOP_SCHEMAS:
        return None
    if schema_id in _CACHE:
        return _CACHE[schema_id]
    schema = None
    try:
        source = _schema_source()
        if source is not None:
            schema = source.lookup(schema_id, True)
    except Exception:  # noqa: BLE001 - a broken source is no source
        schema = None
    _CACHE[schema_id] = schema
    return schema


def available(schema_id: str) -> bool:
    """Whether this desktop actually installs the schema."""
    return _schema(schema_id) is not None


def keys(schema_id: str) -> tuple:
    """The schema's key names, or ``()`` when it is not available."""
    schema = _schema(schema_id)
    if schema is None:
        return ()
    try:
        return tuple(schema.list_keys())
    except Exception:  # noqa: BLE001
        return ()


def describe(schema_id: str) -> dict:
    """``{key name: the schema's own description}``, or ``{}`` when absent.

    Description first, summary second, empty string when the key declares
    neither - the same rule `list_shortcuts` used before this provider existed.
    A key with no words is still a key, so it stays in the dict.
    """
    schema = _schema(schema_id)
    if schema is None:
        return {}
    out: dict = {}
    for name in keys(schema_id):
        try:
            key = schema.get_key(name)
            out[name] = key.get_description() or key.get_summary() or ""
        except Exception:  # noqa: BLE001 - one odd key must not cost the rest
            out[name] = ""
    return out


def get(schema_id: str, key: str, default=None):
    """One key's value, or ``default`` when the schema/key cannot be read."""
    if not available(schema_id):
        return default
    try:
        settings = _new_settings(schema_id)
        value = settings.get_value(key)
        return value.unpack() if hasattr(value, "unpack") else value
    except Exception:  # noqa: BLE001
        return default


def get_bool(schema_id: str, key: str, default: bool = False) -> bool:
    """One boolean key, or ``default`` when the schema/key cannot be read."""
    if not available(schema_id):
        return bool(default)
    try:
        return bool(_new_settings(schema_id).get_boolean(key))
    except Exception:  # noqa: BLE001
        return bool(default)
