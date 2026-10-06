"""Export: the way a person gets things back out of a local-first assistant.

Local-first is only defensible if leaving is as easy as arriving. Everything
Chronoa holds lives in this user's own data directory, which is the property
that makes it private and, just as much, the property that makes it a cage if
there is no way to take the conversation somewhere else and stop. So this panel
is not a convenience row: it is the answer to "can I get my data back, and can
I stop using you?" - and that answer is only worth anything if it is a real
file, written by a real `Gtk.FileDialog`, in a place the user chose.

**What is in a file is named on the row that writes it, and the two cannot
drift.** Each export carries a `fields` sentence, displayed next to its own
button, that names the keys the exporter actually writes. A panel claiming
fields the code does not emit is worse than no panel, because someone checking
their own conversation deserves the exact answer.

**Tool traffic is left out, on purpose, and the row says so.** A transcript
holds `role: "tool"` messages, and a tool result is where a sense's reading
arrives: `skills/list_windows.py` runs the accessibility sense, `location.py`
the location sense, and their output is recorded in the transcript like anything
else. Window titles, an OCR'd page, a location fix - that is the user's private
perception data, and this is a panel whose whole purpose is writing files a
person then sends somewhere. What is exported is the conversation itself: what
you said and what Chronoa said, as `role` and text. Nothing is guessed at and
nothing is re-derived; `assistant.py` already keeps percepts out of `_history`
precisely so a snapshot of the conversation cannot capture one, so excluding
tool messages is the *second* half of that guarantee, not a substitute for it.

**The JSON exporter is an allowlist, written out.** It builds its payload from
`role` and `text` per message rather than by dumping the record the store
holds, because that record also carries `tool_call_id`, tool arguments and
tool output. A key set that is assembled by exclusion is a key set that grows
by accident; one assembled by naming is a key set that can be *checked*, which
is why `tests/test_surface_export.py` asserts the keys are exactly these.

**The facts file is facts, and that is what it says.** `PerceptStore.durable()`
is the on-disk tier, which by construction holds only the memory sense's facts
(`senses/scheduler.py` refuses to let any other sense declare `ttl_seconds is
None`, and discards one that returns it anyway). So this reads
`PerceptStore.active()` - the same read path `ContextBuilder` uses, so an
expired fact is *absent* here rather than quietly present - and keeps the
memory-sense durable entries. A transient percept, a window title, a room
sound, a screenshot summary: never in this file, whatever its lifetime.

**A missing store is a sentence, not an exception.** `build()` is called with
whatever the window has; an application with no `percept_store`, a
conversation directory that cannot be read, a store with nothing in it. Each is
its own message, each leaves the buttons insensitive with the reason on their
tooltips, and none of them is drawn as "there is nothing to export" when in
fact there is no way to find out.

**Filenames are made safe before they are used.** A conversation's title is the
first thing the user said, so it contains whatever they type - and `5/3` or
`12:30 standup` are ordinary titles that would otherwise become a path with a
directory in it (and a name no FAT-formatted SD card will take).
`safe_filename()` runs over every name before it reaches `set_initial_name()`.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from shani_chronoa import conversation_store, markdown_lite  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Export"
ICON = "document-save-symbolic"
#: `surfaces.sections()` reads this off the module and files the panel under it;
#: it is deliberately its own sentence rather than "Desktop and system",
#: because getting your data out is neither of those things.
SECTION = "What Chronoa did"

SUBTITLE = (
    "Chronoa keeps your conversation and your facts in your own data "
    "directory. These write them out as files you choose, and name what is "
    "in each one."
)

# ---------------------------------------------------------------------------
# What goes in a file, named once
# ---------------------------------------------------------------------------

CONVERSATION_FORMAT = "chronoa-conversation-export"
FACTS_FORMAT = "chronoa-facts-export"
EXPORT_VERSION = 1

#: The keys of the conversation JSON, exactly. Asserted by name in the tests,
#: because the claim on the row and this tuple are the same claim.
JSON_KEYS: Tuple[str, ...] = ("format", "version", "exported_at", "conversation", "messages")
JSON_CONVERSATION_KEYS: Tuple[str, ...] = ("id", "title", "created", "updated")
JSON_MESSAGE_KEYS: Tuple[str, ...] = ("role", "text")
JSON_FACTS_KEYS: Tuple[str, ...] = ("format", "version", "exported_at", "count", "facts")
JSON_FACT_KEYS: Tuple[str, ...] = ("sense", "text", "noted_at", "source")

#: The only two roles an export writes. `system` is prompt scaffolding and
#: `tool` is machine traffic - see this module's docstring.
EXPORTED_ROLES = ("user", "assistant")

#: The one sense whose durable percepts are facts rather than perception.
FACTS_SENSE = "memory"

MARKDOWN_FIELDS = (
    "What you and Chronoa said, as Markdown. What Chronoa ran, and what it "
    "came back with, is not in it."
)
PLAIN_FIELDS = (
    "The same conversation as plain text, one turn per line: no Markdown, no "
    "tool calls, no attachments."
)
JSON_FIELDS = (
    "Only what was said. Keys: "
    + ", ".join(JSON_KEYS)
    + "; conversation: "
    + ", ".join(JSON_CONVERSATION_KEYS)
    + "; each message: "
    + ", ".join(JSON_MESSAGE_KEYS)
    + ". Tool calls, tool output, attachments and percepts are not in this file."
)
FACTS_FIELDS = (
    "The facts you asked Chronoa to remember, as they are right now. Each one "
    "names its sense (" + FACTS_SENSE + "), the text, when it was noted and "
    "where it came from: " + ", ".join(JSON_FACT_KEYS) + ". What a sense "
    "perceived - a screen, a window title, something said in the room - is not "
    "in this file, whatever its lifetime."
)

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
ROW_CSS = "export-row"
STATUS_CSS = "export-status"
BANNER_CSS = "export-banner"
EMPTY_CSS = "export-empty"

NO_STORE_REASON = (
    "this window has no percept store, so what Chronoa believes cannot be "
    "read from here"
)
NO_CONVERSATION_REASON = (
    "there is no open conversation yet - the first thing you say becomes one"
)

#: Longest name offered to the file chooser before it is cut, so a 60-character
#: conversation title does not arrive as a name the chooser has to scroll.
NAME_LIMIT = 60


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def safe_filename(name: str, fallback: str = "chronoa-export") -> str:
    """A name with nothing in it that a path or a filesystem objects to.

    `/` becomes `-` rather than being dropped, so `5/3` reads as `5-3` instead
    of `53`, and every other character outside word characters, `.`, `-` and a
    space is replaced too (`:` is illegal on Windows and on FAT, `*?<>|"` on
    both). Leading dots and spaces go, because `.` and `..` are not names, and
    a leading dot makes a hidden file on a Unix one. Too long is cut at a word
    boundary, and a name left with nothing usable in it becomes `fallback`.
    """
    flat = " ".join(str(name or "").split())
    flat = re.sub(r"[^\w.\- ]+", "-", flat, flags=re.UNICODE)
    flat = re.sub(r"-{2,}", "-", flat).strip(" .-")
    if not flat or set(flat) <= {".", "-"}:
        return fallback
    if len(flat) > NAME_LIMIT:
        cut = flat[:NAME_LIMIT]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        flat = cut.strip(" .-") or fallback
    return flat


def _dated(stem: str, extension: str, fallback: str = "chronoa-export") -> str:
    """`stem-YYYY-MM-DD.ext`, with `stem` made safe first."""
    return f"{safe_filename(stem, fallback)}-{time.strftime('%Y-%m-%d')}.{extension}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        # A hand-edited index can hold anything; an unreadable date is left out
        # of the claim rather than guessed at, and never raises out of an export.
        return ""


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Document:
    """One file's worth of export, before anybody chooses where it goes.

    `name` is already safe (`safe_filename` ran over it), so a caller cannot
    accidentally put an unfiltered title into a path. `fields` is the sentence
    the row shows and is a constant per kind, not something derived from the
    data - a claim about a file that changes with the file's contents is a
    claim nobody can check.
    """

    kind: str
    name: str
    text: str
    fields: str

    def as_bytes(self) -> bytes:
        return self.text.encode("utf-8")


def _text_of(message: dict) -> str:
    """A message's text, for a `content` that is either a string or parts.

    The same shape `conversation_store._text_of` reads, kept here rather than
    imported because it is a module-private helper and a panel must not depend
    on another module's internals to build a row.
    """
    content = message.get("content")
    if isinstance(content, list):  # multimodal parts
        content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


def _meta(root: Path, sid: str) -> Dict[str, Any]:
    """The index entry for one conversation id, or an empty dict."""
    data = conversation_store.index(root)
    sessions = data.get("sessions")
    entry = sessions.get(sid) if isinstance(sessions, dict) else None
    return entry if isinstance(entry, dict) else {}


def _turns(root: Path, sid: str) -> List[Tuple[str, str]]:
    """`(role, text)` for what was said, oldest first. Tool traffic is dropped.

    `_KNOWN_ROLES` in the store also admits `system` and `tool`; both are
    refused here by `EXPORTED_ROLES`, which is the whole of the perception
    guarantee for the text exports.
    """
    out: List[Tuple[str, str]] = []
    for message in conversation_store.load(Path(root) / f"{sid}.jsonl"):
        if message.get("role") not in EXPORTED_ROLES:
            continue
        text = _text_of(message).strip()
        if text:
            out.append((str(message["role"]), text))
    return out


def markdown_export(root: Path, ref: str) -> Tuple[Optional[Document], str]:
    """The conversation as Markdown, from `conversation_store.export_markdown`.

    The store already knows how to write one - it backs the `conversations`
    skill - so this panel does not grow a second, slightly different Markdown
    writer that could disagree with the skill's.
    """
    try:
        title, text = conversation_store.export_markdown(Path(root), ref)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is a sentence
        logger.warning("could not read the conversation for a Markdown export", exc_info=True)
        return None, f"{type(exc).__name__}: {exc}"
    if not text:
        return None, "that conversation is no longer in the store"
    return Document("markdown", _dated(title, "md", "conversation"), text, MARKDOWN_FIELDS), ""


def plain_text_export(root: Path, ref: str) -> Tuple[Optional[Document], str]:
    """The same conversation as plain text: one turn per line."""
    turns, reason = _conversation(root, ref)
    if turns is None:
        return None, reason
    meta, said = turns
    title = str(meta.get("title") or "") or "Conversation"
    started = _epoch_iso(meta.get("created"))
    lines = [f"Chat with Chronoa - {title}"]
    if started:
        lines.append(f"Started {started}")
    lines.append("")
    for role, text in said:
        lines.append(f"{'You' if role == 'user' else 'Chronoa'}: {text}")
        lines.append("")
    return Document("plain", _dated(title, "txt", "conversation"), "\n".join(lines).rstrip() + "\n",
                    PLAIN_FIELDS), ""


def json_export(root: Path, ref: str) -> Tuple[Optional[Document], str]:
    """The conversation as JSON, keys written out by name rather than dumped.

    A record in the store is `{"role": ..., "content": ..., "tool_call_id": ...}`
    and its content may be multimodal parts; neither shape is what a person
    wants in an export, and the tool fields are exactly the perception-bearing
    ones. So each message is rebuilt from `role` and `text`.
    """
    turns, reason = _conversation(root, ref)
    if turns is None:
        return None, reason
    meta, said = turns
    title = str(meta.get("title") or "") or "Conversation"
    payload = {
        "format": CONVERSATION_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _now_iso(),
        "conversation": {
            "id": ref,
            "title": title,
            "created": meta.get("created", 0),
            "updated": meta.get("updated", 0),
        },
        "messages": [{"role": role, "text": text} for role, text in said],
    }
    return Document("json", _dated(title, "json", "conversation"),
                    json.dumps(payload, ensure_ascii=False, indent=1) + "\n", JSON_FIELDS), ""


def facts_export(store: Any) -> Tuple[Optional[Document], str]:
    """What Chronoa believes, as it stands right now: facts only.

    `active()` rather than `durable()` because `active()` is the read the
    context builder uses, so a fact whose window has closed is absent here
    instead of being quoted as current. The memory sense is then required,
    which is what makes the file a list of things you said rather than a list
    of things a sense saw.
    """
    if store is None:
        return None, NO_STORE_REASON
    try:
        held = list(store.active(time.time()))
    except Exception as exc:  # noqa: BLE001 - unreadable is a state, not a crash
        logger.warning("could not read the percept store to export it", exc_info=True)
        return None, f"{type(exc).__name__}: {exc}"
    facts = [p for p in held
             if getattr(p, "ttl_seconds", None) is None
             and str(getattr(p, "sense", "")) == FACTS_SENSE]
    payload = {
        "format": FACTS_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": _now_iso(),
        "count": len(facts),
        "facts": [
            {
                "sense": str(getattr(p, "sense", "")),
                "text": str(getattr(p, "content", "")),
                "noted_at": _epoch_iso(getattr(p, "created_at", 0)),
                "source": str(getattr(p, "source", "") or ""),
            }
            for p in facts
        ],
    }
    return Document("facts", _dated("chronoa-facts", "json", "chronoa-facts"),
                    json.dumps(payload, ensure_ascii=False, indent=1) + "\n", FACTS_FIELDS), ""


def _conversation(root: Path, ref: str) -> Tuple[Optional[Tuple[dict, List[Tuple[str, str]]]], str]:
    """`(meta, turns)` for one conversation, or `(None, reason)`."""
    if not ref:
        return None, NO_CONVERSATION_REASON
    try:
        meta = _meta(root, ref)
        turns = _turns(root, ref)
    except Exception as exc:  # noqa: BLE001 - a store that cannot be read says so
        logger.warning("could not read the conversation store", exc_info=True)
        return None, f"{type(exc).__name__}: {exc}"
    if not meta and not turns:
        return None, "that conversation is no longer in the store"
    return (meta, turns), ""


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _dim(text: str) -> Gtk.Label:
    """A plain-text label. `set_text` takes no markup at all, which is the one
    call that cannot be half-done for a string the store produced."""
    label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
    label.set_text(text)
    label.add_css_class("dim-label")
    return label


def _adw_text(text: str) -> str:
    """`text` as the row that shows it wants it.

    `Adw.PreferencesRow.use-markup` defaults to True (measured on libadwaita
    1.5), so a title, subtitle or group description is parsed as Pango markup:
    a conversation title of `A & B < C` would fail its parse and render the
    row **empty**. The plain-GTK answer is a `Gtk.Label`, which takes no
    markup and would print the entities themselves, so the escaping follows
    whichever branch built the widget.
    """
    return markdown_lite.escape(text) if common.adw_ready() else text


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    """Put a row in a group, whichever kind `common.group()` built.

    `Adw.PreferencesGroup` takes rows through `add()`; a plain `Gtk.Box` on GTK4
    has `append`, because `add` was removed. Duck-typed, so this module does
    not require libadwaita itself - `common` treats it as optional and so must
    everything built on it.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(child)
    else:
        group.append(child)


