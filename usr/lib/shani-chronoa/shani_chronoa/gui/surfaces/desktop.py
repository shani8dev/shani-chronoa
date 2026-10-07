"""Desktop: is any of Chronoa's desktop integration actually connected right now.

One question per row - *is this piece of Chronoa wired to the desktop on this
machine?* - and every answer read from the thing that would make it true rather
than from a copy of it. Six rows, because six questions:

| row | read from |
|---|---|
| Search provider service file | the `.service` file's own path, and the `Exec=` binary it points at |
| Answering on the session bus | `busctl --user --list` (one call, shared with the keyring row) |
| Global shortcut | `global-shortcut-enabled`, plus a standing admission |
| Document search | `document-search-enabled`, and `tracker3` / `baloosearch6` / `localsearch` on `PATH` |
| Desktop keyring | `secret_store.py`'s own availability probe, and `org.freedesktop.secrets` |
| Open with Chronoa | the installed `.desktop` entry, and the `MimeType` list in it |

**Every row leads with the evidence it used**, and the vocabulary is closed:
`installed`, `not installed`, `running`, `not running`, `could not ask`. A row
cannot invent a fourth word, so "the file is on disk" and "a service is
answering" can never collapse into one another, and a probe that blew up cannot
render as a clean negative.

**"Could not ask" is a state, and it is never a negative.** `busctl` exits
non-zero for perfectly ordinary outcomes and prints nothing useful on stdout
when it cannot reach the bus at all, so the exit code alone cannot answer the
question - measured on this machine, `busctl --user --list` against a
`DBUS_SESSION_BUS_ADDRESS` naming a socket that does not exist exits 1 with
*empty stdout* and `Failed to connect to bus: No such file or directory` on
stderr. So a listing that parsed is a real answer and anything else is
"could not ask", with busctl's own first stderr line as the reason. This is the
same rule `surfaces/daemon.py` applies to `systemctl is-*`, for the same
measured reason: reading a failure as a fact is how a healthy machine gets a
panel insisting it is broken.

**"Not running" for the search provider is not a verdict on it.** The provider
is D-Bus *activated* (`usr/share/dbus-1/services/...SearchProvider.service`) and
`search_provider.py:main()` exits after two idle minutes, so between two searches
there is deliberately nothing on the bus. The row says that in the same
sentence as the finding, because "not running" read alone sends someone to
reinstall a package that is working.

**The global shortcut is the one row whose answer is a permanent "could not
ask", and it says why in those words.** `app/desktop_integration.py` binds it
over `GlobalShortcuts` on a thread of its own and logs either the bind or the
`PortalError` that stopped it; the bind lives inside the desktop portal and
inside that thread, so **a portal bind cannot be proven from outside the
process**. This panel therefore reports the part that *is* knowable - what
`global-shortcut-enabled` says - and refuses to upgrade it into a claim that
the shortcut works. Reading the gate is not calling the portal: nothing here
shows a dialog, binds a shortcut, or touches a portal at all.

**The keyring is asked in two halves, because one half is not enough.**
`secret_store.py` needs libsecret's Python typelib *and* a Secret Service
answering on the session bus; either alone is not a keyring, and reporting
either alone would be a plausible-looking wrong answer. So the row reads
`secret_store._secret()` - that module's own "is there anything to ask with"
probe, which is an import and cannot block - and then looks for
`org.freedesktop.secrets` in the bus listing (the Secret Service specification's
own name, which is what gnome-keyring and KWallet's compatibility service both
publish). A keyring that is present but *locked* is not distinguished: this
panel does not unlock anything, and says so rather than implying the keys are
readable.

**Read once, when the panel is built, and never written.** One `busctl --list`
for the whole panel, a handful of `PATH` lookups and three file reads; no
polling, no threads, no timer. Every subprocess carries a five second timeout
and every failure path is a sentence - nothing here raises, because a surface
that raises takes the window with it and the window is the product. No gate is
written from this panel: `global-shortcut-enabled` and `document-search-enabled`
are granted in Settings, and the Settings and Privacy panels are where that
happens.

**Built from `surfaces/common.py`, like every other panel**, and the strings are
escaped on exactly the condition `common.row()` itself branches on: an
`Adw.PreferencesRow` parses its title and subtitle as markup (measured on
libadwaita 1.5, where `use-markup` defaults to true), while the no-Adw fallback
is a `Gtk.Label`, which takes no markup and would print the entities themselves.
"""

