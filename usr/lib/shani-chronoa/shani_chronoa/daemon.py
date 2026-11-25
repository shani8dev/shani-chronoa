"""Background mode: Chronoa's automatic rules and senses with no window open.

Run by the systemd *user* unit shani-chronoa-daemon.service, which the
"Keep Chronoa's automatic rules running in the background" switch enables -
installed, never enabled by default. It is the same AmbientScheduler and
EventEngine the window runs, with no GTK at all, so there is no second
implementation of rules, consent or approvals to drift: approvals still ask
through a notification, and every consent key is still read per event.

What it deliberately does not do: listen. A wake phrase with no window on
screen is a microphone open with nothing to show it, so the wake word stays a
window feature. And it does not race the window: whichever of the two holds
`runner_lock` runs the rules.
"""

from __future__ import annotations

import logging
import signal
import time
import threading

logger = logging.getLogger(__name__)


#: How long after starting to sleep for the first time. Long enough that
#: `systemctl start` returns promptly and the log line means what it says.
SLEEP_FIRST_DELAY_SECONDS = 60.0
#: How often to consider sleeping. Consolidation is cheap when there is nothing
#: new, but it is not free when there is.
SLEEP_EVERY_SECONDS = 600.0


def _rules_allowed(*, critical: bool = False, armed: bool = False) -> bool:
    """The scheduler thread's view of the rhythm rule gate.

    `RhythmMonitor.tick` records its observation through `rhythm.note_state`,
    so the poller reads the same state the once-a-second loop used - a stale
    reading can hold a routine rule for one more tick, never grant one early.
    Critical notifications and unattended actions the person explicitly armed
    pass their flag and are never held here.
    """
    from shani_chronoa import rhythm

    return rhythm.should_run_rules(
        rhythm.current_state(), critical=critical, armed=armed
    )


def _dream_tick() -> None:
    """One dream pass, and a log line for every outcome it can have.

    **Three outcomes, not two.** `write_dream()` returns `None` when the pass
    runs and its result cannot be written to disk, and the old caller's
    `if written:` was simply false - so that case logged nothing at all, and was
    as silent as a pass that found nothing to say. Measured, with the destination
    unwritable: the pass read this machine's whole 18.9 MB log and produced its
    full 40-line report, and the daemon's log held **no line about it**. A pass
    that could not be recorded is not a pass that found nothing.

    Named rather than inline in the loop because a block inside a `while` is
    unreachable from a test, and this is the one piece of the daemon whose
    failure has to be observable.
    """
    from shani_chronoa import dream as dream_module

    try:
        written = dream_module.write_dream()
    except Exception:
        # Same reasoning as the consolidation pass: a dream that cannot run must
        # not take down the rules and senses that are the point of the daemon.
        logger.warning("dream pass failed", exc_info=True)
        return
    if written:
        logger.info("dreamed; wrote %s", written.name)
    else:
        logger.info("dreamed, but the result could not be written to disk")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    from shani_chronoa.senses.scheduler import AmbientScheduler
    from shani_chronoa.senses.store import PerceptStore
    from shani_chronoa.triggers import EventEngine

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    from shani_chronoa import rhythm
    from shani_chronoa.senses import idle as idle_sense

    store = PerceptStore()
    monitor = rhythm.RhythmMonitor(store=store, read_idle=idle_sense.read_idle_seconds)
    # The scheduler owns the polling thread, so the rhythm decision has to be
    # visible there: routine rules wait in sleep/dream/maintenance while senses
    # and the reflex layer keep running. Consent and approvals are re-read per
    # event in the engine and are not bypassed by this gate.
    scheduler = AmbientScheduler(
        store=store,
        event_engine=EventEngine(),
        rule_gate=_rules_allowed,
    )
    scheduler.start()
    logger.info(
        "Chronoa background mode running (rules and ambient senses; no microphone)"
    )
    # Sleep. Runs on a timer rather than on every tick, because consolidation
    # reads whole conversations and a per-second poll would be absurd. The first
    # pass is delayed so starting the daemon is not also a burst of work; the
    # loop wakes once a second regardless and only *considers* it every ten
    # minutes, which is checked with a monotonic deadline rather than a counter
    # so a slow pass cannot make it fire twice.
    #
    # The dream rides on the same tick rather than getting its own timer unit.
    # It reads the tool-call log this machine already writes and asks what the
    # day looks like - which skills kept failing, which errors repeat - and that
    # is the same kind of offline reflection as consolidation, over the same
    # evidence, on the same clock. A second timer would mean a second thing to
    # stop, start and misfire independently. `_dream_tick` holds the pass and
    # every log line its outcomes can produce.
    from shani_chronoa import consolidation

    next_sleep = time.monotonic() + SLEEP_FIRST_DELAY_SECONDS
    try:
        while not stop.wait(1.0):
            # Consult the rule gate with the state just observed instead of
            # discarding it. The scheduler's poller asks the same question
            # through `current_state`; this line makes the observed decision
            # visible in the log. Interrupt policy reads the same state.
            state = monitor.tick().state
            if not rhythm.should_run_rules(state):
                logger.debug("rhythm %s holds routine rules this tick", state.value)
            now = time.monotonic()
            if now >= next_sleep:
                next_sleep = now + SLEEP_EVERY_SECONDS
                try:
                    # An optional summary from the local server if it is
                    # already up; `None` keeps the extractive digest, so a
                    # machine with no model still sleeps. The index refresh
                    # happens in the sleep pass itself.
                    from shani_chronoa import conversation_learning

                    slept = consolidation.sleep_now(
                        enrich=conversation_learning.local_enricher()
                    )
                    if slept:
                        logger.info("slept on %d conversation(s)", len(slept))
                except Exception:
                    # Sleep is the most optional thing the daemon does. A failure
                    # here must not take down the rules and senses that are the
                    # point of running it.
                    logger.warning("consolidation failed", exc_info=True)
                _dream_tick()
    finally:
        scheduler.stop()
        from shani_chronoa import runner_lock

        runner_lock.release()
        logger.info("Chronoa background mode stopped")
    return 0
