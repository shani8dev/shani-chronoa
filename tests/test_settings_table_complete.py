"""The settings table must cover what the code reads and nothing the code lost.

Two directions, both cheap to check and both easy to break:

* a key passed as a literal to ``config.get``/``get_bool``/``get_double``/
  ``set`` but absent from the table is a dead switch - the call silently takes
  its fallback and the person's setting is ignored;
* a key in the table that nothing in the shipped tree mentions is a switch
  that can never be read, which is the same dead switch from the other side.

The literal-key scan is deliberately narrow about receivers. ``.get(...)`` is
everywhere on dicts, so only config-looking receivers are considered; the
methods ``get_bool`` and ``get_double`` are unique to ``ChronoaConfig`` and
need no receiver check. Keys built from a variable are out of scope here -
they are covered by the usage scan below.

The person's default decision is pinned here as well as in the generator. A
copy in two places is intentional: if the generator is edited, this test is
the independent statement of what was decided.

Run: python3 -m pytest -q tests/test_settings_table_complete.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "usr/lib/shani-chronoa"))
from shani_chronoa import settings_table as T  # noqa: E402

TABLE_FILE = REPO / "usr/share/shani-chronoa/settings.json"
GENERATOR = REPO / "tools/gen_settings_table.py"

# The decision, restated independently of the generator that applies it.
ON_SENSES = """
hwmon printing polkitpolicy privilege dnsresolvers wirelesslink labnetworks
idle accessibility bluetooth modelfit capture monitoring containers listeners
stale git sessions
failure containerrun screenlock powerstate netstate usbplug btconnect schedule
sleepwake audiodevice journalmatch dbusprop calendar phone fswatch expiry
unithealth
""".split()
ON_KEYS = """
trigger-control-enabled ocr-sense-enabled filesystem-sense-enabled
mcp-client-enabled cleanup-enabled mascot-enabled organ-audible-enabled
sandbox-seccomp-enabled
file-delete-enabled process-kill-enabled power-control-enabled
window-close-enabled service-control-enabled mount-control-enabled
trash-empty-enabled bulk-edit-enabled run-command-enabled
phone-control-enabled phone-messages-read-enabled landline-enabled
watch-nowplaying-enabled watch-weather-enabled email-read-enabled
""".split()
OFF_KEYS = """
cloud-stt-enabled cloud-tts-enabled web-sense-enabled email-send-enabled
phone-messages-send-enabled git-push-enabled voice-clone-enabled
vision-sense-enabled hearing-sense-enabled wake-word-enabled
heard-sound-sense-enabled sound-sense-enabled rfsense-sense-enabled
location-sense-enabled meeting-enabled camera-sense-enabled
packet-capture-enabled i2c-write-enabled
""".split()
REMOVED_KEYS = """
lan-bridge-enabled smart-sense-enabled cooling-sense-enabled
monitors-sense-enabled link-sense-enabled contention-sense-enabled
thermal-sense-enabled camera-sense-enabled
""".split()
STILL_ASKS = """
file-delete-enabled process-kill-enabled power-control-enabled
window-close-enabled service-control-enabled mount-control-enabled
trash-empty-enabled bulk-edit-enabled run-command-enabled
""".split()

ON = {f"{name}-sense-enabled" for name in ON_SENSES} | set(ON_KEYS)
OFF = set(OFF_KEYS)

# Two literals deliberately name keys the table does not have:
# * `no-such-setting`: proves config.get refuses an unknown key;
# * `link-sense-enabled`: a pre-merge grant set in test_sense_network.py:240 to
#   prove config._SENSE_CONSENT_ALIASES still honours a retired key. The key is
#   gone from the table by decision; the alias is what keeps it meaningful.
DELIBERATE_UNKNOWN = frozenset({"no-such-setting", "link-sense-enabled"})

METHODS = frozenset({"get", "get_bool", "get_double", "set"})
CONFIG_RECEIVER = re.compile(r"(?i)config|cfg|settings")
CONFIG_ALIASES = frozenset({"c", "s", "calls", "fresh", "vision_enabled"})
TEXT_SUFFIXES = frozenset(
    {".py", ".qml", ".js", ".xml", ".json", ".md", ".sh", ".txt", ".ui", ".desktop"}
)
# Files that name keys by decision rather than use them: the table itself,
# the schema it came from, and the two places the decision is written down.
SELF_REFERENTIAL = frozenset(
    {
        "usr/share/shani-chronoa/settings.json",
        "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml",
        "tools/gen_settings_table.py",
        "tests/test_settings_table_complete.py",
    }
)


def _python_files():
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("build/", ".git/")):
            continue
        yield path


def _literal_calls():
    """String literal first arguments of config getters/setters, with sites."""
    found: dict[str, list[str]] = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            method = node.func.attr
            if method not in METHODS or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            receiver = ast.unparse(node.func.value)
            if method in ("get", "set") and not (
                CONFIG_RECEIVER.search(receiver) or receiver in CONFIG_ALIASES
            ):
                continue
            key = first.value
            # Include known table keys and anything shaped like one, so a
            # deleted or mistyped key is reported rather than silently skipped.
            if key in T.KEYS or (("-" in key) and re.fullmatch(r"[a-z0-9-]+", key)):
                where = f"{path.relative_to(REPO)}:{node.lineno}"
                found.setdefault(key, []).append(where)
    return found


def test_literal_setting_keys_are_all_in_the_table():
    calls = _literal_calls()
    missing = {
        key: sites
        for key, sites in calls.items()
        if key not in T.KEYS and key not in DELIBERATE_UNKNOWN
    }
    assert not missing, (
        f"config call sites name keys absent from settings.json: {missing}"
    )


def test_every_table_key_is_used_somewhere():
    # No exception list is needed: every one of the shipped keys is named by
    # real code or a real schema/GUI file. If a dynamically read key is ever
    # added, it must be named here with the reason, not silently exempted.
    corpus = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("build/", ".git/")) or rel in SELF_REFERENTIAL:
            continue
        try:
            corpus.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
    text = "\n".join(corpus)
    unused = [key for key in T.KEYS if key not in text]
    assert not unused, f"table keys no shipped file mentions: {unused}"


def test_decision_on_keys_are_on():
    wrong = sorted(key for key in ON if T.default_for(key) is not True)
    assert not wrong, f"should be on by decision: {wrong}"


def test_decision_off_keys_are_off():
    wrong = sorted(
        key for key in OFF if key in T.KEYS and T.default_for(key) is not False
    )
    assert not wrong, f"should be off by decision: {wrong}"


def test_removed_keys_are_absent():
    present = sorted(key for key in REMOVED_KEYS if key in T.KEYS)
    assert not present, f"retired/removed keys still in the table: {present}"


def test_destructive_switches_say_they_still_ask():
    missing = sorted(
        key for key in STILL_ASKS if "still asks before each action" not in T.label(key)
    )
    assert not missing, f"labels missing the per-action prompt: {missing}"


def test_shipped_table_matches_a_fresh_generator_run():
    spec = importlib.util.spec_from_file_location("gen_settings_table", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.build() == json.loads(TABLE_FILE.read_text(encoding="utf-8"))


def test_table_shape_and_public_helpers():
    assert T.KEYS == tuple(sorted(T.KEYS))
    assert set(T.DEFAULTS) == set(T.KEYS)
    for key in T.KEYS:
        row = T.TABLE[key]
        assert set(row) == {
            "type",
            "default",
            "label",
            "summary",
            "consent",
            "destructive",
            "secret",
        }, key
        assert row["type"] in ("b", "d", "s"), key
        assert row["label"] and row["summary"], key
        assert T.default_for(key) == T.DEFAULTS[key]
        assert T.kind(key) == row["type"]
        assert T.label(key) == row["label"]
        assert T.flags(key) == {
            "consent": row["consent"],
            "destructive": row["destructive"],
            "secret": row["secret"],
        }


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
