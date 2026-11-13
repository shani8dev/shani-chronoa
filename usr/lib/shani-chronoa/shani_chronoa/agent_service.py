"""The D-Bus service that owns dev.shani.chronoa.Agent.

A turn is a coroutine on the app's background asyncio loop; the tool loop
asks through ask_bridge, and while a turn runs that module's turn-bound
resolver routes the question here. Approve resolves it from whichever thread
called it.

**The events are the shell's shape, not the assistant's.** `EventSink` names
what the core emits (`on_tool_start`, `on_tool_finish`, `on_state`); a person
watching a panel needs one line per thing that happened, so the sink maps each
core event onto one JSON event and leaves the core's names out of the wire.
That mapping lives here rather than in the shell's bridge because this is the
side that knows what a tool call *was*; the bridge would only be guessing from
the same four fields.

Wire shape, all as `Event(json)`:

  {"turn": id, "event": "tool",      "text": "read_file · path=…", "tool": …, "args": …}
  {"turn": id, "event": "tool_done", "tool": …, "ok": true, "text": "…"}
  {"turn": id, "event": "reply",     "text": "chunk"}
  {"turn": id, "event": "state",     "state": "thinking"}
  {"turn": id, "event": "approval",  "id": id, "text": question,
                                     "options": ["Allow this once", …], "detail": preview}
  {"turn": id, "event": "approval_closed", "id": id}
  {"turn": id, "event": "notice",    "text": "…"}
  {"turn": id, "event": "selection", "record": …}
  {"turn": id, "event": "error",     "text": "…"}
  {"turn": id, "event": "done",      "text": ""}

`approval.id` is the turn id: the contract approves a *turn*, and the tool
loop asks one question at a time, so the id is unambiguous. `options` is the
list `ask()` was given, in order - the shell draws one button per option and
sends the chosen label back, because a decision like "Allow this session" only
means anything if it reaches `permissions.parse_reply` unchanged.
"""

from __future__ import annotations

import difflib
import json
import logging
import threading
import uuid
from typing import TYPE_CHECKING, Callable, Optional

from gi.repository import Gio, GLib

from shani_chronoa import ask_bridge
from shani_chronoa.agent_contract import BUS_NAME, IFACE, PATH, XML
from shani_chronoa.events import EventSink

if TYPE_CHECKING:
    from shani_chronoa.app.application import ChronoaApplication

logger = logging.getLogger(__name__)

#: What every Write is told before its instruction. The answer is pasted back
#: where the text was, so anything around it - a preamble, quotes, a note on
#: what changed - would land in someone's email.
WRITE_SYSTEM = (
    "You change text as asked. Reply with only the resulting text: no preamble, "
    "no explanation, no quotes or code fences around it. Keep the language of the "
    "text unless asked to translate, and keep names, numbers and links exactly.")
#: Where FindFiles looks: the folders a person keeps their own things in
#: (XDG's, else the usual names), the ones that exist.
def find_roots():
    import os
    import pathlib
    home = pathlib.Path.home()
    out = []
    for var, name in (("XDG_DOCUMENTS_DIR", "Documents"), ("XDG_DOWNLOAD_DIR", "Downloads"), ("XDG_DESKTOP_DIR", "Desktop")):
        p = pathlib.Path(os.environ.get(var) or home / name)
        if p.is_dir() and p not in out:
            out.append(p)
    return out


#: How old the file index may be before a FindFiles brings it up to date (in the background).
FIND_REINDEX_SECONDS = 600.0
#: The longest text one Write takes (a selection or a message, never a book).
WRITE_MAX_CHARS = 12000
#: How long a Write waits for the model before saying it did not finish.
WRITE_TIMEOUT_SECONDS = 180.0


def clean_written(reply: str) -> str:
    """The text a Write returns: without a thinking block, fences or wrapping quotes."""
    out = str(reply or "")
    if "</think>" in out:
        out = out.split("</think>")[-1]
    out = out.strip()
    if out.startswith("```") and out.endswith("```") and out.count("```") == 2:
        out = out[3:-3]
        out = out.split("\n", 1)[1] if "\n" in out and not out.split("\n", 1)[0].strip().count(" ") else out
        out = out.strip()
    for a, b in (('"', '"'), ("“", "”"), ("'", "'")):
        if len(out) > 1 and out.startswith(a) and out.endswith(b) and out.count(a) == 1 + (a == b):
            out = out[1:-1].strip()
    return out


#: How much of a tool's result travels over the bus. The result is for the
#: pill under the question, not for replaying the turn: a file read can be a
#: megabyte, and a D-Bus signal is not a file transfer.
TOOL_RESULT_PREVIEW = 400

#: How much of an approval preview travels. A diff of a large file is the one
#: thing this is for; past this the person is reading a wall, not a change.
PREVIEW_LIMIT = 1200


