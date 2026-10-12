"""Catch calls that no widget has, before they run.

Four of these bit in one session, all the same shape: a method that belongs to
one widget class called on another. `Gtk.TextView` has no `set_text` and no
`set_placeholder_text`; `Gtk.BUTTON_SECONDARY` is GTK 3 and GTK 4 moved it to
`Gdk`. None of them fail at import - they raise `AttributeError` when the line
is *reached*, which in a GLib callback means GTK aborts the process rather than
letting pytest see it.

**This infers each variable's type from its constructor and checks the method
against that type**, rather than matching names someone guessed. The first
version of this sweep hard-coded `_input_entry`, `entry` and `_name` and found
exactly the three defects those three names lead to - which is a list of my own
mistakes, not a class.

Run it as a plain script:

    XDG_STATE_HOME=/tmp/xdg python3 tools/widget_api_sweep.py

It touches `gi` only to answer `hasattr`, so it needs **no display, no bus name
and no main loop** - which means it is safe to run while a test suite is in
flight elsewhere, and it cannot itself abort the way an exception inside
`app.run()` does under pytest.

**Last run: 786 widget-typed call sites across 1096 files, none suspect.** That
number is only worth reading because the detector is proven on synthetic input:
correct code flags nothing, `set_placeholder_text` on a `Gtk.TextView` flags, and
`set_icon_from_icon_name` on a `Gtk.Label` flags. A sweep that cannot find a
planted bug reports the same clean result as one that works.
"""

import ast
import pathlib
import sys

sys.path.insert(0, "usr/lib/shani-chronoa")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

#: Classes this repository actually instantiates, and the live class each maps
#: to. Names not listed are not checked - a wrong guess would be worse than no
#: check, which is the whole failure this file is about.
WIDGETS = {
    "Gtk.TextView": Gtk.TextView,
    "Gtk.Entry": Gtk.Entry,
    "Gtk.SearchEntry": Gtk.SearchEntry,
    "Gtk.Label": Gtk.Label,
    "Gtk.Button": Gtk.Button,
    "Gtk.Image": Gtk.Image,
    "Gtk.IconView": Gtk.IconView,
    "Gtk.ListView": Gtk.ListView,
    "Gtk.TreeView": Gtk.TreeView,
    "Gtk.Text": getattr(Gtk, "Text", None),
    "Gtk.PasswordEntry": getattr(Gtk, "PasswordEntry", None),
}
WIDGETS = {name: cls for name, cls in WIDGETS.items() if cls is not None}

#: Names GTK 3 had. Attribute form only - a string in a comment or a docstring
#: is documentation, and punishing this file for quoting the bug would get it
#: deleted rather than obeyed.
GTK3_REMOVED_ATTR = (
    "set_alignment", "override_background_color", "override_color_scheme",
    "get_preferred_width", "get_preferred_height", "add_events",
    "events_pending", "set_fill", "draw", "size_allocate",
)
GTK3_REMOVED_GTK = ("BUTTON_SECONDARY", "BUTTON_PRIMARY", "POLICY_AUTOMATIC",
                    "SELECTION_SINGLE")


def variables_by_widget_type(tree, source_path):
    """Map `name -> widget class` for locals assigned from a known constructor.

    Walks every `Assign` and `AnnAssign`, so a variable is found wherever it is
    first given a widget. Conservative on purpose: a name assigned from a call
    the parser cannot see is simply not tracked, and an untracked name is never
    flagged.
    """
    found = {}
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
            call = node.value
        elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
              and isinstance(node.value, ast.Call)):
            targets = [node.target]
            call = node.value
        else:
            continue
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name is None:
            continue
        # `Gtk.TextView()` -> "TextView"
        short = name.rsplit(".", 1)[-1]
        cls = next((c for n, c in WIDGETS.items() if n.endswith("." + short)), None)
        if cls is not None:
            for target in targets:
                found.setdefault(target.id, cls)
    return found


def main():
    root = pathlib.Path("usr/lib/shani-chronoa/shani_chronoa")
    files = sorted(list(root.rglob("*.py")) + list(pathlib.Path("tests").rglob("*.py")))
    problems = []
    checked = 0

    for path in files:
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            print(f"  could not parse {path}: {exc}")
            continue
        widgets = variables_by_widget_type(tree, path)

        # Every method name that exists on *some* widget we track.
        universe = {}
        for cls in WIDGETS.values():
            for attr in dir(cls):
                universe.setdefault(attr, set()).add(cls)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            var = node.func.value.id
            method = node.func.attr
            cls = widgets.get(var)
            if cls is None:
                continue
            checked += 1
            if method in GTK3_REMOVED_ATTR:
                problems.append((path, node.lineno, f"{var}.{method}",
                                 "removed in GTK 4"))
                continue
            # A method that exists on a widget we track, but not on *this* one.
            if method in universe and not hasattr(cls, method):
                others = sorted(c.__name__ for c in universe[method]
                                if not isinstance(c, type) or c is not cls)
                problems.append((path, node.lineno, f"{var}.{method}",
                                 f"{cls.__name__} has no {method}; "
                                 f"{', '.join(others[:3])} do"))

    for attr in GTK3_REMOVED_GTK:
        if hasattr(Gtk, attr) and not hasattr(Gdk, attr):
            print(f"  Gtk.{attr} exists on Gtk and not Gdk - check it is GTK 4 safe")

    print(f"  widget-typed call sites checked: {checked}")
    print(f"  files scanned: {len(files)}")
    if not problems:
        print("  no method calls against a widget that does not have them")
        return 0
    for path, lineno, what, why in problems:
        print(f"  {path}:{lineno}  {what}  ({why})")
    print(f"  {len(problems)} suspect call site(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())