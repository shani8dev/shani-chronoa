"""The phone over plain Bluetooth (`phone_bluez`): the parts checkable without one.

Measured against a real phone on 2026-10-08 (see AGENTS.md); these pin the
formats so a later edit cannot quietly break them.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import phone as ph  # noqa: E402
from shani_chronoa import phone_bluez as pb  # noqa: E402


def test_bmessage_length_counts_begin_msg_through_end_msg_in_bytes():
    """A wrong LENGTH is the commonest reason a phone rejects a pushed SMS."""
    out = pb.bmessage("+919800000000", "héllo")
    body = "BEGIN:MSG\r\nhéllo\r\nEND:MSG\r\n"
    assert f"LENGTH:{len(body.encode('utf-8'))}\r\n{body}END:BBODY" in out
    assert len(body.encode("utf-8")) == 28          # é is two bytes, not one
    assert "TEL:+919800000000\r\n" in out and "TYPE:SMS_GSM" in out
    assert out.startswith("BEGIN:BMSG\r\n") and out.endswith("END:BMSG\r\n")


def test_vcards_are_parsed_with_folding_and_without_nameless_cards():
    text = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Asha\r\n Rao\r\nTEL;TYPE=CELL:+91 98000 00001\r\n"
            "TEL:tel:022-1234567\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nTEL:111\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:No Number\r\nEND:VCARD\r\n")
    assert pb.parse_vcards(text) == [("AshaRao", ("+91 98000 00001", "022-1234567"))]


def test_a_number_with_and_without_its_country_code_is_one_conversation():
    """The phone's ConversationId was the same for all 225 messages, so the
    correspondent is the key."""
    assert pb.thread_key("+919876543210") == pb.thread_key("09876543210") == "876543210"
    assert pb.thread_key("Agent@rbm.goog ") == "agent@rbm.goog"
    assert pb.thread_key("+919876543210") != pb.thread_key("+919876543211")


def test_map_timestamps_become_epoch_milliseconds():
    from datetime import datetime
    assert pb._epoch_ms("20261008T143005") == int(datetime(2026, 10, 8, 14, 30, 5).timestamp() * 1000)
    assert pb._epoch_ms("garbage") == 0


def test_the_permission_refusal_names_the_phone_setting_to_turn_on():
    why = pb._why(Exception("OBEX Connect failed with 0x46"), "pbap")
    assert "Contacts and call history" in why


def test_bluetooth_is_the_fallback_only_when_no_app_link_is_installed(monkeypatch):
    monkeypatch.setattr(ph, "_gsconnect_daemon", lambda: None)
    monkeypatch.setattr(ph.shutil, "which", lambda name: None)
    monkeypatch.setattr(pb, "phones", lambda: [("74:6B:AB:67:7F:91", "acer ZX", True)])
    assert ph.backend() == "bluetooth" and ph.places_calls()
    monkeypatch.setattr(pb, "phones", lambda: [])
    assert ph.backend() is None
    monkeypatch.setattr(ph, "_gsconnect_daemon", lambda: "/x/daemon.js")
    monkeypatch.setattr(pb, "phones", lambda: pytest.fail("an app link must win over Bluetooth"))
    assert ph.backend() == "gsconnect" and not ph.places_calls()


def test_ringing_over_bluetooth_is_refused_and_not_attempted(monkeypatch):
    monkeypatch.setattr(ph, "backend", lambda: "bluetooth")
    monkeypatch.setattr(pb, "send_file", lambda *a: pytest.fail("ring must not send anything"))
    ok, why = ph.act(ph.Device("74:6B:AB:67:7F:91", "acer ZX", True, True), "ring")
    assert not ok and "GSConnect or KDE Connect" in why


def test_call_history_reads_kind_and_time_from_the_irmc_property():
    text = ("BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Asha\r\nTEL:+919800000001\r\n"
            "X-IRMC-CALL-DATETIME;MISSED:20261008T101010\r\nEND:VCARD\r\n"
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:\r\nTEL:+919800000009\r\n"
            "X-IRMC-CALL-DATETIME;TYPE=DIALED:20261007T090000\r\nEND:VCARD\r\n")
    got = pb.parse_call_history(text)
    assert [(c["name"], c["number"], c["kind"]) for c in got] == [
        ("Asha", "+919800000001", "missed"), ("", "+919800000009", "dialed")]
    assert got[0]["date"] == pb._epoch_ms("20261008T101010")


def test_the_phone_panel_asks_nothing_while_its_switch_is_off(monkeypatch):
    pytest.importorskip("gi")
    from shani_chronoa.gui.surfaces import phone as panel
    monkeypatch.setattr(ph, "devices", lambda: pytest.fail("the phone was asked with the switch off"))

    class Off:
        def get_bool(self, key, default=False):
            return False

    class App:
        config = Off()
    page = panel.build(App())
    status = page.status() if callable(page.status) else page.status
    assert status == "off", status


def test_text_from_the_phone_cannot_disguise_a_link(monkeypatch):
    """U+202E makes "gpj.exe" display as "exe.jpg"; such characters are removed
    before any message or contact name is shown (rule from Maze Connect)."""
    sneaky = "see photo‮gpj.exe and ⁦x⁩\x07"
    assert ph.clean_text(sneaky) == "see photogpj.exe and x"
    assert ph.clean_text("line one\nline two\ttab") == "line one\nline two\ttab"
    monkeypatch.setattr(ph, "_conversations", lambda d: [ph.Message("1", ("+91‮98",), sneaky, 1, True, False)])
    m = ph.conversations(None)[0]
    assert "‮" not in m.body and m.addresses == ("+9198",)
    monkeypatch.setattr(ph, "_contacts", lambda d: [("Bad‮Name", ("1",))])
    assert ph.contacts(None) == [("BadName", ("1",))]


# --- the phone's music over AVRCP --------------------------------------------

class _Run:
    """`_run` stand-in: answers get-property from a state the calls change."""

    def __init__(self, state="paused", obeys=True):
        self.state, self.obeys, self.calls = state, obeys, []

    def __call__(self, argv):
        import subprocess
        self.calls.append(argv)
        if "get-property" in argv:
            prop = argv[-1]
            out = f's "{self.state}"' if prop == "Status" else 'a{sv} 2 "Title" s "Song" "Artist" s "Band"'
            return subprocess.CompletedProcess(argv, 0, out, "")
        if "call" in argv and self.obeys:
            self.state = {"Play": "playing", "Pause": "paused"}.get(argv[-1], self.state)
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_play_that_changes_nothing_is_not_reported_as_done(monkeypatch):
    """Measured: Play accepted, player still paused (no music app open)."""
    monkeypatch.setattr(pb, "_run", _Run(obeys=False))
    ok, why = pb.avrcp("/org/bluez/hci0/dev_X/avrcp/player0", "play")
    assert not ok and "still paused" in why


def test_play_that_takes_effect_is_done_and_toggle_reads_the_state(monkeypatch):
    run = _Run()
    monkeypatch.setattr(pb, "_run", run)
    assert pb.avrcp("/p", "play") == (True, "")
    assert pb.avrcp("/p", "toggle") == (True, "") and run.state == "paused"
    assert pb.avrcp_status("/p") == ("Paused", "Song by Band")


def test_media_control_falls_back_to_the_phone_when_no_mpris_player_runs(monkeypatch):
    from shani_chronoa.skills import media_control as mc
    monkeypatch.setattr(mc, "players", lambda: [])
    monkeypatch.setattr(mc.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(pb, "avrcp_players", lambda: [("/p", "acer ZX")])
    monkeypatch.setattr(pb, "_run", _Run(state="playing"))
    assert mc._run({"action": "status"}) == "acer ZX is playing: Song by Band."
    assert mc._run({"action": "pause"}) == "Done: pause on acer ZX."
