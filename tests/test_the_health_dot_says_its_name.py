"""The health dot says its name out loud.

The dot is the sidebar's loudest signal - a column of green, orange and grey
beside every panel - and it had no key anywhere. No legend, and the row's
accessible label was the title alone, so a newcomer saw a colour they could not
read and a screen-reader user was told the panel is called "Memory" and nothing
at all about whether it is healthy.

**Six pixels per row turns a list into a dashboard** is what `set_status`'s own
docstring says it did. A dashboard nobody can read is a list with confetti on
it.
"""

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
SIDEBAR = REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/sidebar.py"


class TestTheDotIsNotOnlyColour:
    def test_set_status_announces(self):
        """**The wiring, because a correct helper nothing calls is a dead
        control** - the shape this repository records for `Registry.register`,
        `Gateway.may_execute` and `refused_tools`.
        """
        body = SIDEBAR.read_text(encoding="utf-8")
        start = body.index("def set_status")
        chunk = body[start:body.index("\n    def ", start)]
        assert "_announce_status(key, status)" in chunk, (
            "set_status returns without announcing, so the status reaches the "
            "screen and nothing else")

    def test_it_reaches_both_readers(self):
        """**Both, because they are read by different people.**

        The tooltip is what a sighted reader who notices a colour they do not
        recognise hovers to ask. The accessible label is the only thing a screen
        reader ever says about the row. Updating one and not the other leaves
        half the users with no key, and the half is whichever one nobody tested.
        """
        body = SIDEBAR.read_text(encoding="utf-8")
        start = body.index("def _announce_status")
        # **To the next top-level `def`, not to the next blank line.** The first
        # version sliced at `\n\n`, which lands inside the docstring - so both
        # assertions below failed for a reason in the test and the message
        # pointed at the code, which is exactly the shape this repository warns
        # about: a probe that measures nothing reads the same as one that is
        # broken.
        end = body.index("\ndef ", start + 1)
        chunk = body[start:end]
        assert "AccessibleProperty.LABEL" in chunk, (
            "the status is not in the accessible label, so a screen reader is "
            "told the panel's name and nothing about its health")
        assert "set_tooltip_text" in chunk, (
            "the status is not in the tooltip, so a sighted reader who cannot "
            "read the colour has nowhere to ask what it means")

    def test_it_invents_no_vocabulary_of_its_own(self):
        """**The status values are already words** - "ok", "attention",
        "unknown" - so a status nobody thought to name does not silently
        become a fourth colour with a fourth meaning.
        """
        body = SIDEBAR.read_text(encoding="utf-8")
        start = body.index("def _announce_status")
        # **To the next top-level `def`, not to the next blank line.** The first
        # version sliced at `\n\n`, which lands inside the docstring - so both
        # assertions below failed for a reason in the test and the message
        # pointed at the code, which is exactly the shape this repository warns
        # about: a probe that measures nothing reads the same as one that is
        # broken.
        end = body.index("\ndef ", start + 1)
        chunk = body[start:end]
        assert "f\"{title} - {status}\"" in chunk, (
            "the announcement builds its own words rather than passing the "
            "status through, so the two can drift apart")
