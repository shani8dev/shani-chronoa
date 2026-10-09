"""API keys in the desktop keyring (Secret Service), not in plain-text dconf.

GSettings keeps its values in ~/.config/dconf/user, unencrypted and inside
every home-directory backup. The Secret Service - gnome-keyring on GNOME,
KWallet's compatibility service on Plasma, both on the images - keeps them
encrypted at rest under the login password. Same-user processes can still
read an unlocked keyring, so this is at-rest protection, not a vault against
the user's own programs; config.cloud_llm_api_keys says the same.

Additive by design: a key is read from the keyring first and from GSettings
otherwise, and `migrate` moves a GSettings key only after reading it back from
the keyring - then clears the plain-text copy. With no keyring (a locked one,
a headless box), everything behaves exactly as before.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)
SCHEMA_NAME = "dev.shani.chronoa.ApiKey"


def _opted_out() -> bool:
    """SHANI_CHRONOA_KEYRING=0: never read or write the session keyring.

    The same opt-out `config._is_secret` honours. Without it here, the hermetic
    test suite still read and wrote the real keyring through `get`/`put`: a key
    typed into a Settings probe outside pytest was read back by
    `test_api_key_defaults_are_empty_not_quoted` inside it (2026-10-08).
    Checked in `get`/`put`, not in `_secret`: `_secret` answers "is libsecret
    installed", which the Desktop panel reports, and an opt-out is not that.
    """
    import os
    return os.environ.get("SHANI_CHRONOA_KEYRING", "1") == "0"


def _secret():
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None, None
    schema = Secret.Schema.new(SCHEMA_NAME, Secret.SchemaFlags.NONE, {"provider": Secret.SchemaAttributeType.STRING})
    return Secret, schema


def get(provider: str) -> Optional[str]:
    """The stored key, '' if none is stored, or None if there is no usable keyring."""
    if _opted_out():
        return None
    Secret, schema = _secret()
    if Secret is None:
        return None
    try:
        value = Secret.password_lookup_sync(schema, {"provider": provider}, None)
    except Exception as exc:  # noqa: BLE001 - GLib.Error: no service, locked, dismissed prompt
        logger.debug("keyring lookup failed: %s", exc)
        return None
    return value or ""


def put(provider: str, value: str) -> bool:
    """Store (or, for an empty value, remove) a key; True when the keyring reads it back."""
    if _opted_out():
        return False
    Secret, schema = _secret()
    if Secret is None:
        return False
    try:
        if not value:
            Secret.password_clear_sync(schema, {"provider": provider}, None)
            return get(provider) == ""
        Secret.password_store_sync(schema, {"provider": provider}, Secret.COLLECTION_DEFAULT,
                                   f"Shani Chronoa API key: {provider}", value, None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("keyring store failed: %s", exc)
        return False
    return get(provider) == value


def migrate(config, providers: "dict[str, str]") -> "list[str]":
    """Move each non-empty GSettings key into the keyring; returns the providers moved."""
    moved = []
    for provider, key in providers.items():
        plain = config.get(key, "")
        if not plain:
            continue
        if put(provider, plain):
            config.set(key, "")  # only now: the keyring has it, and read it back
            moved.append(provider)
    if moved:
        logger.info("Moved %d API key(s) from dconf into the desktop keyring", len(moved))
    return moved
