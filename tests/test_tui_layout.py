"""Pure geometry tests for the full-screen TUI layout."""

from shani_chronoa.tui.layout import Rect, clamp_scroll, layout, wrap


def test_layout_100x30():
    assert layout(100, 30) == {
        "header": Rect(0, 0, 100, 1),
        "sidebar": Rect(0, 1, 24, 27),
        "chat": Rect(24, 1, 76, 27),
        "detail": Rect(0, 0, 0, 0),
        "input": Rect(0, 28, 100, 1),
        "footer": Rect(0, 29, 100, 1),
    }


def test_layout_100x30_detail_open_shrinks_chat():
    rects = layout(100, 30, detail_open=True)
    assert rects["sidebar"] == Rect(0, 1, 24, 27)
    assert rects["chat"] == Rect(24, 1, 46, 27)
    assert rects["detail"] == Rect(70, 1, 30, 27)
    assert rects["input"] == Rect(0, 28, 100, 1)


def test_layout_90x30_keeps_sidebar():
    assert layout(90, 30) == {
        "header": Rect(0, 0, 90, 1),
        "sidebar": Rect(0, 1, 24, 27),
        "chat": Rect(24, 1, 66, 27),
        "detail": Rect(0, 0, 0, 0),
        "input": Rect(0, 28, 90, 1),
        "footer": Rect(0, 29, 90, 1),
    }


def test_layout_90x30_detail_is_full_height_overlay():
    rects = layout(90, 30, detail_open=True)
    assert rects["detail"] == rects["chat"] == Rect(24, 1, 66, 27)
    assert rects["sidebar"] == Rect(0, 1, 24, 27)


def test_layout_60x20_collapses_sidebar():
    assert layout(60, 20) == {
        "header": Rect(0, 0, 60, 1),
        "sidebar": Rect(0, 1, 0, 17),
        "chat": Rect(0, 1, 60, 17),
        "detail": Rect(0, 0, 0, 0),
        "input": Rect(0, 18, 60, 1),
        "footer": Rect(0, 19, 60, 1),
    }


def test_layout_60x20_detail_overlays_full_chat():
    rects = layout(60, 20, detail_open=True)
    assert rects["detail"] == rects["chat"] == Rect(0, 1, 60, 17)
    assert rects["sidebar"].w == 0


def test_layout_35x12_keeps_footer_and_chat_rows():
    rects = layout(35, 12)
    assert rects["header"] == Rect(0, 0, 35, 1)
    assert rects["sidebar"] == Rect(0, 1, 0, 9)
    assert rects["chat"] == Rect(0, 1, 35, 9)
    assert rects["input"] == Rect(0, 10, 35, 1)
    assert rects["footer"] == Rect(0, 11, 35, 1)
    assert layout(35, 12, detail_open=True)["detail"] == Rect(0, 1, 35, 9)


def test_layout_short_height_clamps_chat_to_three_rows():
    rects = layout(20, 3)
    assert rects["chat"] == Rect(0, 1, 20, 3)
    assert rects["input"] == Rect(0, 4, 20, 1)
    assert rects["footer"] == Rect(0, 5, 20, 1)


def test_layout_custom_widths():
    rects = layout(120, 30, sidebar_w=20, detail_w=40, detail_open=True)
    assert rects["chat"] == Rect(20, 1, 60, 27)
    assert rects["detail"] == Rect(80, 1, 40, 27)


def test_wrap_words_and_overlong_tokens():
    assert wrap("hello world", 5) == ["hello", "world"]
    assert wrap("one two three", 7) == ["one two", "three"]
    assert wrap("abcdefgh", 3) == ["abc", "def", "gh"]


def test_wrap_newlines_and_empty_text():
    assert wrap("a\n\nb", 5) == ["a", "", "b"]
    assert wrap("", 5) == []
    assert wrap("hi", 1) == ["h", "i"]


def test_clamp_scroll():
    assert clamp_scroll(100, 10, 0) == 0
    assert clamp_scroll(100, 10, 50) == 50
    assert clamp_scroll(100, 10, 95) == 90
    assert clamp_scroll(5, 10, 3) == 0
    assert clamp_scroll(100, 10, -5) == 0