def _say(status: Gtk.Label, text: str) -> None:
    """Put a sentence on a row, where the person who pressed the button sees it."""
    status.set_text(text)
    status.set_visible(bool(text))


def _file_root(widget: Gtk.Widget) -> Any:
    """The widget the chooser should be modal to, or None.

    `Gtk.Widget.get_root()` returns None for a widget that is not in a window
    yet, which is exactly the case a test and an early window build are in; a
    FileDialog accepts a None parent and opens unmodal rather than raising.
    """
    return widget.get_root()


class _ExportView(Gtk.Box):
    """The rows: one per thing that can be exported, each with a Save and a Copy.

    Nothing is read at construction except whether there is anything to export
    *at all* - the documents themselves are built when a button is pressed, so
    a panel left open across a long conversation cannot export a stale one.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._app = app
        self._rows: List[Gtk.Widget] = []
        #: button -> the status label on its row. A mapping rather than a walk
        #: up the tree to the row: an `Adw.ActionRow` keeps its children inside
        #: boxes libadwaita owns, and "the nearest ancestor with a class on it"
        #: is a guess about a widget tree this module does not own.
        self._statuses: Dict[Gtk.Widget, Gtk.Label] = {}
        self._body = common.page_body(12)
        self.append(common.scrolled(self._body))
        #: The panel's health, in the row written at the top and in the dot on
        #: its sidebar row. Recreated on every `refresh()` with the row, so the
        #: two cannot describe different moments.
        self.status_recorder = common.StatusRecorder()
        self.refresh()

    # -- reading what there is to export ------------------------------------

    def _store(self) -> Any:
        return getattr(self._app, "percept_store", None)

    def _open_conversation(self) -> Tuple[Optional[Path], str, str]:
        """`(root, conversation id, reason it is not there)`.

        `conversation_store.session_dir()` is resolved per call rather than
        taken from the app, because that is how every other reader of the
        store resolves it and a panel holding its own copy would read a
        different directory than the assistant writes.
        """
        try:
            root = conversation_store.session_dir()
            data = conversation_store.index(root)
        except Exception as exc:  # noqa: BLE001 - a store that cannot be opened says so
            logger.warning("could not open the conversation store", exc_info=True)
            return None, "", f"{type(exc).__name__}: {exc}"
        sid = str(data.get("active") or "")
        if not sid:
            return root, "", NO_CONVERSATION_REASON
        try:
            turns = _turns(root, sid)
        except Exception as exc:  # noqa: BLE001
            return root, "", f"{type(exc).__name__}: {exc}"
        if not turns:
            return root, "", "the open conversation has no messages yet"
        return root, sid, ""

    # -- building -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-read what there is to export and rebuild every row."""
        child = self._body.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._body.remove(child)
            child = following
        self._rows = []

        root, sid, reason = self._open_conversation()
        if reason:
            self._body.append(self._banner(f"Nothing to export from a conversation: {reason}."))

        # The panel's own health, above the
        # export rows: is there a conversation
        # to export, is it open, or could it
        # not be told? One row, one dot, one
        # word - the question the panel is
        # opened for, before the rows that
        # hold the exports.
        if reason:
            self._body.append(self.status_recorder.row(
                common.STATUS_ATTENTION,
                "Nothing to export",
                reason))
        elif root is not None and sid:
            try:
                count = len(_turns(root, sid))
            except Exception:  # noqa: BLE001 - the count is decoration
                count = 0
            self._body.append(self.status_recorder.row(
                common.STATUS_OK,
                f"A conversation is open - {count} message(s)",
                f"{sid}: {count} message(s) you and Chronoa exchanged"))
        else:
            self._body.append(self.status_recorder.row(
                common.STATUS_UNKNOWN,
                "No conversation is open",
                "the session index did not name an active one"))

        said = 0
        if root is not None and sid:
            try:
                said = len(_turns(root, sid))
            except Exception:  # noqa: BLE001 - the count is decoration, not the answer
                said = 0
        meta = {}
        if root is not None and sid:
            try:
                meta = _meta(root, sid)
            except Exception:  # noqa: BLE001 - a name for the group, not the answer
                meta = {}
        title = str(meta.get("title") or "") or "the open conversation"
        # `_adw_text`, because the one place user text reaches an Adw widget
        # that parses markup is a group's description: a conversation titled
        # `A & B` fails its parse there and the description renders empty, so
        # the panel would name no conversation at all without it.
        group = common.group(
            "The open conversation",
            _adw_text(f"{title} - {said} message(s) you and Chronoa exchanged."
                      if not reason else reason[0].upper() + reason[1:] + "."),
        )
        for kind, label, fields in (
            ("markdown", "Markdown", MARKDOWN_FIELDS),
            ("plain", "Plain text", PLAIN_FIELDS),
            ("json", "JSON", JSON_FIELDS),
        ):
            row = self._export_row(
                f"{label}",
                fields,
                lambda b, k=kind: self._on_save(b, k),
                lambda b, k=kind: self._on_copy(b, k),
                available=not reason,
                reason=reason,
                tooltip=f"Write {label} out of the open conversation to a file you choose.",
            )
            _add(group, row)
            self._rows.append(row)
        self._body.append(group)

        store = self._store()
        facts_reason = NO_STORE_REASON if store is None else ""
        count = ""
        if store is not None:
            try:
                held = [p for p in store.active(time.time())
                        if getattr(p, "ttl_seconds", None) is None
                        and str(getattr(p, "sense", "")) == FACTS_SENSE]
                count = f"{len(held)} fact(s) you asked Chronoa to remember, as they stand now."
            except Exception as exc:  # noqa: BLE001
                facts_reason = f"{type(exc).__name__}: {exc}"
        facts_group = common.group("What Chronoa believes", count or facts_reason)
        row = self._export_row(
            "Facts as JSON",
            FACTS_FIELDS,
            lambda b: self._on_save(b, "facts"),
            lambda b: self._on_copy(b, "facts"),
            available=not facts_reason,
            reason=facts_reason,
            tooltip="Write the facts Chronoa believes, and only those, to a file you choose.",
        )
        _add(facts_group, row)
        self._rows.append(row)
        self._body.append(facts_group)

    def _banner(self, text: str) -> Gtk.Widget:
        widget = common.banner(_adw_text(text))
        widget.add_css_class(BANNER_CSS)
        return widget

    def _export_row(self, title: str, fields: str, on_save: Callable, on_copy: Callable,
                    available: bool, reason: str, tooltip: str) -> Gtk.Widget:
        """One export: what it is, what is in it, and where it went."""
        save = Gtk.Button(label="Save…")
        copy = Gtk.Button(label="Copy")
        for button, label in ((save, "Save"), (copy, "Copy")):
            button.set_valign(Gtk.Align.CENTER)
            button.set_sensitive(available)
            button.set_tooltip_text(tooltip if available else f"Not available: {reason}.")
            button.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} the {title} export"])
        save.connect("clicked", on_save)
        copy.connect("clicked", on_copy)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.append(save)
        actions.append(copy)
        status = _dim("")
        status.add_css_class(STATUS_CSS)
        status.set_visible(False)
        self._statuses[save] = status
        self._statuses[copy] = status
        suffix = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        suffix.set_valign(Gtk.Align.CENTER)
        suffix.append(actions)
        suffix.append(status)

        row = common.row(_adw_text(title), _adw_text(fields), suffix=suffix)
        row.add_css_class(ROW_CSS)
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"Export {title}"])
        row.set_tooltip_text(fields)
        return row

    def rows(self) -> List[Gtk.Widget]:
        """The rows this page built, in order. For a caller - and a test."""
        return list(self._rows)

    # -- the two actions ----------------------------------------------------

    def _document(self, kind: str) -> Tuple[Optional[Document], str]:
        """Built at the moment of the press, not at build time."""
        if kind == "facts":
            return facts_export(self._store())
        root, sid, reason = self._open_conversation()
        if reason or root is None or not sid:
            return None, reason or "there is no open conversation"
        exporter = {
            "markdown": markdown_export,
            "plain": plain_text_export,
            "json": json_export,
        }.get(kind)
        if exporter is None:
            return None, f"{kind!r} is not an export this panel offers"
        return exporter(root, sid)

    def _on_save(self, button: Gtk.Button, kind: str) -> None:
        status = self._statuses[button]
        document, reason = self._document(kind)
        if document is None:
            _say(status, f"Nothing to save: {reason}.")
            return
        dialog = Gtk.FileDialog()
        dialog.set_title(f"Save the {kind} export")
        # Set on the dialog, not on a path: the user still chooses where, and
        # `document.name` has already been through `safe_filename`.
        dialog.set_initial_name(document.name)
        # **The callback takes `*_user_data` because PyGObject passes three
        # arguments, not two.** `save` takes a `Gio.AsyncReadyCallback`, and
        # measured on this PyGObject (`Gio.File.load_contents_async`, the same
        # callback type) it is invoked as `(source_object, result, user_data)`.
        # The two-argument lambda this replaced matched a *test fake* that called
        # it with one argument, so it raised `TypeError` on every real save and
        # both save tests failed for that one reason. Verified here by
        # `tests/test_surface_export.py::TestCallbackArity`, which asserts the
        # arity against the installed library rather than against a fake.
        dialog.save(_file_root(button), None,
                    lambda _dlg, result, *_user_data: self._saved(
                        dialog, document, result, status))

    def _saved(self, dialog: Gtk.FileDialog, document: Document, result: Any,
               status: Gtk.Label) -> None:
        try:
            handle = dialog.save_finish(result)
        except GLib.Error as exc:
            logger.info("%s export not saved: %s", document.kind, exc.message)
            _say(status, f"Not saved: {exc.message}")
            return
        try:
            # `Gio.FileCreateFlags`, not `GLib.FileCreateFlags`: the latter does
            # not exist in this PyGObject's GLib, so a copy of the call written
            # the other way raises AttributeError - which is not a GLib.Error
            # and would not be caught by the handler below.
            handle.replace_contents(document.as_bytes(), None, False,
                                    Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        except GLib.Error as exc:
            logger.info("%s export could not be written: %s", document.kind, exc.message)
            _say(status, f"Could not write the file: {exc.message}")
            return
        # Where it went, in the row that wrote it. A GFile with no local path
        # (a portal handing back a URI) still names itself.
        _say(status, f"Saved to {handle.get_path() or handle.get_uri()}")

    def _on_copy(self, button: Gtk.Button, kind: str) -> None:
        status = self._statuses[button]
        document, reason = self._document(kind)
        if document is None:
            _say(status, f"Nothing to copy: {reason}.")
            return
        try:
            clipboard = button.get_clipboard()
            try:
                clipboard.set_content(Gdk.ContentProvider.new_for_value(document.text))
            except AttributeError:  # pragma: no cover - GTK older than 4.10
                clipboard.set(document.text)
        except Exception as exc:  # noqa: BLE001 - no clipboard is a sentence, not a crash
            logger.warning("could not write the clipboard", exc_info=True)
            _say(status, f"Could not reach the clipboard: {exc}")
            return
        _say(status, "Copied to the clipboard.")


def build(app: Any) -> Gtk.Widget:
    """The export page for `app`: an `Adw.NavigationPage`.

    `build()` hands back the page, so the things a caller needs from this
    surface are reachable on it - the rows, and `refresh()` for re-reading
    what there is to export. Set as attributes rather than wrapped, because the
    page is a libadwaita widget and this is the one shape that works on it.
    """
    view = _ExportView(app)
    page, set_content = common.surface(TITLE, SUBTITLE)
    set_content(view)
    page.rows = view.rows
    page.refresh = view.refresh
    # What this panel says about itself, for the sidebar's health dot - read from
    # the recorder the row at the top of the panel was written through, so the
    # dot and the row cannot be describing different exports.
    page.status = view.status_recorder.status
    return page


__all__ = [
    "TITLE", "ICON", "SECTION", "SUBTITLE", "build", "Document",
    "safe_filename", "markdown_export", "plain_text_export", "json_export",
    "facts_export", "JSON_KEYS", "JSON_CONVERSATION_KEYS", "JSON_MESSAGE_KEYS",
    "JSON_FACTS_KEYS", "JSON_FACT_KEYS", "ROW_CSS", "STATUS_CSS",
]