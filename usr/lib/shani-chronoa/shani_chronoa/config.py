"""Configuration module for Shani Chronoa.

Handles GSettings schema loading, hardware-based model selection,
and privacy controls.
"""

import logging
import os
from typing import Optional
from urllib.parse import urlparse

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

logger = logging.getLogger(__name__)

# GSettings schema constants
SCHEMA_ID = "org.shani.chronoa"
SCHEMA_PATH = "/org/shani/chronoa/"

# Hostnames that count as "local" for local-only privacy mode. Anything
# else (a LAN IP, a hostname, a public URL) is rejected while privacy mode
# is on - local-only means no traffic to a remote Ollama server either.
_LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})

# The default Ollama host: the schema fallback and the local-only
# enforcement target when a remote host is rejected.
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"


# API keys live in the desktop keyring (Secret Service: GNOME Keyring,
# KWallet), not in GSettings - dconf is plaintext and ends up in home
# backups. A key still found in GSettings is moved on first read.
try:
    gi.require_version("Secret", "1")
    from gi.repository import Secret  # type: ignore
    _SECRET_SCHEMA = Secret.Schema.new("org.shani.chronoa.ApiKey", Secret.SchemaFlags.NONE,
                                       {"key": Secret.SchemaAttributeType.STRING})
except (ImportError, ValueError):  # no libsecret GIR
    Secret = None  # type: ignore[assignment]
    _SECRET_SCHEMA = None


def _is_secret(key: str) -> bool:
    # SHANI_CHRONOA_KEYRING=0: settings only (the hermetic test suite - it
    # must not read or write the real session keyring)
    return key.endswith("-api-key") and os.environ.get("SHANI_CHRONOA_KEYRING", "1") != "0"


# Senses that reach outside this machine no matter how they are configured,
# and are therefore refused outright while privacy mode is on.
#
# Vision is deliberately NOT listed. Whether a screen capture leaves the
# machine depends on which model is selected, and that decision belongs to
# the vision sense where the model is actually chosen - gating it here would
# deny local vision under privacy mode, which is the one case privacy mode
# is supposed to permit.
# `location` is here because GeoClue's Wi-Fi source sends nearby network IDs
# to a location service: privacy mode means no.
_NETWORKED_SENSES = frozenset({"web", "location"})