def _short(value, limit: int = 90) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_line(name: str, arguments: object) -> str:
    """One line naming a tool call and the arguments that identify it."""
    if isinstance(arguments, dict) and arguments:
        pairs = ", ".join(
            f"{key}={_short(value, 40)}"
            for key, value in list(arguments.items())[:3])
        return _short(f"{name} · {pairs}", 160)
    return name


def _preview_detail(preview: object) -> str:
    """An approval preview as bounded text the panel can draw.

    `tools.approval_preview` returns a dict for a command, a
    `(path, before, after)` triple for a file change, and None when there is
    nothing to show. json.dumps would draw the first two as one unreadable
    line, so each gets the shape a person can read: key by key for a command,
    a unified diff for a change.
    """
    if preview is None:
        return ""
    if isinstance(preview, dict):
        return "\n".join(
            f"{key}: {_short(value, 160)}"
            for key, value in preview.items()
            if value not in (None, "", False))
    if isinstance(preview, (tuple, list)) and len(preview) == 3:
        path, before, after = (str(part) for part in preview)
        diff = list(difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile="before", tofile="after", lineterm="", n=2))
        body = "\n".join(diff[2:]) if len(diff) > 2 else after
        text = f"{path}\n{body}" if body else path
        return text if len(text) <= PREVIEW_LIMIT else text[: PREVIEW_LIMIT - 1] + "…"
    return _short(preview, 300)


class _AgentSink(EventSink):
    """Turn events -> D-Bus Event signal, marshalled onto the GLib main loop.

    Subclasses `EventSink` rather than passing loose callbacks, because
    `Assistant.handle` fires `sink.fire("on_tool_start", …)` whether or not
    the object has that method - and an object without `fire` took the first
    tool call of every shell turn down with an AttributeError.
    """

    def __init__(self, service: "AgentService", turn_id: str) -> None:
        self._service = service
        self._turn_id = turn_id
        super().__init__(
            on_tool_start=self._on_tool_start,
            on_tool_finish=self._on_tool_finish,
            on_text=self._on_text,
            on_state=self._on_state,
            on_selection=self._on_selection,
            on_compaction=self._on_compaction,
            on_cloud_turn=self._on_cloud_turn,
        )

    @property
    def turn_id(self) -> str:
        return self._turn_id

    def _emit(self, event: str, **kw) -> None:
        payload = json.dumps({"turn": self._turn_id, "event": event, **kw},
                             ensure_ascii=False, default=str)
        GLib.idle_add(self._service.emit_event, payload)

    # -- core events, one wire event each -------------------------------

    def _on_text(self, text: str) -> None:
        self._emit("reply", text=text)

    def _on_tool_start(self, name: str, arguments: dict) -> None:
        self._emit("tool", text=_tool_line(name, arguments),
                   tool=name, args=arguments)

    def _on_tool_finish(self, name: str, arguments: dict, result: str,
                        ok: bool, artifacts: tuple) -> None:
        self._emit("tool_done", tool=name, text=_short(result, TOOL_RESULT_PREVIEW),
                   ok=bool(ok))

    def _on_state(self, state: str) -> None:
        # The core's states are the loop's ("tool_call"), the bar's are the
        # person's ("thinking"). Map the one that differs rather than making
        # every consumer learn both vocabularies.
        mapped = {"tool_call": "thinking"}.get(state, state)
        self._emit("state", state=mapped)

    def _on_selection(self, record) -> None:
        self._emit("selection", record=record)

    def _on_compaction(self, sentence: str) -> None:
        self._emit("notice", text=sentence)

    def _on_cloud_turn(self, provider: str) -> None:
        self._emit("notice", text=f"A cloud provider answered this turn: {provider}")

    # -- approvals ------------------------------------------------------

    def ask(self, question: str, options: list, preview=None,
            kind: str = "greet"):
        """Emit an approval, register it, and return the wait `ask()` uses."""
        done, resolve = ask_bridge.make_event()
        stale = self._service.register(self._turn_id, done, resolve)
        # A second question for one turn: the tool loop asks one at a time, so
        # the earlier wait is stale. Wake it rather than leaving it to time out,
        # and take its card down before drawing the new one - both are the same
        # turn id, so order is what keeps them apart.
        if stale is not None:
            stale.chronoa_answer = ""  # type: ignore[attr-defined]
            stale.set()
            self._emit("approval_closed", id=self._turn_id)
        done.chronoa_closed = lambda: self._service.forget(  # type: ignore[attr-defined]
            self._turn_id, done, self._emit_closed)
        self._emit("approval", id=self._turn_id, text=question,
                   options=[str(option) for option in options],
                   detail=_preview_detail(preview), kind=kind)
        return done, resolve

    def _emit_closed(self) -> None:
        self._emit("approval_closed", id=self._turn_id)


