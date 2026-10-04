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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from shani_chronoa.senses.scheduler import AmbientScheduler
    from shani_chronoa.senses.store import PerceptStore
    from shani_chronoa.triggers import EventEngine

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    scheduler = AmbientScheduler(store=PerceptStore(), event_engine=EventEngine())
    scheduler.start()
    logger.info("Chronoa background mode running (rules and ambient senses; no microphone)")
    # Sleep. Runs on a timer rather than on every tick, because consolidation
    # reads whole conversations and a per-second poll would be absurd. The first
    # pass is delayed so starting the daemon is not also a burst of work; the
    # loop wakes once a second regardless and only *considers* it every ten
    # minutes, which is checked with a monotonic deadline rather than a counter
    # so a slow pass cannot make it fire twice.
    from shani_chronoa import consolidation
    next_sleep = time.monotonic() + SLEEP_FIRST_DELAY_SECONDS
    try:
        while not stop.wait(1.0):
            now = time.monotonic()
            if now >= next_sleep:
                next_sleep = now + SLEEP_EVERY_SECONDS
                try:
                    slept = consolidation.sleep_now()
                    if slept:
                        logger.info("slept on %d conversation(s)", len(slept))
                except Exception:
                    # Sleep is the most optional thing the daemon does. A failure
                    # here must not take down the rules and senses that are the
                    # point of running it.
                    logger.warning("consolidation failed", exc_info=True)
    finally:
        scheduler.stop()
        from shani_chronoa import runner_lock
        runner_lock.release()
        logger.info("Chronoa background mode stopped")
    return 0
