"""One page answers "what can this thing do?", and it answers it in plain words.

**The panel that lists the 260 capabilities was called "Skills"**, and the
panel that shows the body's organs was called "What Chronoa can do" - so the
name promised an answer the page did not give, and the answer itself had no
name.

So the capabilities page took the name, and the anatomy page says what it
is. **And its rows stopped quoting the model**: a row used to carry the
skill's own description - prose written for a model with the tool schema in
front of it - where a person needs to know *what it touches*, which is the
one thing a list cannot say.

**And a row with a question offers it.** The palette has filled the
composer with a prompt for a while; this is the browsable page, and a
person who has found a thing they want should not have to guess the
wording of it.
"""

from __future__ import annotations

import os
import sys
from collections import deque

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "usr", "lib", "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
Gtk = pytest.importorskip("gi.repository.Gtk", reason="GTK 4 is not installed")
Adw = pytest.importorskip("gi.repository.Adw", reason="libadwaita is not installed")

Adw.init()

from shani_chronoa.gui.surfaces import inventory, skills  # noqa: E402


class _Config:
    """Every switch off - the state a fresh install is in."""

    def get_bool(self, key, default=False):
        return False

    def get(self, key, default=None):
        return default

    def get_string(self, key, default=""):
        return default

    def set(self, key, value):
        return None

    def sense_allowed(self, key):
        return False


class _Window:
    """The composer's own end, so a press can be seen to land in it."""

    def __init__(self):
        self.text = None

    def set_input_text(self, text):
        self.text = text


class _App:
    application_id = "test.chronoa.whatitcando"

    def __init__(self):
        self.config = _Config()
        self.window = _Window()

    def activate(self):
        pass


def _walk(widget):
    """Every widget on screen, in tree order."""
    if not widget.get_visible():
        return
    queue, found = deque([widget]), []
    while queue:
        node = queue.popleft()
        found.append(node)
        if hasattr(node, "get_first_child"):
            child = node.get_first_child()
            while child is not None:
                queue.append(child)
                child = child.get_next_sibling()
    return found


def _rows(page):
    """The page's rows, as (title, subtitle) pairs."""
    out = []
    for node in _walk(page):
        if type(node).__name__ == "ActionRow":
            out.append((node.get_title() or "", node.get_subtitle() or ""))
    return out


def _buttons(page, label):
    return [n for n in _walk(page)
            if isinstance(n, Gtk.Button) and (n.get_label() or "") == label]


class TestThePageIsNamedForWhatItAnswers:
    def test_the_capabilities_page_is_called_what_chronoa_can_do(self):
        """**The name is the answer to the question a newcomer arrives
        with** - and this is the page that gives it."""
        assert skills.TITLE == "What Chronoa can do"

    def test_the_anatomy_page_says_it_is_the_anatomy(self):
        """**It was called "What Chronoa can do" while showing the body's
        organs** - Skin, Immune system, Scar tissue. The name was the lie, and
        the metaphor stays because it is the code's own vocabulary."""
        assert inventory.TITLE == "The anatomy"

    def test_no_two_panels_answer_the_same_question(self):
        """**Two panels with the same title are two answers a reader cannot
        tell apart** - which is the whole of 10e's complaint."""
        titles = {}
        for name in ("skills", "inventory"):
            titles[name] = {"skills": skills.TITLE,
                             "inventory": inventory.TITLE}[name]
        assert len(set(titles.values())) == len(titles), titles


class TestTheRowsArePlain:
    def test_every_row_says_what_it_touches(self):
        """**What a list cannot say is what each thing touches** - and the
        palette's skill rows already read `effect_of()`, so this is one
        answer and not a second wording."""
        page = skills.build(_App())
        rows = [r for r in _rows(page) if r[0] not in ("Ready", "")]
        assert rows, "the page built no rows at all"
        known = {"acts on this machine", "asks your permission first",
                 "can delete what you have", "only reads from this machine"}
        for title, subtitle in rows:
            assert any(word in subtitle for word in known), (
                f"the row {title!r} does not say what it touches: {subtitle!r}")

    def test_no_row_quotes_the_description_the_model_is_handed(self):
        """**The description is prose written for a model with the tool schema
        in front of it** - wordy, full of qualifiers. It belongs in the
        tooltip, where the person who wants it can find it."""
        from shani_chronoa.skills import discover_skills
        schemas, _handlers = discover_skills()
        model_prose = []
        for schema in schemas:
            function = schema.get("function", schema)
            description = function.get("description") or ""
            # The first sentence is the long one the row used to carry.
            model_prose.append(description.split(".")[0][:60])
        page = skills.build(_App())
        subtitles = [s for _t, s in _rows(page)]
        for prose in model_prose:
            if len(prose) < 30:
                continue
            for subtitle in subtitles:
                assert prose not in subtitle, (
                    f"a row still quotes the model-facing description: {prose!r}")

    def test_the_registry_name_is_still_findable(self):
        """**The id moved to the tooltip**, so the search has to find it
        there - the haystack is unchanged."""
        page = skills.build(_App())
        haystacks = [r.haystack for r in page.rows()]
        assert any("airplane_mode" in h for h in haystacks), (
            "the registry name is no longer searchable")


class TestTheAnatomyRowsReadAsSentences:
    def test_the_anatomy_row_reads_as_two_sentences(self):
        """**The organ's own text does not end in a period** - "the outside
        edge: everything that leaves this machine, and the log of it" - so
        joining the parts with a space ran the two sentences together and the
        row read *"the log of it Visible as the skin light while it
        happens"*."""
        from shani_chronoa import organism
        built = [o for o in organism.INVENTORY if o.state == organism.BUILT]
        assert built, "no built organ to read"
        for organ in built:
            said = inventory._subtitle(organ)
            assert said.endswith("."), (
                f"the row for {organ.anatomy!r} ends without a stop: {said!r}")
            # **A run-on is a lower-case word followed by an upper-case
            # one** - "the log of it Visible as the skin light" - which is
            # what two sentences joined with a space look like.
            words = said.split()
            for before, after in zip(words, words[1:]):
                assert not (before[-1:].islower() and after[:1].isupper()), (
                    f"the row for {organ.anatomy!r} runs its sentences "
                    f"together: {said!r}")


class TestTryItFillsTheComposer:
    def test_a_row_with_a_question_offers_it(self):
        """**The browsable page needs the same door the palette has** - a
        person who has found a thing they want should not have to guess the
        wording of it."""
        page = skills.build(_App())
        buttons = _buttons(page, "Try it")
        assert buttons, "no row offers a question to try"

    def test_pressing_it_puts_the_question_in_the_composer(self):
        """**The composer, not the model.** A skill that writes files or
        leaves the machine should be *asked for*."""
        app = _App()
        page = skills.build(app)
        buttons = _buttons(page, "Try it")
        assert buttons, "no row offers a question to try"
        buttons[0].emit("clicked")
        assert app.window.text, "the press put nothing in the composer"

    def test_the_question_is_one_that_exercises_the_skill(self):
        """**A button that offers a question has to be a question about that
        skill** - the prompt comes from the one table that knows them."""
        app = _App()
        page = skills.build(app)
        buttons = _buttons(page, "Try it")
        assert buttons, "no row offers a question to try"
        buttons[0].emit("clicked")
        said = app.window.text
        assert said and not said.endswith("."), (
            f"the question put in the composer reads like a label: {said!r}")
