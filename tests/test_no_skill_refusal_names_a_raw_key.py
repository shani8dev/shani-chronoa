"""A skill refusal names the Settings row, never the raw gsettings key.

**The defect.** A refusal used to read *"switching radios is turned off
(enable 'radio-control-enabled' in Settings)"*. Settings spells that switch
"Let Chronoa act on apps and the desktop"; the key is a spelling no screen
shows, so a person following the instruction hunts for a switch that is not
there. `capabilities.gate_label()` is the one answer, and every shipped skill
refusal now reads it.

**The instrument.** Only the literal parts of a string are text a person
reads. A `gate_label(key)` call inside an f-string is code, not text, so the
scan looks at `FormattedValue` sources separately: a bare key variable there
is a raw key, a `gate_label(...)` call is the fix. Schema descriptions are
model-facing and deliberately keep their keys - the instruction phrases below
are the line between the two populations.
"""

from __future__ import annotations

import ast
import pathlib
import re

from shani_chronoa import capabilities


SKILLS = pathlib.Path(capabilities.__file__).parent / "skills"

KEY = re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)*-enabled\b")
KEY_EXPR = re.compile(r"^[A-Za-z_]*KEY$|^key$")
INSTRUCTION = re.compile(
    r"(?:enable|switch on|turn on)\s+['\"\u2018\u201c]"
    r"|in Settings"
    r"|switched off \(['\"\u2018\u201c]"
    r"|needs the ['\"\u2018\u201c][^'\"\u2018\u201d]{0,80}switch",
    re.I,
)


def _parts(node, source):
    """(literal text, formatted expressions) for one string node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value], []
    if isinstance(node, ast.JoinedStr):
        text = [
            v.value
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        expr = [
            ast.get_source_segment(source, v.value) or ""
            for v in node.values
            if isinstance(v, ast.FormattedValue)
        ]
        return text, expr
    return [], []


def _scan(source, name="<source>"):
    """Refusals in one module that still name a raw key."""
    found = []
    for node in ast.walk(ast.parse(source)):
        text, expr = _parts(node, source)
        if not text or not INSTRUCTION.search(" ".join(text)):
            continue
        if KEY.search(" ".join(text)) or any(KEY_EXPR.match(e) for e in expr):
            found.append((name, node.lineno, " ".join(text).strip()[:120]))
    return found


def _shipped():
    hits = []
    for path in sorted(SKILLS.glob("*.py")):
        hits.extend(_scan(path.read_text(), path.name))
    return hits


def test_no_shipped_skill_refusal_names_a_raw_key():
    hits = _shipped()
    assert not hits, hits


def test_the_scan_is_not_looking_at_an_empty_population():
    assert len(list(SKILLS.glob("*.py"))) >= 100


def test_the_scan_can_see_both_spellings_of_the_defect():
    """Positive control: a literal key and a key placeholder are both found."""
    source = (
        "def a():\n"
        "    return \"turned off (enable 'radio-control-enabled' in Settings)\"\n"
        "def b():\n"
        "    return f\"turned off (enable '{_CONSENT_KEY}' in Settings)\"\n"
    )
    assert len(_scan(source)) == 2


def test_a_gate_label_call_is_not_text():
    """The fix is a call in an f-string, not a word a person reads."""
    source = (
        "def c():\n"
        '    return f"turned off (enable \\u201c{gate_label(_CONSENT_KEY)}\\u201d in Settings)"\n'
    )
    assert not _scan(source)


def test_every_literal_key_passed_to_gate_label_has_a_row():
    """`gate_label` falls back to the key when no row names it.

    A refusal behind that fallback still sends a person after a spelling no
    screen shows, so the label itself is part of this contract.
    """
    bad = []
    for path in sorted(SKILLS.glob("*.py")):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "gate_label":
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    label = capabilities.gate_label(arg.value)
                    if label == arg.value:
                        bad.append((path.name, node.lineno, arg.value))
    assert not bad, bad
