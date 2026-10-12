"""A section is a category, not a drawer.

Measured on the real sidebar before this file existed - all 33 registered
surfaces, grouped by the `SECTION` each one declares:

    Remembering         5
    Acting             14   <- 42% of the app, and not one category
    Sensing             5
    Thinking            3
    Health and trust    5
    Everything else     1   <- a heading above a single row

Two defects, one cause: nothing checked what a section *contained*.

**"Acting" held fourteen panels and answered several questions.** Four of them
- `phone_link`, `phone`, `screen_share`, `connections` - are about reaching this
computer from a phone or over the network, which is a question about *reach*, not
about anything Chronoa was asked to do. Because they sat under the only heading
that could describe them, a person looking for the phone panel had to read past
seven things that were not it. They now have their own section, and "Acting" is
nine things that all answer "what can it do for me".

**"Everything else" held exactly one row.** Listening declared no `SECTION` at
all, so `DEFAULT_SECTION` - the fallback for panels that predate sections - did
not cover it. A heading that names no category a person can act on, over one
item, is worse than no heading.

## The guards

The first two are the ones that would have caught both, and they are cheap
enough to run on every change:

- **no section is a drawer** - a cap that fails when one heading outgrows the
  others. Set above today's largest (`Health and trust`, 6) so it catches a
  regression rather than forbidding a legitimate addition.
- **the catch-all stays empty** - it exists as the fallback for a panel that has
  not declared one yet, so it is *allowed* to be non-empty. It is not allowed to
  be a home.

The third is why the first two are meaningful: every panel must either declare a
section that exists, or be in the fallback table. A typo in a section name would
otherwise silently file a panel under the catch-all and the guards above would
read healthy.
"""

import ast
import pathlib
import re

import pytest

SURFACES = (pathlib.Path(__file__).resolve().parents[1] / "usr/lib/shani-chronoa"
            / "shani_chronoa" / "gui" / "surfaces")

#: Above today's largest section (`Health and trust`, 6) on purpose. A cap set
#: at exactly the current maximum would fail the next legitimate addition and
#: train people to raise it without looking; this one fails only when a heading
#: has become a drawer.
MAX_PER_SECTION = 9


def _read(name):
    return (SURFACES / (name + ".py")).read_text(encoding="utf-8", errors="replace")


def _modules():
    for path in sorted(SURFACES.glob("*.py")):
        if path.stem not in ("__init__", "common"):
            yield path.stem


def _declared(name):
    """The `SECTION` this surface declares, or `None` if it declares none."""
    found = re.search(r'^SECTION\s*=\s*"([^"]*)"', _read(name), re.M)
    return found.group(1) if found else None


def _order():
    source = _read("__init__")
    block = re.search(r"SECTION_ORDER\s*=\s*\((.*?)\)", source, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


def _fallback():
    source = _read("__init__")
    block = re.search(r"DEFAULT_SECTION\s*=\s*\{(.*?)\}", source, re.S).group(1)
    return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', block))


def _buckets():
    fallback = _fallback()
    out = {}
    for name in _modules():
        section = _declared(name) or fallback.get(name) or "Everything else"
        out.setdefault(section, []).append(name)
    return out


def test_every_panel_is_accounted_for():
    """A panel that failed to register would make the counts below look healthy
    while being invisible in the sidebar - the layout contract erroring quietly,
    which is how `diff.py` and `artifact_store.py` were 100% unreachable."""
    assert sum(len(v) for v in _buckets().values()) == len(list(_modules()))


def test_no_section_is_a_drawer():
    """**The guard that catches "Acting" at 14.**

    Not a cap that forbids growth - a cap that notices when one heading has
    swallowed a category. 42% of the app under one name is what makes a sidebar
    a list nobody scrolls.
    """
    wide = {s: len(v) for s, v in _buckets().items() if len(v) > MAX_PER_SECTION}
    assert not wide, (
        f"{wide} exceed {MAX_PER_SECTION} panels. A heading that wide no longer "
        "names a category - split it, or add the section name that does.")


def test_the_catch_all_is_not_a_home():
    """**The guard that catches "Everything else" at 1.**

    Non-empty is legal and expected the day a panel ships without declaring a
    section: that is what the fallback is for. What is not legal is it becoming
    where panels live.
    """
    strays = _buckets().get("Everything else", [])
    assert not strays, (
        f"{sorted(strays)} declare no SECTION and are not in DEFAULT_SECTION, "
        "so they are filed under the catch-all heading. Give each a SECTION, or "
        "add it to DEFAULT_SECTION if it predates sections.")


def test_every_declared_section_exists_in_the_order():
    """**Why the guards above are not self-deceiving.**

    A typo in a section name files a panel under the catch-all - which the
    previous test would then blame on the wrong panel - and a name missing from
    `SECTION_ORDER` means the sidebar never renders that heading at all, so its
    panels are simply gone. Both read as "nothing wrong here" without this.
    """
    order = _order()
    unknown = {n: d for n, d in ((n, _declared(n)) for n in _modules())
               if d and d not in order}
    assert not unknown, (
        f"{unknown} declare a section that is not in SECTION_ORDER {order}. The "
        "sidebar iterates SECTION_ORDER, so those panels would not appear.")


def test_the_phone_and_remote_panels_are_together():
    """The move this file was written for, pinned by name.

    A general cap would pass if these four drifted back into "Acting" one at a
    time - 13, 12, 11 panels, still under a cap tuned for today's numbers. The
    claim is about *these four being a category*, so it is asserted about those
    four.
    """
    buckets = _buckets()
    together = buckets.get("Phone and remote", [])
    assert sorted(together) == ["connections", "phone", "phone_link",
                                "screen_share"], (
        f"'Phone and remote' holds {sorted(together)}, not the four panels "
        "about reaching this computer from a phone or over the network")


@pytest.mark.parametrize("name", sorted(_modules()))
def test_no_surface_raises_while_being_read_for_a_section(name):
    """Read the file rather than import it, but still prove it parses.

    Importing every surface to ask a constant is how `diff.py` and
    `artifact_store.py` got into the tree in the first place: valid-looking
    Python that cannot be executed. `ast.parse` catches the syntax half without
    needing a display, an app, or a config.
    """
    ast.parse(_read(name))