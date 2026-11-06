"""Pin the Chronoa <-> shani-link --connect route contract.

Chronoa's palette/menu send bluetooth/android/kdeconnect/"" to
shani-link-app --connect=ROUTE; shani-link's connect_device.ROUTES
must accept all of them. A route Chronoa sends that shani-link does not
know opens the chooser instead of the intended page.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHANI_LINK = ROOT.parent / "shani-link"
sys.path.insert(0, str(ROOT / "usr" / "lib" / "shani-chronoa"))
sys.path.insert(0, str(SHANI_LINK))

from shani_chronoa import link_app  # noqa: E402


def test_every_route_chronoa_sends_is_accepted_by_shani_link():
    # shani-link's ROUTES, read from its own module so a rename fails here.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "connect_device",
        str(SHANI_LINK / "shani_link" / "app" / "connect_device.py"),
    )
    cd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cd)
    routes = set(cd.ROUTES)
    sent = {"", "bluetooth", "android", "kdeconnect"}
    assert sent <= routes | {""}, (
        "Chronoa sends a route shani-link does not know: "
        f"{sent - (routes | {''})}"
    )


def test_argv_carries_each_route():
    for r in ("", "bluetooth", "android", "kdeconnect"):
        out = link_app.argv(connect=r)
        if r == "":
            assert out == ["shani-link-app", "--connect"]
        else:
            assert out == ["shani-link-app", f"--connect={r}"]