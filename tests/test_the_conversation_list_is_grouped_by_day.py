"""The conversations list is grouped by day, and a heading with nothing under it is
a lie.

The list was flat, newest-first, with `Today 14:02` on every row. Three problems,
all one problem - **the day is on every row instead of once where it belongs**:

- **"Today" is a filter, not a place.** A person coming back after a weekend reads
  down past three dates to find out whether anything happened today, and finds the
  same word repeated.
- **The day and the clock were one string**, so a grouped list could not be built
  on top without either duplicating the day or editing `_when` at every call site.
  They are two functions now, and `_when` is built from both - one vocabulary, so
  the heading and the row cannot drift into calling the same day "Yesterday" and
  "Tuesday" on different rows.
- **A day heading is a claim about a day**, so the filter has to own it. Typing one
  word used to hide rows; with headings it also has to hide a heading whose rows
  the query matched none of, or the panel says "Tuesday" above an empty gap.

**The panel is driven through its own `_refresh`, not through the helpers.** The
first version of this file asserted on `_day_label` directly, which is the module's
private vocabulary rather than the thing a person reads - and it would have stayed
green through a `_refresh` that never inserted a heading at all.

**And the store's listing is what is stubbed, not the panel.** `list_sessions` is
the data source; controlling `updated` is controlling the data a store holds. The
alternative - a `SimpleNamespace` app - is the recorded failure where a
stub-built panel measures the stub, because `common._permitted` reads `app.config`
and the rows answer differently than they do on a machine.
"""
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "usr" / "lib" / "shani-chronoa"))

# **No `gi` import here.** An earlier version pulled `Gtk` in for
# `require_version` and then measured nothing with it; the panel module does its
# own version pinning, and this file only ever drove the view. Leaving the import
# would have been a claim that this test needs a toolkit it does not touch.
from shani_chronoa.gui.surfaces import conversations  # noqa: E402

NOW = 1_760_000_000.0
DAY = 86_400.0


class _App:
    """The smallest thing the view touches. Every method here is called only on a
    click, so nothing in this file depends on their behaviour."""

    config = None

    def _open_conversation(self, ref):
        return ref

    def _delete_conversation(self, ref):
        return ref

    def _rename_conversation(self, ref, title):
        return ref

    def _fork_conversation(self, ref):
        return ref


def _store_with(ages_days):
    """A listing whose conversations were last touched `ages_days` ago.

    Returns what `list_sessions` would return, and patches it in - the store is
    the data, and the timestamps are the data.
    """
    listed = []
    for n, age in enumerate(ages_days):
        stamp = NOW - age * DAY
        listed.append({
            "id": f"c{n}",
            "title": f"Conversation {n}",
            "updated": stamp,
            "messages": 3,
            "active": False,
        })
    return listed


def _view(monkeypatch, ages_days):
    """The real `_ConversationsView`, refreshed against a store of `ages_days`."""
    listed = _store_with(ages_days)
    monkeypatch.setattr(conversations.conversation_store, "list_sessions",
                        lambda *_a, **_k: listed)
    monkeypatch.setattr(
        conversations.conversation_store, "load",
        lambda *_a, **_k: [{"role": "user", "content": "hello"},
                           {"role": "assistant", "content": "hi"}])
    monkeypatch.setattr(conversations.time, "time", lambda: NOW)
    view = conversations._ConversationsView(_App())
    view._refresh()
    return view, listed


def _headings(view):
    """The day labels the panel actually put in the group, in order."""
    return [row.get_child().get_text() if hasattr(row.get_child(), "get_text")
            else "" for row, _rows in view._day_rows]


def _row_metas(view):
    """What each conversation row says under its title."""
    return [btn._meta.get_text() for _row, btn, _del, _h in view._row_widgets]


def _label_text(view):
    return " | ".join(_headings(view))


def _walk_down(widget):
    """Every widget in `widget`'s subtree, walking **down**.

    `get_first_child()`/`get_next_sibling()` rather than `get_children()`, which
    GTK4 removed - and **down**, because `Adw.PreferencesGroup` wraps its rows in
    boxes of its own, so a row's parent is an internal box and not the group.
    """
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _walk_down(child)
        child = child.get_next_sibling()


