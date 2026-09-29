"""Skill: say what is currently perceived.

The percept layer deposits facts into a store, `ContextBuilder` injects them
into every prompt, and **nothing let a person look at either**. So the honest
answer to "what do you actually know about me right now?" was unavailable - and
that is a trust gap, not a missing convenience. Everything else this project
reports, a user can check; this could not.

Reads the same store the assistant reads, so the answer cannot drift from what
would actually be sent.

Honesty rules:

- **Withheld percepts are named.** `ContextBuilder` drops any whose sense is no
  longer permitted, and this reports both counts, because a fact that is
  collected but silently not sent is exactly the thing a user would want to
  know.
- An empty store says the store is empty, which is different from saying nothing
  is being perceived.
- TTL is reported as remaining seconds, so "this expires soon" is answerable
  without reading the code.
"""

from __future__ import annotations

import time

from shani_chronoa.skills import Skill

SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_percepts",
        "description": (
            "Show what Chronoa is currently perceiving - every fact held about "
            "this machine and the user right now, with its age, what expires "
            "it, and whether it would be included in the next reply. Use this to "
            "check what the assistant actually knows rather than what it says."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sense_filter": {
                    "type": "string",
                    "description": "Only percepts from this sense, e.g. 'power'.",
                },
                "include_expired": {
                    "type": "boolean",
                    "description": "Include facts whose lifetime has run out. Defaults to false.",
                },
            },
        },
    },
}


#: A live view older than this is from a process that is gone or wedged, and
#: saying "right now" about it would be its own small lie.
_LIVE_STALE_SECONDS = 300.0


def _load_live(now: float):
    """(percepts, provenance) from the app's published view, or ([], "")."""
    import json

    from shani_chronoa.senses.context import Percept
    from shani_chronoa.senses.store import LIVE_FILE

    try:
        payload = json.loads(LIVE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ""
    written = payload.get("written_at")
    if not isinstance(written, (int, float)):
        return [], ""
    rows = []
    for entry in payload.get("transient", []):
        try:
            rows.append(Percept(**entry))
        except (TypeError, ValueError):
            continue
    age = now - written
    if age > _LIVE_STALE_SECONDS:
        return rows, (f"published {age:.0f}s ago by a process that is no longer "
                     f"running, so this is a leftover rather than a live view")
    return rows, f"live, published {age:.0f}s ago by the running app (pid {payload.get('pid')})"


def _run(arguments: dict) -> str:
    from shani_chronoa.config import ChronoaConfig
    from shani_chronoa.senses.store import PerceptStore

    config = ChronoaConfig()
    store = PerceptStore()
    now = time.time()

    transient, provenance = _load_live(now)
    durable = store.durable()
    everything = transient + durable
    if not everything:
        return (
            "Nothing is being perceived right now, and there are no stored "
            "facts. That is different from Chronoa having nothing to say - a "
            "sense produces a fact when it is polled and has something new to "
            "report."
        )

    needle = (arguments.get("sense_filter") or "").strip().lower()
    what = f" from '{needle}'" if needle else ""
    include_expired = bool(arguments.get("include_expired"))

    rows, live, expired = [], 0, 0
    for p in everything:
        if needle and needle not in (p.sense or "").lower():
            continue
        ttl = getattr(p, "ttl_seconds", None)
        if ttl is None:
            life = "durable - does not expire"
        else:
            remaining = (p.created_at + ttl) - now
            if remaining <= 0:
                if not include_expired:
                    expired += 1
                    continue
                life = f"EXPIRED {-remaining:.0f}s ago"
            else:
                life = f"expires in {remaining:.0f}s"
        if life.startswith("expires"):
            live += 1
        rows.append((p.sense or "?", p.kind or "?", p.content, life,
                     p.sensitivity or "?"))
        if len(rows) >= 200:
            break

    if not rows:
        if expired:
            return (f"No live percepts{what}; {expired} had expired. Pass "
                    f"include_expired to see them.")
        return f"No percepts{what} matched."

    permitted = [r for r in rows if config.sense_allowed(r[0])]
    withheld = [r for r in rows if not config.sense_allowed(r[0])]

    source_note = provenance or (
        "no running app is publishing a live view, so this is the stored facts "
        "only - a sense polled by a running app holds its transient percepts in "
        "memory, and this process cannot see them"
    )
    lines = [
        f"{len(rows)} percept(s) held{what}: {len(permitted)} would be sent with "
        f"the next reply, {len(withheld)} are withheld by current consent.",
        f"Source: {source_note}.",
        "",
    ]
    for sense, kind, content, life, sens in rows:
        body = str(content).replace("\n", " ")[:70]
        lines.append(f"  [{sense}/{kind}] ({sens}, {life})")
        lines.append(f"      {body}")

    if withheld:
        lines.append("")
        lines.append(
            "  withheld by consent - still stored, not sent: "
            + ", ".join(sorted({w[0] for w in withheld}))
            + ". Turn that sense back on to have these included again.")
    if expired:
        lines.append(f"  {expired} expired percept(s) not shown.")
    return "\n".join(lines)


SKILLS = [Skill(name="list_percepts", schema=SCHEMA, run=_run)]
