"""Skill: see and use another app's buttons, fields and menus through the accessibility tree (AT-SPI).

`press_key` and `click_pointer` act blind - a keystroke or a coordinate - and
`screenshot` + OCR can only guess where a button is. Every GTK, Qt/KDE,
LibreOffice/OnlyOffice, Firefox and (with accessibility on) Chromium window
already publishes its controls on the AT-SPI bus with a role, a name, a value
and the actions it supports - the same interface Orca uses. So this reads that
tree and acts on elements by what they *are*: press "Save", put text in the
"Name" field, open File > Export. Harvested from qwen-code's cua-driver
(`get_window_state`, `set_value`, `invoke_menu`, `verify_state`), done with
`gi.repository.Atspi`, which both images ship (at-spi2-core).

Element ids are a path of child indexes ("3/0/1/2") under one app, valid for
the snapshot they came from; acting on one re-walks to it and checks the role
and name still match, so an id from a stale listing is refused rather than
pressing whatever moved into its place. Every action reports what the element
reads back afterwards.

Gated by `input-control-enabled` - acting on another app's controls is the
same permission as typing or clicking into it - and that covers listing too,
because the tree holds whatever the windows show (messages, documents).

Chromium/Electron only publish their tree when an assistive technology is
announced; this does not announce one, because on GNOME that request starts
the Orca screen reader (qwen-code's `a11y.rs` documents the same trap).
"""

from __future__ import annotations

import time

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_ACTIONS = ("apps", "inspect", "press", "set_text", "read", "menu")
MAX_ELEMENTS = 300
MAX_DEPTH = 30
_PRESS_NAMES = ("click", "press", "activate", "toggle", "jump", "open")
#: Roles worth listing even without an action: they say what a window is showing.
_CONTEXT_ROLES = {"label", "heading", "status bar", "alert", "notification", "text", "entry",
                  "password text", "spin button", "slider", "progress bar", "page tab", "list item",
                  "table cell", "combo box", "check box", "radio button", "toggle button", "link",
                  "menu item", "check menu item", "radio menu item", "push button", "button", "menu"}

SCHEMA = {
    "type": "function",
    "function": {
        "name": "ui_elements",
        "description": (
            "Use another app's controls by what they are, through the accessibility tree. apps: which apps "
            "are reachable. inspect: list a window's buttons, fields, menus and labels with ids (app = its "
            "name; query narrows by text). press: activate an element by id (a button, checkbox, link, menu "
            "item). set_text: put text in a field by id. read: an element's text or value. menu: open a menu "
            "path like 'File > Export'. Requires 'input-control-enabled'."
        ),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "enum": list(_ACTIONS)},
            "app": {"type": "string", "description": "The app's name as 'apps' lists it, e.g. 'gedit'."},
            "id": {"type": "string", "description": "An element id from inspect, e.g. '0/2/1'."},
            "text": {"type": "string"},
            "query": {"type": "string"},
            "path": {"type": "string", "description": "menu: e.g. 'File > Export'."},
        }, "required": ["action"]},
    },
}


class UIError(Exception):
    pass


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    if not config.input_control_enabled:
        return False, "using other apps' controls is turned off (enable 'input-control-enabled' in Settings)"
    return True, ""


def _atspi():
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError) as exc:
        raise UIError("the accessibility library (at-spi2-core) is not available") from exc
    return Atspi


def _apps(Atspi) -> list:
    desktop = Atspi.get_desktop(0)
    out = []
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is not None and (app.get_name() or "").strip():
            out.append(app)
    return out


def _find_app(Atspi, name: str):
    want = (name or "").strip().lower()
    if not want:
        raise UIError("say which app (the 'apps' action lists them)")
    apps = _apps(Atspi)
    exact = [a for a in apps if a.get_name().lower() == want]
    partial = [a for a in apps if want in a.get_name().lower()]
    pick = exact or partial
    if not pick:
        raise UIError(f"no app named {name!r} is on the accessibility bus; reachable: "
                      + ", ".join(sorted({a.get_name() for a in apps})[:20]))
    return pick[0]


