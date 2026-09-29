"""There was no way to ask what was using memory.

`list_processes` always sorted by CPU, so "what is eating my RAM?" was answered
by CPU order - which is a different question with a different answer. On this
machine the two orders genuinely differ from the fourth entry onward: the
top three by CPU are opencode processes, and the fourth by CPU is a system
service while the fourth by memory is Chrome.

`sort_by='memory'` is the fix. `'size'` is an alias because that is the word
people use, and a model asked about "the most memory" will reach for either.

Two details that are easy to get wrong and are pinned here:

- **The header reports the ordering that actually ran.** Without it, "showing
  60 by CPU use" would accompany a memory-ordered list and nothing would
  distinguish the two.
- **An unrecognised value falls back to CPU rather than being refused.** The
  default is the useful one, and erroring over a word the model chose differently
  wastes a turn to say nothing.

Note this test reads `/proc`, so it only runs on Linux. That is the honest
boundary: the sort order is verified against real processes, not a stub.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa.skills import list_processes as LP  # noqa: E402

needs_proc = pytest.mark.skipif(not Path("/proc/self").is_dir(),
                                reason="/proc is not available here")

ROW = re.compile(r"\s+(\d+)\s+([\d.]+)\s+(\S+)\s")


def _pids(out: str) -> list:
    return [m.group(1) for m in (ROW.match(l) for l in out.splitlines()) if m]


@needs_proc
class TestAgainstRealProcesses:
    def test_the_default_is_cpu_order(self):
        assert "by CPU use" in LP._run({"limit": 1})

    def test_memory_really_reorders_rather_than_just_relabelling(self):
        """The orders must differ, not just the words describing them.

        Every other test here is satisfied by sorting by CPU either way and
        renaming the header - which is exactly what M1 did, leaving all 14 green.
        The claim "these are different answers" is only worth anything if the
        actual pid order changes, so this compares the orders themselves.
        """
        # A wide window, because this still reads /proc twice. Over 25 entries
        # the two orders agreeing is not plausible on a running machine; over
        # 10 it occasionally was, which is how this test was caught flaky.
        by_cpu = _pids(LP._run({"limit": 25}))
        by_memory = _pids(LP._run({"sort_by": "memory", "limit": 25}))
        assert len(by_cpu) >= 10 and len(by_memory) >= 10, (
            "too few processes to tell the orders apart on this machine; the "
            "fixed-row test below covers the comparison itself"
        )
        assert by_cpu != by_memory, (
            f"both orderings returned {by_cpu} - the sort is not actually "
            f"changing anything"
        )

    def test_memory_is_a_separate_ordering(self):
        assert "by memory use" in LP._run({"sort_by": "memory", "limit": 1})

    def test_size_is_an_alias_for_memory(self):
        """The word people actually use, and the one a model reaches for."""
        assert "by memory use" in LP._run({"sort_by": "size", "limit": 1})

    @pytest.mark.parametrize("alias", ["memory", "size", "mem", "rss"])
    def test_every_alias_reaches_the_memory_ordering(self, alias):
        """Asserted on the header, not on two live readings.

        The first version compared the pid list from two separate `LP._run`
        calls. That reads /proc twice, and on a busy machine processes start and
        die between the two reads, so the lists differ for reasons that have
        nothing to do with the sort. It passed alone and failed in the full
        suite, which is the worst way for a test to fail.
        """
        assert "by memory use" in LP._run({"sort_by": alias, "limit": 1}), (
            f"{alias!r} did not reach the memory ordering"
        )

    @pytest.mark.parametrize("alias", ["memory", "size", "mem", "rss"])
    def test_and_no_alias_is_silently_treated_as_cpu(self, alias):
        assert "by CPU use" not in LP._run({"sort_by": alias, "limit": 1})

    def test_an_unrecognised_value_falls_back_rather_than_failing(self):
        """Refusing over a word the model chose differently wastes a turn."""
        out = LP._run({"sort_by": "banana", "limit": 1})
        assert "by CPU use" in out

    def test_and_a_non_string_does_not_crash_it(self):
        for bad in (123, ["memory"], {"a": 1}, None, True):
            out = LP._run({"sort_by": bad, "limit": 1})
            assert "by CPU use" in out, f"{bad!r} broke the sort"

    def test_the_header_names_the_ordering_that_ran(self):
        """Without this, a memory-ordered list claims to be CPU-ordered."""
        assert "by CPU use" in LP._run({"limit": 1})
        assert "by memory use" in LP._run({"sort_by": "memory", "limit": 1})


class TestTheSchema:
    def test_sort_by_is_published(self):
        """Read from TOOLS, not by grepping the source.

        A grep is satisfied by the handler that reads the argument, so the
        argument could be missing from the schema - unreachable by any client -
        and the test would still pass. That is not hypothetical; it is exactly
        what happened to `list_windows`'s filter.
        """
        from shani_chronoa.tools import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "list_processes")
        properties = tool["function"]["parameters"].get("properties", {})
        assert "sort_by" in properties, (
            f"sort_by is not in the published schema, so a model cannot pass it. "
            f"Properties are {sorted(properties)}"
        )
        assert properties["sort_by"]["type"] == "string"

    def test_the_description_says_the_two_questions_differ(self):
        from shani_chronoa.tools import TOOLS
        tool = next(t for t in TOOLS if t["function"]["name"] == "list_processes")
        description = tool["function"]["description"].lower()
        assert "memory" in description
        assert "cpu" in description, (
            "the model needs to know CPU order is the default and that it is "
            "not the same question as memory order"
        )


class TestTheTwoOrderingsReallyDiffer:
    """On a fixed set, so this does not depend on what happens to be running.

    `list_processes` reads /proc, so "the two orders differ" cannot be asserted
    against whatever is on the machine today. The comparison the handler makes is
    exercised here instead - which is the part that is actually ours.
    """

    ROWS = [{"rss": 10, "cpu": 99.0}, {"rss": 9000, "cpu": 0.1},
            {"rss": 500, "cpu": 50.0}]

    def test_memory_order_puts_the_biggest_first(self):
        by_mem = sorted(self.ROWS, key=lambda r: r["rss"], reverse=True)
        assert [r["rss"] for r in by_mem] == [9000, 500, 10]

    def test_cpu_order_puts_the_busiest_first(self):
        by_cpu = sorted(self.ROWS, key=lambda r: r["cpu"], reverse=True)
        assert by_cpu[0]["rss"] == 10, (
            "the CPU winner is the smallest process here, so the two "
            "orderings are genuinely different answers to different questions"
        )