class AgentService:
    """Owns dev.shani.chronoa.Agent on the session bus."""

    def __init__(self, app: "ChronoaApplication") -> None:
        self._app = app
        self._pending: "dict[str, tuple[threading.Event, Callable[[str], None]]]" = {}
        self._lock = threading.Lock()
        self._owner = None
        self._registration = None
        self._connection: Optional[Gio.DBusConnection] = None

    # -- the two methods ------------------------------------------------

    def Turn(self, connection, sender, path, interface, method, params,
             invocation) -> None:
        text = str(params.unpack()[0] or "").strip()
        if not text:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.InvalidArgs",
                "Turn needs text to start a turn")
            return
        turn_id = "turn-" + uuid.uuid4().hex[:12]
        logger.info("agent turn %s: %r", turn_id, text[:80])
        sink = _AgentSink(self, turn_id)
        try:
            coro = self._app.assistant.handle(text, sink=sink)
            # **Bound only while the turn is scheduled.** The async bridge
            # copies the context before the task runs, and the assistant
            # copies it again into the tool thread, so the question a tool
            # asks finds this turn's resolver - and no other turn can.
            with ask_bridge.turn_resolver(sink.ask):
                self._app._async.run(coro, lambda result: self._on_done(turn_id, result))
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            logger.exception("agent turn %s could not start", turn_id)
            invocation.return_dbus_error(
                "dev.shani.chronoa.Error.TurnFailed",
                f"{type(exc).__name__}: {exc}")
            return
        invocation.return_value(GLib.Variant("(s)", (turn_id,)))

    def Approve(self, connection, sender, path, interface, method, params,
                invocation) -> None:
        """Approve or reject a pending turn.

        **No Python caller in this tree**: this is a D-Bus method invoked by
        clients over the session bus, so the enforcement scan has no caller to
        find. Only the test server and the real client call it.
        """
        turn_id, decision = params.unpack()
        with self._lock:
            entry = self._pending.pop(turn_id, None)
        if entry is None:
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return
        entry[1](decision)
        invocation.return_value(GLib.Variant("(b)", (True,)))

    # -- Write: text in, text out, with no tools and no conversation ----

    def Write(self, connection, sender, path, interface, method, params,
              invocation) -> None:
        """Transform a piece of text the way `instruction` says, and return it.

        The shell's writing tools (proofread, rewrite, shorter, ...), its smart
        replies and its file naming all come here. **Not a turn:** a turn picks
        context, may call tools and lands in the conversation, all wrong for
        "proofread this sentence" - so this is the model and nothing else,
        with the same model choice (local first) the turns make. Runs off the
        main loop (`export` dispatches every method that way), so waiting for
        the model blocks no one.
        """
        instruction, text = (str(v or "") for v in params.unpack())
        if not instruction.strip():
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.InvalidArgs", "Write needs an instruction")
            return
        llm = getattr(self._app, "llm", None)
        if llm is None:
            invocation.return_dbus_error(
                "dev.shani.chronoa.Error.NoModel", "Chronoa has no model to write with yet")
            return
        messages = [
            {"role": "system", "content": WRITE_SYSTEM},
            {"role": "user", "content": instruction.strip() + "\n\n<text>\n" + text[:WRITE_MAX_CHARS] + "\n</text>"},
        ]
        logger.info("write: %r on %d characters", instruction[:60], len(text))
        try:
            # chat_message: what every client has (the local one has no chat())
            message = self._app._async.run(llm.chat_message(messages)).result(timeout=WRITE_TIMEOUT_SECONDS)
            reply = message.get("content", "") if isinstance(message, dict) else str(message or "")
        except Exception as exc:  # noqa: BLE001 - the caller is told, in words
            invocation.return_dbus_error(
                "dev.shani.chronoa.Error.WriteFailed", f"{type(exc).__name__}: {exc}")
            return
        invocation.return_value(GLib.Variant("(s)", (clean_written(reply),)))

    # -- FindFiles: the person's files by meaning (file_index) --------

    def FindFiles(self, connection, sender, path, interface, method, params,
                  invocation) -> None:
        """`{"how", "hits": [{path, kind, size, excerpt}], "indexing"}` as JSON.

        The shell's launcher asks this for "the electricity bill from March":
        file_index's own search over the folders a person keeps things in
        (FIND_ROOTS), by meaning when the embedding model answers and by
        keyword when it does not - and `how` says which, as the skill does. An
        index older than FIND_REINDEX_SECONDS is brought up to date in the
        background (incremental: unchanged files are not re-read), so the
        search answers now from what is there and better next time.
        """
        query, limit = params.unpack()
        query = str(query or "").strip()
        limit = min(max(int(limit or 8), 1), 25)
        if len(query) < 3:
            invocation.return_value(GLib.Variant("(s)", (json.dumps({"how": "", "hits": [], "indexing": False}),)))
            return
        from shani_chronoa import file_index
        hits, how = [], ""
        for root in find_roots():
            try:
                got, how = file_index.search(root, query, limit=limit, embed=True)
            except Exception as exc:  # noqa: BLE001 - one folder's trouble is not the search's
                logger.info("find files in %s: %s", root, exc)
                continue
            hits.extend(dict(h, root=str(root)) for h in got)
        indexing = self._reindex_if_stale()
        out = {"how": how, "hits": hits[:limit], "indexing": indexing}
        invocation.return_value(GLib.Variant("(s)", (json.dumps(out),)))

    # None, never 0.0: time.monotonic() counts from boot, so for the first ten minutes
    # after a boot 0.0 looked fresh and the index was never built (measured: 350 s)
    _last_index = None
    _index_lock = threading.Lock()

    def _reindex_if_stale(self) -> bool:
        import time
        last = AgentService._last_index
        if (last is not None and time.monotonic() - last < FIND_REINDEX_SECONDS) or not AgentService._index_lock.acquire(blocking=False):
            return False
        AgentService._last_index = time.monotonic()

        def run():
            from shani_chronoa import file_index
            try:
                for root in find_roots():
                    file_index.index_directory(root, embed=True)
            except Exception:  # noqa: BLE001 - logged; the next search tries again
                logger.exception("indexing for FindFiles")
            finally:
                AgentService._index_lock.release()
        threading.Thread(target=run, name="find-files-index", daemon=True).start()
        return True

    def _on_done(self, turn_id: str, result) -> None:
        if isinstance(result, Exception):
            self.emit_event(json.dumps(
                {"turn": turn_id, "event": "error",
                 "text": f"{type(result).__name__}: {result}"}))
        self.emit_event(json.dumps({"turn": turn_id, "event": "done"}))

    def emit_event(self, payload: str) -> bool:
        if self._connection is None:
            return False
        self._connection.emit_signal(
            None, PATH, IFACE, "Event", GLib.Variant("(s)", (payload,)))
        return False

    # -- the resolver ask_bridge binds to a turn ------------------------

    def register(self, turn_id: str, done: threading.Event,
                 resolve: Callable[[str], None]) -> "Optional[threading.Event]":
        """Register one turn's approval wait; return a stale one it replaces."""
        with self._lock:
            stale = self._pending.get(turn_id)
            self._pending[turn_id] = (done, resolve)
        if stale is not None and stale[0] is not done:
            return stale[0]
        return None

    def forget(self, turn_id: str, done: threading.Event,
               closed: Callable[[], None]) -> None:
        """Drop a wait that stopped waiting, telling the shell if it was live."""
        with self._lock:
            current = self._pending.get(turn_id)
            if current is None or current[0] is not done:
                return
            self._pending.pop(turn_id, None)
        closed()

    def resolve(self, turn_id: str, decision: str) -> bool:
        with self._lock:
            entry = self._pending.pop(turn_id, None)
        if entry is None:
            return False
        entry[1](decision)
        return True

    # -- bus export -----------------------------------------------------

    def _dispatch(self, connection_, sender, path, interface, method,
                  params, invocation):
        handler = getattr(self, method, None)
        if handler is None:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod",
                f"{interface} has no method {method!r}")
            return
        handler(connection_, sender, path, interface, method, params, invocation)

    def export(self) -> None:
        from shani_chronoa.gateway import off_the_main_loop

        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def dispatch(connection_, sender, path, interface, method, params,
                     invocation):
            call = (connection_, sender, path, interface, method, params,
                    invocation)
            off_the_main_loop(lambda: self._dispatch(*call), f"agent-{method}")

        self._registration = self._connection.register_object(
            PATH, node.interfaces[0], dispatch, None, None)
        self._owner = Gio.bus_own_name_on_connection(
            self._connection, BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE
            | Gio.BusNameOwnerFlags.ALLOW_REPLACEMENT,
            None, None)
        logger.info("agent interface requested at %s (%s)", PATH, BUS_NAME)

    def unexport(self) -> None:
        if self._owner is not None:
            Gio.bus_unown_name(self._owner)
            self._owner = None
        connection, registration = self._connection, self._registration
        self._registration = None
        if registration is not None and connection is not None:
            try:
                connection.unregister_object(registration)
            except Exception:
                logger.debug("agent object already gone", exc_info=True)
        # Nothing after this point should try to emit on a connection whose
        # object is gone; a shutdown turn finishing late is not a signal.
        self._connection = None
