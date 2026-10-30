"""The Agent contract: a turn stream with approval.

This is Chronoa's own harness-side interface, owned by `agent_service.py`.
It is the contract the shell-side bridge (`chronoa-bridge.py`) expects, and
which the gateway's fire-and-forget `Submit` does not provide - a gateway
answers with a string and is gone; an agent turn has an identity, a stream of
events, and moments where the person must decide.

Methods:
  Turn(text) -> turn_id        # starts a turn; its events arrive via Event
  Approve(id, decision) -> ok  # the person approves or denies a turn step

Signal:
  Event(json)                   # the stream of events for a turn

The shape is deliberate:

- `Turn` returns immediately with an id. A turn is not a call that blocks
  until the reply exists - it runs on a worker, it may ask the person
  something, and it may take minutes. The id is what every later event and
  every approval references, so a shell can multiplex several turns and know
  which is which.
- `Approve` is the only way a harness-side person influences a turn that is
  already running. The tool loop asks through `ask_bridge`; when an agent
  resolver is installed, the question is routed here instead of to the GTK
  presenter, and this method is what resolves it. Nothing else on the bus
  reaches a blocked tool call. `decision` is the chosen option *label*, not a
  code: a permission decision like "Allow this session" is matched by
  `permissions.parse_reply` on exactly that string. `ok` is false when no
  question was waiting - a late click, or a card that had already timed out.
- `Event` carries everything else: thinking, a tool starting, a tool's
  result, streamed reply text, an approval to draw, and done. It is one
  signal rather than many because a consumer that can subscribe once gets the
  whole story; a signal per event type would have the same consumer
  subscribing six times and keeping six state machines in sync.

The wire shape of each event is documented in `agent_service.py`; the two
that matter to a consumer are `approval` (`id`, `text`, `options`, `detail`)
and `done`. Every event carries `turn`, so a consumer that started one turn
can ignore the rest.


Not a replacement for the gateway. A gateway is a channel that wants a reply;
the agent is the turn engine itself, and a gateway may sit on top of it.
"""

BUS_NAME = "dev.shani.chronoa.Agent"
PATH = "/dev/shani/chronoa/Agent"
IFACE = "dev.shani.chronoa.Agent"

#: The introspection XML. Interpolated so the name cannot drift from `IFACE`.
XML = f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{IFACE}">
    <method name="Turn">
      <arg name="text" type="s" direction="in"/>
      <arg name="turn_id" type="s" direction="out"/>
    </method>
    <method name="Approve">
      <arg name="turn_id" type="s" direction="in"/>
      <arg name="decision" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <signal name="Event">
      <arg name="json" type="s"/>
    </signal>
  </interface>
</node>
"""


def make_event() -> "tuple[object, object]":
    """A fresh approval wait plus its resolver, for one turn step.

    Re-exported from `ask_bridge` rather than re-implemented here: the event
    type and the answer attribute are defined once, and a second definition
    would be the kind of duplication that silently diverges.
    """
    from shani_chronoa.ask_bridge import make_event as _make

    return _make()