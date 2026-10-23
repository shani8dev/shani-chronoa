"""A portal probe left its own MainContext pushed on the GTK thread.

`portal.PortalClient()` pushes a fresh `GLib.MainContext` as the calling
thread's default and only `close()` pops it. Three callers never closed it:

- the Driving panel's portal row built a `RemoteInput` to see whether it could,
  and dropped it - on the GTK thread, at start-up (the panel was prebuilt);
- `RemoteInput.__enter__` raising (no RemoteDesktop on this desktop) skips
  `__exit__`, which is Python's rule for `with`;
- `ui_driver.portal_available()` asked for a version and dropped the client.

With a dead context left as the GTK thread's default, async D-Bus replies started
there afterwards went nowhere. Measured in a shani-testbed slot (2026-10-23):
every AT-SPI query to Chronoa timed out and the setup wizard's buttons never
reached a screen reader; bisected over all 39 panels to Driving, then to
`_portal_input()`.

A fake connection stands in for the session bus, so nothing here reaches a real
portal - but the push and the pop are the real `PortalClient`'s.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
from gi.repository import GLib  # noqa: E402

from shani_chronoa import portal, ui_driver  # noqa: E402


class _NoPortalBus:
    """A session bus on which the portal answers nothing."""

    def get_unique_name(self):
        return ":1.42"

    def call_sync(self, *args, **kwargs):
        raise GLib.Error("org.freedesktop.DBus.Error.ServiceUnknown")


def _client(**kwargs):
    return portal.PortalClient(connection=_NoPortalBus(), timeout=1.0, **kwargs)


@pytest.fixture
def context_before():
    before = GLib.MainContext.get_thread_default()
    yield before
    # never leave a pushed context behind for the next test, whatever failed
    while GLib.MainContext.get_thread_default() not in (None, before):
        GLib.MainContext.get_thread_default().pop_thread_default()


def _unchanged(before):
    return GLib.MainContext.get_thread_default() == before


def test_the_control_a_client_left_open_does_change_it(context_before):
    """Without this, the three tests below could pass on a no-op."""
    client = _client()
    assert not _unchanged(context_before)
    client.close()
    assert _unchanged(context_before)


def test_a_failed_session_start_pops_the_context(context_before):
    with pytest.raises(portal.PortalError):
        with portal.RemoteInput(client=_client()):
            pass
    assert _unchanged(context_before), "RemoteInput.__enter__ raised and left its context pushed"


def test_portal_available_pops_the_context(monkeypatch, context_before):
    monkeypatch.setattr(portal, "PortalClient", _make)
    assert ui_driver.portal_available() is False
    assert _unchanged(context_before), "portal_available() left its client's context pushed"


_REAL_CLIENT = portal.PortalClient


def _make(*_args, **_kwargs):
    return _REAL_CLIENT(connection=_NoPortalBus(), timeout=1.0)


def test_the_driving_panels_portal_row_pops_the_context(monkeypatch, context_before):
    pytest.importorskip("gi.repository.Gtk")
    from shani_chronoa.gui.surfaces import driving

    monkeypatch.setattr(ui_driver, "_portal_input",
                        lambda: portal.RemoteInput(client=_client()))
    row = driving._DrivingView._portal_row(None)
    assert row.label == "RemoteDesktop portal"
    assert _unchanged(context_before), "the Driving panel's portal row left its context pushed"