class TestTheHeadingAndTheClockAreOneVocabulary:

    def test_the_ladder_is_the_one_the_module_always_used(self):
        """`Today`, `Yesterday`, `Tuesday`, `02 Oct`, `02 Oct 2025` - the same
        five steps `_when` always walked, with the clock taken off.

        **The last two are the ones that matter here.** A weekday name stops being
        enough after a week, which is the module's own documented reason for the
        ladder, and a heading that said "Friday" for something from October would
        make a returning reader believe it.
        """
        assert conversations._day_label(NOW, NOW) == "Today"
        assert conversations._day_label(NOW - DAY, NOW) == "Yesterday"
        this_week = conversations._day_label(NOW - 3 * DAY, NOW)
        assert this_week == time.strftime("%A", time.localtime(NOW - 3 * DAY)), (
            f"a three-day-old conversation read {this_week!r}")
        old = NOW - 40 * DAY
        assert conversations._day_label(old, NOW) == time.strftime(
            "%d %b", time.localtime(old))

    def test_days_are_calendar_days_not_a_24_hour_span(self):
        """A conversation at 23:50 and one at 00:10 the next morning are ten
        minutes apart and on different sides of the word.

        `_days_between` subtracts **dates**, not seconds, which is the only reason
        this holds across midnight and across a daylight-saving hour.
        """
        midnight = time.mktime((2026, 3, 14, 0, 0, 0, 0, 0, -1))
        late = midnight + 23 * 3600 + 50 * 60          # 23:50
        early = midnight + 24 * 3600 + 10 * 60         # 00:10 next day
        assert conversations._day_label(late, midnight + DAY) == "Yesterday"
        assert conversations._day_label(early, midnight + DAY) == "Today"
        assert abs(early - late - 20 * 60) < 1         # twenty minutes apart

    def test_when_is_built_from_the_two(self):
        """One vocabulary, not two. `_when` is still the whole sentence - a
        tooltip or a test wants it - but it cannot now disagree with the heading
        above the row, because it **is** the heading plus the clock.

        **And the clock is only joined when there is one.** The first version of
        this file asserted `_when == day + " " + clock` at every age and was
        wrong: `_clock` returns `""` past a week, so the unconditional join
        produced `02 Sep 08:00` where the module has always said `02 Sep`. The
        pre-existing `test_surface_conversations.py::test_row_dates_are_relative`
        is what caught it. This asserts the join **follows the clock** rather than
        assuming one, which is also what lets the row and `_when` share a rule.
        """
        for age in (0, 1, 3, 6, 7, 40, 400):
            stamp = NOW - age * DAY
            day = conversations._day_label(stamp, NOW)
            clock = conversations._clock(stamp, NOW)
            expected = f"{day} {clock}" if clock else day
            assert conversations._when(stamp, NOW) == expected, (
                f"{age} day(s) old: _when is not the day and clock joined "
                f"correctly")

    def test_a_past_week_carries_no_clock_at_all(self):
        """**The rule, stated once, and it is why `_clock` can return empty.**

        Inside the week a day can hold a dozen conversations and the clock is
        what separates them. Past it the heading is a date that identifies the
        conversation by itself, and `02 Sep 08:00` is a time attached to a day
        nobody can place - which is why this module has always dropped it.

        Both halves, because either alone passes: an upper bound the clock never
        reached would be satisfied by a function that never prints one, and a
        lower bound alone would let `days >= 7` be replaced by `days >= 1`.
        """
        assert conversations._clock(NOW - 3 * DAY, NOW) == time.strftime(
            "%H:%M", time.localtime(NOW - 3 * DAY)), "inside the week, no clock"
        assert conversations._clock(NOW - 7 * DAY, NOW) == "", (
            "a week-old conversation is past the boundary and carries no clock")
        assert conversations._clock(NOW - 40 * DAY, NOW) == "", (
            "a forty-day-old conversation carries no clock")

    def test_the_row_takes_its_clock_from_the_pass_instant(self, monkeypatch):
        """**The `now` parameter is load-bearing, and the fixture hides that.**

        `_refresh` reads the clock once and `_add_row` is handed it, because two
        reads straddling midnight put a conversation under a "Today" heading whose
        row says "Yesterday". A mutation dropping the argument and letting
        `_add_row` read `time.time()` for itself passes every other test in this
        file - because the fixture **pins `time.time()` to a constant**, so both
        reads are identical by construction and the question never arises.

        **`now` only decides whether a clock is printed, not what it reads** - the
        time of day comes from the stamp - so the two instants have to straddle
        the seven-day boundary. An earlier version of this test passed a `now` one
        day *behind* `time.time()`, which is on the same side and therefore yields
        the same clock either way: a fixture that cannot distinguish the two
        behaviours passes the mutation that removes one of them.

        The direction is `date(now) - date(stamp)` - `_days_between`'s own order -
        so the instant handed in is the *later* of the two. That is the scenario
        being protected: the heading pass ran, then the row pass ran with a later
        clock, and a ten-minute refresh that crossed a week boundary is the only
        way the two ever disagree.
        """
        view, listed = _view(monkeypatch, [0])
        item = dict(listed[0])

        # `time.time()` is NOW, so a row reading the clock for itself sees a
        # brand-new conversation and prints a clock. Ten days later the same
        # conversation is past the window, and the row must print none.
        past_the_clock = NOW + 10 * DAY
        view._add_row(item, "probe", "", past_the_clock)

        row_meta = view._row_widgets[-1][1]._meta.get_text()
        assert conversations._clock(item["updated"], past_the_clock) == "", (
            "the fixture instant does not put the row outside the clock window, "
            "so this test cannot tell the two behaviours apart")
        assert not row_meta.startswith(conversations._clock(item["updated"])), (
            f"the row printed a clock for an instant it was handed "
            f"({row_meta!r}) - it read its own time instead")

    def test_a_refresh_reads_the_clock_once_for_the_whole_pass(self, monkeypatch):
        """**The call site, not the signature.** Removing `now` from the
        `_add_row(...)` call leaves the parameter intact and every test above
        still green - the panel's own `_refresh` is what supplies the instant, and
        the fixture freezes `time.time()` so reading it again returns the same
        answer.

        So the clock is made to **move during the refresh**, which is the whole
        point of passing it: the first read is the pass's `now`, and every read
        after it jumps forward ten days. Under the shipped code there is exactly
        one read and today's rows carry a clock; with the argument dropped, each
        row reads a clock that has moved past the window and none of them do.
        """
        calls = {"n": 0}

        def moving_clock():
            calls["n"] += 1
            return NOW if calls["n"] == 1 else NOW + 10 * DAY

        view, listed = _view(monkeypatch, [0, 0])
        calls["n"] = 0
        monkeypatch.setattr(conversations.time, "time", moving_clock)
        view._refresh()

        metas = _row_metas(view)
        assert metas, "the refresh built no rows"
        assert calls["n"] == 1, (
            f"a refresh read the clock {calls['n']} time(s); it must read it once "
            f"and hand the same instant to every row, or a pass that crosses a "
            f"boundary dates its own rows differently")
        for meta in metas:
            assert meta.startswith(conversations._clock(listed[0]["updated"])), (
                f"a row was dated from a later instant than its heading: {meta!r}")

    def test_the_row_of_an_old_conversation_opens_with_no_separator(self, monkeypatch):
        """A row whose clock is empty must not begin with `" · "`.

        `_clock` returning `""` put an empty first element into the row's `bits`
        list, and `" · ".join(["", "3 messages"])` is a row that **opens with a
        separator**. The symptom is invisible in every assertion about the clock's
        value and obvious on screen.
        """
        view, _ = _view(monkeypatch, [40])
        metas = _row_metas(view)
        assert metas, "no rows were built at all"
        for meta in metas:
            assert not meta.startswith(" · "), f"leading separator: {meta!r}"
            assert "·" in meta or "message" in meta, f"empty meta: {meta!r}"


