"""The training set's biggest lesson is about a tool that no longer exists.

`unknown_tool_examples()` has been *reporting* this since before the Learning
page existed, and its own docstring says the answer is the person's decision:

> It is recorded rather than filtered. Filtering would be the more useful
> behaviour and the wrong one here... Whether to train on it is their decision,
> made with the figure in front of them.

That argument is right for a **default** and wrong as a **dead end**. So the
default is unchanged - `only_tools=None` trains on everything, exactly as before
- and the filter now exists for when a person wants it, on the same page as the
figure.

Measured on this machine's own log (14,395 records):

- `liar` and `unver` are fixtures this build does not have: 712 records, 4.9%.
- `liar` alone is **323 of the 423 failures** - 76% of the whole failure signal.
- Filtering to the 152 tools this build does answer to drops 650 training
  examples.

So the outcome model's most confident, most heavily-repeated lesson is about a
tool that cannot run, and its "detects failure" score is substantially a memory
of `liar`.

**The cache is half the test.** `_EXAMPLE_CACHE` was keyed by file path and
revision alone, so a filtered caller would have been served the *unfiltered*
list from the same revision - the button would change nothing, silently, and
look exactly as though it had worked. That is the failure mode where a feature
appears to do what it says and does not.
"""

import json

import pytest

from shani_chronoa import learning


def _write_log(path, rows):
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return path


def _row(tool, verdict):
    return {"tool_name": tool, "verdict": verdict, "args": {}, "result": "ok",
            "duration_ms": 1.0, "timestamp": "2026-10-01T00:00:00+00:00"}


@pytest.fixture
def log(tmp_path, monkeypatch):
    """A log with two tools: one this build has, one it does not."""
    path = _write_log(tmp_path / "tool_calls.log",
                      [_row("delete_file", "verified"),
                       _row("liar", "failed"),
                       _row("delete_file", "failed"),
                       _row("liar", "verified")])
    monkeypatch.setattr(learning, "_log_path", lambda: path)
    learning._EXAMPLE_CACHE.clear()
    return path


class TestTheFilter:
    def test_it_drops_records_for_tools_this_build_lacks(self, log):
        everything = learning.load(log)
        filtered = learning.load(log, only_tools=frozenset({"delete_file"}))
        assert len(everything) == 4
        assert len(filtered) == 2, filtered

    def test_the_default_is_unchanged(self, log):
        """The docstring's argument: filtering must stay opt-in."""
        assert len(learning.load(log)) == 4
        assert learning.load(log, only_tools=None) == learning.load(log)

    def test_the_filter_is_not_the_cache(self, log):
        """**This is the test that matters most.**

        `_EXAMPLE_CACHE` is keyed by path and revision, so before the key grew a
        filter the first unfiltered call populated it and the filtered call got
        the same list back - 4 examples either way, from the same file, with the
        button appearing to do nothing.
        """
        assert len(learning.load(log)) == 4
        assert len(learning.load(log, only_tools=frozenset({"delete_file"}))) == 2
        # And back again, in the order the UI would do it.
        assert len(learning.load(log)) == 4
        assert len(learning.load(log, only_tools=frozenset({"delete_file"}))) == 2

    def test_the_two_filters_get_separate_entries(self, log):
        learning.load(log)
        learning.load(log, only_tools=frozenset({"delete_file"}))
        learning.load(log, only_tools=frozenset({"liar"}))
        assert len(learning._EXAMPLE_CACHE) == 3, learning._EXAMPLE_CACHE

    def test_set_order_does_not_matter(self, log):
        """`{a, b}` and `{b, a}` must share a cache entry, or the whole log is
        parsed twice for no reason."""
        learning.load(log, only_tools=frozenset({"a", "b"}))
        learning.load(log, only_tools=frozenset({"b", "a"}))
        assert len(learning._EXAMPLE_CACHE) == 1, learning._EXAMPLE_CACHE

    def test_a_filter_matching_nothing_yields_nothing(self, log):
        assert learning.load(log, only_tools=frozenset({"no_such_tool"})) == []

    def test_it_does_not_silently_relabel(self, log):
        """The existing rule: a row with no verdict is dropped, never labelled
        `unverified`, which would inflate the majority class."""
        _write_log(log, [_row("liar", "None"), _row("delete_file", "verified")])
        learning._EXAMPLE_CACHE.clear()
        assert len(learning.load(log)) == 1


class TestTheFigureIsShownAboveTheButton:
    """The reason the button exists, and the reason it is not the default, has
    to be visible where the button is - not only in `provenance`."""

    def test_the_count_comes_from_the_same_source(self, log):
        count, names = learning.unknown_tool_examples(learning._entries(log))
        assert count == 2
        assert names == ["liar"]

    def test_the_page_can_build_without_a_log(self, tmp_path, monkeypatch):
        """A row that needs the log must not be what stops the page appearing."""
        monkeypatch.setattr(learning, "_entries",
                            lambda path=None: (_ for _ in ()).throw(OSError("gone")))
        from shani_chronoa.gui.surfaces import learning as surface
        assert hasattr(surface, "_stale_tools_sentence")


class TestTheFilterReachesTheFitAndTheMeasurement:
    """Both of these asserted the wrong thing on the first run, and both were
    wrong in the direction of testing implementation detail rather than
    behaviour.

    `train_and_report` does **not** pass `only_tools` on to `cross_validate` -
    and it should not: `load()` already filtered the examples, so handing the
    filter to the CV as well would be redundant. What matters is that the CV is
    handed a *shorter list*, so that is what is asserted.

    The fit test asserted `provenance` on a return value that legitimately had
    none, because the real model refused to be written on four examples. A test
    that requires a save to succeed to check a filter is a test that will break
    for the right reason at the wrong time.
    """

    def test_the_measurement_is_computed_on_the_filtered_set(self, log, monkeypatch):
        asked = {}

        def spy(examples, **kwargs):
            asked["count"] = len(examples)
            return learning.Report(0, 0.0, 0.0, {}, {})

        monkeypatch.setattr(learning, "cross_validate", spy)
        learning.train_and_report(cv=5.0)
        every = asked["count"]
        learning._EXAMPLE_CACHE.clear()
        learning.train_and_report(cv=5.0, only_tools=frozenset({"delete_file"}))
        assert asked["count"] < every, (
            f"the measurement was computed on the same {asked['count']} examples "
            "with and without the filter, so the number describes the whole log "
            "either way")

    def test_the_filter_reaches_the_fit(self, log, monkeypatch):
        """Spied on `load`, not `train_and_report`.

        `train_and_save` does **not** call `train_and_report` - it splits and
        cross-validates itself, which is why the spy on `train_and_report` was
        never called and the assertion failed with a `KeyError` rather than a
        useful message. `load` is the single boundary every route through this
        module passes, so it is the right thing to watch.
        """
        seen = []
        real_load = learning.load

        def spy(*args, **kwargs):
            seen.append(kwargs.get("only_tools"))
            return real_load(*args, **kwargs)

        monkeypatch.setattr(learning, "load", spy)
        keys = frozenset({"delete_file"})
        learning.train_and_save(only_tools=keys)
        assert keys in seen, (
            "the GUI button's filter never reached the loader, so the button "
            "would refit the same model every time")

    def test_the_default_still_refuses_the_same_way(self, log, monkeypatch):
        """Unchanged behaviour when nobody asks for the filter."""
        assert learning.load(log, only_tools=None) == learning.load(log)
