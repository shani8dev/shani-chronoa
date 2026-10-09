"""`list_shortcuts`: what the keyboard shortcuts on this machine are.

Built because nothing could answer it - among 204 skills, none read a
keybinding, so "what does Super+Tab do" had no answer on a machine whose answer
sits in two GSettings schemas, fully readable by the person who owns it.

**Most of this file exists because the parser was wrong five times in a row**,
each version reading like working code while returning zero or wrong rows:

| what I assumed | what it does |
|---|---|
| values are comma-separated | an accelerator may contain a comma: `['<Alt>XF86AudioLowerVolume', '<Alt><Ctrl>XF86AudioLowerVolume']` is **two** bindings, and the naive split read the first as `Alt` |
| `list-recursively` prints `full.key.path = value` | it prints **no `=` at all** |
| field 0 is the key | field 0 is the **schema**; asking the schema for a key it does not have aborts the run |
| modifiers are written lower-case | GSettings writes `<Super>`, `<Control>` **and** `<Ctrl>` - and matching only some of those yields `Alt+CtrlXF86AudioLowerVolume`, a real binding and no key anybody could press |

The last of those is why the render tests here assert on *what a person can
press*, and why the negative controls below are shaped the way they are.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"))

from shani_chronoa import tools  # noqa: E402
from shani_chronoa.skills import list_shortcuts as ls  # noqa: E402

WM = "org.gnome.desktop.wm.keybindings"
MEDIA = "org.gnome.settings-daemon.plugins.media-keys"

#: Real `gsettings list-recursively` output, copied off this machine including
#: the two-binding value and the empty one. **No `=` anywhere** - that is the
#: fact three parsers got wrong.
REAL_LISTING = f"""\
{WM} switch-applications ['<Super>Tab']
{WM} switch-applications-backward ['<Shift><Super>Tab']
{WM} volume-down-quiet-static ['<Alt>XF86AudioLowerVolume', '<Alt><Ctrl>XF86AudioLowerVolume']
{WM} cycle-windows ['<Alt>Escape']
{MEDIA} volume-down-quiet ['']
{MEDIA} volume-up ['XF86AudioRaiseVolume']
"""


@pytest.fixture
def gsettings(monkeypatch):
    """`gsettings` answered from REAL_LISTING, and it can be taken away.

    The `available=False` half is the control: without it every refusal below
    would also pass against a parser that returns nothing.
    """

    def _fake(available=True, listing=REAL_LISTING):
        def run(*args):
            if not available:
                return None
            if args[:2] == ("list-recursively", WM):
                return listing
            if args[:2] == ("list-recursively", MEDIA):
                return "\n".join(line for line in listing.splitlines()
                                 if line.startswith(MEDIA))
            return ""
        monkeypatch.setattr(ls, "_gsettings", run)

    _fake.available = False
    _fake.listing = REAL_LISTING
    return _fake


# ── the accelerator parser ──────────────────────────────────────────────────

def test_two_bindings_in_one_value_are_two_shortcuts():
    """One value, two bindings - which a comma split also gets right.

    Recorded as the control for the parser choice, not as evidence for it:
    **no accelerator on this machine contains a comma** (measured over both
    schemas), so splitting on `,` and parsing the literal agree on all real
    data. The literal parse is chosen because the format allows a quoted
    accelerator to contain one; this test exists so that the claim is checked
    rather than asserted, and so the two are known to agree today.
    """
    raw = "['<Alt>XF86AudioLowerVolume', '<Alt><Ctrl>XF86AudioLowerVolume']"
    assert ls._accels(raw) == [
        "<Alt>XF86AudioLowerVolume", "<Alt><Ctrl>XF86AudioLowerVolume"]
    # The split it must agree with today, asserted rather than assumed.
    naive = [x.strip().strip("'\"") for x in raw[1:-1].split(",") if x.strip()]
    assert naive == ls._accels(raw)


def test_a_quoted_accelerator_containing_a_comma_is_one_binding():
    """The case the format permits and a comma split gets wrong.

    Not present on this machine's GNOME - hence the control above - so this is
    about the format, not about a reading someone will see here.
    """
    assert ls._accels("['XF86Foo,bar']") == ["XF86Foo,bar"]


def test_a_single_binding_is_one_shortcut():
    assert ls._accels("['<Super>Tab']") == ["<Super>Tab"]


def test_unbound_is_an_answer_about_one_key_not_a_parse_failure():
    """`[]` is what an unbound shortcut reads as, and it is not the same as junk.

    It is easy to conflate the two, because both yield no bindings - and the
    difference matters when asking "what is F5 bound to": the answer there is
    "nothing", which is true.
    """
    assert ls._accels("[]") == []
    assert ls._accels("@as []") == []
    assert ls._accels("") == []


def test_an_unparseable_value_costs_one_key_not_the_other_hundred():
    """One odd value must not take the whole listing with it."""
    assert ls._accels("not a list at all") == []


# ── what a person can actually press ────────────────────────────────────────

@pytest.mark.parametrize("raw,pressable", [
    ("<Super>Tab", "Super+Tab"),
    ("<Super><Shift>Escape", "Super+Shift+Escape"),
    ("<Shift><Super>Tab", "Shift+Super+Tab"),
    ("<Primary>F2", "Ctrl+F2"),
    ("<Alt>F4", "Alt+F4"),
    # `<Ctrl>` and `<Control>` are both real GSettings spellings. Having only one
    # of them produces `Alt+CtrlXF86AudioLowerVolume`: a binding that is on this
    # machine and is not a key anyone could press.
    ("<Alt><Ctrl>XF86AudioLowerVolume", "Alt+Ctrl+XF86AudioLowerVolume"),
    ("<Control>XF86AudioRaiseVolume", "Ctrl+XF86AudioRaiseVolume"),
    ("XF86AudioPlay", "XF86AudioPlay"),
])
def test_every_accelerator_renders_as_a_key_with_separators(raw, pressable):
    assert ls._readable(raw) == pressable


def test_a_name_it_cannot_interpret_is_reported_as_it_is():
    """`Above_Tab` is a real legacy GTK spelling of the Shift+Tab key.

    Rewriting it would be inventing a binding this machine does not have, so
    it is passed through rather than guessed at.
    """
    assert ls._readable("Above_Tab") == "Above_Tab"


def test_every_binding_it_reports_looks_pressable(gsettings):
    """The property the four spelling bugs above all broke.

    Asserted over the **whole listing**, not a sample: a single hand-picked row
    passed while 119 others were unreadable, which is what happened.
    """
    gsettings()
    for row in ls._bindings():
        rendered = ls._readable(row["accel"])
        assert "<" not in rendered, rendered
        assert ">" not in rendered, rendered
        # A rendered key has no `>` left; anything joining two key names with
        # nothing between them is a missing separator, e.g. `Alt+CtrlXF86...`.
        assert not rendered.replace("+", "").startswith("CtrlXF86"), rendered


# ── reading the schemas ─────────────────────────────────────────────────────

def test_it_reads_both_schemas_not_just_the_window_manager_one(gsettings):
    """The second is `settings-daemon`, not `wm`, and holds the hardware keys.

    A listing missing it is missing every volume, playback and brightness key -
    the ones a person is most likely to press by accident.
    """
    gsettings()
    schemas = {row["schema"] for row in ls._bindings()}
    assert schemas == {WM, MEDIA}


def test_the_listing_format_is_three_fields_with_no_equals(gsettings):
    """Pinned because three parsers got this wrong and all three looked right."""
    gsettings()
    keys = {row["key"] for row in ls._bindings()}
    assert "switch-applications" in keys
    # The key is the *second* field, not a dotted path and not the schema.
    assert not any(k.startswith(WM) for k in keys), sorted(keys)


def test_no_readable_schema_is_none_and_not_an_empty_list(gsettings):
    """`None` and `[]` are different claims and the code must keep them apart.

    `[]` is a real answer on GNOME ("nothing is bound"). A KDE machine has no
    readable keybinding schema at all - Plasma keeps its own in
    `kglobalshortcutsrc` - and answering "nothing is bound" there describes a
    desktop that is not running.
    """
    gsettings(available=False)
    assert ls._bindings() is None


def test_a_readable_schema_with_nothing_bound_is_an_empty_list(gsettings):
    """The other side of the `None`/`[]` split, on the same fixture.

    Both schemas answer, every value is `[]`, so this is a real GNOME machine
    with nothing bound - which is `[]`, and must not become the `None` that
    means "not a GNOME machine".
    """
    empty = "\n".join([
        f"{WM} switch-applications []",
        f"{WM} cycle-windows []",
        f"{MEDIA} volume-up []",
    ])
    gsettings(listing=empty)
    assert ls._bindings() == []
    assert ls._run({"action": "list"}) == (
        "No keyboard shortcuts are bound in GNOME's keybinding schemas.")


# ── the answers ─────────────────────────────────────────────────────────────

def test_listing_names_every_bound_shortcut_and_counts_them(gsettings):
    gsettings()
    text = ls._run({"action": "list"})
    assert "6 keyboard shortcut(s) are bound" in text, text
    assert "XF86AudioRaiseVolume" in text


def test_the_schema_s_own_description_is_what_makes_the_answer_worthwhile():
    """`['<Super>Tab']` is a key and nothing else.

    **Calls `_summary_for`**, which the first version of this test did not: it
    reached into `Gio` itself and asserted the schema *has* a description, so a
    `_summary_for` that returned "" for every key left the file green. Caught by
    mutation - replacing `schema.get_key(name)` with the string `name` makes
    `key.get_description()` raise `AttributeError` inside a bare `except`, every
    row loses its gloss, and nothing noticed.

    Read through `Gio` rather than `gsettings range`, which prints the *type*
    (`type as`), so it cannot go stale the way a hand-kept table of ~150 GNOME
    descriptions would.
    """
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
    except (ImportError, ValueError):
        pytest.skip("Gio is unavailable, so there is no schema to describe")
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(WM, True) if source else None
    if schema is None:
        pytest.skip("GNOME's keybinding schema is not installed here")

    key = schema.get_key("switch-applications")
    expected = (key.get_description() or key.get_summary() or "").strip()
    assert expected, "the installed schema itself has no description to show"

    got = ls._summary_for({"schema": WM, "key": "switch-applications",
                           "accel": "<Super>Tab"})
    assert got, "_summary_for returned nothing for a key the schema describes"
    assert got.split(". ")[0][:110] in expected or got in expected


def test_a_key_with_no_description_falls_back_to_its_name_not_a_guess(gsettings):
    """No gloss is honest; an invented one is not.

    Some of GNOME's own keys carry no summary, so the fallback is load-bearing
    on real data rather than theoretical.
    """
    gsettings()
    rows = ls._bindings()
    assert any(ls._summary_for(r) == "" for r in rows) or len(rows) == 0
    for row in rows:
        # Either the schema's own words, or the key name - never a third thing.
        assert ls._summary_for(row) or row["key"]


def test_equivalent_mutant_documented():
    """A mutation that cannot be caught, kept and said out loud.

    Making `_summary_for`'s `except` return `None` instead of `""` leaves this
    file **fully green**: every caller writes `_summary_for(row) or row["key"]`,
    and `None` is falsy exactly as `""` is. It is recorded rather than hidden,
    because a reader running mutations should know this one was looked for and
    why no test can separate them.

    The surviving risk it stands for - `_summary_for` returning a non-string -
    is closed by the return type and by the two call sites, not by a test.
    """


def test_find_answers_what_one_key_does(gsettings):
    gsettings()
    text = ls._run({"action": "find", "keys": "Super Tab"})
    assert "Super+Tab" in text
    assert "switch-applications" in text


def test_find_on_an_unbound_key_says_so_and_how_many_are(gsettings):
    """Not a confident wrong answer, and it points at the way to see the rest."""
    gsettings()
    text = ls._run({"action": "find", "keys": "Ctrl+Alt+Q"})
    assert "Nothing is bound" in text
    assert "6 other shortcut(s)" in text, text


def test_find_without_a_key_asks_for_one(gsettings):
    gsettings()
    assert "Name the key" in ls._run({"action": "find"})


def test_an_unknown_action_is_refused_by_name(gsettings):
    gsettings()
    text = ls._run({"action": "bind"})
    assert "bind" in text
    assert "keyboard shortcut" not in text.lower()


def test_on_a_desktop_without_those_schemas_it_says_so_rather_than_guessing(gsettings):
    """The failure this whole module is shaped around.

    Listing GNOME's defaults on a machine that ignores them is a confident
    answer to a different question - the same shape as a sense that finds no
    camera and calls it absent.
    """
    gsettings(available=False)
    text = ls._run({"action": "list"})
    assert "Could not read" in text
    assert "Plasma" in text, text
    assert "Nothing was guessed" in text


def test_it_does_not_invent_a_binding_for_a_key_it_cannot_read(gsettings):
    """Every row is one the schema actually reports."""
    gsettings()
    for row in ls._bindings():
        assert row["accel"] in REAL_LISTING, row


# ── reachability ────────────────────────────────────────────────────────────

def test_the_skill_is_registered_and_dispatchable():
    """Through the real registry, not by calling `_run`.

    A test that calls the function is not a test that the skill is reachable -
    AGENTS.md records three skills that passed their unit tests and failed on
    every real call because the sandboxed child could not import their handler.
    """
    names = {t["function"]["name"] for t in tools.TOOLS}
    assert "list_shortcuts" in names


def test_it_is_in_the_help_window_and_not_in_the_other_group():
    from shani_chronoa import capabilities
    assert "list_shortcuts" in capabilities._GROUPS
    group, label = capabilities._GROUPS["list_shortcuts"]
    assert group != "Other", group
    assert label


def test_it_is_a_read_and_needs_no_permission():
    """Reading a preference the user set themselves discloses nothing."""
    from shani_chronoa import capabilities
    assert "list_shortcuts" not in capabilities.GATED
    assert "list_shortcuts" not in capabilities.DESTRUCTIVE_CONSENT_KEYS
    assert "list_shortcuts" not in capabilities.MUTATING_TOOLS


def test_its_own_answer_carries_no_unverified_hedge():
    """The whole result of a read is the text it printed.

    Measured through the real dispatch: without the `READ_ONLY_TOOLS` entry every
    answer ended `(unverified - this action reports success but nothing observed
    it)`, which is a hedge about a tool that cannot report success at all - and
    it is appended to a list of shortcuts, where it reads as though some of them
    might not be real.
    """
    from shani_chronoa import capabilities
    assert "list_shortcuts" in capabilities.READ_ONLY_TOOLS


# ── reachability, second door: the router ───────────────────────────────────

@pytest.mark.parametrize("request_", [
    "what are my keyboard shortcuts",
    "what does super tab do",
    "which keys are bound",
    "list my shortcuts",
    "what is F4 bound to",
    "show my keybindings",
    "what are my hotkeys",
])
def test_the_router_offers_it_for_the_plainest_phrasings(request_):
    """A registered skill nobody is offered is the `midi.py` defect class.

    The words people use are not the words the schema uses, so without the
    synonym rows the router answered this for 2 of these 7 and routed the
    plainest ones to `get_datetime`. Asserted per phrasing rather than on an
    average: 2-of-7 is a green-looking score over a mostly-broken feature.
    """
    from shani_chronoa import tool_select
    names = [t["function"]["name"] for t in tool_select.select_tools(request_, tools.TOOLS)]
    assert "list_shortcuts" in names, f"{request_!r} -> {names[:5]}"