# The authoritative sense -> consent-key table. Written out rather than
# composed as f"{sense}-sense-enabled" so that adding a sense is one visible
# line here, and so a sense with no declared key is denied outright instead
# of silently probing a setting that does not exist. It stays exactly a
# bijection with the senses the registry can discover - `git` is in it because
# `git` is a real sense, not because a trigger event type happens to share
# its name.
_SENSE_CONSENT_KEYS = {
    "vision": "vision-sense-enabled",
    "ocr": "ocr-sense-enabled",
    "filesystem": "filesystem-sense-enabled",
    "web": "web-sense-enabled",
    # Off by default: where someone is is private on any reading.
    "location": "location-sense-enabled",
    "memory": "memory-sense-enabled",
    "hearing": "hearing-sense-enabled",
    "capture": "capture-sense-enabled",
    "privilege": "privilege-sense-enabled",
    "display": "display-sense-enabled",
    "network": "network-sense-enabled",
    # The lab networks this machine happens to have. Its own key, NOT
    # `network-sense-enabled` and deliberately not the builder's
    # `network-provision-enabled`: this sense only reads a JSON record and lists
    # namespace directories, and noticing which isolated networks exist says
    # nothing about the user - no addresses, no contents, no traffic. The
    # builder's key is for *changing* networks and is far stricter.
    "labnetworks": "labnetworks-sense-enabled",
    # The link this machine already joined: signal, negotiated rate, retries and
    # the regulatory domain. Its own key, and NOT `list_wifi_networks`' - that
    # one *scans for nearby networks*, which says where the machine is. This
    # one reads the association the machine is already in and scans nothing.
    "wirelesslink": "wirelesslink-sense-enabled",
    # The resolvers this machine uses, and whether DNS is encrypted or validated.
    # Reads no query log and enables none - which names were looked up is a
    # separate question with its own permission, and this does not need it.
    "dnsresolvers": "dnsresolvers-sense-enabled",
    "bluetooth": "bluetooth-sense-enabled",
    # Off by default: window titles and application names say what the
    # user is working on, which is personal on any reading.
    "accessibility": "accessibility-sense-enabled",
    # Idle time draws the shape of someone's day: when they arrive,
    # when they leave. Personal, and off until asked for.
    "idle": "idle-sense-enabled",
    "rfsense": "rfsense-sense-enabled",
    "thermalgrid": "thermalgrid-sense-enabled",
    "hwmon": "hwmon-sense-enabled",
    "modelfit": "modelfit-sense-enabled",
    "power": "power-sense-enabled",
    "storage": "storage-sense-enabled",
    "cpu": "cpu-sense-enabled",
    "gpu": "gpu-sense-enabled",
    "security": "security-sense-enabled",
    # What this user may do without a password: the one question whose answer is a
    # *permission* rather than a state. Hand-kept table, so a sense missing from
    # it is permanently ungrantable - see `kernellog` above.
    "polkitpolicy": "polkitpolicy-sense-enabled",
    "devices": "devices-sense-enabled",
    "audio": "audio-sense-enabled",
    "printing": "printing-sense-enabled",
    "filesystems": "filesystems-sense-enabled",
    "services": "services-sense-enabled",
    "timebase": "timebase-sense-enabled",
    "usb": "usb-sense-enabled",
    "resources": "resources-sense-enabled",
    "updates": "updates-sense-enabled",
    "faults": "faults-sense-enabled",
    # The kernel ring buffer, which is a different record from `faults`':
    # hardware faults and OOM kills that no service has touched, including the
    # ones from before journald was running. Off by default (not in
    # `_SENSE_DEFAULT_ENABLED`) because reading it usually needs root or a
    # group membership, and it is the machine's hardware detail rather than a
    # service's own account of itself.
    #
    # **This table is hand-kept, and a sense missing from it is permanently
    # ungrantable** - `_default_on` resolves through `key in
    # frozenset(_SENSE_CONSENT_KEYS.values())`, so an unlisted sense's key falls
    # to the *event* defaults and `ChronoaConfig.set()` writes to a key nothing
    # reads. That is the `heard-sound` defect, and `tests/test_sense_manifest.py`
    # is what catches it: it writes the key through the real config and asks
    # whether a fresh one honours it.
    "kernellog": "kernellog-sense-enabled",
    # Whether the machine is on mains or on a UPS battery, and how long is left.
    # Reads /sys/class/power_supply (type "UPS") plus NUT's own upsc. On by
    # default with the other machine-state set: it is a hardware fact, it needs
    # no binary for the sysfs half, and an absent UPS is reported as absent
    # rather than as a fault. NOT a reading of the user's world.
    "ups": "ups-sense-enabled",
    "sessions": "sessions-sense-enabled",
    "snapshots": "snapshots-sense-enabled",
    "coredumps": "coredumps-sense-enabled",
    "firewall": "firewall-sense-enabled",
    "hardware": "hardware-sense-enabled",
    "kernel": "kernel-sense-enabled",
    "cgroup": "cgroup-sense-enabled",
    "containers": "containers-sense-enabled",
    "listeners": "listeners-sense-enabled",
    "stale": "stale-sense-enabled",
    # Off by default, and deliberately so. Filenames, branch names and
    # uncommitted work are the user's work product, not the machine's state -
    # the same class as `accessibility` (which window titles are open) and
    # `idle` (the shape of someone's day). "Is my tree dirty" is a fair
    # question, but the answer is theirs, not the hardware's.
    "git": "git-sense-enabled",
    "boots": "boots-sense-enabled",
    # Opens the microphone. Its own key, and not the `sound` event's: one key
    # gates one thing, and a user who allows the doorbell trigger has not
    # agreed to let anything listen whenever it is asked.
    "heard-sound": "heard-sound-sense-enabled",
}

# Trigger event types that are not senses, and the key gating each one.
#
# `triggers.EventRule.sense` returns the rule's *event type*, and both
# `TriggerEngine._consent` and `EventEngine._consent` hand that straight to
# `sense_allowed()`. So an event type with no row in a table here was refused
# at both arm time and fire time, forever: five of the six event types
# (`fswatch`, `failure`, `expiry`, `containerrun`, `unithealth`) shipped
# unarmable and the defect was invisible, because a gate that is shut looks
# exactly like a gate that is working.
#
# A separate table rather than five more rows in `_SENSE_CONSENT_KEYS`, because
# that dict is asserted to be exactly the set of names `discover_senses()`
# knows. Folding events into it would make the settings window, the sense
# registry sweep and the manifest tests believe there are 43 senses rather
# than 38. The key *names* stay `-sense-enabled` because the gate that reads
# them is `sense_allowed()` and the refusal it produces says "sense" - a key
# called `fswatch-event-enabled` would send a user after a switch that the
# message never mentions.
#
# `git` is deliberately absent: it is a real sense with its own key, and one
# key must gate one thing. `git-sense-enabled` already governs git event rules
# through the `sense` property above, and a second key for the same word would
# let a user permit one and not the other without being able to say why.
_EVENT_CONSENT_KEYS = {
    "fswatch": "fswatch-sense-enabled",
    "failure": "failure-sense-enabled",
    "expiry": "expiry-sense-enabled",
    "containerrun": "containerrun-sense-enabled",
    "unithealth": "unithealth-sense-enabled",
    "screenlock": "screenlock-sense-enabled",
    "powerstate": "powerstate-sense-enabled",
    "netstate": "netstate-sense-enabled",
    "usbplug": "usbplug-sense-enabled",
    "btconnect": "btconnect-sense-enabled",
    "schedule": "schedule-sense-enabled",
    "sleepwake": "sleepwake-sense-enabled",
    "audiodevice": "audiodevice-sense-enabled",
    "journalmatch": "journalmatch-sense-enabled",
    "dbusprop": "dbusprop-sense-enabled",
    "calendar": "calendar-sense-enabled",
    "phone": "phone-sense-enabled",
    "sound": "sound-sense-enabled",
}

