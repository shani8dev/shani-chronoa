"""Sense: perceive a web page's text, under explicit per-sense consent.

This is the perception half of the former `skills/web_search.py` behaviour.
The old skill opened the query in the default browser and returned a fixed
string, so the model was told to go look at something and never learned what
was there. Here a fetch actually happens and the readable text of the
response body is what reaches the model. The fetch itself is
`shani_chronoa.webtext`; this module owns the sense: consent, lifetime and
sensitivity.

**Consent is the whole point of this file.** Every entry point checks
`ChronoaConfig().sense_allowed("web")`, which requires BOTH the
`web-sense-enabled` gsetting (default false) AND privacy mode off, because
this is the one sense that necessarily sends a request off the machine. A
fresh `ChronoaConfig()` is built per call, matching the rest of the
codebase, so a mid-session toggle takes effect on the very next call rather
than being cached. The refusal text comes from `sense_allowed_reason()`, so
the user is told which of the two gates stopped it.

**Sensitivity is PUBLIC, deliberately.** Fetched third-party page content is
not the user's data: it is text some other machine published for anyone to
read. The privacy exposure here is the *request* - the URL, and whatever the
query in it says - which is why the gate is a network gate, not the response
body. Marking it PERSONAL or PRIVATE would be dishonest in both directions:
it would make `ContextBuilder` shed the percept first under budget pressure
and, worse, imply in an audit that the content is the user's when it is not.
The URL is recorded in the percept's `source` field, which is where the
request actually went.

**TTL is 5 minutes, not `None`.** A page's content goes stale fast, and
`PerceptStore` drops a transient percept on read once its TTL passes, so a
stale fetch cannot be presented as current later in a long session. A
durable (None-TTL) web percept would be a stored copy of a third-party page
in the per-user data dir indefinitely, which is a surveillance-shaped default
for something the project advertises as local-only.

**Poll interval is `None`.** Reactive only. Polling the web on a timer is
not something a user consented to by enabling a sense, and it is the one
sense where periodic egress is hard to notice.
"""

import time

from shani_chronoa.config import ChronoaConfig
from shani_chronoa.senses import SENSITIVITY_PUBLIC, Percept, Sense
from shani_chronoa.webtext import RetrievalError, render, retrieve

# Percept lifetime: a retrieved page is a snapshot of someone else's page.
PAGE_TTL_SECONDS = 300.0

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web",
        "description": (
            "Fetch one web page and return its readable text. Only works when "
            "the web sense is enabled and privacy mode is off."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The absolute http:// or https:// URL to fetch.",
                },
            },
            "required": ["url"],
        },
    },
}


def _refusal(config: ChronoaConfig) -> str:
    return f"Web retrieval is not permitted: {config.sense_allowed_reason('web')}."


def _run(arguments: dict):
    """Fetch `arguments['url']` and return a Percept of its text.

    A refusal is returned as plain text rather than a Percept: it is not a
    perception of anything, and letting the loader wrap it would file a
    non-observation in the transient store as though it were one.
    """
    config = ChronoaConfig()
    if not config.sense_allowed("web"):
        return _refusal(config)

    url = (arguments.get("url") or "").strip()
    if not url:
        return "No URL given to fetch."

    try:
        page = retrieve(url)
    except RetrievalError as e:
        return f"Could not fetch {url}: {e}"

    return Percept(
        sense="web",
        kind="page",
        content=render(page),
        created_at=time.time(),
        ttl_seconds=PAGE_TTL_SECONDS,
        source=page.url,
        sensitivity=SENSITIVITY_PUBLIC,
        metadata={"title": page.title, "chars_dropped": page.chars_dropped},
    )


SENSE = Sense(
    name="web",
    kind="page",
    ttl_seconds=PAGE_TTL_SECONDS,
    sensitivity=SENSITIVITY_PUBLIC,
    schema=_SCHEMA,
    run=_run,
    poll_interval=None,
)

SENSES = [SENSE]
