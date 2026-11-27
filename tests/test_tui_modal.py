"""Unit tests for approval, confirmation and secret modals."""

from __future__ import annotations

from dataclasses import dataclass

from shani_chronoa.tui import approve, modal


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


class FakeCanvas:
    """Accepts only `text`/`box`, so any other canvas call fails the test."""

    def __init__(self) -> None:
        self.texts: list[tuple[int, int, str]] = []
        self.boxes: list[tuple[Rect, str]] = []

    def text(self, row, col, value):
        self.texts.append((row, col, value))

    def box(self, rect, title):
        self.boxes.append((rect, title))

    def painted(self) -> str:
        return "\n".join(value for _, _, value in self.texts)


EVENT = {
    "id": "turn-1",
    "text": "Run the command?",
    "options": ["Allow this once", "Deny"],
    "detail": "echo hi",
    "kind": "command",
}


def test_approval_hint_keeps_choice_marker_and_options_safe():
    approval = modal.ApprovalModal(EVENT)
    assert "choice [1]:" in approval.hint()
    assert approve.options_safe(0, 2) in approval.hint()
    assert approval.handle("down") is None
    assert "choice [2]:" in approval.hint()
    assert approve.options_safe(1, 2) in approval.hint()
    assert approval.handle("up") is None
    assert "choice [1]:" in approval.hint()


def test_approval_digit_arrow_enter_and_refusals_return_labels_or_empty():
    approval = modal.ApprovalModal(EVENT)
    assert approval.handle("text:2") is None
    assert approval.handle("enter") == "Deny"

    assert modal.ApprovalModal(EVENT).handle("enter") == "Allow this once"
    approval = modal.ApprovalModal(EVENT)
    assert approval.handle("down") is None
    assert approval.handle("enter") == "Deny"
    approval = modal.ApprovalModal(EVENT)
    assert approval.handle("text:Allow this once") == "Allow this once"

    assert modal.ApprovalModal(EVENT).handle("q") == ""
    assert modal.ApprovalModal(EVENT).handle("text:q") == ""
    assert modal.ApprovalModal(EVENT).handle("escape") == ""
    assert modal.ApprovalModal(EVENT).handle("interrupt") == ""
    assert modal.ApprovalModal(EVENT).handle("ctrl+c") == ""
    assert modal.ApprovalModal(EVENT).handle("tab") is None  # moves, stays open


def test_approval_render_shows_question_options_and_hint():
    canvas = FakeCanvas()
    rect = Rect(10, 4, 50, 7)
    modal.ApprovalModal(EVENT).render(canvas, rect)
    assert canvas.boxes == [(rect, "APPROVAL")]
    painted = canvas.painted()
    assert "Run the command?" in painted
    assert "1) Allow this once" in painted
    assert "2) Deny" in painted
    assert "choice [1]:" in painted


def test_approval_render_without_canvas_methods_or_options_does_not_raise():
    modal.ApprovalModal(EVENT).render(object(), Rect(0, 0, 40, 6))
    modal.ApprovalModal({}).render(FakeCanvas(), Rect(0, 0, 40, 6))


def test_confirm_accepts_y_and_enter_and_refuses_n_and_escape():
    assert modal.ConfirmModal("Delete it?").handle("y") is True
    assert modal.ConfirmModal("Delete it?").handle("text:Y") is True
    assert modal.ConfirmModal("Delete it?").handle("enter") is True
    assert modal.ConfirmModal("Delete it?").handle("n") is False
    assert modal.ConfirmModal("Delete it?").handle("text:N") is False
    assert modal.ConfirmModal("Delete it?").handle("escape") is False
    assert modal.ConfirmModal("Delete it?").handle("ctrl+c") is False
    assert modal.ConfirmModal("Delete it?").handle("maybe") is None

    canvas = FakeCanvas()
    modal.ConfirmModal("Delete it?").render(canvas, Rect(0, 0, 30, 5))
    assert canvas.boxes[0][1] == "CONFIRM"
    assert "Delete it?" in canvas.painted()
    assert "Continue? [y/N]" in canvas.painted()


def test_secret_masks_value_and_never_echoes_it(capsys):
    secret = modal.SecretModal("API key:")
    for char in "s3cret":
        assert secret.handle("text:" + char) is None
    assert secret.handle("backspace") is None
    assert secret.handle("text:t") is None
    assert secret.handle("enter") == "s3cret"

    canvas = FakeCanvas()
    secret.render(canvas, Rect(0, 0, 30, 5))
    painted = canvas.painted()
    assert "*" * 6 in painted
    assert "s3cret" not in painted
    assert "s3cre" not in repr(canvas.boxes)
    assert capsys.readouterr().out == ""
    assert capsys.readouterr().err == ""


def test_secret_escape_refuses_and_render_without_canvas_is_safe(capsys):
    secret = modal.SecretModal("API key:")
    secret.handle("text:hunter2")
    assert secret.handle("escape") == ""
    secret.handle("text:hunter2")
    assert secret.handle("interrupt") == ""
    secret.render(object(), Rect(0, 0, 30, 5))
    assert capsys.readouterr().out == ""
