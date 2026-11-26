"""Chronoa's settings store: one table, then three later-wins layers.

Built-in defaults come from the shipped table. A distribution's
``defaults.d`` overrides them, an admin's ``settings.d`` overrides that, and
the person's own ``settings.json`` wins over all three. Only this module
writes the person's file, atomically. A Gio.FileMonitor on its directory lets
a running engine and the GTK app see edits made elsewhere.

GSettings is read exactly once, during migration, when the person has no
settings file yet. After that this module never touches it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

MIGRATION_KEY = "_migrated"
_VENDOR = "/usr/share/shani-chronoa/defaults.d"
_ADMIN = "/etc/shani-chronoa/settings.d"


def _table():
    from shani_chronoa import settings_table

    return settings_table


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def user_path() -> Path:
    return _config_home() / "shani-chronoa" / "settings.json"


def vendor_dir() -> Path:
    return Path(os.environ.get("SHANI_CHRONOA_VENDOR_DEFAULTS", _VENDOR))


def admin_dir() -> Path:
    return Path(os.environ.get("SHANI_CHRONOA_ADMIN_DEFAULTS", _ADMIN))


def _read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.info("could not read settings layer %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _coerce(key: str, value, default):
    """Keep a layer's value in the type the table declares."""
    kind = str(_table().kind(key)).lower()
    if kind in ("b", "bool"):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes")
    if kind in ("d", "i", "number", "int"):
        try:
            return int(value) if kind == "i" else float(value)
        except (TypeError, ValueError):
            return default
    if kind == "string":
        return "" if value is None else str(value)
    return value


def _layer(directory: Path) -> dict:
    out: dict = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        data = _read_json(path)
        for key, value in data.items():
            if key == MIGRATION_KEY or key not in _table().KEYS:
                continue
            out[key] = _coerce(key, value, _table().default_for(key))
    return out


_cache: "dict[str, object]" = {}
_cache_lock = threading.Lock()
_monitor = None
_monitor_callbacks: "list[Callable[[], None]]" = []


def invalidate() -> None:
    """Forget the merged layers; the next read rebuilds them from disk."""
    with _cache_lock:
        _cache.clear()
    for callback in list(_monitor_callbacks):
        try:
            callback()
        except Exception:  # noqa: BLE001 - one listener cannot break a reload
            logger.debug("settings change listener failed", exc_info=True)


def values() -> dict:
    """The merged layers, rebuilt when a watched file changes."""
    with _cache_lock:
        if "values" in _cache:
            return dict(_cache["values"])
    table = _table()
    merged = dict(table.DEFAULTS)
    merged.update(_layer(vendor_dir()))
    merged.update(_layer(admin_dir()))
    user = _read_json(user_path())
    for key, value in user.items():
        if key == MIGRATION_KEY or key not in table.KEYS:
            continue
        merged[key] = _coerce(key, value, merged[key])
    with _cache_lock:
        _cache["values"] = merged
    return dict(merged)


def get(key: str, default=None):
    current = values()
    return current.get(key, default)


def set_value(key: str, value) -> None:
    """Write one key into the person's layer, keeping other keys intact."""
    table = _table()
    if key not in table.KEYS:
        raise KeyError(key)
    path = user_path()
    data = _read_json(path)
    data = {k: v for k, v in data.items() if k == MIGRATION_KEY or k in table.KEYS}
    coerced = _coerce(key, value, table.default_for(key))
    # Layers below the person's file may have moved; compare with all of them
    # so a value that matches the effective default is not stored needlessly.
    without_user = dict(table.DEFAULTS)
    without_user.update(_layer(vendor_dir()))
    without_user.update(_layer(admin_dir()))
    if without_user.get(key) == coerced:
        data.pop(key, None)
    else:
        data[key] = coerced
    write_user(data)


def write_user(data: dict) -> None:
    """Atomic write: temp file in the same directory, then rename."""
    path = user_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    invalidate()


# -- migration -------------------------------------------------------------


