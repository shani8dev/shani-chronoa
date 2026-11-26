"""Approval picking sends back the label, exactly as the contract requires."""

import io

from shani_chronoa.tui.approve import choose, options_safe

OPTIONS = ["Allow this once", "Deny"]


class _LineStdin:
    def __init__(self, line):
        self.line = line

    def isatty(self):
        return False

    def readline(self):
        return self.line


def test_number_picks_the_exact_option_label():
    assert choose(OPTIONS, stream=io.StringIO(), stdin=_LineStdin("2\n")) == "Deny"
    assert (
        choose(OPTIONS, stream=io.StringIO(), stdin=_LineStdin("1"))
        == "Allow this once"
    )


def test_exact_label_is_accepted_and_unknown_answer_refuses():
    out = io.StringIO()
    assert choose(OPTIONS, stream=out, stdin=_LineStdin("Deny\n")) == "Deny"
    assert choose(OPTIONS, stream=out, stdin=_LineStdin("maybe\n")) == ""
    assert choose(OPTIONS, stream=out, stdin=_LineStdin("\n")) == ""


def test_numbered_choices_are_shown_with_their_labels():
    out = io.StringIO()
    choose(OPTIONS, stream=out, stdin=_LineStdin("1\n"))
    assert "1) Allow this once" in out.getvalue()
    assert "2) Deny" in out.getvalue()


def test_options_can_be_hidden_when_the_renderer_already_drew_them():
    out = io.StringIO()
    choose(OPTIONS, stream=out, stdin=_LineStdin("1\n"), show_options=False)
    assert out.getvalue() == ""


def test_no_options_is_an_immediate_refusal():
    assert choose([], stream=io.StringIO(), stdin=_LineStdin("1\n")) == ""


def test_status_hint_names_the_keys():
    assert "Up/Down" in options_safe(0, 2)
    assert "1/2" in options_safe(0, 2)