class TestThePanelGroupsThem:

    def test_three_days_are_three_headings_and_not_nine_days(self, monkeypatch):
        ages = [0, 0, 1, 1, 3]
        view, listed = _view(monkeypatch, ages)
        expected = []
        for item in listed:
            day = conversations._day_label(item["updated"], NOW)
            if day not in expected:
                expected.append(day)
        assert _headings(view) == expected, (
            f"the panel headed {len(ages)} rows with {_headings(view)}, "
            f"expected {expected}")
        assert len(view._day_rows) == 3, (
            f"{len(view._day_rows)} headings for five rows over three days")

    def test_the_row_says_the_clock_and_not_the_day(self, monkeypatch):
        """**The point of the change.** Under a heading that says "Today", a row
        reading `Today 14:02` is the day said twice; `14:02` says more in fewer
        words, because the heading is right above it.

        **The shape of the text is asserted, not its agreement with `_clock`.**
        The first version asked that each meta *start with* `conversations.
        _clock(stamp)` - which is the module's own function, so a `_clock` that
        grew into `time.strftime("%d %b %H:%M", ...)` satisfied every assertion
        while each row read `13 Oct 14:02`, the exact duplication this change
        exists to remove. A test that compares a function's output against
        itself cannot see the function change.
        """
        view, _ = _view(monkeypatch, [0, 1])
        clock_only = re.compile(r"^\d{2}:\d{2}\b")
        for meta in _row_metas(view):
            assert clock_only.match(meta), (
                f"a row is not a bare clock - the day belongs to its heading: "
                f"{meta!r}")
            assert not re.search(r"[A-Za-z]{3}", meta.split("·")[0]), (
                f"a row still spells a day or month its heading already says: "
                f"{meta!r}")

    def test_a_heading_is_not_a_focus_stop(self, monkeypatch):
        """`compare.py` had exactly this: `_row()` made headings, counts and
        instructions selectable, so every heading in the panel was a Tab stop. A
        heading you can land on and do nothing with is a dead control.
        """
        view, _ = _view(monkeypatch, [0, 1])
        for heading, _rows in view._day_rows:
            assert not heading.get_activatable(), (
                "a day heading is activatable - a Tab stop that goes nowhere")

    def test_a_second_refresh_does_not_stack_a_second_copy(self, monkeypatch):
        """The rows are removed by reference and so are the headings; forgetting the
        second leaves a refresh that doubles every day heading in the panel, and
        the list grows by a heading per keystroke on the filter.

        **The assertion counts the widgets in the group, not the widgets the view
        is tracking** - and that is not pedantry. The first version of this read
        `_day_rows` before and after and passed with the removal loop deleted,
        because `self._day_rows = []` runs either way: the tracking list is right
        and the panel is wrong, which is the whole failure. Measured on the
        mutated file, three refreshes take the group from 118 widgets to 136 - six
        orphans per pass, growing forever, and invisible to every assertion that
        looks at the bookkeeping.
        """
        view, _ = _view(monkeypatch, [0, 1, 3])
        assert _headings(view) == ["Today", "Yesterday",
                                   conversations._day_label(NOW - 3 * DAY, NOW)]
        after_one = sum(1 for _ in _walk_down(view._group))
        view._refresh()
        view._refresh()
        after_three = sum(1 for _ in _walk_down(view._group))
        assert after_three == after_one, (
            f"the list holds {after_one} widgets after one refresh and "
            f"{after_three} after three - each refresh stacked another copy of "
            f"every day heading")

    def test_one_day_is_one_heading_not_one_per_row(self, monkeypatch):
        view, _ = _view(monkeypatch, [0, 0, 0, 0, 0])
        assert len(view._day_rows) == 1, _headings(view)


