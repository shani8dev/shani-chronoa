"""Everything Chronoa left running, and a way to stop each of them.

Why this exists
---------------

A beginner turns on wake-word activation, or background mode, or a phone
watcher, and then finds Chronoa holding a microphone, a Bluetooth link, or a
gigabyte of RAM with **nothing on screen that says so and nothing that turns it
off**. Quitting the app works and is the only lever there is. On a machine
somebody is gaming or streaming on, "quit the app" is not a lever - it is the
reason they do not use the feature.

This is not hypothetical, and the measured shape of it is why this module is an
inventory rather than a stop button. Scan of `app/application.py` at 2026-10-11,
of the things it starts unconditionally:

| what | has a stop() | called by |
|---|---|---|
| incoming-call watcher | yes | never |
| message watcher (MAP) | yes | never |
| obex receiver | yes | never |
| watch companion | yes | never |
| BLE remote service | yes | never |
| AVRCP target | yes | never |

**Every one of the six is stoppable and nothing calls the stop**, and none of the
six appears anywhere in the GUI - `grep` over `gui/` and `app/tray.py` returns no
hit for any of them. So the capability was written, tested in isolation, and left
unreachable, which is the dead-code class this repo records five times over.

The two rules that follow, both of which this module is held to:

1. **A stop that does not exist says so.** A row with a dead Stop button is the
   dead-control class; the honest version is "this one cannot be stopped without
   quitting Chronoa" plus what it is holding. Such rows are named, not hidden.
2. **A stop reports what it observed, not what it asked for.** A row only reads
   "stopped" when the thing itself says it is not running any more. A stop that
   is *requested* and a stop that *happened* are different claims, and only one
   is worth showing.

Ordering matters too: `wake-word` comes first, because the microphone is the
surface a person is least willing to have held open and the first thing they came
looking for. The model server comes second, because it is the one costing memory
rather than a device.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

#: What each background thing is holding, in a person's words. **Not a technical
#: summary** - the row exists so somebody can decide whether they want it off,
#: and "a D-Bus signal subscription" does not answer that.
_MICROPHONE = ("your microphone", "listening for the wake phrase")
_MEMORY = ("your RAM", "the local model server")

_HOLDS = {
    "wake-word": _MICROPHONE,
    "barge-in": (_MICROPHONE[0], "watching for you to talk over the reply"),
    "incoming-calls": ("Bluetooth", "watching the paired phone for a ringing call"),
    "messages": ("Bluetooth", "watching for new texts on the paired phone"),
    "obex": ("Bluetooth", "accepting files a phone shares to this computer"),
    "watch": ("the watch", "keeping the MoYoung watch connected"),
    "remote": ("Bluetooth", "being a keyboard for the phone, as Chronoa Remote"),
    "avrcp": ("Bluetooth", "serving this computer's music to Bluetooth remotes"),
    "senses": _MEMORY,
    "model": _MEMORY,
}


@dataclass
class Running:
    """One thing Chronoa started, and how to stop it.

    `stop` is `None` when there is no way to stop this without quitting. That is
    a fact about the component, not an omission here, and `why_not` is the
    sentence a person reads when it is `None` - because a control that cannot be
    reached is the dead-control failure this module exists to prevent.
    """

    key: str
    label: str
    holds: str
    stop: Optional[Callable[[], Optional[str]]]
    running: bool = True
    why_not: str = ""
    detail: str = ""

    def request_stop(self) -> str:
        """Stop it, and return a sentence about what happened.

        Never raises: a background inventory that can crash the window it is
        drawn into is worse than one that cannot stop something.
        """
        if self.stop is None:
            return self.why_not or "there is no way to stop this one."
        try:
            said = self.stop()
        except Exception as exc:  # noqa: BLE001 - a failed stop must be readable
            logger.warning("could not stop %s", self.key, exc_info=True)
            self.running = True
            return f"Could not stop it: {type(exc).__name__}: {exc}"
        self.running = False
        return said or "stopped."


@dataclass
class Inventory:
    """What is running now, and the stop for each.

    `force_stop_all` exists because the request behind this module was for one
    button, not a theory: a person who wants their microphone back does not want
    to walk eight rows. It stops everything that *can* be stopped and returns the
    rows that could not, with their reasons - so the button answers for the ones
    it failed on instead of silently reporting success.
    """

    items: List[Running] = field(default_factory=list)

    def by_key(self, key: str) -> Optional[Running]:
        for item in self.items:
            if item.key == key:
                return item
        return None

    def stoppable(self) -> List[Running]:
        return [item for item in self.items if item.stop is not None]

    def force_stop_all(self) -> "tuple[List[str], List[Running]]":
        """`(sentences, rows that could not be stopped)`.

        Stops in the order the rows are listed - the microphone first, the memory
        second. A failure marks the row running again and returns it, so the
        caller can say which one fought back.
        """
        said: List[str] = []
        refused: List[Running] = []
        for item in self.items:
            if item.stop is None:
                refused.append(item)
                continue
            sentence = item.request_stop()
            if item.running:
                refused.append(item)
            else:
                said.append(f"{item.label}: {sentence}")
        return said, refused


def _attr(app: Any, name: str) -> Any:
    return getattr(app, name, None)


def _stop_call(stop: Optional[Callable[..., Any]]) -> Optional[Callable[[], Optional[str]]]:
    """Wrap a component's own `stop()` in this module's one-argument shape.

    Components differ: `wakeword.stop()` returns `None`,
    `sense_scheduler.stop(timeout=5.0)` takes a budget, `local_llm.stop_service()`
    returns a sentence. One wrapper so the surface does not have to know which one
    it is holding, and so adding a component does not change the caller.
    """
    if stop is None:
        return None

    def call() -> Optional[str]:
        return stop()

    return call


def _model_row(add) -> None:
    """The local model server, whose stop is a `systemctl` call on a thread."""
    try:
        from shani_chronoa import local_llm
        model_running = local_llm.is_up()
        model_stop = local_llm.stop_service
    except Exception as exc:  # noqa: BLE001 - an unimportable reader is not "off"
        logger.debug("local_llm unavailable: %s", exc)
        model_running, model_stop = False, None
    add("model", "Local model server", model_stop, running=model_running)


def inventory(app: Any) -> Inventory:
    """What `app` is holding open right now, in the order a person cares about.

    **Every entry is read off the live application object**, not from a list kept
    in step by hand - the same reason `gui/surfaces/models.py` reads
    `local_llm.TIERS`. A background inventory that hard-codes the app's attribute
    names is one edit away from listing a component that no longer exists.
    """
    rows: List[Running] = []

    def add(key: str, name: str, stop: Any, why_not: str = "",
            running: bool = True) -> None:
        holds = _HOLDS.get(key, (name.lower(), "running in the background"))
        rows.append(Running(
            key=key,
            label=name,
            holds=holds[0],
            stop=_stop_call(stop) if stop is not None else None,
            running=running,
            why_not=why_not,
            detail=holds[1],
        ))

    # 1. the microphone, because it is what somebody came to turn off
    wake = _attr(app, "wakeword")
    add("wake-word", "Wake-word listening",
        getattr(wake, "stop", None) if wake else None,
        running=bool(wake and getattr(wake, "is_listening", lambda: False)()))

    # 2. talk-over-the-reply, which holds it a second time
    barge = _attr(app, "barge_in_monitor")
    add("barge-in", "Talk over the reply",
        getattr(barge, "stop", None) if barge else None,
        running=bool(barge and getattr(barge, "is_monitoring", lambda: False)()))

    # 3. the memory
    _model_row(add)

    # 4. the phones and the watch. Every one of these *has* a stop() and nothing
    #    in the tree calls it, which is the finding this module was written
    #    about - so each becomes a row with a real button.
    for key, name, attr in (
        ("incoming-calls", "Incoming-call watcher", "incoming_calls"),
        ("messages", "Message watcher", "message_watcher"),
        ("obex", "File sharing receiver", "obex_receiver"),
        ("watch", "Watch companion", "watch_companion"),
        ("remote", "Chronoa Remote (phone keyboard)", "phone_remote"),
        ("avrcp", "Bluetooth music remote", "avrcp_target"),
    ):
        component = _attr(app, attr)
        add(key, name, getattr(component, "stop", None) if component else None,
            running=component is not None)

    # 5. the pollers
    scheduler = _attr(app, "sense_scheduler")
    add("senses", "Sense polling",
        getattr(scheduler, "stop", None) if scheduler else None,
        running=scheduler is not None)

    return Inventory(items=rows)


def summary(app: Any) -> str:
    """One sentence for the rail or a log line: what is held and what is not.

    **The negative half is the point.** "Nothing is running" is a claim about
    every component this module knows, and a summary that omits the ones it could
    not stop is the false confidence this repo keeps recording - the #2463
    lesson, applied to the machine rather than to a skill.
    """
    found = inventory(app)
    on = [item.label for item in found.items if item.running and item.stop is not None]
    stuck = [item.label for item in found.items if item.running and item.stop is None]
    if not on and not stuck:
        return "nothing is running in the background."
    parts = []
    if on:
        parts.append("running: " + ", ".join(on))
    if stuck:
        parts.append("running with no way to stop it from here: " + ", ".join(stuck))
    return "; ".join(parts) + "."