# Deliberately empty, and written out rather than omitted.
#
# Every sense above that reports the machine's own hardware ships on; every
# event type here ships off. An event rule acts with nobody asking, so a
# default-on entry would arm unattended behaviour on a fresh install with no
# switch ever touched. `git` is off for the same reason it is off as a sense:
# what a rule fires *on* here is the user's files, their work product and a
# credential deadline, not a disk-free reading.
_EVENT_DEFAULT_ENABLED = frozenset()


def _consent_key_for(name: str) -> Optional[str]:
    """The consent key gating `name`, or None if nothing declares one.

    Two tables because a sense and an event type are different things; one
    lookup because the caller - `triggers.py`, which owns `EventRule.sense` -
    hands us an event type through the *sense* gate and cannot be changed from
    here. A name in neither table denies, which is the fail-closed direction
    and the reason a seventh event type is inert rather than unguarded.
    """
    return _SENSE_CONSENT_KEYS.get(name) or _EVENT_CONSENT_KEYS.get(name)


def _default_on(name: str, key: str) -> bool:
    """Whether `name` is permitted on an install that has granted nothing.

    Resolved through the key rather than through the name alone, so an event
    type can never inherit `_SENSE_DEFAULT_ENABLED` by sharing a name with a
    sense that happens to be default-on. `get_bool` returns this for any key
    the running schema does not declare, so it is also what an older installed
    schema falls back to.
    """
    if key in frozenset(_SENSE_CONSENT_KEYS.values()):
        return name in _SENSE_DEFAULT_ENABLED
    return name in _EVENT_DEFAULT_ENABLED


# Retired consent keys, still honoured.
#
# Merging two senses retires one name, and the consent key is derived from that
# name - so a plain merge silently revokes every grant a user had already made.
# Each entry lists the keys that used to gate the sense and still do, so an
# existing grant survives the merge and either key is enough to allow it.
_SENSE_CONSENT_ALIASES = {
    # `monitors` walked /sys/class/drm and reported which connectors were
    # connected; `display` walked the same tree and reported the same list, plus
    # the backlight. One enumeration now produces both halves.
    "display": ("monitors-sense-enabled",),
    # `smart` was merged into `storage`: same drives, same /sys/block walk, and
    # `storage` was already reporting NVMe wear from sysfs while `smart`
    # reported the same endurance figure from SMART attribute 233.
    "storage": ("smart-sense-enabled",),
    # `camera` and `contention` became `capture`. They already shared the holder
    # lookup - camera imported contention.describe - and contention already
    # walked both /dev/video* and /dev/snd, so the two senses were reporting the
    # same devices from two directions and could disagree about whether one was
    # free.
    "capture": ("camera-sense-enabled", "contention-sense-enabled"),
    # `thermal` and `cooling` were merged into `hwmon`. The kernel exposes the
    # same physical sensors twice - once as ACPI thermal zones and once as
    # hwmon channels - and on this machine they agree exactly (90.0C, 69.0C,
    # 57.0C), so the same temperature was reported under two names by two
    # senses. `cooling` also walked /sys/class/hwmon a second time to find fan
    # channels `hwmon` had already read.
    "hwmon": ("thermal-sense-enabled", "cooling-sense-enabled"),
    # `link` was merged into `network`. Both walked /sys/class/net and printed
    # the same per-interface "<name>: <operstate>, carrier" line; link added the
    # speed and the duplicate-address check, network added the wireless flag and
    # DNS. Measured on this machine, the two lists were byte-identical apart
    # from those extras.
    "network": ("link-sense-enabled",),
}

