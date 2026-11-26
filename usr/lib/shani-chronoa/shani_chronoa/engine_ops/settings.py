"""Settings as plain rows, for the shell panel, the TUI and the CLI.

Every row comes from the running schema (`ChronoaConfig._valid_keys`), so a
setting added to the gschema appears here without a second list to keep in step.
Secret values are never returned: a secret row carries ``value=""`` and
``secret=True``. Writes that turn a consent switch on, or write an API key,
carry ``confirm`` and are refused without it - the engine refuses too, not just
the client that asks.
"""

from __future__ import annotations

from shani_chronoa import capabilities, config as config_mod

_SECRET_KEYS = frozenset(config_mod.API_KEY_SETTINGS.values())
_CONSENT_KEYS = (
    frozenset(capabilities.GATED.values())
    | capabilities.DESTRUCTIVE_CONSENT_KEYS
    | capabilities.NEVER_DEFAULTED_ON
)
_DANGEROUS_ON = _CONSENT_KEYS | _SECRET_KEYS


def _config(config=None):
    return config if config is not None else config_mod.ChronoaConfig()


def _secret(key: str) -> bool:
    return key in _SECRET_KEYS or key.endswith("-api-key")


def _kind(config, key: str) -> str:
    from shani_chronoa import settings_table

    code = settings_table.kind(key)
    return {"b": "bool", "d": "number", "i": "number"}.get(code, code)


def _read(config, key: str, kind: str):
    if kind == "bool":
        return config.get_bool(key)
    if kind == "number":
        return config.get_double(key)
    return config.get(key)


def _label(key: str) -> str:
    return capabilities.GATE_NAMES.get(key) or key.replace("-", " ").capitalize()


def _row(config, key: str) -> dict:
    kind = _kind(config, key)
    secret = _secret(key)
    return {
        "name": key,
        "label": _label(key),
        "value": "" if secret else _read(config, key, kind),
        "kind": kind,
        "secret": secret,
        "consent_key": key if key in _CONSENT_KEYS else "",
    }


def rows(config=None) -> list[dict]:
    """One row per schema key: {name, label, value, kind, secret, consent_key}."""
    cfg = _config(config)
    return [_row(cfg, key) for key in sorted(getattr(cfg, "_valid_keys", ()))]


def get(name, config=None) -> dict:
    """One row, addressed by schema key or by the label a person reads."""
    wanted = str(name or "").strip().casefold()
    for row in rows(config):
        if wanted in (row["name"].casefold(), row["label"].casefold()):
            return row
    return {"error": "unknown setting: %s" % (name,)}


def set(name, value, *, confirm: bool = False, config=None) -> dict:
    """Write one setting; refuse a dangerous-ON or secret write without confirm."""
    found = get(name, config=config)
    if "error" in found:
        return found
    cfg = _config(config)
    key, kind = found["name"], found["kind"]

    if kind == "bool":
        if isinstance(value, bool):
            text = "true" if value else "false"
        else:
            text = str(value).strip().lower()
            if text in ("1", "yes", "on"):
                text = "true"
            elif text in ("0", "no", "off"):
                text = "false"
            if text not in ("true", "false"):
                return {"error": "value must be true or false"}
        on = text == "true"
    elif kind == "number":
        try:
            text = str(float(value))
        except (TypeError, ValueError):
            return {"error": "value must be a number"}
        on = False
    else:
        text = str(value)
        on = False

    if (found["secret"] or (key in _DANGEROUS_ON and on)) and not confirm:
        return {"error": "confirmation required: %s needs confirmation" % key}

    before = _read(cfg, key, kind)
    cfg.set(key, text)
    after = _read(cfg, key, kind)
    return {
        "name": key,
        "label": found["label"],
        "value": "" if found["secret"] else after,
        "changed": after != before,
    }


def preset(name, *, confirm: bool = False, config=None) -> dict:
    """Apply a named permission preset; every preset needs confirm."""
    wanted = str(name or "").strip().lower()
    if wanted not in capabilities.PRESETS:
        return {"error": "unknown preset: %s" % (name,)}
    if not confirm:
        return {"error": "confirmation required: a preset changes consent switches"}
    cfg = _config(config)
    capabilities.apply_preset(wanted, cfg)
    return {"preset": wanted, "current": capabilities.current_preset(cfg)}


def email(values, *, confirm: bool = False, config=None) -> dict:
    """Save the local email account through email_setup, never reimplementing it."""
    from shani_chronoa import email_setup

    if not isinstance(values, dict):
        return {"error": "email values must be a dictionary"}
    if any(k in values for k in ("password", "app_password", "token")):
        return {
            "error": "email secrets are not stored in the account file; "
            "use the session keyring as email:<user>"
        }
    unknown = sorted(str(k) for k in values if k not in email_setup.FIELDS)
    if unknown:
        return {"error": "unknown email field: %s" % ", ".join(unknown)}
    if not confirm:
        return {"error": "confirmation required: saving the email account"}
    try:
        path = email_setup.save(values)
    except ValueError as exc:
        return {"error": str(exc)}
    status, message = email_setup.status()
    return {"status": status, "message": message, "path": str(path)}
