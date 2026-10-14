"""A refusal names the row its Settings screen shows, and never the raw key.

**The defect.** `config.sense_allowed_reason()` is the sentence a person reads
when Chronoa declines something. It ended, for every sense, with the gsettings
key spelled out - *"the web sense is turned off (enable 'web-sense-enabled')"*
- while the row in Settings that switches it on is titled **"Web search"**. So
the instruction the refusal gave pointed at a spelling no screen anywhere uses,
and a person following it was hunting for a switch that does not exist under
that name. Measured 2026-10-14: **47 hand-written refusals across 20 files** all
named a key the same way, and **36 of the 53 sense consent keys had no friendly
label anywhere at all**.

**Why the information was already there.** `capabilities.GATE_NAMES` maps a
consent key to the label its Settings row carries - 87 entries, covering all 57
distinct keys gating a tool, **zero unlabelled**. It was written for the Help
window and the suggestion chips and reached nothing a person reads when they are
refused. `Capability.gate_label()` has always answered the question, but it is a
*method*, so the 46 sites holding only a key - every sense refusal and the
hand-written ones across `skills/` - could not call it. `capabilities.gate_label`
is that answer as a function.

**The 36-key gap, and why it is not a hole any more.** A sense with no tool of
its own has no entry in `GATE_NAMES`, because that table is keyed on tools.
`SENSE_TITLES` carries what the Senses *panel* calls each of them - and it was
trapped in `gui/surfaces/common.py`, which imports GTK, while the refusal is
reached from a subprocess that has no display. So the table moved to
`capabilities` (which imports only `re` and `dataclasses`) and the chain is: the
Settings row's wording, then the sense panel's title, then the key itself.

**The trailing full stop.** This string is a *clause* in sentences built by
thirty-odd callers - `"Not shown: {reason}. It reveals ..."` - so the old form
ended without one and every caller supplied its own. The first version of this
change added one and produced *"...to use it.. It reveals when you are at the
machine"*, which `test_sense_idle` caught on the next run.

Three populations, because a scan that treats them alike reports noise: a
**tool** key (in `GATE_NAMES`), a **sense** key with no tool (resolved by
`SENSE_TITLES`), and a key **neither table carries** - which must still be named,
and must fall back to itself rather than to something invented.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from shani_chronoa.capabilities import GATE_NAMES, SENSE_TITLES, gate_label, sense_title
from shani_chronoa.config import ChronoaConfig, _consent_key_for
from shani_chronoa.senses import discover_senses


class _Refused(ChronoaConfig):
    """A real config whose every consent key reads false - the refusal path.

    A subclass of the real thing rather than a stand-in, because
    `sense_allowed_reason` calls `get_bool`, `_default_on` and the alias table,
    and a fake shaped like the call rather than like the config is the fixture
    this file's own header warns about.
    """

    def get_bool(self, key, default=False):  # noqa: D102
        return False


@pytest.fixture(scope="module")
def refused():
    return _Refused()


@pytest.fixture(scope="module")
def senses():
    return sorted(discover_senses())


# -- the population -------------------------------------------------------

ALL_SENSES = sorted(discover_senses())


class TestEverySenseRefusalNamesItsRow:
    """All 53, not a sample, because the gap was exactly in the unsampled ones.

    The 36 keys with no `GATE_NAMES` entry are the population a hand-written list
    of examples would have missed - `hearing`, `ocr`, `cpu`, `idle` and the rest
    are the ones that stayed wrong.
    """

    @pytest.mark.parametrize("name", ALL_SENSES)
    def test_the_label_is_present_and_the_key_is_not(self, refused, name):
        said = refused.sense_allowed_reason(name)
        key = _consent_key_for(name)
        assert key is not None, f"{name} resolves to no consent key"
        assert key not in said, (
            f"{name} named its raw gsettings key, which no settings window "
            f"spells that way: {said!r}"
        )
        # Something friendly is present, and it is quoted - the sentence's own
        # shape is what the label sits inside.
        assert "\u201c" in said and "\u201d" in said, said
        assert gate_label(key) in said, (
            f"{name} did not name the row {gate_label(key)!r}: {said!r}"
        )

    def test_the_population_is_the_whole_registry(self, senses):
        """A parameter list that quietly shrinks is a witness that stops biting."""
        assert len(ALL_SENSES) == len(senses) == 53, (
            f"{len(ALL_SENSES)} listed against {len(senses)} registered"
        )


class TestTheLabelIsTheOneAScreenShows:
    """Not merely *a* label - the one the screen shows.

    An invented label would pass the "key is absent" half while pointing at a
    switch nobody can find, which is the same defect wearing a different costume.
    So the sentence's label is compared **equal** to the table entry, not merely
    contained in it.
    """

    @pytest.mark.parametrize("name", ["web", "vision", "hwmon", "location"])
    def test_a_tool_key_takes_the_settings_rows_own_wording(self, refused, name):
        key = _consent_key_for(name)
        said = refused.sense_allowed_reason(name)
        assert f"\u201c{GATE_NAMES[key]}\u201d" in said, said

    @pytest.mark.parametrize("name", ["hearing", "ocr", "cpu", "idle"])
    def test_a_sense_with_no_tool_takes_the_panels_title(self, refused, name):
        """The 36 keys `GATE_NAMES` does not carry - the population that stayed
        raw until the table moved."""
        key = _consent_key_for(name)
        assert key not in GATE_NAMES, (
            f"{key} is in GATE_NAMES now, so this case no longer exercises the "
            f"SENSE_TITLES branch"
        )
        said = refused.sense_allowed_reason(name)
        assert f"\u201c{sense_title(name)}\u201d" in said, said
        # The accessor and the table agree, so this is not a test of a second
        # spelling of the same fact.
        assert sense_title(name) == SENSE_TITLES[name]

    def test_the_two_tables_disagree_on_purpose_and_that_is_fine(self):
        """They are different screens with different wordings, and neither is a
        copy of the other - which is why the chain has an order at all."""
        assert GATE_NAMES is not SENSE_TITLES
        # `hwmon` is in both, and the two wordings differ: "Temperatures, fans
        # and power" in Settings against "Fans and temperatures" in the panel.
        assert GATE_NAMES["hwmon-sense-enabled"] != SENSE_TITLES["hwmon"]
        assert GATE_NAMES["hwmon-sense-enabled"] in refused_reason("hwmon")


def refused_reason(name, refused=None):
    """`sense_allowed_reason` for a fresh refused config."""
    return (refused or _Refused()).sense_allowed_reason(name)


class TestTheFallbackChain:
    """`gate_label` on its own, because the chain is the whole fix.

    Each rung is asked directly: a key in the first table, a key only the second
    can answer, and a key neither can. The last must fall back to the **key**,
    never to a guess - an invented label would be the confident wrong answer
    this repository keeps recording.
    """

    def test_the_settings_row_wins(self):
        assert gate_label("hwmon-sense-enabled") == "Temperatures, fans and power"

    def test_a_sense_key_is_resolved_by_the_panel_table(self):
        assert gate_label("hearing-sense-enabled") == "Hearing"
        assert gate_label("ocr-sense-enabled") == "Text in images"

    def test_a_key_neither_table_carries_falls_back_to_itself(self):
        """A key with no row must still be named. An unnamed key is the honest
        ceiling; a guessed label is worse than none."""
        assert gate_label("no-such-key-enabled") == "no-such-key-enabled"

    def test_the_sense_suffix_is_what_reaches_the_second_table(self):
        """`hearing` is the module name; the key is the name plus the suffix."""
        assert gate_label("hearing") == "hearing", (
            "a bare module name is not a key and must not be resolved as one"
        )

    def test_the_over_fix_returns_a_humanised_key_for_everything(self):
        """Making the first rung unreachable is the same defect, arrived at from
        the other direction: every tool key loses its Settings wording."""
        assert gate_label("web-sense-enabled") == "Web search"


class TestTheSentenceComposes:
    """The punctuation, because this string is a clause in other sentences.

    `test_sense_idle` caught the trailing full stop the first version of this
    change shipped: thirty-odd callers append their own, and the old form ended
    without one. Read the call sites before choosing the punctuation.
    """

    @pytest.mark.parametrize(
        "name", ["web", "hearing", "idle", "hwmon"]
    )
    def test_no_trailing_full_stop_and_no_doubled_one(self, refused, name):
        said = refused.sense_allowed_reason(name)
        assert not said.endswith("."), said
        assert ".." not in said, said

    def test_a_caller_composing_after_it_still_reads_as_one_sentence(self):
        """The shape `senses/idle.py` and `senses/ocr.py` build."""
        reason = refused_reason("idle")
        composed = f"Not reading the idle clock: {reason}. It reveals when you " \
                   f"are at the machine, which is personal."
        assert ".. " not in composed, composed
        assert "Not reading the idle clock: the idle sense is turned off" in composed
        assert "Switch on “Idle time” in Settings to use it. It reveals" in composed

    def test_the_sentence_names_the_sense_as_well_as_the_row(self):
        """Both halves: the sense says what declined, the row says what to do."""
        said = refused_reason("web")
        assert "web sense" in said
        assert "Web search" in said


class TestTheGrantedCaseIsUnchanged:
    """A control, so the refusal branch is not the only thing measured.

    With every key granted the reason is the privacy-mode sentence, which is
    unchanged by this work and must stay so.
    """

    def test_a_granted_networked_sense_names_privacy_mode(self):
        class Granted(ChronoaConfig):
            def get_bool(self, key, default=False):  # noqa: D102
                return True

        assert "privacy mode" in Granted().sense_allowed_reason("web")

    def test_a_granted_local_sense_is_permitted(self):
        class Granted(ChronoaConfig):
            def get_bool(self, key, default=False):  # noqa: D102
                return True

        assert Granted().sense_allowed_reason("hwmon") == ""

    def test_an_unknown_sense_is_still_refused_by_name(self, refused):
        assert refused.sense_allowed_reason("no-such-sense") == (
            "there is no 'no-such-sense' sense"
        )


class TestTheTableHasOneDefinition:
    """The consolidation itself, because it has no other witness.

    `SENSE_TITLES` lived in `gui/surfaces/common.py` until 2026-10-14 and moved
    to `capabilities` so the refusal path could read it. **Nothing stops a second
    copy appearing in the old home**, which is the `wpctl`-three-copies shape this
    repository records four times - and the failure is silent, because the two
    copies agree the day they are written and drift the week after.

    A source-level check is the only instrument: both tables agree *today*, and a
    behavioural test cannot tell one table from two that currently match.
    """

    def test_exactly_one_definition_in_the_shipped_package(self):
        import shani_chronoa  # noqa: F401 - the import makes the path real

        package = pathlib.Path(shani_chronoa.__file__).parent
        defined = sorted(
            str(path.relative_to(package.parent.parent))
            for path in package.rglob("*.py")
            if re.search(r"^SENSE_TITLES\s*=\s*\{", path.read_text(), re.M)
        )
        assert defined == ["shani-chronoa/shani_chronoa/capabilities.py"], (
            f"SENSE_TITLES is defined in {len(defined)} places: {defined}"
        )
    def test_the_old_home_does_not_re_export_the_table(self):
        """**A re-export is how the second copy returns.**

        The first version of this change kept `from ...capabilities import
        SENSE_TITLES` in `gui/surfaces/common.py` so every existing caller kept
        working. It was removed and the two real call sites repointed instead:
        pyflakes reports a re-export as an unused import, and the workaround for
        that is a `__all__` entry - which is one line away from a second
        definition that nobody notices. So the old home is asserted to **not**
        carry the name at all, which is the shape that cannot drift.
        """
        import shani_chronoa.gui.surfaces.common as common

        assert not hasattr(common, "SENSE_TITLES"), (
            "gui/surfaces/common.py carries SENSE_TITLES again - a re-export "
            "today and a second copy tomorrow"
        )
        assert not hasattr(common, "sense_title"), (
            "gui/surfaces/common.py carries sense_title again"
        )

    def test_the_two_gui_call_sites_use_the_moved_table(self):
        """The two panels that draw sense rows, checked at the name they read.

        `gui/surfaces/senses.py` and `gui/surfaces/privacy.py` both called
        `common.sense_title`; both reach `capabilities` now. A string read is the
        wrong instrument here (it matches the comment explaining the move), so
        each module's own binding is asked.
        """
        import shani_chronoa.capabilities as capabilities
        import shani_chronoa.gui.surfaces.privacy as privacy
        import shani_chronoa.gui.surfaces.senses as senses_panel

        for module in (senses_panel, privacy):
            assert module.sense_title is capabilities.sense_title, (
                f"{module.__name__} bound its own sense_title"
            )


class TestTheRetiredAliasRuleStillHolds:
    """The compatibility mechanism, which the label must not disturb.

    A retired key still grants its successor and is still **not named** in the
    refusal - naming one sends the user to a row that no longer exists. That is
    the rule the comment above the branch states, and this pins it from the
    other side of the change.
    """

    def test_a_retired_alias_is_absent_from_the_refusal(self, gsettings_env):
        from shani_chronoa.config import _SENSE_CONSENT_ALIASES

        for sense, aliases in _SENSE_CONSENT_ALIASES.items():
            said = refused_reason(sense)
            for alias in aliases:
                assert alias not in said, (
                    f"{sense} named the retired key {alias}: {said!r}"
                )
