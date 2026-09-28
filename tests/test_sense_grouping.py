"""Senses are grouped by what a person wants, not by where the module sorts.

Seventeen switches in one alphabetical list is the registry printed out. The
grouping exists to answer the only question someone opening this has - "which
of these do I turn on?" - and the invariants below are the ones that make it
safe to reorganise, plus the one that makes the bulk action legitimate.
"""

import sys

import pytest

sys.path.insert(0, "usr/lib/shani-chronoa")
from shani_chronoa.senses import discover_senses  # noqa: E402
from shani_chronoa.settings_window import (  # noqa: E402
    SENSE_CATEGORIES,
    SENSE_LABELS,
    SUGGESTED,
)

# Senses that observe the room, the machine, or who is using what. Excluded
# from the suggested baseline on their own merits: they are opt-in because
# they watch, not because they are unusual.
WATCHES_THE_WORLD = {
    "camera", "vision", "contention", "privilege", "rfsense",
    "thermal", "thermalgrid", "hwmon",
    # `power` reads the machine's own hardware, like hwmon does, but it is NOT
    # in this set: it ships enabled by default. Battery charge and wear are the
    # same class of fact as a disk-free reading and never leave the machine,
    # whereas hwmon and thermalgrid are opt-in because the project treats
    # observing the machine as worth a deliberate yes.
    "power", "storage", "link", "cpu", "smart", "gpu", "cooling",
}


@pytest.fixture(scope="module")
def registry():
    return discover_senses()


def _all_categorised():
    return [n for _t, _d, names in SENSE_CATEGORIES for n in names]


class TestTheGroupingCoversTheRegistryExactly:
    def test_every_sense_is_in_a_group(self, registry):
        missing = sorted(set(registry) - set(_all_categorised()))
        assert not missing, (
            f"these senses are in no category, so they would not be rendered at "
            f"all: {missing}"
        )

    def test_no_sense_is_in_two_groups(self, registry):
        names = _all_categorised()
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert not dupes, f"a sense in two categories would get two switches: {dupes}"

    def test_every_categorised_sense_exists(self, registry):
        phantom = sorted(set(_all_categorised()) - set(registry))
        assert not phantom, (
            f"SENSE_CATEGORIES names senses that are not registered, so those "
            f"rows would never render: {phantom}"
        )

    def test_categories_are_in_the_declared_order(self):
        """The order is the argument - what most people want first - so it is
        part of the data, not an accident of dict order."""
        assert [t for t, _d, _n in SENSE_CATEGORIES] == [
            "Talking to Chronoa", "Looking at things", "Getting work done",
            "The screen", "Network and wireless", "The machine itself",
            "Security and privacy", "Model capability",
        ]

    def test_no_group_is_empty(self):
        empty = [t for t, _d, names in SENSE_CATEGORIES if not names]
        assert not empty, f"groups with no senses: {empty}"

    def test_no_category_title_collides_with_another_section(self):
        """The window also has "Privacy and network", "Voice", "Models",
        "System" and "In effect right now". A sense category sharing a title
        with one of those renders two identically-headed groups, and the user
        cannot tell which one a row belongs to."""
        others = {
            "Privacy and network", "Free cloud providers",
            "Cloud providers that require a key", "Voice", "Models",
            "In effect right now", "System",
        }
        clashes = sorted({t for t, _d, _n in SENSE_CATEGORIES} & others)
        assert not clashes, (
            f"a sense category shares its title with a non-sense section, so "
            f"the window shows two identically-headed groups: {clashes}"
        )

    def test_no_two_categories_share_a_title(self):
        titles = [t for t, _d, _n in SENSE_CATEGORIES]
        dupes = sorted({t for t in titles if titles.count(t) > 1})
        assert not dupes, f"duplicate category titles: {dupes}"

    def test_every_category_has_a_description(self):
        for title, desc, _names in SENSE_CATEGORIES:
            assert desc, f"category {title!r} has no description"


class TestTheSuggestedBaselineIsConservative:
    def test_every_suggested_sense_exists(self, registry):
        assert not [n for n in SUGGESTED if n not in registry], (
            "SUGGESTED names senses that do not exist, so the action would "
            "offer to enable nothing"
        )

    def test_it_excludes_everything_that_watches(self, registry):
        """The invariant that makes a one-click action defensible.

        A bulk enable of the everyday senses is a reasonable offer. A bulk
        enable that also turns on the camera, the thermal array, the RF motion
        sensor and the two that report who holds a dangerous capability is not,
        and the fact that it would be one click is exactly why it must not be.
        """
        overlap = sorted(set(SUGGESTED) & WATCHES_THE_WORLD)
        assert not overlap, (
            f"the suggested baseline would switch on senses that watch the "
            f"user or the machine without asking individually: {overlap}"
        )

    def test_it_is_not_empty(self):
        assert SUGGESTED, "an empty suggested baseline offers nothing"


class TestLabelsStillLineUp:
    def test_every_categorised_sense_is_labelled(self, registry):
        unlabelled = sorted(set(_all_categorised()) - set(SENSE_LABELS))
        assert not unlabelled, (
            f"these appear in a group but have no human title, so they render "
            f"as module names: {unlabelled}"
        )
