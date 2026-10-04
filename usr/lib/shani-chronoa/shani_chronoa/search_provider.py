"""Ask Chronoa from the desktop's own search: GNOME Shell's overview and Plasma's KRunner.

One small D-Bus service, `dev.shani.chronoa.SearchProvider`, serves both:

- `org.gnome.Shell.SearchProvider2` at /dev/shani/chronoa/SearchProvider
  (declared by usr/share/gnome-shell/search-providers/shani-chronoa.ini);
- `org.kde.krunner1` at /runner
  (declared by usr/share/krunner/dbusplugins/shani-chronoa.desktop).

**The overview sends every keystroke to every enabled provider**, so this
process does nothing a keystroke should not cause: no network, no language
model, no log of the terms, no file it writes. It offers one result -
"Ask Chronoa: <query>" - and, when the query is plain arithmetic or a unit
conversion, the answer itself, computed by the same local skills Chronoa uses
(`calculate`, `convert_units`). Activating a result opens Chronoa with
`--ask=<query>`, through the window's own send path, so the question shows in
the transcript as if typed. A person can switch the provider off in GNOME
Settings > Search or Plasma's KRunner settings like any other.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

BUS_NAME = "dev.shani.chronoa.SearchProvider"
GNOME_PATH = "/dev/shani/chronoa/SearchProvider"
KRUNNER_PATH = "/runner"
MIN_CHARS = 3
MAX_QUERY = 300

_MATH = re.compile(r"^[\d\s.+\-*/^()%,]+$|^\s*(sqrt|sin|cos|tan|log|ln)\s*\(", re.I)
_CONVERT = re.compile(r"^\s*[-\d.,]+\s*[a-zA-Z°/²³ ]+\s+(?:to|in)\s+[a-zA-Z°/²³ ]+\s*$", re.I)


def _quick_answer(query: str) -> str:
    """An instant local answer for arithmetic or a unit conversion, else ''."""
    q = query.strip()
    try:
        if _MATH.search(q) and re.search(r"\d", q):
            from shani_chronoa.skills import calculate
            out = calculate.SKILLS[0].run({"expression": q})
        elif _CONVERT.match(q):
            from shani_chronoa.skills import convert_units
            m = re.match(r"^\s*([-\d.,]+)\s*(.+?)\s+(?:to|in)\s+(.+?)\s*$", q, re.I)
            out = convert_units.SKILLS[0].run({"value": float(m.group(1).replace(",", "")),
                                               "from_unit": m.group(2), "to_unit": m.group(3)})
        else:
            return ""
    except Exception as exc:  # noqa: BLE001 - a search provider must never raise into the shell
        logger.debug("quick answer failed: %s", exc)
        return ""
    out = (out or "").strip()
    if not out or re.search(r"\b(error|invalid|cannot|could not|unknown|not)\b", out.lower()):
        return ""
    return out.splitlines()[0][:120]


def results_for(query: str) -> "list[dict]":
    """The provider's whole behaviour, independent of D-Bus: [{id, name, description}]."""
    q = " ".join(query.split())[:MAX_QUERY]
    if len(q) < MIN_CHARS:
        return []
    out = []
    answer = _quick_answer(q)
    if answer:
        out.append({"id": f"answer:{q}", "name": answer, "description": f"Chronoa, locally: {q}"})
    out.append({"id": f"ask:{q}", "name": f"Ask Chronoa: {q}",
                "description": "Opens Chronoa and asks this - nothing is sent anywhere until you do"})
    return out


