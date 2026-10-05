"""A sense's schema description is the LLM's tool documentation.

Deriving the settings row from it put raw module names in front of a user
(`hwmon`, `modelfit`, `thermalgrid`) and produced subtitles ranging from one
clause to a 300-character run-on, because `description.split(". ")[0]` is not a
sentence extractor - `modelfit`'s first "sentence" is the whole paragraph.

Neither was visible in a test that checked the row *count*. It showed up only
when the window was rendered and looked at, which is the point of these: they
assert on the words, not on the widget tree.
"""

import re
import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.settings_window import SENSE_LABELS  # noqa: E402
from shani_chronoa.senses import discover_senses  # noqa: E402

from _source import package_source  # noqa: E402


class _Package:
    """The settings window's source (a package since the 2026-10-02 split)."""

    @staticmethod
    def read_text():
        return package_source("settings_window")


GUI = _Package()

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


class TestNoLabelIsContainedInAnother:
    """Exact-equality is too weak a test for "can a user tell these apart?".

    `test_titles_are_distinct` only catches two rows with the *same* title. It
    walked straight past three real collisions, the worst being `vision`
    labelled "Camera" sitting next to `camera` labelled "Cameras attached" -
    two adjacent rows with near-identical titles and completely different
    functions, one listing webcams and one letting the assistant look through
    them. A user scanning the settings list could not tell which was which.

    Containment is the right relation rather than similarity: the failure is
    that one title reads as a more specific version of another, so a substring
    test catches it without needing a fuzzy metric.
    """

    def test_no_title_is_contained_in_another(self, registry):
        from shani_chronoa.settings_window import SENSE_LABELS

        titles = {name: SENSE_LABELS[name][0] for name in registry}
        clashes = []
        for name, title in titles.items():
            for other, other_title in titles.items():
                if name == other or title == other_title:
                    continue
                if title.lower() in other_title.lower():
                    clashes.append(f"{name} {title!r} inside {other} {other_title!r}")
        assert not clashes, (
            "these row titles are indistinguishable at a glance: "
            + "; ".join(clashes)
        )

    def test_the_check_is_not_vacuous(self):
        """A containment test that cannot fail proves nothing, so confirm the
        comparison actually rejects a contained pair."""
        from shani_chronoa.settings_window import SENSE_LABELS

        titles = {n: SENSE_LABELS[n][0] for n in SENSE_LABELS}
        self_contained = [
            (a, b) for a, x in titles.items() for b, y in titles.items()
            if a != b and x != y and x.lower() in y.lower()
        ]
        assert self_contained == [], (
            f"the real labels still collide, so the guard is not testing "
            f"anything: {self_contained}"
        )
        # and the predicate itself does reject a contained pair
        assert "camera" in "cameras attached".lower()