from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from shani_chronoa import markdown_lite, secret_store  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Desktop"
ICON = "applications-system-symbolic"
SECTION = "Health and trust"

SUBTITLE = (
    "Is Chronoa actually hooked into this desktop right now? Every row is read "
    "from the thing that would make it true, and says which."
)

#: The D-Bus name the search provider owns. The same constant
#: `search_provider.py:BUS_NAME` uses; named here rather than imported so that
#: reading this panel cannot construct a provider or a bus connection.
SEARCH_PROVIDER_NAME = "dev.shani.chronoa.SearchProvider"

#: The activation file that teaches D-Bus to start the provider at all. A
#: provider with no service file is not "off", it is unreachable: no keystroke
#: can ever arrive, whatever the desktop thinks is enabled.
SERVICE_FILE = f"/usr/share/dbus-1/services/{SEARCH_PROVIDER_NAME}.service"

#: The Secret Service's own bus name, from the freedesktop specification.
#: gnome-keyring and KWallet's Secret Service compatibility both publish it, so
#: this one name covers both desktops Chronoa ships on.
SECRET_SERVICE_NAME = "org.freedesktop.secrets"

#: The "Open with Chronoa" entry. The same path the app checks before it will
#: symlink an autostart entry (`app/desktop_integration.py`:
#: `_INSTALLED_DESKTOP_FILE`), so a missing file here is also why autostart
#: silently does nothing.
DESKTOP_ENTRY = "/usr/share/applications/shani-chronoa.desktop"

#: The gate `app/desktop_integration.py:_start_global_shortcut` reads before it
#: asks the portal for anything. It is the same key the settings switch writes.
SHORTCUT_KEY = "global-shortcut-enabled"

#: The gate `skills/search_documents.py:_consent` refuses behind.
DOCUMENT_KEY = "document-search-enabled"

#: The content-search indexers this panel looks for. `tracker3` is GNOME's
#: Tracker (older GNOME, and the GNOME image as built), `baloosearch6` is
#: Plasma's Baloo; `skills/search_documents.py:backend()` tries `localsearch`
#: first on GNOME, so a machine that only has `localsearch` - current GNOME -
#: must not be told it has no index.
INDEX_TOOLS = ("tracker3", "baloosearch6")
LOCALSEARCH = "localsearch"

#: What a well-known or unique bus name looks like, so a line of error text that
#: reached stdout is not read as a name. Measured, not assumed: dbus itself
#: rejects a dotless destination before the message is even built -
#: `dbus-send --dest=bogus` answers `invalid value (bogus) of "--dest"`, while
#: `--dest=org.example.bogus` gets as far as sending. So: a unique name
#: (`:1.264973`) or at least two dot-separated elements, each starting with a
#: letter or an underscore. Measured too: the NAME column is 47 characters wide,
#: so this panel's 32-character provider name and 24-character secret-service
#: name are both printed in full and never elided.
_BUS_NAME = re.compile(r"^(?::[0-9]+(?:\.[0-9]+)*"
                       r"|[A-Za-z_-][A-Za-z0-9_-]*(?:\.[A-Za-z_-][A-Za-z0-9_-]*)+)$")

#: Short, because this is a panel somebody is looking at: a `busctl` that has
#: blocked for ten seconds is a window that has stopped repainting.
BUSCTL_TIMEOUT = 5