def activate(result_id: str) -> bool:
    """Open Chronoa with the query; True if it was launched."""
    _, _, q = result_id.partition(":")
    if not q:
        return False
    exe = shutil.which("shani-chronoa")
    if exe is None:
        return False
    subprocess.Popen([exe, f"--ask={q}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


INTROSPECTION = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet"><arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/></method>
    <method name="GetSubsearchResultSet"><arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/><arg type="as" name="results" direction="out"/></method>
    <method name="GetResultMetas"><arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" name="metas" direction="out"/></method>
    <method name="ActivateResult"><arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
    <method name="LaunchSearch"><arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/></method>
  </interface>
  <interface name="org.kde.krunner1">
    <method name="Actions"><arg name="matches" type="a(sss)" direction="out"/></method>
    <method name="Run"><arg name="matchId" type="s" direction="in"/><arg name="actionId" type="s" direction="in"/></method>
    <method name="Match"><arg name="query" type="s" direction="in"/>
      <arg name="matches" type="a(sssida{sv})" direction="out"/></method>
    <method name="Teardown"/>
    <method name="Config"><arg name="config" type="a{sv}" direction="out"/></method>
    <method name="SetActivationToken"><arg name="token" type="s" direction="in"/></method>
  </interface>
</node>
"""


class Provider:
    """D-Bus method handling for both interfaces; `results_for`/`activate` hold the logic."""

    def __init__(self) -> None:
        self._metas: dict = {}

    def _remember(self, results: "list[dict]") -> "list[str]":
        for r in results:
            self._metas[r["id"]] = r
        if len(self._metas) > 500:
            self._metas = dict(list(self._metas.items())[-200:])
        return [r["id"] for r in results]

    def handle(self, interface: str, method: str, args: tuple):
        """Returns (signature, value) for the reply, or (None, None) for no return value."""
        from gi.repository import GLib

        if interface == "org.gnome.Shell.SearchProvider2":
            if method in ("GetInitialResultSet", "GetSubsearchResultSet"):
                terms = args[-1]
                return "(as)", (self._remember(results_for(" ".join(terms))),)
            if method == "GetResultMetas":
                metas = []
                for ident in args[0]:
                    r = self._metas.get(ident)
                    if r:
                        metas.append({"id": GLib.Variant("s", ident), "name": GLib.Variant("s", r["name"]),
                                      "description": GLib.Variant("s", r["description"]),
                                      "gicon": GLib.Variant("s", "shani-chronoa")})
                return "(aa{sv})", (metas,)
            if method == "ActivateResult":
                activate(args[0])
                return None, None
            if method == "LaunchSearch":
                activate("ask:" + " ".join(args[0]))
                return None, None
        if interface == "org.kde.krunner1":
            if method == "Match":
                out = [(r["id"], r["name"], "shani-chronoa", 100, 0.9 if r["id"].startswith("answer:") else 0.5,
                        {"subtext": GLib.Variant("s", r["description"])}) for r in results_for(args[0])]
                return "(a(sssida{sv}))", (out,)
            if method == "Run":
                activate(args[0])
                return None, None
            if method == "Actions":
                return "(a(sss))", ([],)
            if method == "Config":
                return "(a{sv})", ({"MinLetterCount": GLib.Variant("i", MIN_CHARS)},)
            if method in ("Teardown", "SetActivationToken"):
                return None, None
        raise ValueError(f"unknown method {interface}.{method}")


def main() -> int:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    provider = Provider()
    node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION)
    loop = GLib.MainLoop()
    idle = {"source": None}

    def rearm():
        # Exit when unused: D-Bus activation starts it again on the next search.
        if idle["source"]:
            GLib.source_remove(idle["source"])
        idle["source"] = GLib.timeout_add_seconds(120, lambda: (loop.quit(), False)[1])

    def on_call(conn, sender, path, interface, method, params, invocation):
        rearm()
        try:
            sig, value = provider.handle(interface, method, params.unpack())
            invocation.return_value(GLib.Variant(sig, value) if sig else None)
        except Exception as exc:  # noqa: BLE001 - answer the shell with an error, never crash
            invocation.return_dbus_error("dev.shani.chronoa.Error", str(exc)[:200])

    def on_bus(conn, name):
        for path, iface in ((GNOME_PATH, node.interfaces[0]), (KRUNNER_PATH, node.interfaces[1])):
            conn.register_object(path, iface, on_call, None, None)

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, on_bus, None,
                     lambda *_: loop.quit())
    rearm()
    loop.run()
    return 0
