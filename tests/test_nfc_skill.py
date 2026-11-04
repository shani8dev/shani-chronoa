"""The `nfc` skill: the NDEF decoder, and refusing what must be refused.

**The decoder is the part that can be tested without hardware, so it is tested
against real bytes.** NDEF is a published format (NFC Forum RTD), so the records
are decoded here rather than by scraping `nfc-list`'s human-readable dump. The
record used throughout is the one an NTAG213 sticker actually carries when it
holds a link:

    E1 01 0C 55 04 65 78 61 6D 70 6C 65 2E 63 6F 6D
    ^^ ^^ ^^ ^^ \\_________________________________/
    |  |  |  |      "example.org"
    |  |  |  type "U" (URI), prefix code 0x04 = "https://"
    |  |  payload length 12 = 1 prefix byte + 11 characters
    |  short record: the payload length is ONE byte, so bit 5 is set
    MB | ME, and TNF = 0x01 (NFC Forum well-known)

**`0xE1`, not `0xD1`.** The short-record bit is 0x20, and the first version of
`ndef_bytes` emitted `0xD1` - MB | ME | TNF=1, with SR *clear*. It then wrote a
one-byte payload length where the decoder, reading the same bits, expected the
high byte of a four-byte one. Every record it produced decoded to nothing at all,
with no error anywhere: `e1` vs `d1` is one bit and the whole feature was dead.
That is the reason the round trip is tested both ways here.

**The refusal tests are the other half, and their order matters.** The
destructive-write check runs *before* the tool-presence check, so "write to my
bank card" is refused identically on a machine with libnfc and one without. With
the checks the other way round the answer was "nfc-mfultralight is not
installed" - so the refusal existed only where the tool happened to be present,
which is the wrong way round for anything about not destroying somebody's card.

Run: `python3 -m pytest tests/test_nfc_skill.py`
"""

from __future__ import annotations

import subprocess
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa.skills import nfc  # noqa: E402


def record(tnf: int, typ: bytes, payload: bytes, short: bool = True,
           ident: bytes = b"") -> bytes:
    """One NDEF record with its lengths computed, never typed by hand.

    Hand-typed fixtures are how the MIME and long-record cases below were
    wrong the first time: three of them had a length field that disagreed with
    the payload, and the decoder was right every time.
    """
    header = 0x80 | 0x40 | tnf
    if short:
        header |= 0x20
    body = bytes([len(typ)])
    body += bytes([len(payload)]) if short else len(payload).to_bytes(4, "big")
    if ident:
        header |= 0x10
        body += bytes([len(ident)])
    return bytes([header]) + body + typ + ident + payload


NTAG213_LINK = bytes.fromhex("e1010c55046578616d706c652e6f7267")












