"""The Agent contract: a turn stream with approval.

This is Chronoa's own harness-side interface, owned by `agent_service.py`.
It is the contract the shell-side bridge (`chronoa-bridge.py`) expects, and
which the gateway's fire-and-forget `Submit` does not provide - a gateway
answers with a string and is gone; an agent turn has an identity, a stream of
events, and moments where the person must decide.

Methods:
  Turn(text) -> turn_id        # starts a turn; its events arrive via Event
  TurnWith(text, options_json) -> turn_id
                               # a turn with a conversation, files and/or no history
  Approve(id, decision) -> ok  # the person approves or denies a turn step
  Cancel(id) -> ok             # stop waiting on a turn that is still running
  Write(instruction, text) -> text
  FindFiles(query, limit) -> json
  Inventory(section) -> json   # read-only facts for a shell's panels

  Additive capability methods (all return one JSON string; see
  `agent_capabilities.py` for the single table both clients also read):
  SettingsList, SettingsGet, SettingsSet, ModelsList, ModelsUse,
  SetupState, SetupStep, Conversations, ConversationsResume,
  ConversationsRename, ConversationsDelete, MemoryList, MemoryDecide,
  Routines, RoutinesRun, Triggers, Notices, NoticesDismiss, VoiceEngines,
  Activity, DiagnosticsProbe.

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


#: The introspection XML. Literal blocks, not generated: the shell's contract
#: test parses this file as text and compares it with its own copy. Every
#: method here has a slash command and a CLI subcommand in `agent_capabilities`.
#: Interpolated so the name cannot drift from `IFACE`.
XML = f"""<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{IFACE}">
    <method name="Turn">
      <arg name="text" type="s" direction="in"/>
      <arg name="turn_id" type="s" direction="out"/>
    </method>
    <method name="TurnWith">
      <arg name="text" type="s" direction="in"/>
      <arg name="options_json" type="s" direction="in"/>
      <arg name="turn_id" type="s" direction="out"/>
    </method>
    <method name="Approve">
      <arg name="turn_id" type="s" direction="in"/>
      <arg name="decision" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="Cancel">
      <arg name="turn_id" type="s" direction="in"/>
      <arg name="ok" type="b" direction="out"/>
    </method>
    <method name="Write">
      <arg name="instruction" type="s" direction="in"/>
      <arg name="text" type="s" direction="in"/>
      <arg name="result" type="s" direction="out"/>
    </method>
    <method name="FindFiles">
      <arg name="query" type="s" direction="in"/>
      <arg name="limit" type="i" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Inventory">
      <arg name="section" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsList">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsGet">
      <arg name="name" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsSet">
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsPreset">
      <arg name="name" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SettingsEmail">
      <arg name="values_json" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ModelsList">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ModelsUse">
      <arg name="name" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SetupState">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="SetupStep">
      <arg name="step" type="s" direction="in"/>
      <arg name="value" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Conversations">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ConversationsResume">
      <arg name="ref" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ConversationsRename">
      <arg name="ref" type="s" direction="in"/>
      <arg name="title" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="ConversationsDelete">
      <arg name="ref" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="MemoryList">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="MemoryDecide">
      <arg name="id" type="s" direction="in"/>
      <arg name="decision" type="s" direction="in"/>
      <arg name="confirm" type="b" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Routines">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="RoutinesRun">
      <arg name="name" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Triggers">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Notices">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="NoticesDismiss">
      <arg name="id" type="s" direction="in"/>
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="VoiceEngines">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="Activity">
      <arg name="json" type="s" direction="out"/>
    </method>
    <method name="DiagnosticsProbe">
      <arg name="json" type="s" direction="out"/>
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
