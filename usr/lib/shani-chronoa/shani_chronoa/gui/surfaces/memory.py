"""What Chronoa believes the user told it: the percept store, fact by fact.

Every other surface answers "what is this machine doing". This one answers the
question that has no other page: **what has the assistant been told, and by
which sense.** It reads `PerceptStore` directly - the same object
`app/_init_components` hands to `Assistant`, to the ambient scheduler and to the
CLI - and shows what that store holds right now, grouped by sense, one row per
fact: the fact text, how old it is, and where it came from.

**The store is read, never guessed at.** `PerceptStore.active()` is the read path
the context builder itself uses, so what this page shows and what would be sent
with the next reply come from one call on one object. The cross-process view
(`percepts/live.json`) is deliberately *not* read: it is a snapshot a separate
process may have written minutes ago, and a page that displayed one while
holding the live store would report a stale answer as the present.

**Four states, and no fifth.**

- **Held.** A fact in the store, with its age and its provenance.
- **Nothing held.** The honest empty state. It says "nothing has been
  remembered", which is different from the two below.
- **No store.** An application object without a `percept_store`. That is a
  failure to read, not an absence of memories, and it is rendered as a state of
  its own rather than as an empty list - this repo's standing rule is that a
  failure which looks like a clean answer is worse than a failure.
- **Unreadable.** A store whose `active()` raised. Same reasoning, and the
  exception's own text is shown rather than swallowed.

**What Forget actually deletes, and what it cannot.** `PerceptStore.forget()`
filters the *durable* tier and rewrites `memory.jsonl`, so a Forget on a durable
row is a real deletion from disk. It matches on object identity rather than on
text: two facts with identical content are two records, and a text match would
delete both when the user asked for one. A transient percept is never written
to disk, and the store's only transient removal is `clear_transient()`, which
drops the whole window - so a transient row's Forget is present, insensitive,
and says why, rather than quietly doing something wider than it was asked for.

One consequence worth naming: `forget()` does not republish `live.json`
(it does on `add()` and on `clear_transient()`), so the cross-process view keeps
counting a forgotten fact until the next add. That is the store's behaviour,
not this page's, and hiding it here would not fix it.

**Privacy mode is stated precisely, not approximately.** `egress.privacy_mode_enabled()`
being on means every sense that reaches outside this machine is refused before a
fact can be sent - `config.sense_allowed()` withholds the percepts of the
networked senses (`web`, `location`) and nothing else. Machine-state percepts
are still quoted to a local model. So the banner says that, in those words; a
banner reading "your percepts are withheld from the model" would be a confident
wrong answer on the majority of the rows above it.

Read-only apart from Forget. Nothing here starts a sense, asks one a question,
or grants consent - "Senses" and "Privacy" are the pages that write those.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa import egress, markdown_lite  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Memory"
ICON = "avatar-default-symbolic"

SUBTITLE = (
    "What Chronoa believes it was told, read from the running assistant's own "
    "percept store. Nothing here reaches a model by being displayed."
)

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
FACT_CSS = "memory-fact"
GROUP_CSS = "memory-group"
BANNER_CSS = "memory-privacy-banner"
EMPTY_CSS = "memory-empty"

EMPTY_TITLE = "Nothing has been remembered"
EMPTY_DETAIL = (
    "No sense has recorded anything for this assistant yet. A fact appears "
    "here the moment one is perceived, and it stays until it expires or you "
    "forget it - nothing on this page is a summary of the conversation."
)

NO_STORE_TITLE = "The assistant's memory is not available to this window"
NO_STORE_DETAIL = (
    "The application object handed to this page carries no percept store, so "
    "nothing could be listed. That is a failure to read, not an absence of "
    "remembered facts, and it is deliberately not drawn as an empty list."
)

UNREADABLE_ICON = "dialog-warning-symbolic"
EMPTY_ICON = ICON

#: `egress.privacy_mode_enabled()` only gates the senses that reach outside
#: this machine - `config.sense_allowed()`'s `_NETWORKED_SENSES` is `web` and
#: `location` - so this says exactly that rather than claiming every fact is
#: held back from the model.
PRIVACY_NOTE = (
    "Privacy mode is on: live percepts are withheld from the model for the "
    "senses that reach outside this machine (web, location). Machine-state "
    "percepts are still quoted to the local model."
)

TRANSIENT_TOOLTIP = (
    "A transient percept is never written to disk and is dropped as it expires. "
    "The store can only clear the whole transient window at once, so there is "
    "nothing here to delete on its own."
)

#: Longest tooltip detail before it is cut. A sense's `content` is a full
#: sentence or a screen summary, and the tooltip carries it uncut.
DETAIL_LIMIT = 400

_UNITS = (
    (31557600.0, "year"),
    (2629800.0, "month"),
    (604800.0, "week"),
    (86400.0, "day"),
    (3600.0, "hour"),
    (60.0, "minute"),
)


# ---------------------------------------------------------------------------
# Reading the store without raising
# ---------------------------------------------------------------------------


def _privacy_mode_on() -> bool:
    """`egress.privacy_mode_enabled()`, which never raises and fails toward ON.

    Patched at `egress` in the tests rather than here, because that is where it
    is read.
    """
    try:
        return bool(egress.privacy_mode_enabled())
    except Exception:  # noqa: BLE001 - it is documented never to raise; belt and braces
        logger.debug("privacy mode could not be read", exc_info=True)
        return True


def _store_of(app: Any) -> Any:
    """The app's own `PerceptStore`, or None.

    A stub without one is a real state (`ChronoaApplication._init_components`
    assigns it, so a window built before that has none) and is rendered as a
    failure to read rather than as an empty store. Nothing here constructs a
    replacement: a `PerceptStore()` of this page's own would read the default
    durable file and answer about a store the assistant is not using.
    """
    return getattr(app, "percept_store", None)


def _read(store: Any) -> Tuple[Optional[List[Any]], str]:
    """`(percepts, error)`. Exactly one of the two is meaningful.

    `active()` is the store's own read path - the one `ContextBuilder` uses -
    and it is what makes an expired percept *absent*, so calling anything else
    (or re-implementing expiry here) would be a second answer to the same
    question and a second thing to be wrong.
    """
    try:
        return list(store.active(time.time())), ""
    except Exception as exc:  # noqa: BLE001 - unreadable is a state this page can show
        logger.warning("could not read the percept store", exc_info=True)
        return None, f"{type(exc).__name__}: {exc}"


def _durable_path(store: Any) -> str:
    """Where the durable tier is on this machine, for the tooltip."""
    try:
        return str(store.durable_path)
    except Exception:  # noqa: BLE001 - a store that cannot name its file is not a crash
        return ""


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def _span(seconds: float) -> str:
    """A duration as a short phrase: "just now", "4 minutes", "1 hour"."""
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "an unknown time"
    if value < 60.0:
        return "just now"
    for size, unit in _UNITS:
        if value >= size:
            count = max(1, int(round(value / size)))
            return f"{count} {unit}" if count == 1 else f"{count} {unit}s"
    return "just now"  # pragma: no cover - unreachable, every value >= 60 matches


def _age(percept: Any, now: float) -> str:
    """How old this percept is, in its own words if it cannot say."""
    try:
        return _span(percept.age_seconds(now))
    except Exception:  # noqa: BLE001 - an unreadable age is shown as unknown, not guessed
        logger.debug("a percept has no readable age", exc_info=True)
        return "an unknown age"


def _clip(text: Any, limit: int = DETAIL_LIMIT) -> str:
    flat = " ".join(str(text if text is not None else "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rsplit(" ", 1)[0].rstrip() + "…"


def _tier(percept: Any, now: float) -> str:
    """The row's line about how long this fact lives and where it is kept.

    Durable and transient are not two renderings of the same thing: one is a
    line in `memory.jsonl` that survives a restart, the other exists only in
    this process's deque. Saying which is the whole point of the row.
    """
    if getattr(percept, "ttl_seconds", None) is not None:
        try:
            left = float(percept.ttl_seconds) - (now - float(percept.created_at))
        except (TypeError, ValueError):
            return "live only, in this process"
        return f"live only, expires in {_span(left)}"
    return "kept on disk until forgotten"


def _detail(percept: Any, now: float) -> str:
    """The subtitle: age, kind, where it is kept, and its provenance."""
    age = _age(percept, now)
    bits = [
        age if age == "just now" else age + " ago",
        str(getattr(percept, "kind", "") or "observation"),
        _tier(percept, now),
    ]
    source = str(getattr(percept, "source", "") or "")
    if source:
        bits.append(f"from {source}")
    sensitivity = str(getattr(percept, "sensitivity", "") or "")
    if sensitivity and sensitivity != "public":
        bits.append(f"{sensitivity} - not sent to a model unless its sense is permitted")
    return " · ".join(bit for bit in bits if bit)


def _tooltip(percept: Any, now: float, durable_path: str) -> str:
    """The whole fact and where it came from, for a row too narrow to show it."""
    lines = [
        str(getattr(percept, "content", "") or ""),
        "",
        f"sense: {getattr(percept, 'sense', '')}",
        f"kind: {getattr(percept, 'kind', '')}",
        f"noted {_age(percept, now)} ago",
        f"kept: {_tier(percept, now)}",
    ]
    source = str(getattr(percept, "source", "") or "")
    if source:
        lines.append(f"source: {source}")
    valid_until = getattr(percept, "valid_until", None)
    if valid_until is not None:
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(valid_until)))
            lines.append(f"no longer current after: {when}")
        except (TypeError, ValueError, OverflowError, OSError):
            lines.append("valid_until could not be read")
    if durable_path:
        lines.append(f"durable store: {durable_path}")
    return "\n".join(lines)


def _as_title(text: str) -> str:
    """A fact's text, made safe for the row it becomes.

    `Adw.PreferencesRow.use-markup` defaults to **True** (measured on
    libadwaita 1.5), so an `Adw.ActionRow` parses both its title and its
    subtitle as Pango markup: unescaped, a fact reading `A & B < C` is markup,
    not text. `common.row()` does not escape - its own docstring describes an
    escaping it does not do - so it is done here, at the point the untrusted
    text enters.

    The plain-GTK answer in `common.row()` is a `Gtk.Label(label=...)`, which
    takes no markup and would print the entities themselves, so the escaping
    follows whichever branch built the row. Both render the same characters.
    """
    return markdown_lite.escape(text) if common.adw_ready() else text


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    """Put a row in a group, whichever kind `common.group()` built.

    `Adw.PreferencesGroup` takes rows through `add()`; the plain-GTK answer is a
    `Gtk.Box`, whose rows are appended. Duck-typed rather than tested against
    `Adw.PreferencesGroup`, so this module does not require libadwaita itself -
    `common` treats it as optional and so must everything built on it.
    """
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(child)
    else:
        group.append(child)


def _forget_button(percept: Any, on_forget: Any) -> Gtk.Button:
    """The row's own Forget control.

    Insensitive for a transient percept, and it says why in its tooltip: the
    store can drop the whole transient window but not one entry of it, so a
    button that worked here would have to do something the user did not ask for.
    """
    transient = getattr(percept, "ttl_seconds", None) is not None
    button = Gtk.Button(label="Forget", valign=Gtk.Align.CENTER)
    button.set_sensitive(not transient)
    if transient:
        button.set_tooltip_text(TRANSIENT_TOOLTIP)
    else:
        button.set_tooltip_text(
            "Delete this fact from the durable store on disk. It will not be "
            "quoted to the model again."
        )
    button.update_property(
        [Gtk.AccessibleProperty.LABEL],
        [f"Forget this fact: {_clip(getattr(percept, 'content', ''), 80)}"],
    )
    button.connect("clicked", lambda _b: on_forget(percept))
    return button


def _fact_row(percept: Any, now: float, durable_path: str, on_forget: Any) -> Gtk.Widget:
    """One fact: its text, how old it is, and a way to be rid of it."""
    content = str(getattr(percept, "content", "") or "")
    built = common.row(
        title=_as_title(content),
        subtitle=_as_title(_detail(percept, now)),
        suffix=_forget_button(percept, on_forget),
    )
    built.add_css_class(FACT_CSS)
    built.update_property(
        [Gtk.AccessibleProperty.LABEL], [f"Fact from the {getattr(percept, 'sense', '')} sense: {content}"]
    )
    built.set_tooltip_text(_tooltip(percept, now, durable_path))
    return built


def _by_sense(percepts: List[Any]) -> "Dict[str, List[Any]]":
    """Grouped by sense, senses alphabetical, facts in the order the store gave.

    The store returns the durable tier oldest-first and then the live window,
    so within a group the oldest fact is the first row - which is the order a
    person scanning for something to forget wants.
    """
    grouped: Dict[str, List[Any]] = {}
    for percept in percepts:
        grouped.setdefault(str(getattr(percept, "sense", "") or "(unnamed sense)"), []).append(percept)
    return dict(sorted(grouped.items()))


# ---------------------------------------------------------------------------
# The widget
# ---------------------------------------------------------------------------


class _MemorySurface(Adw.NavigationPage):
    """The page: an optional privacy banner, then one group per sense.

    Rebuilt from the store on every `refresh()`, which is what a Forget does
    after it deletes. Nothing is cached, because "what is held right now" is a
    claim about the moment it was read and the store changes under the app's
    own scheduler.
    """

    def __init__(self, app: Any) -> None:
        self._store = _store_of(app)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        #: The panel's health, in the row it writes at the top of itself and in
        #: the dot on its sidebar row. `refresh()` rebuilds the row, so the dot
        #: follows the last refresh rather than the state at build time - which
        #: is what makes "remember a fact and watch the dot change" work.
        self.status_recorder = common.StatusRecorder()

        # Wrap content in a surface to get the header
        page_content, set_content = common.surface(TITLE, SUBTITLE)
        set_content(self._content)

        super().__init__(child=page_content, title=TITLE)
        self.refresh()

    # -- remembering one -----------------------------------------------------

    def _remember(self, text: str) -> None:
        """Store a fact the person typed, then rebuild from the store.

        `remember_fact()` is the same path the `remember_fact` skill takes, so
        the page cannot store something the assistant could not store. A blank
        or refused write is reported in the log rather than as a toast, because
        a "remembered" claim this page could not keep would be the same false
        promise the whole repo audits for.
        """
        store = self._store
        text = (text or "").strip()
        if store is None or not text:
            return
        try:
            from shani_chronoa.senses.memory import remember_fact

            kept = remember_fact(text, store=store)
        except Exception:  # noqa: BLE001 - a refused write must not take the window down
            logger.warning("could not remember a fact", exc_info=True)
            kept = None
        if kept is None:
            logger.warning("a fact was not remembered (empty or refused)")
        self.refresh()

    # -- content ------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the store and rebuild every row."""
        child = self._content.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._content.remove(child)
            child = following

        if _privacy_mode_on():
            banner = common.banner(PRIVACY_NOTE)
            banner.add_css_class(BANNER_CSS)
            self._content.append(banner)

        # The panel's own health, before any CRUD: is the store
        # readable, is it holding facts, or could it not be told?
        # One row, one dot, one word - the question the panel is
        # opened for, before the rows that hold the facts.
        if self._store is None:
            self._content.append(self.status_recorder.row(
                common.STATUS_UNKNOWN,
                "The percept store is not reachable",
                NO_STORE_DETAIL))
        else:
            percepts, error = _read(self._store)
            if percepts is None:
                self._content.append(self.status_recorder.row(
                    common.STATUS_ATTENTION,
                    "The percept store could not be read",
                    error or "unknown error"))
            elif not percepts:
                self._content.append(self.status_recorder.row(
                    common.STATUS_ATTENTION,
                    "No facts remembered yet",
                    EMPTY_DETAIL))
            else:
                self._content.append(self.status_recorder.row(
                    common.STATUS_OK,
                    f"{len(percepts)} fact(s) remembered",
                    f"held by {len(_by_sense(percepts))} sense(s)"))

        # The create half of CRUD, on the same page as the delete half: a page
        # that can only forget cannot be the answer to "remember this for me".
        if self._store is not None:
            entry = Gtk.Entry(placeholder_text="Remember a fact…", hexpand=True)
            button = Gtk.Button(label="Remember")
            entry.connect("activate", lambda e: (self._remember(e.get_text()), e.set_text("")))
            button.connect("clicked", lambda b: (self._remember(entry.get_text()), entry.set_text("")))
            bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            bar.set_margin_top(6)
            bar.set_margin_bottom(6)
            bar.set_margin_start(12)
            bar.set_margin_end(12)
            bar.append(entry)
            bar.append(button)
            self._content.append(bar)

        if self._store is None:
            self._content.append(self._status(UNREADABLE_ICON, NO_STORE_TITLE, NO_STORE_DETAIL))
            return

        percepts, error = _read(self._store)
        if percepts is None:
            self._content.append(
                self._status(UNREADABLE_ICON, "The percept store could not be read", error)
            )
            return
        if not percepts:
            self._content.append(self._status(EMPTY_ICON, EMPTY_TITLE, EMPTY_DETAIL))
            return

        now = time.time()
        path = _durable_path(self._store)
        body = common.page_body(18)
        for sense, held in _by_sense(percepts).items():
            group = common.group(sense, f"{len(held)} fact(s) this sense has recorded")
            group.add_css_class(GROUP_CSS)
            for percept in held:
                _add(group, _fact_row(percept, now, path, self._forget))
            body.append(group)
        self._content.append(common.scrolled(body))

    def _status(self, icon: str, title: str, detail: str) -> Gtk.Widget:
        """One of the honest non-list states, marked so a test can find it."""
        widget = common.empty_state(icon, title, detail)
        widget.add_css_class(EMPTY_CSS)
        widget.set_vexpand(True)
        return widget

    # -- the one write ------------------------------------------------------

    def _forget(self, percept: Any) -> None:
        """Delete one durable fact, then rebuild from what the store now holds.

        Identity, not text: two facts with identical content are two records,
        and a text match would remove both when the user asked for one. The
        view is re-read rather than patched, so a `forget()` that removed
        nothing leaves the row standing - which is the honest outcome and is
        visible as such.
        """
        store = self._store
        if store is None:  # pragma: no cover - the button only exists with a store
            return
        try:
            removed = int(store.forget(lambda held: held is percept))
        except Exception:  # noqa: BLE001 - a refused delete must not take the window down
            logger.warning("could not forget a percept", exc_info=True)
            removed = 0
        if removed != 1:
            logger.warning("forgetting one percept removed %d records", removed)
        self.refresh()

    # -- for tests ----------------------------------------------------------

    def store(self) -> Any:
        """The store this page reads, so a test can check the file it wrote."""
        return self._store


def build(app: Any) -> Gtk.Widget:
    """The memory page for `app`.

    Reads `app.percept_store` and `egress.privacy_mode_enabled()`, and writes
    only through controls on this page: Remember (create) and Forget (delete).
    """
    surface = _MemorySurface(app)
    # What this panel says about itself, for the sidebar's health dot. Memory is
    # the panel where a stale dot would matter most: "privacy off but facts
    # stored" is the state a person needs to see from the sidebar without opening
    # the panel that explains it.
    surface.status = surface.status_recorder.status
    return surface


__all__ = ["TITLE", "ICON", "build"]