def _states(acc, Atspi) -> set:
    try:
        ss = acc.get_state_set()
    except Exception:  # noqa: BLE001 - a dying element
        return set()
    names = {"showing": Atspi.StateType.SHOWING, "enabled": Atspi.StateType.ENABLED,
             "sensitive": Atspi.StateType.SENSITIVE, "checkable": Atspi.StateType.CHECKABLE,
             "checked": Atspi.StateType.CHECKED, "focused": Atspi.StateType.FOCUSED,
             "editable": Atspi.StateType.EDITABLE, "selected": Atspi.StateType.SELECTED,
             "visible": Atspi.StateType.VISIBLE, "pressed": Atspi.StateType.PRESSED}
    return {k for k, v in names.items() if ss.contains(v)}


# Interface methods are called as `Atspi.Text.get_text(acc, ...)`, never as
# `acc.get_text_iface().get_text(...)`: in PyGObject the iface object is the
# accessible itself, so `.get_text` resolves to the deprecated one-argument
# `Atspi.Accessible.get_text` and every read came back empty (found by running
# this against a real GTK4 window; a mock tree cannot show it).


def _has(acc, iface: str) -> bool:
    try:
        return iface in acc.get_interfaces()
    except Exception:  # noqa: BLE001
        return False


def _action_names(acc) -> list:
    Atspi = _atspi()
    if not _has(acc, "Action"):
        return []
    try:
        return [Atspi.Action.get_action_name(acc, i) for i in range(Atspi.Action.get_n_actions(acc))]
    except Exception:  # noqa: BLE001
        return []


def _value(acc) -> str:
    Atspi = _atspi()
    if _has(acc, "Text"):
        try:
            return Atspi.Text.get_text(acc, 0, Atspi.Text.get_character_count(acc))
        except Exception:  # noqa: BLE001
            pass
    if _has(acc, "Value"):
        try:
            return f"{Atspi.Value.get_current_value(acc):g}"
        except Exception:  # noqa: BLE001
            pass
    return ""


def _walk(app, Atspi):
    """(id, element, depth) for every element under `app` in showing subtrees, bounded."""
    out, stack = [], [(app, "", 0)]
    while stack and len(out) < MAX_ELEMENTS * 4:
        node, path, depth = stack.pop()
        if depth > MAX_DEPTH:
            continue
        try:
            count = node.get_child_count()
        except Exception:  # noqa: BLE001
            continue
        children = []
        for i in range(min(count, 500)):
            child = node.get_child_at_index(i)
            if child is None:
                continue
            cid = f"{path}/{i}" if path else str(i)
            st = _states(child, Atspi)
            role = child.get_role_name()
            if depth >= 1 and "showing" not in st and role not in ("menu", "menu item"):
                continue  # hidden subtrees are not what the person sees (closed menus stay walkable)
            out.append((cid, child, depth))
            children.append((child, cid, depth + 1))
        stack.extend(reversed(children))
    return out


def _resolve(app, element_id: str):
    node = app
    try:
        for part in element_id.strip().split("/"):
            node = node.get_child_at_index(int(part))
            if node is None:
                raise UIError(f"element {element_id} is no longer there; inspect again")
    except ValueError as exc:
        raise UIError(f"{element_id!r} is not an element id like 0/2/1") from exc
    return node


def _line(cid, acc, depth, Atspi) -> str:
    role, name = acc.get_role_name(), (acc.get_name() or "").strip()
    st = _states(acc, Atspi)
    bits = [f"[{cid}] {role}"]
    if name:
        bits.append(repr(name))
    value = _value(acc).strip()
    if value and value != name:
        bits.append(f"={value[:80]!r}")
    # GTK4 reports SENSITIVE and never ENABLED; GTK3/Qt report both
    usable = "enabled" in st or "sensitive" in st
    flags = [s for s in ("checked", "focused", "selected") if s in st] + ([] if usable else ["disabled"])
    if "checkable" in st and "checked" not in st:
        flags.append("unchecked")
    acts = [a for a in _action_names(acc) if a]
    if acts:
        bits.append("actions:" + ",".join(acts[:4]))
    if _has(acc, "EditableText") and "editable" in st:
        bits.append("editable")
    if flags:
        bits.append("(" + ", ".join(flags) + ")")
    return "  " * min(depth, 6) + " ".join(bits)


