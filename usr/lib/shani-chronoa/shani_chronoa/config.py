"""Configuration module for Shani Chronoa.

Handles GSettings schema loading, hardware-based model selection,
and privacy controls.
"""

import logging
import os
import subprocess
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
        """Get a boolean configuration value (schema type "b")."""
        if self._settings is None or key not in self._valid_keys:
            return default
        value = self._settings.get_value(key)
        if value.get_type_string() == "b":
            return value.get_boolean()
        return default

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
    def hardware_profile(self) -> str:
        """Get the persisted hardware profile override, or "auto" if none is set."""
        return self.get("hardware-profile", "auto")

    @property
    def privacy_mode(self) -> bool:
        """Get privacy mode status."""
        return self.get_bool("privacy-mode", True)

    @property
    def whisper_model(self) -> str:
        """Get the persisted whisper.cpp model override, or "" if none is set.

        Same reasoning as `model` above - was never actually read by
        `_init_components`, which always used `HardwareProfile.get_whisper_model()`.
        """
        return self.get("whisper-model", "")

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
    def notification_enabled(self) -> bool:
        """Get notification status."""
        return self.get_bool("notification-enabled", True)

    @property
    def wake_word_enabled(self) -> bool:
        """Get hands-free wake-word activation status."""
        return self.get_bool("wake-word-enabled", False)

    @property
    def wake_word_model(self) -> str:
        """Get the openWakeWord model name to listen for."""
        return self.get("wake-word-model", "hey_jarvis")

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

        Threat model: keys are stored in GSettings (dconf on a real
        install), which any process running as the same user can read -
        this is same-user protection, not a secure vault. GSettings is
        chosen for consistency with the rest of Chronoa's settings, not as
        a secret store. Keys are never logged and never passed through
        subprocess argv.
        """
        return {
            "llm7": self.get("llm7-api-key", ""),
            "kilo": self.get("kilo-api-key", ""),
            "blockrun": self.get("blockrun-api-key", ""),
            "openai": self.get("openai-api-key", ""),
            "anthropic": self.get("anthropic-api-key", ""),
            "google": self.get("google-api-key", ""),
            "groq": self.get("groq-api-key", ""),
        }


class HardwareProfile:
    """Hardware detection and model selection."""

    def __init__(self) -> None:
        self.gpu_available: bool = False
        self.gpu_type: str = "none"
        self.ram_mb: int = 0
        self.cpu_cores: int = 0
        self.profile: str = "auto"
        self._detect()

    def _detect(self) -> None:
        """Detect hardware capabilities."""
        self._detect_ram()
        self._detect_cpu()
        self._detect_gpu()
        self._select_profile()

    def _detect_ram(self) -> None:
        """Detect total system RAM."""
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        self.ram_mb = int(line.split()[1]) // 1024
                        break
        except Exception:
            self.ram_mb = 4096  # Default fallback

    def _detect_cpu(self) -> None:
        """Detect CPU core count."""
        try:
            self.cpu_cores = os.cpu_count() or 4
        except Exception:
            self.cpu_cores = 4

    def _detect_gpu(self) -> None:
        """Detect GPU availability."""
        try:
            # Check for NVIDIA
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                self.gpu_available = True
                self.gpu_type = "nvidia"
                return
        except Exception:
            pass

        # Check for Vulkan
        try:
            result = subprocess.run(
                ["vulkaninfo", "--summary"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.gpu_available = True
                self.gpu_type = "vulkan"
                return
        except Exception:
            pass

        # Check for AMD
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                self.gpu_available = True
                self.gpu_type = "amd"
                return
        except Exception:
            pass

    def _select_profile(self) -> None:
        """Select hardware profile based on detected capabilities."""
        if self.gpu_available:
            self.profile = "gpu"
        elif self.ram_mb >= 16384 and self.cpu_cores >= 8:
            self.profile = "high"
        elif self.ram_mb >= 8192:
            self.profile = "medium"
        else:
            self.profile = "low"

    def get_model(self) -> str:
        """Get the smallest Ollama model that still does reliable tool-calling.

        Qwen3 is tool-call-trained at every dense size (unlike most small
        model families, which only call functions reliably at 7B+); 4B keeps
        argument formatting reliable while staying ~2.5GB on disk. 1.7B is
        used only on the lowest tier and may need bumping back to 4B via
        `--model=qwen3:4b` if tool calls come out malformed on real hardware.
        """
        if self.profile in ("gpu", "high", "medium"):
            return "qwen3:4b"
        else:
            return "qwen3:1.7b"

    def get_whisper_model(self) -> str:
        """Get the appropriate whisper model for hardware."""
        if self.profile in ("high", "gpu"):
            return "medium"
        elif self.profile == "medium":
            return "base"
        else:
            return "tiny"

    def get_context_window(self) -> int:
        """Get an Ollama context window (num_ctx) size for the hardware tier.

        Was a single hardcoded 2048 in llm.py regardless of hardware - too
        tight once the system prompt, all ~8 tool schemas, and a multi-turn
        tool-calling conversation are all in context at once.
        """
        if self.profile in ("gpu", "high"):
            return 8192
        elif self.profile == "medium":
            return 4096
        else:
            return 2048


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

    def get_network_policy(self) -> dict:
        """Get network policy based on privacy mode."""
        if self.is_local_only:
            return {
                "ollama_allowed": True,  # Local Ollama is OK
                "external_api": False,
                "telemetry": False,
                "analytics": False,
                "allowed_hosts": ["localhost"],
            }
        return {
            "ollama_allowed": True,
            "external_api": True,
            "telemetry": True,
            "analytics": True,
            "allowed_hosts": ["*"],
        }