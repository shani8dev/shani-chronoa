"""Chronoa as an organism: one inventory of every function it has.

`ARCHITECTURE-TARGET.md` already describes the target as a digital organism and
Part 2 of it works through the boxes. What was missing is the thing that makes
such a document rot: nobody can tell, from reading it, whether a box is real.

So the mapping lives here, as data, next to the code it describes, and three
things read it - the body's indicator strip, the Inventory panel, and a test that
refuses to let it drift. Each organ names:

- the **human function** it is (which is the point: "Chronoa hears" is a claim
  someone can check against their own body);
- the **code that does it**, as real paths, so "✅ built" is a statement about
  files rather than an intention;
- its **state**, in the same three words Part 2 already uses - `built`, `part`,
  `absent` - so nothing here has to invent a vocabulary;
- whether the **indicator strip** covers it. Most do not, and saying so is the
  useful part: an inventory that claimed forty live indicators would be the same
  confident-untruth this repo keeps catching.

**The anatomy is standard physiology rather than a poetic gloss**, because the
useful property of "amygdala" is that it means fear and fast evaluation, and
that is exactly what `guardrail.py` and `approvals.py` do. Where a metaphor would
be flattering and wrong, the organ is not used at all - there is no
"chronoa's left pinky", because nothing in here has one.

Six systems, from the outside in: **boundary** (what touches the world), **senses**
(how it perceives), **action** (how it changes the world), **core** (how it
thinks), **regulation** (how it keeps itself running), **memory and learning**
(what it keeps). Order matters for the panel: it is the order a person reads a
body in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

#: The three states, borrowed verbatim from ARCHITECTURE-TARGET.md Part 2 so
#: nothing here has to invent a word. `part` means the honest middle: some of it
#: works, and the panel says which part.
BUILT = "built"
PART = "part"
ABSENT = "absent"

SYSTEMS = (
    ("Boundary", "what touches the world, and what keeps the world out"),
    ("Senses", "how it perceives, in the order a body does"),
    ("Action", "how it changes the world"),
    ("Core", "how it thinks"),
    ("Regulation", "how it keeps itself running"),
    ("Memory and learning", "what it keeps, and what it learns from it"),
)


@dataclass(frozen=True)
class Organ:
    """One function, its code, and how much of it there is."""

    key: str
    system: str
    anatomy: str
    function: str
    code: Tuple[str, ...]
    state: str
    #: What the organ is doing, as a strip says it mid-activity: "listening",
    #: not "ears". Only on organs the strip can actually light.
    gerund: str = ""
    #: Where the light comes from - a file, not a description. A strip whose
    #: sources were guesses would lie the same way an unlabelled light does.
    source: str = ""
    #: The `body.ORGANS` entry that lights while this is happening, or None.
    indicator: Optional[str] = None
    #: What is missing, when the state is not `built`. Shown verbatim: an
    #: inventory that says "part" without saying which part is a status light
    #: with a nice colour.
    missing: str = ""

    @property
    def is_live(self) -> bool:
        """Whether the indicator can show this one."""
        return self.indicator is not None


INVENTORY: Tuple[Organ, ...] = (
    # -- Boundary --------------------------------------------------------
    Organ("skin", "Boundary", "Skin",
          "the outside edge: everything that leaves this machine, and the log of it",
          ("egress.py", "senses/network.py"), BUILT, gerund="on the network", source="every request off this machine (egress.py)", indicator="skin",
          missing=""),
    Organ("immune", "Boundary", "Immune system",
          "what defends against a hostile world - untrusted input, privilege, secrets",
          ("guardrail.py", "redaction.py", "sandbox/", "permissions.py",
           "senses/security.py"), BUILT,
          missing=""),
    Organ("barrier", "Boundary", "Scar tissue",
          "the wall between a skill and the machine it runs on",
          ("sandbox/executor.py", "sandbox/profiles.py", "sandbox/landlock.py",
           "sandbox/seccomp.py"), BUILT, missing=""),

    # -- Senses ----------------------------------------------------------
    Organ("ears", "Senses", "Ears",
          "hearing: the microphone, speech recognition, wake phrase, and what is heard",
          ("audio.py", "stt.py", "stt_provision.py", "wakeword.py", "vad.py",
           "senses/hearing.py"), BUILT, gerund="listening", source="the microphone (audio.py, stt.py, wakeword.py)", indicator="ears"),
    Organ("eyes", "Senses", "Eyes",
          "sight: the camera, the screen, OCR, faces, objects, and the vision model",
          ("screengrab.py", "local_vision.py", "opencv/",
           "photo_library.py", "senses/vision.py"), BUILT, gerund="looking", source="the camera and the screen (screengrab.py, local_vision.py)", indicator="eyes"),
    Organ("nose", "Senses", "Nose",
          "smell: the machine's own state - power, heat, disks, services, network",
          ("senses/power.py", "senses/thermalgrid.py", "senses/hwmon.py",
           "senses/services.py", "senses/network.py", "senses/security.py"),
          BUILT, gerund="feeling the machine", source="the machine-state senses (senses/*)", indicator="nose"),
    Organ("skin_touch", "Senses", "Touch",
          "contact: idle time, and what is plugged into it",
          ("senses/idle.py", "senses/devices.py", "senses/usb.py",
           "senses/bluetooth.py"), PART, gerund="feeling the machine", source="the machine-state senses (senses/*)", indicator="nose",
          missing="no raw input events, on purpose - it watches devices, not you"),
    Organ("tongue", "Senses", "Tongue",
          "taste: judging whether an action actually worked",
          ("verification.py", "history_repair.py", "guardrail.py"), BUILT),
    Organ("balance", "Senses", "Vestibular sense",
          "balance: what deserves attention now, out of everything that happened",
          ("tool_select.py", "compression.py", "triggers/events.py"), PART,
          missing="no salience or priority queue; selection is by tool relevance only"),

    # -- Action ----------------------------------------------------------
    Organ("mouth", "Action", "Mouth",
          "speech: saying things, in the voice that was chosen",
          ("tts.py", "voices.py", "sherpa.py", "audio.py"), BUILT, gerund="speaking", source="the speaker (tts.py, audio.py playback)", indicator="mouth"),
    Organ("hands", "Action", "Hands",
          "acting: changing the world - files, windows, services, settings, devices",
          ("tools.py", "skills/", "portal.py", "desktop_session.py", "phone.py"),
          BUILT, gerund="acting", source="an actuator (tools.py execute_tool)", indicator="hands"),
    Organ("legs", "Action", "Legs",
          "getting about without being asked: rules that fire on their own",
          ("triggers/rules.py", "triggers/event_rules.py", "skills/timer.py",
           "approvals.py"), BUILT),
    Organ("lungs", "Action", "Diaphragm",
          "breath: the rhythm of a day - awake, active, idle, asleep",
          ("daemon.py", "senses/idle.py", "model_service.py"), PART,
          missing="no awake/idle/sleep state machine; the daemon runs, nothing sleeps"),

    # -- Core ------------------------------------------------------------
    Organ("brain", "Core", "Brain",
          "thinking: the language model and the tool loop it drives",
          ("assistant.py", "local_llm.py", "ollama_llm.py", "cloud_llm.py",
           "mcp.py"), BUILT, gerund="thinking", source="a model call (assistant.py)", indicator="brain"),
    Organ("prefrontal", "Core", "Prefrontal cortex",
          "judgement before acting: permissions, plan mode, and the refusals",
          ("capabilities.py", "planmode.py", "permissions.py", "approvals.py"),
          BUILT),
    Organ("cerebellum", "Core", "Cerebellum",
          "checking its own work: post-conditions, and repairing a broken turn",
          ("verification.py", "history_repair.py", "guardrail.py"), BUILT),
    Organ("amygdala", "Core", "Amygdala",
          "fast evaluation of danger, before the slow part of the brain is asked",
          ("guardrail.py", "permissions.py", "approvals.py",
           "senses/security.py"), BUILT),
    Organ("attention", "Core", "Reticular formation",
          "attention: deciding what is worth interrupting for",
          ("triggers/events.py", "cues.py", "speech.py"), PART,
          missing="no interrupt policy; everything notable is announced equally"),

    # -- Regulation ------------------------------------------------------
    Organ("heart", "Regulation", "Heart",
          "pulse: is it alive, is the model up, is the clock sane",
          ("model_service.py", "local_llm.py", "senses/timebase.py"), PART,
          missing="a liveness signal exists per subsystem; nothing consumes it yet"),
    Organ("temperature", "Regulation", "Thermoregulation",
          "keeping itself at a working temperature, and noticing when it cannot",
          ("senses/thermalgrid.py", "senses/hwmon.py", "skills/power_profile.py"),
          PART, gerund="feeling the machine", source="the machine-state senses (senses/*)", indicator="nose",
          missing="it reads its temperature; it does not act on it unasked"),
    Organ("homeostasis", "Regulation", "Hypothalamus",
          "setpoints: the conditions it should notice on its own",
          ("triggers/rules.py", "approvals.py"), PART,
          missing="no built-in presets; a rule has to be armed by the user"),
    Organ("circadian", "Regulation", "Circadian rhythm",
          "rhythm: knowing what time it is and whether that matters now",
          ("senses/timebase.py", "senses/idle.py", "triggers/"), PART,
          missing="the clock is read; nothing is scheduled *because* of the hour"),

    # -- Memory and learning ---------------------------------------------
    Organ("memory", "Memory and learning", "Hippocampus",
          "what it was told, and what it perceived",
          ("senses/store.py", "conversation_store.py", "local_embed.py"), BUILT,
          gerund="remembering", source="the percept store (senses/store.py)", indicator="memory"),
    Organ("consolidation", "Memory and learning", "Sleep",
          "consolidation: turning a long conversation into something usable",
          ("consolidation.py", "compression.py"), BUILT,
          missing="an extractive digest and a queue of proposed facts, both "
                  "written without calling a model; no dream, and nothing "
                  "proposes while a conversation is still in progress"),
    Organ("learning", "Memory and learning", "Synaptic plasticity",
          "learning from how a call went - the outcome, whether it was approved, undone",
          ("learning.py", "tool_tracking.py", "tool_select.py"), BUILT,
          gerund="weighing what worked", indicator="brain",
          missing="verification verdicts reorder the tools that are already "
                  "allowed; there is no preference learning from what you "
                  "liked, and a score can never grant a tool"),
    Organ("endocrine", "Memory and learning", "Endocrine system",
          "slow signals about how things are going: pain and reward",
          ("tool_tracking.py", "cues.py", "verification.py"), PART,
          missing="faults and pressures are reported; there is no reward signal at all"),
)


def by_system() -> "List[Tuple[str, List[Organ]]]":
    """The inventory grouped for display, in `SYSTEMS` order.

    An organ whose system is not in `SYSTEMS` is still shown, under "Everything
    else", rather than dropped - a silently missing entry is how an inventory
    starts lying about its own completeness.
    """
    grouped: "List[Tuple[str, List[Organ]]]" = []
    for system, _description in SYSTEMS:
        members = [organ for organ in INVENTORY if organ.system == system]
        if members:
            grouped.append((system, members))
    elsewhere = [organ for organ in INVENTORY
                 if organ not in [o for _s, members in grouped for o in members]]
    if elsewhere:
        grouped.append(("Everything else", elsewhere))
    return grouped


def live_organs() -> "List[str]":
    """Which organs the indicator strip can actually show."""
    return [organ.indicator for organ in INVENTORY
            if organ.indicator and organ.state in (BUILT, PART)]


def tally() -> "dict":
    """How much of the organism exists, counted the way the target document does."""
    counts = {BUILT: 0, PART: 0, ABSENT: 0}
    for organ in INVENTORY:
        counts[organ.state] = counts.get(organ.state, 0) + 1
    counts["total"] = len(INVENTORY)
    return counts


def known_organ(anatomy: str) -> Optional[Organ]:
    for organ in INVENTORY:
        if organ.anatomy == anatomy:
            return organ
    return None