# On by default are `memory` and the machine-state senses. `memory` because it is
# local-only and remembering is the point of an assistant; the rest because they
# report the machine's own hardware and never leave it - the same class of fact
# as a disk-free reading. `smart` defaults on with smartctl absent, because the
# sense reports that health was not determined rather than a clean bill of
# health, and a missing tool must not be what decides a default. Everything
# that captures or reads the user's world waits to be asked for.
_SENSE_DEFAULT_ENABLED = frozenset({
    "memory", "power", "storage", "network", "cpu", "gpu", "devices",
    "audio", "display", "security",
    # Default-on alongside `storage` and `cpu`: this is the machine's
    # own state, and `services` reports only failures rather than the whole
    # service list, so it is not the noise a default-on sense should avoid.
    "filesystems", "services", "timebase", "usb", "resources",
    # Default-on with the rest of the machine's own state: `updates` reads the
    # local package database, `faults` reads the local journal, and `snapshots`
    # reads the filesystem layout. None of them leaves the machine.
    "updates", "faults", "snapshots", "coredumps", "firewall",
    # Default-on with the rest of the machine's own state: whether utility power
    # is present, and how much battery is left. Read from sysfs, which needs no
    # binary, and an absent UPS reports absent rather than as a fault.
    "ups",
    # Default-on: the machine's own immutable and current facts. Identity, the
    # running kernel, the limits this process is held to, and when it last
    # booted. Each reads a kernel-owned value and none of them leaves the
    # machine.
    "hardware", "kernel", "cgroup", "boots",
    # `containers`, `listeners` and `stale` were moved OFF here on 2026-09-30.
    # They sat with the block above under the claim that they "read the kernel's
    # own view of the machine", which measurement contradicts: `listeners`
    # resolves each socket to an OWNER PROCESS NAME and PID, `stale` reports a
    # per-PID EXECUTABLE PATH, and `containers` reports names and images. That is
    # process identity and the user's work - which is why all three already
    # declared themselves SENSITIVITY_PERSONAL and so contradicted the default
    # they shipped with. `SENSITIVITY` is declared and enforced nowhere, so the
    # default was the only thing deciding this. ("git" is off for the same
    # class of reason - see `_SENSE_CONSENT_KEYS`.)
    # `sessions` defaults OFF deliberately. It reports who *else* is on this
    # machine and what is running as root outside the service tree - that is
    # other people's presence, not this machine's own hardware, and it belongs to
    # the "reads the user's world" side of the line rather than the "reports a
    # disk-free reading" side. `git` defaults off for the same class of reason:
    # uncommitted filenames and branch names are the user's work product.
    #
    # ("sessions" and "git" are intentionally absent from this frozenset.)
})

# Input control is not a sense (it has no percept to emit), so it lives here
# rather than in `_SENSE_CONSENT_KEYS`. It still needs the same fail-closed
# treatment: a missing key denies, never silently permits.
_INPUT_CONTROL_KEY = "input-control-enabled"


#: Names the single consent key a user grant opened for this process. Set by the
#: dispatcher in the child it is about to run, not in the parent - the parent
#: handles many calls at once, so a process-wide value there would be whichever
#: call happened to be in flight.
CONSENT_GRANT_ENV = "SHANI_CHRONOA_CONSENT_GRANT"


def granted_consent_key() -> Optional[str]:
    """The consent key a user grant opened for this process, if any."""
    return os.environ.get(CONSENT_GRANT_ENV) or None