class TestTheConsentGate:
    def test_the_key_is_in_the_schema_so_a_person_can_turn_it_on(self):
        """A gate that names a switch nobody can set is a permanent refusal
        wearing the shape of a permission - this repo has shipped that twice.

        **The default is asserted against the preset, not against a remembered
        `false`.** It used to read `assert match.group(1) == "false", "a new
        consent key must default off"`, and that rule was superseded: `cf525453`
        ("consent defaults: a fresh install now arrives as the `everyday` preset,
        not as nothing") flipped 33 keys off->on because *the preset enables
        them*, and `nfc-enabled` is one of them - `EVERYDAY("nfc-enabled")` is
        **True** today, and the global invariant holds all 46 action permissions
        to it.

        Two wrong attributions came before the right one, and both are worth the
        space. `nfc-enabled` reads `true` while its own description ends *"Off by
        default"*, so the first reading was that the schema contradicts itself -
        but **31 keys match that pattern**, including ones deliberately turned
        on, because the phrase is boilerplate in the descriptions. The second was
        that `5be170b` ("defaults: 16 more on") flipped it as an unlisted
        side-effect; measured, that commit flipped **18** and listed **18** (its
        title says "sixteen"), and did not touch nfc at all. The flip is
        `cf525453`'s, documented, on purpose.

        **So the test that encodes the obsolete rule is the one that is wrong**,
        and asserting a remembered `false` here would have reverted a deliberate
        decision while looking like a product fix.
        """
        import pathlib

        from shani_chronoa import capabilities

        schema = pathlib.Path("usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml").read_text()
        # Still the test's own first claim: the switch has to exist at all.
        assert f'<key name="{nfc._CONSENT_KEY}" type="b">' in schema, (
            f"{nfc._CONSENT_KEY} is not in the schema at all, so nobody can set it")

        # **Read the default the way the app reads it.** GSettings loads
        # `gschemas.compiled`, which is tracked separately from the `.xml`, so
        # parsing the XML would pass while the app saw something else - the trap
        # `cf525453` records, where the flip measured `False` and looked like it
        # had not worked.
        gi = pytest.importorskip("gi")
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        from shani_chronoa.config import SCHEMA_ID

        source = Gio.SettingsSchemaSource.new_from_directory(
            str(pathlib.Path("usr/share/glib-2.0/schemas")), None, False)
        found = source.lookup(SCHEMA_ID, True)
        if found is None:
            pytest.skip("the %s schema is not readable here" % SCHEMA_ID)
        default = found.get_key(nfc._CONSENT_KEY).get_default_value().get_boolean()

        every_day = capabilities.PRESETS["everyday"][2](nfc._CONSENT_KEY)
        assert default is every_day, (
            f"{nfc._CONSENT_KEY} ships {default} but the everyday preset says "
            f"{every_day} - the schema and capabilities.PRESETS have drifted, "
            f"which is the two-files-apart failure this file was written for")

    def test_it_is_declared_gated_and_off_by_default(self):
        from shani_chronoa import capabilities
        assert capabilities.GATED.get("nfc") == nfc._CONSENT_KEY

    def test_every_action_is_refused_with_the_key_off(self, monkeypatch):
        class _Off:
            def get_bool(self, key, default=False):
                return default
        monkeypatch.setattr(nfc, "ChronoaConfig", _Off)
        for action in nfc._ACTIONS:
            out = nfc._run({"action": action, "url": "https://x.org"})
            assert nfc._CONSENT_KEY in out, (action, out)

    def test_the_sweep_can_drive_it(self):
        """`tests/test_question_presenter.py` calls `_consent(config)` and
        unpacks two values. A different shape breaks that sweep with a
        TypeError rather than a failure anyone reads."""
        allowed, reason = nfc._consent(type("C", (), {"get_bool": lambda s, k, d=False: False})())
        assert allowed is False
        assert nfc._CONSENT_KEY in reason


class TestWritingRefusesWhatWouldDestroySomething:
    @pytest.mark.parametrize("named", [
        "bank card", "transit pass", "hotel key", "credit card", "fob",
        "mifare classic", "door card", "smartcard",
    ])
    def test_naming_something_that_is_not_a_sticker_is_refused(self, monkeypatch, named):
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        calls: list[list] = []
        monkeypatch.setattr(nfc._engine, "_run_tool",
                            lambda argv, timeout=20: calls.append(argv))
        out = nfc._run({"action": "write", "url": "https://x.org", "tag": named})
        assert "Refusing" in out, out
        assert "irreversible" in out
        assert calls == [], f"the tool ran despite the refusal: {calls}"

    def test_the_refusal_happens_before_the_tool_is_looked_for(self, monkeypatch):
        """Otherwise the refusal only exists where libnfc is installed, and on a
        machine without it the answer to 'write to my bank card' is a missing
        package."""
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        monkeypatch.setattr(nfc.shutil, "which", lambda name: None)
        out = nfc._run({"action": "write", "url": "https://x.org", "tag": "bank card"})
        assert "Refusing" in out and "not installed" not in out, out

    @pytest.mark.parametrize("named", ["ultralight", "NTAG213", "sticker", "ntag", "ntag21x"])
    def test_a_sticker_is_accepted(self, monkeypatch, named):
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            nfc._engine, "_run_tool",
            lambda argv, timeout=20: subprocess.CompletedProcess(argv, 0, "", ""))
        out = nfc._run({"action": "write", "url": "https://x.org", "tag": named})
        assert "Refusing" not in out, out
        assert "Wrote a link" in out, out

    def test_a_refused_write_does_not_reach_the_tool_at_all(self, monkeypatch):
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        ran: list[list] = []
        monkeypatch.setattr(nfc._engine, "_run_tool",
                            lambda argv, timeout=20: ran.append(argv) or None)
        nfc._run({"action": "write", "url": "https://x.org", "tag": "bank card"})
        assert ran == []

    def test_a_failed_write_is_reported_as_a_failure_not_a_success(self, monkeypatch):
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        monkeypatch.setattr(nfc.shutil, "which", lambda name: "/usr/bin/" + name)
        monkeypatch.setattr(
            nfc._engine, "_run_tool",
            lambda argv, timeout=20: subprocess.CompletedProcess(argv, 1, "", "card is not Ultralight"))
        out = nfc._run({"action": "write", "url": "https://x.org"})
        assert "not written" in out and "Nothing was changed" in out, out
        assert "Wrote a link" not in out

    def test_nothing_to_write_says_so_and_names_the_argument(self, monkeypatch):
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        out = nfc._run({"action": "write"})
        assert "url" in out and "Nothing was written" in out, out