def _day_of(now, listed, index):
    return conversations._day_label(listed[index]["updated"], now)


class TestTheFilterOwnsTheHeadings:

    def test_a_heading_with_nothing_under_it_is_hidden(self, monkeypatch):
        """**A heading names a day, so a heading above nothing is a claim about a
        day the search found nothing from.** Typing one word must not leave
        "Tuesday" over an empty gap.

        This is the half that makes grouping safe to add to a list with a search
        box on it, and it is derived from the rows rather than tracked separately -
        so a row the filter cannot reach cannot leave a heading behind.
        """
        view, listed = _view(monkeypatch, [0, 1, 3])
        view.search.set_text("Conversation 0")
        view._apply_filter("Conversation 0")

        hidden = [h.get_child().get_text() for h, rows in view._day_rows
                  if not h.get_visible()]
        assert len(hidden) == 2, (
            f"only {hidden} hid - the filter matched conversation 0 on Today, so "
            f"Yesterday and the third day's headings should both be gone")
        for heading, rows in view._day_rows:
            assert heading.get_visible() == any(r.get_visible() for r in rows), (
                "a heading's visibility and its rows' disagree")

    def test_a_query_matching_two_days_keeps_both_headings(self, monkeypatch):
        view, _ = _view(monkeypatch, [0, 1, 3])
        view._apply_filter("Conversation")
        shown = [h.get_child().get_text() for h, rows in view._day_rows
                 if h.get_visible()]
        assert len(shown) == 3, shown

    def test_clearing_the_query_brings_every_heading_back(self, monkeypatch):
        """**A multi-row heading, deliberately.** The first version used three
        conversations over three days, so every heading held exactly one row -
        and a filter that left any heading with more than one row permanently
        visible passed it. A store where most of one day's conversations are on
        one day is the ordinary case, not the edge, and that is the case in which
        the filter has the most to get right.
        """
        view, _ = _view(monkeypatch, [0, 0, 0, 1])
        counts = [len(rows) for _h, rows in view._day_rows]
        assert counts == [3, 1], f"the fixture is not multi-row: {counts}"

        # **The query is chosen to empty the multi-row day.** An earlier version
        # used "Conversation 1", which matches a *today* row - so Today's heading
        # was legitimately visible either way and a filter that pinned any
        # heading with more than one row open passed it. Measured against the
        # mutation: "Conversation 3" leaves Today with 0 visible rows under both
        # builds, and the heading is the only thing that differs.
        view._apply_filter("Conversation 3")
        empty = [h.get_child().get_text() for h, rows in view._day_rows
                 if not any(r.get_visible() for r in rows)]
        hidden = [h.get_child().get_text() for h, rows in view._day_rows
                  if not h.get_visible()]
        assert empty == ["Today"], f"the fixture does not empty a day: {empty}"
        assert hidden == empty, (
            f"a day with nothing left under it kept its heading; "
            f"empty={empty} heading-shown={hidden}")

        view._apply_filter("")
        assert all(h.get_visible() for h, _r in view._day_rows), (
            "clearing the filter left a heading hidden - a day that is still there "
            "and is no longer named")


