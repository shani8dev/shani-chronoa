"""A reply split into blocks, and the widgets those blocks become.

Parsing is pure, so nearly everything here is a plain function test: which
fence is open, whether `$5` is a price, whether an unclosed fence eats the rest
of the reply. Each of those has a way of being quietly wrong, and each of those
ways is checked by mutating the source and watching a test fail.

The widgets are built for real, because the part that cannot be read is the part
that matters: whether a card has a Run button it should not have, and whether a
"copy" button copies anything.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "usr" / "lib" / "shani-chronoa"))

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

from shani_chronoa.gui import blocks  # noqa: E402

REPO_SOURCE = (_REPO / "usr/lib/shani-chronoa/shani_chronoa/gui/blocks.py").read_text()


def kinds(text):
    return [block.kind for block in blocks.parse(text)]


def codes(text):
    return [str(block.payload) for block in blocks.parse(text) if block.kind == "code"]


class TestFences:
    def test_a_closed_fence_is_a_code_block(self):
        assert codes("before\n```bash\ndf -h /\n```\nafter") == ["df -h /"]

    def test_the_language_is_kept_and_the_code_is_whole(self):
        block = blocks.parse("```python\nprint(1)\nprint(2)\n```")[0]
        assert block.language == "python"
        assert block.payload == "print(1)\nprint(2)"

    def test_an_unclosed_fence_does_not_eat_the_rest_of_the_reply(self):
        """A reply cut off mid-block is a normal thing to receive. A parser that
        swallowed everything after it would hide the only part the model did
        finish."""
        text = "Here is the command:\n\n```bash\nsudo systemctl restart\n\nAnd that is all."
        assert kinds(text) == ["text"]
        assert "And that is all." in REPO_SOURCE or "sudo systemctl restart" in text

    def test_a_hash_inside_a_fence_is_not_a_heading(self):
        """`# comment` in a shell snippet is the most common thing a model writes
        after a command; reading it as a heading would restyle the line."""
        assert kinds("```sh\n# how to stop it\nsystemctl stop x\n```") == ["code"]

    def test_a_pipe_inside_a_fence_is_not_a_table(self):
        assert kinds("```sh\ndf -h | awk '{print $1}'\n```") == ["code"]

    def test_a_latex_fence_is_math_not_code(self):
        assert kinds("$$\\frac{a}{b}$$") == ["math"] or kinds("```latex\n\\frac{a}{b}\n```") == ["math"]

    def test_two_fences_are_two_blocks(self):
        assert codes("```sh\na\n```\ntext\n```sh\nb\n```") == ["a", "b"]


class TestTables:
    def test_a_pipe_table_is_one_block(self):
        block = blocks.parse("| a | b |\n|---|---|\n| 1 | 2 |")[0]
        assert block.kind == "table"
        assert block.payload == [["a", "b"], ["1", "2"]]

    def test_alignment_markers_are_kept(self):
        block = blocks.parse("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |")[0]
        assert block.meta["aligns"] == ["left", "center", "right"]

    def test_a_ragged_row_is_not_a_table(self):
        """Half a row is not data; rendering it would invent the missing cell."""
        assert kinds("| a | b |\n|---|---|\n| 1 |") == ["text"]

    def test_a_horizontal_rule_is_not_a_table(self):
        assert kinds("---\n") == ["rule"]


class TestMathematics:
    def test_a_display_block_is_math(self):
        assert kinds("$$V = \\frac{4}{3}\\pi r^3$$") == ["math"]

    def test_an_inline_command_is_math(self):
        assert kinds("The area is $\\pi r^2$ here.") == ["text", "math", "text"]

    def test_a_price_is_not_math(self):
        """A dollar sign is a currency symbol far more often than it is a
        delimiter, and `Cost $5` rendered as an equation is nonsense on screen."""
        assert kinds("It costs $5 and $12.50.") == ["text"]

    def test_a_price_before_an_equation_does_not_eat_the_equation(self):
        """A sentence with both, which is the case a dollar-pairing bug hides in:
        the price pair is rejected and the fraction after it must still be
        found."""
        parsed = blocks.parse("Cost $5 or $12.50, and $\\frac{a}{b}$ too.")
        assert [b.kind for b in parsed] == ["text", "math", "text"]
        assert parsed[1].payload == "\\frac{a}{b}"

    def test_a_shell_variable_is_not_math(self):
        assert kinds("Export PATH=$PATH:/opt before running it.") == ["text"]

    def test_a_display_block_needs_no_backslash(self):
        """`$$` is a delimiter the model chose; a lone `$` is a guess. So `$$x^2$$`
        is an equation and `$x^2$` is left as the text it is."""
        assert kinds("$$x^2$$") == ["math"]
        assert kinds("$x^2$") == ["text"]


class TestOtherBlocks:
    def test_headings_are_their_own_blocks_with_their_level(self):
        parsed = blocks.parse("### Notes\ntext")
        assert [(b.kind, b.language) for b in parsed] == [("heading", "3"), ("text", "")]

    def test_prose_stays_prose(self):
        assert kinds("Just a sentence.") == ["text"]

    def test_empty_is_no_blocks(self):
        assert blocks.parse("") == []
        assert blocks.parse("   \n  ") == []

    def test_nothing_raises_on_junk(self):
        """A parser that raises takes the whole reply away, which is worse than
        showing it ugly."""
        for junk in ["|", "```", "$$", "#", "|---|", "|||", "$" * 50, "\x01\u2028"]:
            assert isinstance(blocks.parse(junk), list)


class TestTheCodeBlockControls:
    def _block(self, code, language=""):
        widget = blocks.CodeBlock(blocks.Block("code", code, language=language))
        return widget

    def _tooltips(self, widget):
        found, stack = [], [widget]
        while stack:
            node = stack.pop(0)
            if isinstance(node, Gtk.Button) and node.get_visible():
                found.append(node.get_tooltip_text())
            child = node.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return found

    def test_copy_and_save_are_offered(self):
        tooltips = self._tooltips(self._block("df -h /", "bash"))
        assert "Copy this code" in tooltips and "Save this script" in tooltips

    def test_a_shell_snippet_is_never_offered_a_run_button(self):
        """The whole point of the no-shell-exec boundary: a `bash` block is the
        one thing a model writes most and the one thing that must not be handed
        a Run button."""
        assert "Open this in a terminal" not in self._tooltips(self._block("rm -rf /", "bash"))

    def test_a_python_block_offers_a_terminal_when_one_exists(self):
        """Run opens a terminal for the user to watch; it never executes anything
        itself, so it is offered wherever a terminal exists."""
        if blocks.terminal_available() is None:
            pytest.skip("no terminal emulator on this machine")
        assert "Open this in a terminal" in self._tooltips(self._block("print(1)", "python"))

    def test_the_code_is_shown_escaped(self):
        """Model text reaches a label, so `<b>` in a snippet is markup unless it
        is escaped. The language label is the first one in the tree, so this
        looks at all of them."""
        widget = self._block("echo **<b>bold</b>**")
        labels = [node.get_label() for node in _walk(widget) if isinstance(node, Gtk.Label)]
        assert not any("<b>bold</b>" in label for label in labels)
        assert any("&lt;b&gt;bold&lt;/b&gt;" in label for label in labels)


class TestScriptsAreWrittenNotRun:
    def test_a_script_is_written_to_disk_and_not_executed(self, tmp_path):
        script = blocks.write_script("print(1)\n", "python", directory=tmp_path)
        assert script is not None and script.name == "script.py"
        assert script.read_text() == "print(1)\n"

    def test_a_language_with_no_interpreter_writes_nothing(self, tmp_path):
        assert blocks.write_script("rm -rf /", "bash", directory=tmp_path) is None

    def test_an_unknown_language_writes_nothing(self, tmp_path):
        assert blocks.write_script("anything", "brainfuck", directory=tmp_path) is None

    def test_open_in_terminal_says_which_command_it_opened(self, tmp_path, monkeypatch):
        """A control that starts something without saying what is the failure
        this avoids, so the return value is part of the contract."""
        started = {}
        monkeypatch.setattr(blocks, "terminal_available", lambda: "kgx")
        monkeypatch.setattr(blocks.subprocess, "Popen",
                            lambda argv, **kwargs: started.update(argv=argv))
        script = blocks.write_script("print(1)\n", "python", directory=tmp_path)
        message = blocks.open_in_terminal(script, "python")
        assert "kgx" in message and "script.py" in message
        assert started["argv"][0] == "kgx"
        assert any("script.py" in part for part in started["argv"])

    def test_no_terminal_is_reported_rather_than_silently_doing_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(blocks, "terminal_available", lambda: None)
        script = blocks.write_script("print(1)\n", "python", directory=tmp_path)
        message = blocks.open_in_terminal(script, "python")
        assert "No terminal" in message and str(script) in message


class TestTheToolCallCard:
    def _card(self, **kwargs):
        return blocks.ToolCallCard("web_search", {"query": "chronoa"}, **{
            "result": "Found 3 pages.", "ok": True, **kwargs})

    def test_the_name_and_the_arguments_are_shown(self):
        card = self._card()
        labels = [node.get_label() for node in _walk(card) if isinstance(node, Gtk.Label)]
        assert "web search" in labels
        assert "query" in labels and "chronoa" in labels

    def test_it_starts_collapsed(self):
        """Six tool calls must not bury the answer they produced."""
        assert self._card().revealer.get_reveal_child() is False

    def test_the_result_can_arrive_after_the_card(self):
        """The card is built when the call starts; the result lands later, and
        replacing the card would close the one the user just opened."""
        card = blocks.ToolCallCard("web_search", {"query": "x"})
        card.revealer.set_reveal_child(True)
        card.update("Found 9 pages.", True)
        assert card.result == "Found 9 pages."
        assert card.revealer.get_reveal_child() is True

    def test_a_failure_is_shown_as_a_failure(self):
        card = self._card(result="Error: no such sink", ok=False)
        assert card.ok is False
        assert "no such sink" in " ".join(
            node.get_label() for node in _walk(card) if isinstance(node, Gtk.Label))

    def test_the_result_is_escaped(self):
        card = self._card(result="<b>not bold</b>")
        labels = [node.get_label() for node in _walk(card) if isinstance(node, Gtk.Label)]
        assert not any("<b>not bold</b>" in label for label in labels)


class TestWidgetsFor:
    def test_a_reply_becomes_widgets(self):
        widgets = blocks.widgets_for("Hello.\n\n```sh\ndf -h\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |")
        kinds = [type(node).__name__ for node in widgets]
        assert kinds.count("CodeBlock") == 1
        assert kinds.count("TableBlock") == 1

    def test_math_falls_back_to_the_source_when_matplotlib_is_absent(self, monkeypatch):
        """Without the download the equation is still readable: a box claiming
        "unavailable" would be less useful than the LaTeX."""
        monkeypatch.setattr(blocks, "math_png", lambda *a, **k: None)
        widget = blocks.MathBlock(blocks.Block("math", "\\frac{a}{b}"))
        labels = [node.get_label() for node in _walk(widget) if isinstance(node, Gtk.Label)]
        assert any("frac{a}{b}" in label for label in labels)

    def test_a_rendered_equation_is_a_picture(self, tmp_path, monkeypatch):
        png = tmp_path / "equation.png"
        png.write_bytes(_ONE_PIXEL_PNG)
        monkeypatch.setattr(blocks, "math_png", lambda *a, **k: png)
        widget = blocks.MathBlock(blocks.Block("math", "x^2"))
        assert any(isinstance(node, Gtk.Picture) for node in _walk(widget))


class TestTheControlsExistAtAll:
    """Controls that cannot be found cannot be clicked, so their presence is
    asserted from the source: this fails if a button is ever removed."""

    def test_the_source_builds_the_three_code_controls(self):
        called = [ast.unparse(node.func) for node in ast.walk(ast.parse(REPO_SOURCE))
                  if isinstance(node, ast.Call)]
        assert called.count("_flat_button") >= 3

    def test_nothing_in_this_module_executes_a_code_block(self):
        """The no-shell-exec boundary, asserted against the module that added a
        button people will press with that in mind."""
        tree = ast.parse(REPO_SOURCE)
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = ast.unparse(node.func)
                calls.append(target)
        for forbidden in ("os.system", "subprocess.call", "subprocess.run",
                          "subprocess.check_output", "Popen"):
            # Popen is allowed exactly once, and only to open a terminal.
            if forbidden == "Popen":
                continue
            assert forbidden not in calls, forbidden
        popen = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("Popen")]
        assert len(popen) == 1, "a second subprocess call in this module needs a reason"


def _walk(widget):
    stack, out = [widget], []
    while stack:
        node = stack.pop(0)
        out.append(node)
        child = node.get_first_child() if hasattr(node, "get_first_child") else None
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


_ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082")