def _worth_listing(acc) -> bool:
    role = acc.get_role_name()
    return bool(_action_names(acc)) or _has(acc, "EditableText") or acc.get_role_name() in (
        "check box", "radio button", "toggle button", "menu item", "push button", "button") or (
        role in _CONTEXT_ROLES and bool((acc.get_name() or _value(acc)).strip())) or role in ("frame", "window", "dialog")


def _press(acc) -> str:
    """Do the element's press-like action; with none, focus it and press the key a person would.

    GTK4 publishes no AT-SPI action on a check box or a menu-bar item (it lists
    the Action interface with zero actions), so a press there is focus + Space
    (Return for a menu) - what the keyboard user does - sent through the
    portal on Wayland or XTest on X11, and then read back by the caller.
    """
    Atspi = _atspi()
    names = _action_names(acc)
    wanted = [i for i, n in enumerate(names) if n.lower() in _PRESS_NAMES]
    if wanted:
        if not Atspi.Action.do_action(acc, wanted[0]):
            raise UIError(f"the app refused the {names[wanted[0]]!r} action")
        return names[wanted[0]]
    focused = False
    if _has(acc, "Component"):
        try:
            focused = bool(Atspi.Component.grab_focus(acc))
        except Exception:  # noqa: BLE001 - GTK4 does not implement GrabFocus
            focused = False
    if focused:
        time.sleep(0.15)
        key = "return" if "menu" in acc.get_role_name() else "space"
        _send_key(key)
        return f"focus + {key}"
    return _click_center(acc)


def _click_center(acc) -> str:
    """Click the element's centre - X11 only, where a window's position is knowable.

    On Wayland no client may learn where a window is on screen, so a GTK4
    control with no action (a check box, a menu-bar item) cannot be reached
    this way, and saying so beats clicking somewhere else.
    """
    import os
    Atspi = _atspi()
    name = f"{acc.get_role_name()} {acc.get_name()!r}"
    if os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("DISPLAY"):
        raise UIError(f"{name} publishes no action, and on Wayland its screen position is hidden "
                      "(a GTK4 limit); focus it with Tab and use press_key, or use the app's keyboard shortcut")
    box = _screen_box(acc)
    if box is None:
        raise UIError(f"{name} publishes no action and no position")
    x, y, w, h = box
    if not Atspi.generate_mouse_event(int(x + w / 2), int(y + h / 2), "b1c"):
        raise UIError(f"could not click {name}")
    return "click at its centre"


def _screen_box(acc):
    """Screen extents; GTK4 reports window-relative ones, corrected by its frame (as shani-testbed's a11y_client)."""
    Atspi = _atspi()
    try:
        app = acc.get_application()
        gtk4 = app.get_toolkit_name() == "GTK" and str(app.get_toolkit_version()).startswith("4")
        if not gtk4:
            r = Atspi.Component.get_extents(acc, Atspi.CoordType.SCREEN)
            return r.x, r.y, r.width, r.height
        w = Atspi.Component.get_extents(acc, Atspi.CoordType.WINDOW)
        frame = acc
        while frame.get_parent() is not None and frame.get_parent().get_role_name() != "application":
            frame = frame.get_parent()
        f = Atspi.Component.get_extents(frame, Atspi.CoordType.WINDOW)
        s = Atspi.Component.get_extents(frame, Atspi.CoordType.SCREEN)
        return s.x + w.x - f.x, s.y + w.y - f.y, w.width, w.height
    except Exception:  # noqa: BLE001
        return None


def _send_key(name: str) -> None:
    import os
    if os.environ.get("WAYLAND_DISPLAY"):
        from shani_chronoa import portal
        try:
            with portal.RemoteInput(portal.KEYBOARD) as ri:
                ri.tap(portal.KEYSYMS[name])
        except portal.PortalError as exc:
            raise UIError(f"could not press {name}: {exc}") from exc
        return
    Atspi = _atspi()
    keysym = {"space": 0x20, "return": 0xFF0D}[name]
    if not Atspi.generate_keyboard_event(keysym, None, Atspi.KeySynthType.SYM):
        raise UIError(f"could not press {name}")


def _check_same(acc, element_id: str, expect: str) -> None:
    if expect and expect.lower() not in (f"{acc.get_role_name()} {acc.get_name()}").lower():
        raise UIError(f"element {element_id} is now {acc.get_role_name()} {acc.get_name()!r}, not {expect!r}; "
                      "inspect again")