class TestTheWiringIsReal:

    def test_the_view_really_did_insert_headings(self, monkeypatch):
        """**The assertion is on the panel, not on the module.**

        A file that asserts on `_day_label` proves the vocabulary and nothing
        about the list, and it would stay green through a `_refresh` that never
        called `_add_day_heading` at all - which is the same "a test that reaches
        past the call site to the function behind it" the compare-panel work found.

        **And "is a descendant of the group" is not enough on its own**, because a
        widget inside a hidden box is still a descendant. So the heading has to be
        *visible* as well - a heading that is tracked but not drawn leaves the rows
        with nothing above them, which is the defect this assertion exists for.
        """
        view, _ = _view(monkeypatch, [0, 1, 3])
        assert len(view._day_rows) == 3
        assert len(view._row_widgets) == 3
        for heading, _rows in view._day_rows:
            assert heading.get_visible(), (
                "a day heading is tracked but not drawn - a reader would see rows "
                "with nothing above them")
        # Walk **down**, not up: `Adw.PreferencesGroup` wraps its children in
        # boxes of its own, so a heading's parent is an internal box rather than
        # the group. The first version of this asserted `parent is group`, which
        # is false on correct code - the same mistake the minimap test made.
        inside = {id(w) for w in _walk_down(view._group)}
        for heading, _rows in view._day_rows:
            assert id(heading) in inside, (
                "the headings are tracked but not in the list a reader sees")

    def test_rows_are_under_the_heading_they_belong_to(self, monkeypatch):
        """Not just "a heading exists" - the *right* rows are under it. Two rows
        from different days under one heading is a heading that lies about both.
        """
        view, listed = _view(monkeypatch, [0, 0, 1])
        counts = [len(rows) for _h, rows in view._day_rows]
        assert counts == [2, 1], (
            f"{counts} rows per heading for rows on Today/Today/Yesterday")