class TestTheHonestNegative:
    """A laptop with no NFC reader is the common case, not an edge case."""


    def test_the_package_hints_that_name_libnfc_are_really_there(self):
        """`files.tool_missing` falls back to the literal string
        "the package that provides it" for a binary with no hint, which produced
        the sentence *"it comes from the 'the package that provides it' package"*.
        libnfc ships all ten of its tools as one package, so every one of them
        maps to the same answer - and each is listed here so a new binary is
        noticed rather than assumed covered.
        """
        from shani_chronoa import files

        for binary in ("nfc-list", "nfc-scan-device", "nfc-mfultralight",
                       "nfc-mfclassic", "nfc-emulate-forum-tag4", "nfc-jewel",
                       "nfc-relay-picc"):
            assert files._PACKAGE_HINTS.get(binary) == "libnfc", (
            f"{binary} has no package hint, so tool_missing would print a "
            "placeholder instead of a package name"
        )








class TestTheSkillShape:
    def test_every_action_is_in_the_schema_enum(self):
        """A tool the schema does not offer is a tool the model cannot call -
        `bluetooth_call` carried a `route` action the code accepted and the
        schema omitted, so it was unreachable."""
        enum = nfc.SCHEMA["function"]["parameters"]["properties"]["action"]["enum"]
        assert set(nfc._ACTIONS) == set(enum), (nfc._ACTIONS, enum)

    def test_an_unknown_action_lists_the_ones_that_exist(self, monkeypatch):
        """Needs consent granted, or the gate answers first and the assertion
        passes on the *refusal* rather than on the action list - which is how a
        test ends up covering something other than it names."""
        class _On:
            def get_bool(self, key, default=False):
                return True
        monkeypatch.setattr(nfc, "ChronoaConfig", _On)
        out = nfc._run({"action": "beam"})
        for action in nfc._ACTIONS:
            assert action in out, (action, out)
        assert "beam" in out

    def test_arguments_that_are_not_an_object_are_refused(self):
        assert "Nothing was read or written" in nfc._run(["read"])

    def test_the_non_numeric_duration_is_refused(self):
        class _On:
            def get_bool(self, key, default=False):
                return True
        original = nfc.ChronoaConfig
        nfc.ChronoaConfig = _On
        try:
            out = nfc._run({"action": "read", "seconds": "soon"})
            assert "not a number of seconds" in out, out
        finally:
            nfc.ChronoaConfig = original

    def test_it_is_discovered_and_has_a_handler(self):
        from shani_chronoa.skills import discover_skills
        tools, handlers = discover_skills()
        assert "nfc" in [t["function"]["name"] for t in tools]
        assert "nfc" in handlers