#: The closed vocabulary of evidence words. Exposed so a caller - and this
#: surface's own tests - can assert that a row led with one of ours rather than
#: with a phrase that happens to read like an answer.
EVIDENCE_INSTALLED = "installed"
EVIDENCE_NOT_INSTALLED = "not installed"
EVIDENCE_RUNNING = "running"
EVIDENCE_NOT_RUNNING = "not running"
EVIDENCE_COULD_NOT_ASK = "could not ask"
EVIDENCE_WORDS = (
    EVIDENCE_INSTALLED,
    EVIDENCE_NOT_INSTALLED,
    EVIDENCE_RUNNING,
    EVIDENCE_NOT_RUNNING,
    EVIDENCE_COULD_NOT_ASK,
)

#: Css class on every row, so a test finds rows by walking the built tree
#: rather than off a list this module stashed on itself - which is the count read
#: back out of the thing being counted, the mistake AGENTS.md records a deleted
#: test for. None of these classes is styled anywhere in the tree; they are
#: markers, and saying so is what keeps them honest.
ROW_CSS = "desktop-row"
VALUE_CSS = "desktop-value"

#: The titles, in the order they are shown. Six questions, six rows, and a
#: seventh thing that looks like a row and is not (the footer) is how a panel
#: grows a row nobody asked for.
ROW_TITLES = (
    "Search provider service file",
    "Answering on the session bus",
    "Global shortcut",
    "Document search",
    "Desktop keyring",
    "Open with Chronoa",
    "Tray icon",
)

_FOOTER = (
    "Read once, when this panel was built; reopen it to ask again. Nothing here "
    "is started, stopped or bound - the panel asks the desktop what is already "
    "true and changes none of it. A portal bind cannot be proven from outside "
    "the process, so the global shortcut row reports the setting and stops "
    "there."
)


def _text(value: Any) -> str:
    """`value` escaped for the row shape `common.row()` is about to build.

    `common.row()` branches on `common.adw_ready()` - an `Adw.ActionRow` when it
    is true, a plain `Gtk.Box` of `Gtk.Label`s when it is not - and that is the
    same condition used here rather than a proxy for it, because the two shapes
    want opposite treatments (see the module docstring).
    """
    return markdown_lite.escape(str(value)) if common.adw_ready() else str(value)


def _find(node: Gtk.Widget, wanted) -> Optional[Gtk.Widget]:
    """The first descendant (or `node`) that `wanted` accepts, or None.

    libadwaita builds a row's title and subtitle labels itself, inside boxes of
    its own, so this module cannot hold a reference to the subtitle one.
    """
    if wanted(node):
        return node
    child = node.get_first_child()
    while child is not None:
        found = _find(child, wanted)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _is_subtitle(node: Gtk.Widget) -> bool:
    """libadwaita gives a row's subtitle label the `subtitle` CSS class."""
    return isinstance(node, Gtk.Label) and "subtitle" in node.get_css_classes()


def _value_label(row: Gtk.Widget) -> Optional[Gtk.Label]:
    """Mark the label carrying this row's answer with `VALUE_CSS`.

    A missing marker costs nothing but a walk, so if a future libadwaita stops
    naming that label `subtitle` the panel is unaffected and the tests fail
    loudly - which is the right way round.
    """
    label = _find(row, _is_subtitle)
    if not isinstance(label, Gtk.Label):
        logger.debug("no subtitle label to mark on %s", type(row).__name__)
        return None
    label.add_css_class(VALUE_CSS)
    return label


def _tray_sentence() -> str:
    """Whether a tray icon exists here, and what is missing if not.

    GTK4 removed `Gtk.StatusIcon`, so the icon needs libappindicator. Saying so
    is the point: a tray that silently does not appear looks like a bug in the
    assistant rather than a missing package, and the fix is one `pacman -S`.
    """
    from shani_chronoa.app import tray
    usable, why = tray.available()
    if usable:
        return f"yes - {why}"
    return f"not installed - {why}"


