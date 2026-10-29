"""Inside shani-shell, Chronoa's Bluetooth file receiver steps aside for the
shell's own (obexd has one agent slot; two receivers raced for it)."""
from shani_chronoa import obex_receive as ob


class _On:
    def get_bool(self, key, default):
        return True                    # phone-control-enabled is on


def _env(monkeypatch, tmp_path, desktop, shell):
    d = tmp_path / "xdg"
    (d / "quickshell" / "shani").mkdir(parents=True) if shell else d.mkdir(parents=True)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", desktop)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(d))


def test_steps_aside_inside_the_shell(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "niri", True)
    r = ob.ObexReceiver.__new__(ob.ObexReceiver)
    r.config = _On()
    assert ob.desktop_has_receiver() and r._allowed() is False


def test_keeps_receiving_elsewhere(monkeypatch, tmp_path):
    for desktop, shell in (("GNOME", True), ("niri", False), ("KDE", False)):
        _env(monkeypatch, tmp_path / desktop, desktop, shell)
        r = ob.ObexReceiver.__new__(ob.ObexReceiver)
        r.config = _On()
        assert r._allowed() is True, desktop
