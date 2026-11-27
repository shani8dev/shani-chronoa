"""Panels turn redacted Inventory shapes into bounded, safe terminal text."""

from shani_chronoa.tui.panels import (
    detail_lines,
    panel_lines,
    redact,
    sidebar_lines,
)


class Loader:
    """Only the snapshot() shape sidebar_lines needs."""

    def __init__(self, data=None):
        self.data = data or {}

    def snapshot(self, section):
        return self.data.get(section)


def test_redact_covers_every_secret_shape_but_keeps_public_values():
    data = {
        "kilo-api-key": "sk-kilo",
        "groq_api_key": "g-groq",
        "access_token": "tok",
        "client_secret": "sec",
        "password": "pw",
        "nested": [{"refresh_token": "r"}, {"name": "visible"}],
        "row": {"secret": True, "value": "hunter2"},
        "plain": "keep me",
    }
    out = redact(data)
    for key in (
        "kilo-api-key",
        "groq_api_key",
        "access_token",
        "client_secret",
        "password",
    ):
        assert out[key] == "<set>"
    assert out["nested"][0]["refresh_token"] == "<set>"
    assert out["nested"][1]["name"] == "visible"
    assert out["row"]["value"] == "<set>"
    assert out["row"]["secret"] is True  # flag survives for display.lines()
    assert out["plain"] == "keep me"
    for value in ("sk-kilo", "g-groq", "hunter2"):
        assert value not in repr(out)


def test_record_shaped_status_renders_as_lines():
    data = {
        "section": "status",
        "items": {
            "llm_model": "qwen3:4b",
            "stt_available": False,
            "tts_engine": "espeak-ng",
        },
    }
    text = "\n".join(panel_lines(data, 80))
    assert "status" in text
    assert "qwen3:4b" in text and "espeak-ng" in text and "no" in text


def test_panel_lines_truncates_by_count_and_width():
    data = {"items": [{"name": f"item-{i}"} for i in range(50)]}
    lines = panel_lines(data, 12, limit=8, title="Things")
    assert lines[0] == "Things"
    assert 1 < len(lines) <= 8
    assert "…" in lines
    assert all(len(line) <= 12 for line in lines)


def test_no_escape_injection_from_engine_strings():
    data = {"items": [{"name": "\x1b[31mRED\x1b[0m\x07\x08", "note": "ok"}]}
    text = "\n".join(panel_lines(data, 80))
    assert "RED" in text and "ok" in text
    for char in ("\x1b", "\x07", "\x08", "\r", "\x00"):
        assert char not in text
    side = "\n".join(
        sidebar_lines(
            Loader({"models": {"data": {"items": [{"task": "\x1b[31mBAD\x1b[0m"}]}}}),
            60,
        )
    )
    assert "\x1b" not in side and "BAD" in side
    detail = "\n".join(detail_lines("models", {"items": ["\x1b]message\x07SAFE"]}, 60))
    assert "\x1b" not in detail and "\x07" not in detail


def test_sidebar_has_four_bounded_groups():
    loader = Loader(
        {
            "conversations": {
                "data": {"items": [{"title": f"Conv {i}"} for i in range(9)]}
            },
            "skills": {"data": {"items": [{"name": "read_file"}]}},
            "models": {"data": {"items": [{"task": "conversion"}]}},
            "status": {"data": {"items": {"llm_model": "qwen3:4b"}}},
        }
    )
    lines = sidebar_lines(loader, 50)
    text = "\n".join(lines)
    for header in ("CONVERSATIONS", "SKILLS", "MODELS", "STATUS"):
        assert header in text
    assert "Conv 0" in text and "Conv 4" in text
    assert "Conv 5" not in text  # five rows max
    assert "read_file" in text and "conversion" in text and "qwen3:4b" in text
    assert all(len(line) <= 50 for line in lines)


def test_sidebar_shows_loading_and_error_placeholders():
    text = "\n".join(sidebar_lines(Loader(), 40))
    assert text.count("loading…") == 4
    broken = Loader({"status": {"error": "engine went away"}})
    assert "error: engine went away" in "\n".join(sidebar_lines(broken, 40))


def test_detail_lines_have_a_title_and_record_rows():
    lines = detail_lines(
        "status", {"items": {"llm_model": "qwen3:4b", "privacy_mode": True}}, 60
    )
    assert lines[0] == "STATUS"
    assert any("qwen3:4b" in line for line in lines)
    assert detail_lines("status", None, 60)[0].strip() == "loading…"