def _row(title: str, value: str) -> Gtk.Widget:
    """One row, filled in at construction and never patched afterwards.

    `Gtk.Box` has no `set_subtitle` and no `add` (measured on GTK 4), so a row
    built and then given its subtitle would raise on the no-Adw fallback path.
    """
    widget = common.row(_text(title), _text(value))
    widget.add_css_class(ROW_CSS)
    _value_label(widget)
    return widget


def _add_row(container: Gtk.Widget, row: Gtk.Widget) -> None:
    """Put `row` into a group built by `common.group()`.

    `Adw.PreferencesGroup` is a `Gtk.ListBox` and takes `add`; the plain
    `Gtk.Box` the no-Adw path returns takes `append` on GTK4, where `add` no
    longer exists (measured).
    """
    adder = getattr(container, "add", None)
    if callable(adder):
        adder(row)
    else:
        container.append(row)


# -- the session bus ---------------------------------------------------------

def _bus_names() -> Tuple[Optional[List[str]], str]:
    """Every name on this user's session bus, or `(None, why)` if unanswerable."""
    if shutil.which("busctl") is None:
        return None, "busctl is not installed on this machine"
    try:
        proc = subprocess.run(
            ["busctl", "--user", "--list", "--no-legend"],
            capture_output=True, text=True, timeout=BUSCTL_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"busctl did not answer within {BUSCTL_TIMEOUT}s"
    except OSError as exc:
        return None, f"busctl could not be run ({exc})"
    stdout = proc.stdout or ""
    if proc.returncode != 0 or not stdout.strip():
        noise = ((proc.stderr or "").strip().splitlines()
                 or [f"busctl exited {proc.returncode}"])[0]
        return None, noise[:160]
    names = []
    for line in stdout.splitlines():
        first = line.split()
        if first and _BUS_NAME.match(first[0]):
            names.append(first[0])
    if not names:
        return None, "busctl printed no bus name this panel could read"
    return names, ""


def _status_row(recorder: "common.StatusRecorder",
                names: Optional[List[str]], problem: str) -> Gtk.Widget:
    """The panel's own health, from the desktop integration it measures.

    Written through `recorder` so the dot on the sidebar's row reads the same
    word this row does, rather than a second reading of the same bus listing.
    """
    if names is None:
        return recorder.row(
            common.STATUS_UNKNOWN,
            "The desktop integration could not be read",
            problem or "the session bus did not answer")
    if SEARCH_PROVIDER_NAME in names:
        return recorder.row(
            common.STATUS_OK,
            "The desktop integration is answering",
            f"{SEARCH_PROVIDER_NAME} is on the session bus")
    # Off the bus is the normal state between searches - D-Bus starts the
    # provider on demand and it exits after two idle minutes, as this panel's
    # own row says. Headlining that in red put "Needs attention" above a row
    # explaining that it was not evidence of anything. The fault worth the red
    # word is the one that stops D-Bus starting it at all: no service file.
    fields, why = _service_fields()
    if fields is not None:
        return recorder.row(
            common.STATUS_OK,
            "The desktop integration is installed",
            f"{SEARCH_PROVIDER_NAME} starts when the desktop searches, and is "
            "not running right now")
    return recorder.row(
        common.STATUS_ATTENTION,
        "The desktop search provider is not installed",
        why or f"{SEARCH_PROVIDER_NAME} is not on the session bus")


# -- row 1 and 2: the desktop search provider -------------------------------

def _service_fields() -> Tuple[Optional[Dict[str, str]], str]:
    """`{key: value}` from the service file, or `(None, why it is not there)`.

    `FileNotFoundError` and every other `OSError` are kept apart: a file that is
    absent is a fact about the install, and a file that cannot be read is not,
    and a panel that says "not installed" about an unreadable file sends someone
    to reinstall a package that is present.
    """
    try:
        text = Path(SERVICE_FILE).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None, f"there is no {SERVICE_FILE} on this machine"
    except OSError as exc:
        return None, f"{SERVICE_FILE} could not be read ({exc})"
    fields: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    return fields, ""


def _service_sentence() -> str:
    fields, problem = _service_fields()
    if fields is None:
        return (f"{EVIDENCE_NOT_INSTALLED} - {problem}, so the desktop has "
                "nothing to activate and no keystroke can reach Chronoa.")
    named = fields.get("Name", "")
    if named and named != SEARCH_PROVIDER_NAME:
        return (f"{EVIDENCE_INSTALLED} - {SERVICE_FILE} is there, but its Name= "
                f"is {named!r} rather than {SEARCH_PROVIDER_NAME!r}, so it would "
                "answer a different name than the one the desktop looks for.")
    exec_line = fields.get("Exec", "")
    if not exec_line:
        return (f"{EVIDENCE_INSTALLED} - {SERVICE_FILE} is there but has no "
                "Exec= line, so D-Bus activation has nothing to run.")
    try:
        argv = shlex.split(exec_line)
    except ValueError as exc:
        return (f"{EVIDENCE_INSTALLED} - {SERVICE_FILE} is there but its Exec= "
                f"line could not be read as an argument list ({exc}).")
    if not argv:
        return (f"{EVIDENCE_INSTALLED} - {SERVICE_FILE} is there but its Exec= "
                "line names no program.")
    program = argv[0]
    if shutil.which(program) is None:
        return (f"{EVIDENCE_INSTALLED} - {SERVICE_FILE} is there, but the "
                f"program its Exec= line names, {program}, is not on this "
                "machine, so D-Bus activation would fail.")
    return (f"{EVIDENCE_INSTALLED} - {SERVICE_FILE} is there and its Exec= line "
            f"names {program}, which is on this machine.")


def _bus_sentence(names: Optional[List[str]], problem: str, name: str,
                  what: str, standing: str) -> str:
    """`running` / `not running` / `could not ask` for one well-known bus name."""
    if names is None:
        return f"{EVIDENCE_COULD_NOT_ASK} - {problem}, so this panel cannot say whether {what}."
    if name in names:
        return f"{EVIDENCE_RUNNING} - {name} is on the session bus right now, so {what}."
    return f"{EVIDENCE_NOT_RUNNING} - {name} is not on the session bus right now, so {what}. {standing}"


def _provider_bus_sentence(names: Optional[List[str]], problem: str) -> str:
    return _bus_sentence(
        names, problem, SEARCH_PROVIDER_NAME,
        "Chronoa answers searches from the GNOME overview and from KRunner",
        "That is the expected state between two searches: the provider is "
        "started on demand by D-Bus and exits after two idle minutes, so this "
        "is not by itself evidence that it is broken.",
    )


# -- row 3: the global shortcut ---------------------------------------------

#: The sentence that has to appear on the shortcut row, in these words. It is
#: the whole reason that row cannot report a working shortcut, and a
#: reworded version of it is the version that reads as a reassurance.
UNPROVABLE_NOTE = "a portal bind cannot be proven from outside the process"


def _gate(config: Any, key: str) -> Tuple[Optional[bool], str]:
    """`(value, "")` when the key could be read; `(None, why not)` otherwise.

    Fails closed and never raises: `get_bool` is the call the settings window's
    own `_read_bool` makes, and an unreadable gate is not a granted one.
    """
    if config is None:
        return None, "this window has no settings to read it with"
    check = getattr(config, "get_bool", None)
    if not callable(check):
        return None, "these settings cannot answer a boolean key"
    try:
        return bool(check(key, False)), ""
    except Exception as exc:  # noqa: BLE001 - an unreadable gate is a closed gate
        logger.debug("reading %s raised", key, exc_info=True)
        return None, f"reading it raised {type(exc).__name__}: {exc}"


def _shortcut_sentence(config: Any) -> str:
    """The gate's own reading, and the standing admission that it is all we have."""
    value, problem = _gate(config, SHORTCUT_KEY)
    if value is None:
        return (f"{EVIDENCE_COULD_NOT_ASK} - {SHORTCUT_KEY} could not be read "
                f"({problem}); nothing was asked of the desktop, and "
                f"{UNPROVABLE_NOTE}: app/desktop_integration.py binds it over "
                "GlobalShortcuts on a thread of its own and logs either the bind "
                "or the PortalError that stopped it.")
    said = "on" if value else "off"
    tail = ("Chronoa asks the desktop for it when it starts, and the desktop "
            "shows its own dialog the first time"
            if value else
            "Chronoa asks for no shortcut at all, so none is bound")
    return (f"{EVIDENCE_COULD_NOT_ASK} - the setting {SHORTCUT_KEY} says {said}. "
            f"{tail}, and {UNPROVABLE_NOTE}: only Chronoa's own thread can say "
            "whether the desktop accepted it. This panel read a setting and "
            "nothing else; it did not ask a portal for anything.")


# -- row 4: document search -------------------------------------------------

def _index_tools() -> List[str]:
    """The content-search indexers this machine actually has."""
    found = [tool for tool in INDEX_TOOLS if shutil.which(tool)]
    if shutil.which(LOCALSEARCH) and LOCALSEARCH not in found:
        found.append(LOCALSEARCH)
    return found


def _document_sentence(config: Any) -> str:
    value, problem = _gate(config, DOCUMENT_KEY)
    tools = _index_tools()
    if value is None:
        gate = (f"The setting {DOCUMENT_KEY} could not be read ({problem}), so "
                "this panel does not say whether Chronoa is allowed to ask it.")
    else:
        gate = (f"The setting {DOCUMENT_KEY} is {'on' if value else 'off'}, so "
                f"Chronoa {'may' if value else 'will refuse to'} ask the index.")
    if not tools:
        return (f"{EVIDENCE_NOT_INSTALLED} - neither {' nor '.join(INDEX_TOOLS)} "
                "is on this machine's PATH. " + gate + " With no indexer "
                "installed, skills/search_documents.py has nothing to ask - "
                "which is not the same as finding nothing. Searching file names "
                "still works (find_files), which needs no permission.")
    return (f"{EVIDENCE_INSTALLED} - {' and '.join(tools)} "
            f"{'are' if len(tools) > 1 else 'is'} on this machine, so the "
            "desktop's own content index is here. " + gate + " An index that is "
            "empty, still building or switched off in the desktop's search "
            "settings is reported by the skill as unavailable, not as no "
            "matches.")


# -- row 5: the desktop keyring ---------------------------------------------

def _keyring_sentence(names: Optional[List[str]], problem: str) -> str:
    """Two halves, because either half alone is not a keyring.

    The typelib half is read through `secret_store.py`'s own `_secret()`, which
    is that module's answer to "is there anything here to ask with": it imports
    `gi.repository.Secret` and builds a schema, and returns `(None, None)` when
    the typelib is absent. It is an import and a schema construction - no bus
    call, no keyring read, nothing that could block the panel - which is why it
    is safe here where `secret_store.get()` is not.
    """
    try:
        Secret, _schema = secret_store._secret()
    except Exception as exc:  # noqa: BLE001 - the probe is not trusted to hold
        logger.debug("secret_store probe raised", exc_info=True)
        return (f"{EVIDENCE_COULD_NOT_ASK} - secret_store.py's own availability "
                f"probe raised {type(exc).__name__}: {exc}, so this panel cannot "
                "say whether there is a keyring.")
    if Secret is None:
        return (f"{EVIDENCE_NOT_INSTALLED} - libsecret's Python typelib is not "
                "available, so secret_store.py has nothing to ask with whatever "
                "the session bus says. API keys stay in GSettings, which is "
                "exactly how they behaved before the keyring existed.")
    return _bus_sentence(
        names, problem, SECRET_SERVICE_NAME,
        "secret_store.py can read and store API keys in the desktop keyring",
        "There is then no keyring here: keys stay in GSettings as they always "
        "did, and nothing is lost. A keyring that is present but locked is not "
        "told apart from an unlocked one - this panel unlocks nothing.",
    )


# -- row 6: "Open with Chronoa" ---------------------------------------------

def _desktop_fields() -> Tuple[Optional[Dict[str, str]], str]:
    """The `[Desktop Entry]` keys of the installed entry, or `(None, why not)`.

    `[Desktop Action ...]` groups are skipped on purpose: the actions carry
    their own `Exec=` lines, and reading one of those as the entry's own would
    report the entry as taking files when its `Exec=` says `shani-chronoa` with
    no field code at all.

    The parser is this panel's own rather than `configparser`, which is not a
    desktop-entry parser: it lowercases keys by default, mangles the duplicate
    keys a `.desktop` file is allowed to carry, and has no notion of a line
    continued with a trailing backslash - which is exactly how a long MimeType
    list is normally written, and the thing whose truncation would be silent.
    """
    try:
        text = Path(DESKTOP_ENTRY).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None, f"there is no {DESKTOP_ENTRY} on this machine"
    except OSError as exc:
        return None, f"{DESKTOP_ENTRY} could not be read ({exc})"
    fields: Dict[str, str] = {}
    in_entry = False
    pending = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if pending:
            # Inside a continued value: the backslash is gone, the rest of the
            # line belongs to the key already being built.
            continued = line[:-1] if line.endswith("\\") else line
            pending += continued
            if line.endswith("\\"):
                continue
            fields[_split_entry(pending)] = pending
            pending = ""
            continue
        if line.startswith("["):
            in_entry = line.strip() == "[Desktop Entry]"
            continue
        if not in_entry or line.startswith("#") or "=" not in line:
            continue
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()
    if pending:
        # A file that ends mid-value: report what the line did say rather than
        # dropping the key, which would read as an absent MimeType.
        fields[_split_entry(pending)] = pending.strip()
    return fields, ""


def _split_entry(line: str) -> str:
    return line.partition("=")[0].strip()


def _desktop_sentence() -> str:
    fields, problem = _desktop_fields()
    if fields is None:
        return (f"{EVIDENCE_NOT_INSTALLED} - {problem}, so no file manager's "
                "\"Open with\" menu offers Chronoa.")
    if "MimeType" not in fields:
        return (f"{EVIDENCE_INSTALLED} - {DESKTOP_ENTRY} is there, but it has no "
                "MimeType line at all, so no \"Open with\" menu can offer "
                "Chronoa: the desktop decides that from the MIME types alone.")
    mimes = [part for part in fields["MimeType"].split(";") if part.strip()]
    exec_line = fields.get("Exec", "")
    takes_file = any(code in exec_line for code in ("%U", "%u", "%F", "%f"))
    if not mimes:
        return (f"{EVIDENCE_INSTALLED} - {DESKTOP_ENTRY} is there, but its "
                "MimeType list is empty (0 type(s)), so the desktop has no reason "
                "to offer it for any file.")
    shown = ", ".join(part.strip() for part in mimes[:4])
    more = f", and {len(mimes) - 4} more" if len(mimes) > 4 else ""
    tail = ("and its Exec= line passes the file on"
            if takes_file else
            "but its Exec= line takes no field code, so a chosen file would not "
            "reach Chronoa")
    return (f"{EVIDENCE_INSTALLED} - {DESKTOP_ENTRY} is there with "
            f"{len(mimes)} MIME type(s) in its MimeType list ({shown}{more}), "
            f"{tail}.")


# -- the panel ---------------------------------------------------------------

def _app_config(app: Any) -> Any:
    """`app.config`, or None - including when reading the attribute raises.

    `getattr` with a default does not help if the property itself throws, and a
    property that throws while a window is being built is a real state.
    """
    try:
        return getattr(app, "config", None)
    except Exception:  # noqa: BLE001 - reported as an unreadable gate
        logger.debug("app.config raised", exc_info=True)
        return None


def _summary_label(read_at: float) -> Gtk.Widget:
    label = Gtk.Label(xalign=0.0, wrap=True)
    label.add_css_class("dim-label")
    label.set_text(
        f"Every row above was read once at "
        f"{time.strftime('%H:%M:%S', time.localtime(read_at))}. A row that could "
        "not be answered says so rather than showing a clean answer; reopen "
        "this panel to ask again."
    )
    return label


def build(app: Any) -> Gtk.Widget:
    """The desktop-integration panel for `app` - anything with a `config` will do.

    One question per row, in `ROW_TITLES` order, each read once at build time.
    An app with no settings still builds every row: the two gates it cannot read
    say so, and the four rows that do not need settings are unaffected. Nothing
    here writes a setting, starts a service, binds a shortcut or opens a
    dialog, so opening this panel changes nothing on the machine.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    config = _app_config(app)
    read_at = time.time()
    # One bus listing for the two rows that need it, and only if either does.
    names, bus_problem = _bus_names()

    body = common.page_body(18)
    body.set_margin_top(12)
    body.set_margin_bottom(12)

    # The panel's own health, above every group: is the desktop
    # integration in place, is it answering, or could it not be
    # told? One row, one dot, one word - the question the panel is
    # opened for, before the rows that hold the settings.
    recorder = common.StatusRecorder()
    body.append(_status_row(recorder, names, bus_problem))

    search = common.group(
        "Searching from the desktop",
        f"GNOME Shell's overview and Plasma's KRunner both ask D-Bus for "
        f"{SEARCH_PROVIDER_NAME}. The service file is what makes that possible; "
        "the bus is what shows whether anything is answering.",
    )
    _add_row(search, _row("Search provider service file", _service_sentence()))
    _add_row(search, _row(
        "Answering on the session bus", _provider_bus_sentence(names, bus_problem)))
    body.append(search)

    keyboard = common.group(
        "Keyboard and documents",
        "Two settings that gate what Chronoa may reach for on your behalf. "
        "Neither is granted here.",
    )
    _add_row(keyboard, _row("Global shortcut", _shortcut_sentence(config)))
    _add_row(keyboard, _row("Document search", _document_sentence(config)))
    body.append(keyboard)

    secrets = common.group(
        "Secrets and files",
        "Where an API key would live, and how a file gets to Chronoa.",
    )
    _add_row(secrets, _row(
        "Desktop keyring", _keyring_sentence(names, bus_problem)))
    _add_row(secrets, _row("Open with Chronoa", _desktop_sentence()))
    # The tray belongs with the keyring and "Open with", not with the search
    # provider: all four are about whether the *desktop* knows Chronoa exists.
    _add_row(secrets, _row("Tray icon", _tray_sentence()))
    body.append(secrets)

    body.append(_summary_label(read_at))
    footer = Gtk.Label(xalign=0.0, wrap=True)
    footer.add_css_class("dim-label")
    footer.set_text(_FOOTER)
    body.append(footer)

    set_content(common.scrolled(body))
    # What this panel says about itself, for the sidebar's health dot. The same
    # recorder that built the row at the top of the panel, so the dot and the
    # row are one statement about one reading.
    page.status = recorder.status
    return page


__all__ = [
    "TITLE", "ICON", "SECTION", "build",
    "ROW_CSS", "VALUE_CSS", "ROW_TITLES", "EVIDENCE_WORDS", "UNPROVABLE_NOTE",
    "SEARCH_PROVIDER_NAME", "SERVICE_FILE", "SECRET_SERVICE_NAME",
    "DESKTOP_ENTRY", "SHORTCUT_KEY", "DOCUMENT_KEY", "INDEX_TOOLS",
    "BUSCTL_TIMEOUT",
]
