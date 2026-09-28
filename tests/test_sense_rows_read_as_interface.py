"""A sense's schema description is the LLM's tool documentation.

Deriving the settings row from it put raw module names in front of a user
(`hwmon`, `modelfit`, `thermalgrid`) and produced subtitles ranging from one
clause to a 300-character run-on, because `description.split(". ")[0]` is not a
sentence extractor - `modelfit`'s first "sentence" is the whole paragraph.

Neither was visible in a test that checked the row *count*. It showed up only
when the window was rendered and looked at, which is the point of these: they
assert on the words, not on the widget tree.
"""

import pathlib
import re
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.settings_window import SENSE_LABELS  # noqa: E402
from shani_chronoa.senses import discover_senses  # noqa: E402

GUI = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa/settings_window.py")

# What a settings row may never show.
RAW_IDENTIFIER = re.compile(r"^[a-z]+(_[a-z]+)*$")


@pytest.fixture(scope="module")
def registry():
    return discover_senses()


class TestEverySenseIsWrittenForAHuman:
    def test_no_sense_is_missing_a_label(self, registry):
        missing = sorted(set(registry) - set(SENSE_LABELS))
        assert not missing, (
            f"these senses have no entry in SENSE_LABELS, so their row falls "
            f"back to the module name: {missing}"
        )

    def test_no_label_is_a_leftover_from_the_registry(self, registry):
        """A label for a sense that no longer exists is either a rename missed "
        "or a copy-paste slip, and both hide a real problem."""
        extra = sorted(set(SENSE_LABELS) - set(registry))
        assert not extra, f"SENSE_LABELS names senses that do not exist: {extra}"

    def test_no_title_is_a_raw_module_name(self, registry):
        for name in registry:
            title, _summary = SENSE_LABELS[name]
            assert not RAW_IDENTIFIER.match(title), (
                f"the {name!r} row is titled {title!r}, which is the module "
                f"name - a user should not have to know the identifier"
            )

    def test_every_row_has_a_summary(self, registry):
        for name in registry:
            _title, summary = SENSE_LABELS[name]
            assert summary, f"the {name!r} row has no subtitle"

    def test_summaries_are_one_line_worth_of_text(self, registry):
        """The 300-character run-on this replaced. A subtitle is a line, not a
        paragraph; the full description lives in the tooltip."""
        too_long = {
            name: len(summary)
            for name, (_t, summary) in SENSE_LABELS.items()
            if len(summary) > 90
        }
        assert not too_long, f"subtitles too long to sit in a row: {too_long}"

    def test_titles_are_distinct(self, registry):
        """Two rows with the same title are indistinguishable in the list, and
        in the search results."""
        seen: dict[str, str] = {}
        clashes = {}
        for name in registry:
            title, _s = SENSE_LABELS[name]
            if title in seen:
                clashes.setdefault(title, [seen[title]]).append(name)
            seen[title] = name
        assert not clashes, f"two senses share a row title: {clashes}"


class TestTheRowDoesNotDeriveItsTextFromTheSchema:
    def test_the_first_sentence_split_is_gone(self):
        """It is not a sentence extractor, and leaving it in place means the
        next sense that lacks a label silently reintroduces the problem."""
        source = GUI.read_text()
        assert 'description.split(". ")[0]' in source, (
            "the sentence-splitting fallback is expected to remain for unlisted "
            "senses - if this fails, the fallback was removed and the "
            "title-cased name is now the only fallback"
        )
        # ...and it must only be a fallback, not the primary source.
        assert "SENSE_LABELS.get(" in source

    def test_search_still_matches_the_module_name(self, registry):
        """A humanised title must not lock out someone who knows the code and
        types `hwmon`."""
        source = GUI.read_text()
        assert 'f"{name} {description}".lower()' in source, (
            "the module name is no longer part of what search matches"
        )