class ChronoaConfig:
    """Configuration manager using GSettings.

    Reads and writes through Gio.Settings' typed accessors (`get_string` /
    `get_boolean`) rather than shelling out to the `gsettings` CLI, so
    values are never GVariant-quoted and booleans are real Python bools.
    A fresh instance reads the same persisted values the instance that
    wrote them did - both share the GSettings backend (the keyfile backend
    under the test harness, dconf on a real install).
    """

    def __init__(self) -> None:
        self._settings: Optional[Gio.Settings] = None
        self._valid_keys: frozenset[str] = frozenset()
        self._load_settings()

    def _load_settings(self) -> None:
        """Load the GSettings object for the Chronoa schema.

        The schema is looked up through `SettingsSchemaSource` first because
        `Gio.Settings.new()` aborts the process (GLib-GIO-ERROR, not a
        catchable exception) when the schema is missing - the lookup lets us
        degrade to Python defaults instead of crashing.
        """
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup(SCHEMA_ID, True) if source is not None else None
        if schema is None:
            logger.warning("GSettings schema %s not found - using Python defaults", SCHEMA_ID)
            self._settings = None
            self._valid_keys = frozenset()
            return
        self._settings = self._new_settings()
        self._valid_keys = frozenset(schema.list_keys())

    def _new_settings(self) -> Gio.Settings:
        """Create the Gio.Settings object for this instance.

        Under the test harness (`GSETTINGS_BACKEND=keyfile`) the default
        backend is a process-wide singleton that caches the keyfile path
        from its first use, so per-test `XDG_CONFIG_HOME` isolation would be
        ignored and values would leak between tests. Creating an explicit
        keyfile backend per instance at the current `XDG_CONFIG_HOME` makes
        each instance deterministically read/write that test's store, and a
        fresh instance over the same path still sees persisted values. On a
        real install (no `GSETTINGS_BACKEND=keyfile`) the default backend
        (dconf) is used so settings persist normally.
        """
        if os.environ.get("GSETTINGS_BACKEND") == "keyfile":
            config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
                os.path.expanduser("~"), ".config"
            )
            keyfile = os.path.join(config_home, "glib-2.0", "settings", "keyfile")
            backend = Gio.keyfile_settings_backend_new(keyfile, SCHEMA_PATH, SCHEMA_ID)
            return Gio.Settings.new_with_backend(SCHEMA_ID, backend)
        return Gio.Settings.new(SCHEMA_ID)

    def get(self, key: str, default: str = "") -> str:
        """Get a string configuration value (schema type "s").

        Returns `default` if the schema is unavailable or the key is not a
        known string key. Values come back unquoted - Gio.Settings never
        wraps them in the GVariant quotes the `gsettings` CLI prints.
        """
        if self._settings is None or key not in self._valid_keys:
            return default
        if _is_secret(key):
            got = self._secret_get(key)
            if got is not None:
                return got
        value = self._settings.get_value(key)
        if value.get_type_string() == "s":
            return value.get_string()
        return default

    # --- keyring (Secret Service) for *-api-key ---------------------------
    def _secret_get(self, key: str) -> Optional[str]:
        """The key from the keyring (migrating a plaintext GSettings value
        there first); None when no keyring is available."""
        if _SECRET_SCHEMA is None:
            return None
        try:
            val = Secret.password_lookup_sync(_SECRET_SCHEMA, {"key": key}, None)
            legacy = self._settings.get_string(key)
            if legacy:
                if not val:
                    Secret.password_store_sync(_SECRET_SCHEMA, {"key": key}, Secret.COLLECTION_DEFAULT,
                                               f"Chronoa {key}", legacy, None)
                    val = legacy
                self._settings.reset(key)  # no plaintext copy left behind
                logger.info("Moved %s from settings to the keyring", key)
            return val or ""
        except Exception as e:  # no Secret Service on this session
            logger.warning("Keyring unavailable (%s); %s stays in settings", type(e).__name__, key)
            return None

    def _secret_set(self, key: str, value: str) -> bool:
        if _SECRET_SCHEMA is None:
            return False
        try:
            if value:
                Secret.password_store_sync(_SECRET_SCHEMA, {"key": key}, Secret.COLLECTION_DEFAULT,
                                           f"Chronoa {key}", value, None)
            else:
                Secret.password_clear_sync(_SECRET_SCHEMA, {"key": key}, None)
            self._settings.reset(key)
            return True
        except Exception as e:
            logger.warning("Keyring unavailable (%s); %s stays in settings", type(e).__name__, key)
            return False

    def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean configuration value (schema type "b").

        A per-turn grant may open exactly one consent key, and only for the
        process it was granted to. `SHANI_CHRONOA_CONSENT_GRANT` names a single
        key - the one gating the tool the user just said yes to - and only that
        key reads as open. Every other key, and every other process, sees the
        stored value untouched.

        The scope is the point. `get_bool` has 35 callers, and the same
        `sense_allowed` that gates a web search is read by the scheduler, the
        senses and the settings window. A grant that flipped a boolean outright
        would leave a background sense running and a settings checkbox that
        disagrees with what the process is doing - a UI that lies is worse than
        a feature that is missing. Naming one key confines it to the tool call
        the user actually approved.
        """
        if granted_consent_key() == key and key in self._valid_keys:
            logger.info("Consent key %s opened for this call by user grant", key)
            return True
        if self._settings is None or key not in self._valid_keys:
            return default
        value = self._settings.get_value(key)
        if value.get_type_string() == "b":
            return value.get_boolean()
        return default

    def get_double(self, key: str, default: float = 0.0) -> float:
        """Get a number configuration value (schema type "d")."""
        if self._settings is None or key not in self._valid_keys:
            return default
        value = self._settings.get_value(key)
        return value.get_double() if value.get_type_string() == "d" else default

    def set(self, key: str, value: str) -> None:
        """Set a configuration value. Boolean keys accept "true"/"false" strings.

        The value is never logged (API keys in particular must not leak into
        logs) and never passed through a subprocess - Gio.Settings writes
        directly to the backend.
        """
        if self._settings is None or key not in self._valid_keys:
            logger.error("Unknown or unavailable setting key: %s", key)
            return
        if _is_secret(key) and self._secret_set(key, value):
            logger.debug("Set %s (keyring)", key)
            return
        current = self._settings.get_value(key)
        if current.get_type_string() == "b":
            self._settings.set_boolean(key, value == "true")
        elif current.get_type_string() == "d":
            try:
                number = float(value)
            except (TypeError, ValueError):
                logger.error("Setting %s needs a number", key)
                return
            low, high = {"speech-rate": (0.5, 2.0), "end-of-speech-pause": (0.5, 3.0)}.get(key, (-1e9, 1e9))
            self._settings.set_double(key, min(high, max(low, number)))
        else:
            self._settings.set_string(key, value)
        logger.debug("Set %s", key)

    @property
    def model(self) -> str:
        """Get the persisted LLM model override, or "" if none is set.

        Empty means "no override, defer to HardwareProfile.get_model()" -
        `app.py`'s `_init_components` never actually read this property
        before (only `--model=`'s in-memory override was consulted), so a
        non-empty default here would have silently defeated hardware-based
        auto-selection for anyone who never explicitly set a model.
        """
        return self.get("model", "")

    @property
    def vision_model(self) -> str:
        """Get the persisted *vision* model override, or "" if none is set.

        Deliberately a different key from `model`, and deliberately not derived
        from it. Vision is a separate capability with a separate model family:
        a text model that cannot see is useless to `senses/vision.py`, and a
        vision model is a much worse tool-caller than a text-tuned one. Folding
        them together - reading `model` here, or letting `--model=` on the
        command line reach this sense - would mean picking a chatty 4B for
        screen descriptions, or a vision model for every tool call in the app.
        Empty means "no override, use `HardwareProfile.get_vision_model()`".
        """
        return self.get("vision-model", "")

    @property
    def hardware_profile(self) -> str:
        """Get the persisted hardware profile override, or "auto" if none is set."""
        return self.get("hardware-profile", "auto")

    @property
    def privacy_mode(self) -> bool:
        """Get privacy mode status."""
        return self.get_bool("privacy-mode", True)

    @property
    def model_download_enabled(self) -> bool:
        """Whether fetching a speech model over the network is permitted.

        Off by default: it is the only setting in this file that causes bytes to
        be fetched from the internet on the user's behalf, so it must never be
        implied by any other one.
        """
        return self.get_bool("model-download-enabled", False)

    def sense_allowed(self, sense: str) -> bool:
        """Whether `sense` is permitted to perceive right now.

        Two independent gates, both of which must pass:

        - the sense's own `<name>-sense-enabled` key, which defaults to
          false for every sense except memory. This is the per-sense consent
          surface: "which perceptions may this assistant form?" has to be
          answerable one sense at a time, because they differ enormously in
          intrusiveness. Screen capture and filesystem traversal are not the
          same request as remembering a stated preference, and a single
          global switch cannot express that difference.
        - `privacy-mode`, which is checked here and not merely relied upon
          downstream. A sense that touches the network or captures the
          screen must not act while the user believes the machine is
          local-only.

        `name` may also be a trigger *event* type rather than a sense:
        `triggers.EventRule.sense` returns one, and the engines hand it here
        unchanged. The second gate does not apply to those - every event type
        reads this machine only, which is the same argument that leaves
        `git` out of `_NETWORKED_SENSES`.

        `get_bool` returns the supplied default for a key the running schema
        does not declare, so an older installed schema denies the new senses
        rather than silently allowing them.
        """
        key = _consent_key_for(sense)
        if key is None:
            return False
        granted = self.get_bool(key, _default_on(sense, key))
        for alias in _SENSE_CONSENT_ALIASES.get(sense, ()):
            # Either key is enough. A merged sense keeps the permissions both
            # of its halves had, rather than the narrower of the two.
            granted = granted or self.get_bool(alias, False)
        if not granted:
            return False
        if sense in _NETWORKED_SENSES and self.privacy_mode:
            return False
        return True

    def sense_allowed_reason(self, sense: str) -> str:
        """A user-facing explanation of why `sense` is or isn't permitted."""
        key = _consent_key_for(sense)
        if key is None:
            return f"there is no '{sense}' sense"
        aliases = _SENSE_CONSENT_ALIASES.get(sense, ())
        granted = self.get_bool(key, _default_on(sense, key)) or any(
            self.get_bool(alias, False) for alias in aliases)
        if not granted:
            # Name the live key only. Retired aliases are honoured in the
            # check above but have no row in the settings window, so naming one
            # sends the user looking for a switch that does not exist. They are
            # a compatibility mechanism, not an instruction - do not "simplify"
            # the alias branch above away on the grounds that this ignores it.
            return f"the {sense} sense is turned off (enable '{key}')"
        if sense in _NETWORKED_SENSES and self.privacy_mode:
            return (
                f"the {sense} sense needs privacy mode off because it reaches "
                f"outside this machine"
            )
        return ""

    @property
    def hearing_sense_enabled(self) -> bool:
        """Whether Chronoa may turn an utterance into a transient percept."""
        return self.sense_allowed("hearing")

    @property
    def input_control_enabled(self) -> bool:
        """Whether Chronoa may move the pointer, click, or type as an action.

        Not a sense (it emits no percept), so it is checked directly against
        its own key rather than through `sense_allowed`. A missing key denies,
        which is the only safe default for something that can move the
        pointer.
        """
        return self.get_bool(_INPUT_CONTROL_KEY, False)

    @property
    def vision_sense_enabled(self) -> bool:
        """Whether Chronoa may capture and describe the screen."""
        return self.sense_allowed("vision")

    @property
    def ocr_sense_enabled(self) -> bool:
        """Whether Chronoa may extract text from images."""
        return self.sense_allowed("ocr")

    @property
    def filesystem_sense_enabled(self) -> bool:
        """Whether Chronoa may read files in the user's home directory."""
        return self.sense_allowed("filesystem")

    @property
    def web_sense_enabled(self) -> bool:
        """Whether Chronoa may fetch web content."""
        return self.sense_allowed("web")

    @property
    def memory_sense_enabled(self) -> bool:
        """Whether Chronoa may keep durable notes across sessions."""
        return self.sense_allowed("memory")

    @property
    def whisper_model(self) -> str:
        """Get the persisted whisper.cpp model override, or "" if none is set.

        Same reasoning as `model` above - was never actually read by
        `_init_components`, which always used `HardwareProfile.get_whisper_model()`.

        The name is kept even though the value is now backend-dependent: it is
        the model *tier*, and `stt.build_stt` passes it to whichever backend
        `stt_backend` selects. Renaming it would ungrant every existing user's
        override, which is the permission-revocation-in-a-refactor's-clothes
        mistake this repo records for the retired sense consent keys.
        """
        return self.get("whisper-model", "")

    @property
    def stt_backend(self) -> str:
        """Which local speech-to-text engine to use.

        `"whisper"` (the default, and the only value that existed before
        Parakeet) or `"parakeet"`. Anything blank or unrecognised is passed
        through to `stt.build_stt`, which falls back to whisper and logs - so
        a bad value degrades to today's behaviour rather than to no speech.
        """
        return self.get("stt-backend", "whisper")

    @property
    def piper_voice(self) -> str:
        """Get the Piper TTS voice."""
        return self.get("piper-voice", "en_US-lessac-medium")

    @property
    def ollama_host(self) -> str:
        """Get the Ollama host URL.

        In local-only privacy mode, a non-localhost host is rejected and the
        local default is returned instead - local-only means no traffic to a
        remote Ollama server either. With privacy mode off, the configured
        host is used as-is.
        """
        host = self.get("ollama-host", _DEFAULT_OLLAMA_HOST)
        if self.privacy_mode and not self._is_local_host(host):
            return _DEFAULT_OLLAMA_HOST
        return host

    @staticmethod
    def _is_local_host(url: str) -> bool:
        """True if the URL points at a loopback/localhost address."""
        try:
            hostname = urlparse(url).hostname
        except ValueError:
            return False
        return hostname in _LOCAL_HOSTNAMES

    @property
    def language(self) -> str:
        """Get the interface language."""
        return self.get("language", "en")

    @property
    def debug_mode(self) -> bool:
        """Get debug mode status."""
        return self.get_bool("debug-mode", False)

    @property
    def auto_start(self) -> bool:
        """Get auto-start status."""
        return self.get_bool("auto-start", False)

    @property
    def start_hidden_at_login(self) -> bool:
        """Get start-hidden-at-login status."""
        return self.get_bool("start-hidden-at-login", False)

    @property
    def reply_style(self) -> str:
        """The named reply-style preset, or 'ordinary' for no styling."""
        got = (self.get("reply-style", "ordinary") or "ordinary").strip().lower()
        return got if got in ("ordinary", "brief", "explanatory") else "ordinary"

    @property
    def notification_enabled(self) -> bool:
        """Get notification status."""
        return self.get_bool("notification-enabled", True)

    @property
    def wake_word_enabled(self) -> bool:
        """Get hands-free wake-word activation status."""
        return self.get_bool("wake-word-enabled", False)

    @property
    def wake_phrase(self) -> str:
        """The spoken phrase that starts listening (matched at the start of what was said)."""
        return self.get("wake-phrase", "hey chronoa")

    @property
    def cloud_fallback_enabled(self) -> bool:
        """Whether Chronoa may fall back to a free cloud LLM when Ollama is unavailable.

        Explicit opt-in, default off - Chronoa is local-first, and this is
        the one setting that can send prompts and tool-call arguments off
        the machine. Only takes effect when privacy mode is also off (see
        PrivacyManager) - both gates must be open.
        """
        return self.get_bool("cloud-fallback-enabled", False)

    @property
    def audio_input_device(self) -> str:
        """PipeWire node name to record from; empty means the default device.

        Applied as `pw-record --target`. Only honoured on the PipeWire
        backends - a PipeWire node name is not an ALSA device identifier - so
        see `pipewire.list_inputs()` for what a valid value looks like.
        """
        return self.get("audio-input-device", "").strip()

    @property
    def audio_output_device(self) -> str:
        """PipeWire node name to play through; empty means the default device."""
        return self.get("audio-output-device", "").strip()

    def set_audio_devices(self, input_device: str, output_device: str) -> None:
        """Persist a chosen input/output pair in one call."""
        self.set("audio-input-device", (input_device or "").strip())
        self.set("audio-output-device", (output_device or "").strip())

    @property
    def barge_in_vad_enabled(self) -> bool:
        """Whether to continuously monitor the mic during playback and interrupt on speech.

        Off by default - no acoustic echo cancellation is implemented, so
        on speakers (not headphones) this can self-interrupt on Chronoa's
        own voice. See the gsetting description for detail.
        """
        return self.get_bool("barge-in-vad-enabled", False)

    def cloud_llm_api_keys(self) -> dict:
        """User-supplied API keys for cloud LLM providers, keyed by provider id.

        For llm7/kilo/blockrun, empty (the default) means anonymous/keyless
        access - a real key is optional and only raises that provider's
        rate limits (confirmed live: Kilo's own 429 error explicitly
        suggests this). For openai/anthropic/google/groq, a key is
        mandatory - confirmed live that all four reject an unauthenticated
        request outright - so leaving these empty just means that provider
        is skipped entirely (see `CloudLLMChain.__init__`), not that it
        runs anonymously. Not a property since it returns a dict, matching
        `cloud_llm.PROVIDERS`' ids exactly.

        Threat model: keys live in the desktop keyring (secret_store.py:
        gnome-keyring / KWallet, encrypted at rest under the login password)
        when there is one, and in GSettings otherwise - dconf, plain text,
        readable by any same-user process. Either way this is same-user
        protection, not a vault against the user's own programs. Keys are
        never logged and never passed through subprocess argv.
        """
        from shani_chronoa import secret_store

        out = {}
        for provider, key in API_KEY_SETTINGS.items():
            stored = secret_store.get(provider)
            out[provider] = stored if stored else self.get(key, "")
        return out

    def api_key_value(self, key: str) -> str:
        """What Settings shows for an API-key row: the keyring's copy first, then GSettings."""
        from shani_chronoa import secret_store

        provider = next((p for p, k in API_KEY_SETTINGS.items() if k == key), None)
        stored = secret_store.get(provider) if provider else None
        return stored if stored else self.get(key, "")

    def set_api_key(self, key: str, value: str) -> None:
        """Save a key from Settings: into the keyring when there is one, else GSettings."""
        from shani_chronoa import secret_store

        provider = next((p for p, k in API_KEY_SETTINGS.items() if k == key), None)
        if provider and secret_store.put(provider, value):
            self.set(key, "")  # the keyring has it (read back); keep no plain-text copy
            return
        self.set(key, value)


#: provider id -> the GSettings key that held its API key before (and without) a keyring
API_KEY_SETTINGS = {
    "llm7": "llm7-api-key", "kilo": "kilo-api-key", "blockrun": "blockrun-api-key", "openai": "openai-api-key",
    "anthropic": "anthropic-api-key", "google": "google-api-key", "groq": "groq-api-key",
    "opencode-zen": "opencode-zen-api-key", "openrouter": "openrouter-api-key",
    # The custom endpoint's key, so it lands in the keyring like every other one
    # rather than being the single secret in this table that is only ever
    # plaintext.
    "custom": "custom-llm-api-key",
}


class PrivacyManager:
    """Privacy controls for local-only mode."""

    def __init__(self, config: ChronoaConfig) -> None:
        self.config = config

    @property
    def is_local_only(self) -> bool:
        """Check if running in local-only mode.

        Reads the persisted privacy-mode setting fresh on every access, so
        an external change (or a fresh PrivacyManager over the same config)
        is always reflected - no stale in-memory copy.
        """
        return self.config.privacy_mode

    def enable_local_mode(self) -> None:
        """Enable local-only privacy mode."""
        self.config.set("privacy-mode", "true")
        logger.info("Privacy mode enabled - all processing local")

    def disable_local_mode(self) -> None:
        """Disable local-only privacy mode."""
        self.config.set("privacy-mode", "false")
        logger.warning("Privacy mode disabled - external services may be used")

    # Deleted: the previous `get_network_policy` reported telemetry and
    # analytics as *allowed* whenever privacy mode was off. Chronoa has never
    # collected telemetry or analytics, so those could never have been allowed;
    # a method that lies about what the switches mean was worse than silence.
    # Privacy mode itself is the network policy: it is reported by the privacy
    # surface and read as `is_local_only` by the brain.