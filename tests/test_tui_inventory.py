"""Slash commands show every Inventory section in the engine's real shapes.

`status` and `privacy` are one record (a dict), not a list. The TUI sliced
`items[:30]` and died with KeyError on /status against the real engine; the
stand-in had the right shape but no test ran /status.
"""

import io

from shani_chronoa.tui.app import show_inventory


class Client:
    def __init__(self, data):
        self.data = data

    def inventory(self, section):
        return self.data


def shown(data, section="status"):
    out = io.StringIO()
    show_inventory(Client(data), section, (section,), out)
    return out.getvalue()


def test_a_record_section_is_shown_as_lines():
    text = shown({"section": "status", "items": {
        "llm_model": "", "llm_installed": [], "stt_available": False, "tts_engine": "espeak-ng"}})
    assert "status:" in text
    assert "llm model: -" in text and "llm installed: none" in text
    assert "stt available: no" in text and "tts engine: espeak-ng" in text


def test_a_list_section_still_lists():
    text = shown({"items": [{"name": "qwen3", "size": 5}]}, "models")
    assert "models (1):" in text and "qwen3" in text


def test_an_unexpected_shape_does_not_end_the_session():
    from shani_chronoa.tui.app import Interactive

    class Broken:
        def inventory(self, section):
            raise RuntimeError("engine went away")

    out = io.StringIO()
    app = Interactive.__new__(Interactive)
    app.client, app.out, app.stop, app.pending_options = Broken(), out, False, {}
    app.handle("/status")
    assert "did not work" in out.getvalue() and app.stop is False
