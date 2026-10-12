"""The panel said "1 conversation(s) recorded - 0 empty, 1 with messages".

Three numbers where one was wanted, and a `(s)` on screen. Measured on the real
panel, rendered through `conversations.build()` and walked:

    Ready
    1 conversation(s) recorded - 0 empty, 1 with messages

`"(s)"` is the programmer hedging a count nobody should have to read. `0 empty`
is bookkeeping: the clause is built unconditionally and only its *value* changes,
so a store with nothing deleted still displayed a zero. And `1 with messages`
restates the count beside it.

**The detail now appears only when it says something a person cannot already
tell** - which is the case where some conversation has no messages in it, because
that is how a deleted conversation is told from one that was opened and left.

Two notes on what this file deliberately does not change:

- **`_when()` is already right** and this change leaves it alone. A timestamp
  read `Thursday 22:26` and looks undated; it is the third case of a deliberate
  ladder - `Today 14:02`, `Yesterday 09:10`, `Tuesday 18:30`, `02 Oct`,
  `02 Oct 2025` - so a date appears exactly when a weekday name stops being
  enough. Rewriting it to always print a date would make the common case worse.
- **The `(s)` had a correct twin thirty lines below it**, in the same file:
  `f"{count} message{'s' if count != 1 else ''}"`. The pluralisation is applied
  where it was noticed and not where it was first typed, which is why the defect
  survived - there was a working example in plain sight.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa.gui.surfaces import conversations  # noqa: E402


def _say(listed):
    """The title and detail the panel builds for a store of `listed`.

    **Calls `conversations._count_sentence`, not a copy of it.** The first
    version of this helper reimplemented the arithmetic inside the test file,
    which is a tautology: it kept passing after the panel had changed, because
    nothing connected it to the panel. The same trap, found and fixed in
    `tests/test_listening_gate_offers_a_way.py` - worth naming a second time,
    because the failure is silent and the test looks thorough.

    `listed` is the raw store listing; `rows` is what the panel filters it to -
    the entries with messages in them, or the active one.
    """
    rows = [item for item in listed if item["messages"] or item["active"]]
    return conversations._count_sentence(rows, listed)


def _conv(messages, active=False):
    return {"messages": messages, "active": active, "title": "t", "updated": 0.0}


def test_one_is_not_one_s():
    """The defect, verbatim."""
    title, _ = _say([_conv(3)])
    assert title == "1 conversation recorded", title
    assert "(s)" not in title


def test_two_still_plural():
    """**The control.** A fix that stopped pluralising would pass the test above,
    so the plural case has to be asserted too."""
    title, _ = _say([_conv(3), _conv(1)])
    assert title == "2 conversations recorded", title


def test_a_store_with_no_empty_conversations_adds_no_detail():
    """`0 empty, 1 with messages` is gone: it restated the title."""
    _, detail = _say([_conv(3)])
    assert detail == "", (
        f"the panel still carries a detail that says nothing a person could not "
        f"already tell from the title beside it: {detail!r}")


def test_a_store_with_an_empty_conversation_says_so():
    """The case that earns the detail: how a person tells a deleted
    conversation from one that was opened and left."""
    title, detail = _say([_conv(3), _conv(0)])
    assert title == "1 conversation recorded", title
    assert detail == "1 of them have no messages", detail


def test_the_timestamp_ladder_is_untouched():
    """`_when()` was not a defect and must not become one.

    Measured on the real panel a timestamp read `Thursday 22:26` with no date,
    which looks like a bug. It is the third rung of a deliberate ladder, and a
    date is added exactly when a weekday name stops being unambiguous.
    """
    import time

    now = time.time()
    assert conversations._when(now, now) == time.strftime("Today %H:%M",
                                                          time.localtime(now))
    assert conversations._when(now - 86400, now).startswith("Yesterday ")
    # Six days back is a weekday name; a fortnight back is a date. This is the
    # boundary, and it is the whole reason the ladder exists.
    week = conversations._when(now - 6 * 86400, now)
    far = conversations._when(now - 14 * 86400, now)
    assert not week[0].isdigit(), f"6 days back should be a weekday name: {week!r}"
    assert far[0].isdigit(), f"14 days back should be a date: {far!r}"