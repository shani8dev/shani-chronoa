"""The Diff panel's docstring is a claim about the panel, so it is checked.

The file advertised **Accept/Reject per file or per hunk**, and an integration
with the `office_document` skill. Neither has a reachable path. The buttons are
built and gated on `applied=False`; `load_recent_changes` passes `True`; and
nothing in the tree ever assigns `on_accept_file`, so flipping the flag alone
would produce two buttons that fire into nothing. The office skill is named
nowhere in the module but in the sentence that claimed it.

A feature list is a promise, and this one had grown two controls nothing can
reach - which is why the panel reads as "here is your change and no way to act
on it" rather than as a surface that never had them.

The check reads the **advertised list** - the block under
`World-class diff features:` up to its first blank line - and not the whole
docstring, because the module is now allowed to explain the absence in prose
without tripping its own gate.
"""

from __future__ import annotations

import inspect

from shani_chronoa.gui.surfaces import diff as diff_mod

HEADING = "World-class diff features:"


def _advertised() -> str:
    """The feature bullets, and nothing else.

    Scoped deliberately: the absence note below the list names both removed
    capabilities, and a check over the whole docstring would refuse its own
    correction.
    """
    doc = inspect.getdoc(diff_mod) or ""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == HEADING:
            block: list[str] = []
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    break
                block.append(nxt)
            return "\n".join(block)
    raise AssertionError(
        f"the feature-list heading {HEADING!r} is gone, so this file cannot "
        f"check what it claims. Put it back, or teach the test the new shape."
    )


class TestTheDiffPanelAdvertisesOnlyWhatItBuilds:
    def test_the_feature_list_is_still_there_to_check(self):
        """A gutted docstring satisfies every absence check in this class.

        Written first because it is the control the other three lean on: if the
        list were emptied, all three would pass while the file claimed nothing
        and built nothing new.
        """
        block = _advertised()
        assert block.strip(), "the feature list is empty, so nothing is claimed"
        for claim in ("File list", "unified diff", "Summary", "Line selection"):
            assert claim in block, f"{claim!r} is no longer advertised"

    def test_it_does_not_advertise_accept_or_reject(self):
        """The buttons exist; no code path reaches them."""
        block = _advertised()
        assert "Accept" not in block
        assert "Reject" not in block

    def test_it_does_not_advertise_an_office_integration(self):
        """Named only in the sentence that claimed it."""
        assert "office" not in _advertised().lower()

    def test_the_absence_is_explained_rather_than_simply_gone(self):
        """Both claims are accounted for in prose.

        Without this the two tests above could be satisfied by deleting the
        explanation instead of by retiring the claim - and the next reader would
        have no idea whether the buttons were removed on purpose or lost.
        """
        doc = inspect.getdoc(diff_mod) or ""
        assert "Accept and Reject" in doc, "the removed control is not accounted for"
        assert "office-document" in doc, "the removed integration is not accounted for"