def _gsettings_values() -> "Optional[dict]":
    """The person's old GSettings values, or None when no schema exists.

    Guarded: a missing schema, a missing bus or a keyring that will not answer
    must never stop a start. This is the only GSettings read in the process.
    """
    try:
        from gi.repository import Gio
    except ImportError:
        return None
    schema_id = "org.shani.chronoa"
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(schema_id, True) if source is not None else None
    if schema is None:
        return None
    try:
        settings = Gio.Settings.new(schema_id)
    except Exception as exc:  # noqa: BLE001 - no schema means nothing to migrate
        logger.info("GSettings migration skipped: %s", exc)
        return None
    out = {}
    for key in _table().KEYS:
        # Retired keys still live in dconf and are migrated onto their
        # successors by the caller; do not read them into the new table.
        try:
            variant = settings.get_value(key)
        except Exception:  # noqa: BLE001 - a retired/unknown key is skipped
            continue
        code = variant.get_type_string()
        if code == "b":
            value = bool(variant.get_boolean())
        elif code == "d":
            value = float(variant.get_double())
        elif code == "i":
            value = int(variant.get_int32())
        else:
            value = str(variant.get_string())
        # Compare with the OLD schema default, not the new table's default:
        # a switch the person never touched must inherit a changed default,
        # not have the old off value frozen into their file.
        try:
            old_default = schema.get_key(key).get_default_value()
            if old_default.get_type_string() == code:
                if code == "b" and bool(old_default.get_boolean()) == value:
                    continue
                if code == "d" and float(old_default.get_double()) == value:
                    continue
                if code == "i" and int(old_default.get_int32()) == value:
                    continue
                if code == "s" and str(old_default.get_string()) == value:
                    continue
        except Exception:  # noqa: BLE001 - a read-only schema has no defaults
            pass
        out[key] = value
    return out


def _retired_map() -> dict:
    """Retired consent key -> the key of the sense that absorbed it.

    Derived from the aliases config.py already honours, so migration and
    runtime permission checks cannot disagree about which sense supersedes
    which old switch.
    """
    from shani_chronoa import config, settings_table

    supplied = getattr(settings_table, "RETIRED", {})
    if supplied:
        return supplied
    out = {}
    for successor, old_names in config._SENSE_CONSENT_ALIASES.items():
        target = successor + "-sense-enabled"
        if target not in settings_table.KEYS:
            continue
        for old in old_names:
            old_key = (
                old if str(old).endswith("-sense-enabled") else old + "-sense-enabled"
            )
            out[old_key] = (target,)
    return out


def retired_values() -> dict:
    """Old keys that no longer exist, for mapping onto their successors."""
    retired = _retired_map()
    if not retired:
        return {}
    raw = _gsettings_values()
    if raw is None:
        return {}
    # The schema may have been compiled without the retired keys; ask Gio for
    # them explicitly so their stored (non-default) values are not lost.
    try:
        from gi.repository import Gio

        settings = Gio.Settings.new("org.shani.chronoa")
        schema_keys = set(settings.props.settings_schema.list_keys())
        for old in retired:
            # Asking GSettings for a key the schema does not declare aborts
            # the whole process (GLib critical, not a Python exception).
            if old not in schema_keys or settings.get_user_value(old) is None:
                continue
            variant = settings.get_value(old)
            if variant.get_type_string() == "b":
                raw[old] = bool(variant.get_boolean())
    except Exception:  # noqa: BLE001 - a retired key nobody set needs no work
        pass
    return {k: v for k, v in raw.items() if k in retired}


def migrate_once() -> bool:
    """First start only: copy non-default GSettings values, then mark done."""
    path = user_path()
    data = _read_json(path)
    if data.get(MIGRATION_KEY):
        return False
    old = _gsettings_values()
    if old is None:
        logger.info("no GSettings schema; using the shipped settings table")
        data[MIGRATION_KEY] = True
        write_user(data)
        return False
    table = _table()
    changed = {}
    for key, value in old.items():
        if key in table.KEYS and value != table.default_for(key):
            changed[key] = value
    for old_key, value in retired_values().items():
        for successor in _retired_map()[old_key]:
            if successor in table.KEYS:
                changed[successor] = value
    data.update(changed)
    data[MIGRATION_KEY] = True
    write_user(data)
    logger.info("migrated %d non-default settings from GSettings", len(changed))
    return bool(changed)


# -- change notices --------------------------------------------------------


def watch(callback: Callable[[], None]) -> object:
    """Notice edits to the person's file (GLib only; no GTK)."""
    _monitor_callbacks.append(callback)
    _ensure_monitor()
    return _monitor


def _ensure_monitor() -> None:
    global _monitor
    if _monitor is not None:
        return
    try:
        from gi.repository import Gio

        directory = user_path().parent
        directory.mkdir(parents=True, exist_ok=True)
        monitor = Gio.File.new_for_path(str(directory)).monitor(
            Gio.FileMonitorFlags.NONE, None
        )
        monitor.connect("changed", lambda *_args: invalidate())
        _monitor = monitor
    except Exception as exc:  # noqa: BLE001 - no monitor means no live reload
        logger.debug("no settings file monitor: %s", exc)


def unwatch_all() -> None:
    global _monitor
    _monitor_callbacks.clear()
    _monitor = None