def _run(arguments: dict) -> str:
    action = (arguments.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return f"action must be one of {', '.join(_ACTIONS)}."
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing: {reason}."
    try:
        Atspi = _atspi()
        if action == "apps":
            names = sorted({a.get_name() for a in _apps(Atspi)})
            return ("Apps on the accessibility bus: " + ", ".join(names)) if names else \
                "No app is publishing an accessibility tree on this session."
        app = _find_app(Atspi, arguments.get("app") or "")
        if action == "inspect":
            query = (arguments.get("query") or "").strip().lower()
            lines = []
            for cid, acc, depth in _walk(app, Atspi):
                if not _worth_listing(acc):
                    continue
                line = _line(cid, acc, depth, Atspi)
                if query and query not in line.lower():
                    continue
                lines.append(line)
                if len(lines) >= MAX_ELEMENTS:
                    lines.append(f"... more than {MAX_ELEMENTS}; give a query to narrow it.")
                    break
            return (f"{app.get_name()} - elements (ids are valid until the window changes):\n" + "\n".join(lines)) \
                if lines else f"{app.get_name()} shows no {'matching ' if query else ''}controls."
        if action == "menu":
            parts = [p.strip() for p in (arguments.get("path") or "").split(">") if p.strip()]
            if not parts:
                return "Give a menu path like 'File > Export'."
            used = []
            for part in parts:
                found = None
                # a GTK4 popover is not a child of its menu item, so each level re-walks the whole app
                for cid, acc, _d in _walk(app, Atspi):
                    if acc.get_role_name() in ("menu", "menu item", "check menu item", "radio menu item") and \
                            (acc.get_name() or "").strip().lower() == part.lower():
                        found = acc
                        break
                if found is None:
                    unnamed = [cid for cid, acc, _d in _walk(app, Atspi)
                               if acc.get_role_name().endswith("menu item") and not (acc.get_name() or "").strip()
                               and "showing" in _states(acc, Atspi)]
                    if used and unnamed:
                        # GTK4 popover menus publish their items with no accessible name
                        return (f"Opened {' > '.join(used)}, but its entries have no accessible names (a GTK4 "
                                f"popover menu), so {part!r} cannot be chosen by name. Unnamed entries: "
                                f"{', '.join(unnamed[:10])} - a screenshot shows which is which; press one by id.")
                    return f"No menu entry {part!r} after {' > '.join(used) or 'the menu bar'}."
                done = _press(found)
                used.append(part)
                time.sleep(0.3)  # let a submenu open before looking inside it
            return f"Chose {' > '.join(used)} in {app.get_name()} ({done})."
        element_id = (arguments.get("id") or "").strip()
        if not element_id:
            return "Give the element's id from inspect."
        acc = _resolve(app, element_id)
        _check_same(acc, element_id, (arguments.get("query") or "").strip())
        label = f"{acc.get_role_name()} {acc.get_name()!r}"
        if action == "read":
            return f"{label}: {_value(acc) or '(no text)'} [{', '.join(sorted(_states(acc, Atspi)))}]"
        if action == "press":
            done = _press(acc)
            time.sleep(0.2)
            return f"Pressed {label} ({done}); it now reads {_line(element_id, acc, 0, Atspi).strip()}."
        text = arguments.get("text")
        if text is None:
            return "Give the text to put in the field."
        if not _has(acc, "EditableText") or "editable" not in _states(acc, Atspi):
            return f"{label} is not an editable field."
        if not Atspi.EditableText.set_text_contents(acc, str(text)):
            return f"The app refused new text for {label}."
        after = _value(acc)
        return f"Set {label} to {after!r} (read back)." if after == str(text) else \
            f"Wrote to {label} but it reads back {after!r}, so this is not verified."
    except UIError as exc:
        return f"Could not do that: {exc}."


def _menu_children(node) -> list:
    out = []
    for i in range(node.get_child_count()):
        c = node.get_child_at_index(i)
        if c is None:
            continue
        out.append(c)
        if c.get_role_name() in ("menu", "panel", "filler", "popup menu", "scroll pane"):
            out.extend(_menu_children(c))
    return out


SKILLS = [Skill(name="ui_elements", schema=SCHEMA, run=_run)]
