"""Skill: ask questions about the shape of a JSON document.

`read_text_file` hands over the bytes and stops; `analyze_table` runs SQL over
CSV. Neither can answer the two questions people actually ask about JSON: *what
is in this* and *what does that field hold*. This is that skill.

**Implemented on `json` from the standard library, not by shelling out to
`jq`.** `jq` is on the image today and the capability matrix flags it as an
unused read-only command, so calling it would have been the obvious wiring. It
is not done, for the same reason `system_info` reads `/proc` instead of calling
`uname`, `list_processes` reads `/proc` instead of `ps`, and `compute_hash`
calls `hashlib` instead of `sha256sum`: each of those binaries can be absent on
a minimal image, and a skill that answers nothing on the machines where knowing
the answer matters most is worth less than one that always answers. The subset
of `jq` that gets asked for is small, so the small implementation is the
honest one. `jq`'s full language is a language; a partial implementation of
one is a trap that answers a different question than the one asked.

The path syntax is the familiar dotted part of `jq`'s - `a.b`, `a[0]`, and
that is **all** of it. Pipes, filters, arithmetic and `..` are not accepted,
and a path using them is refused by name rather than misresolved into a
plausible-looking wrong answer.

**How this divides from `analyze_table`, which also reads JSON.** That skill
loads a document into SQLite as a table and the model writes SQL, which is the
right tool for "which month did I spend most". Its `describe` measures the
largest array of records in the file. Measured on this machine, given
`{"user": {"name": "ana", "tags": ["a","b"]}, "items": [{"price": 10}, {"price": 20}]}`,
it reports `Table data: 2 rows` over one column `price` - the `user` object is
gone without being mentioned - and `SELECT name FROM data` fails with
"no such column: name". Flattening is the right trade for a question about
rows and the wrong one for a question about shape, and a `describe` that
reports the rows it kept without saying what it dropped is the exact failure
this skill exists to avoid. This one answers about structure; use
`analyze_table` for the arithmetic.

Honesty rules, each of which is a way this question gets answered wrongly:

- **A missing field is not `null`.** `jq` prints `null` for a key that is not
  there, which reads to a model as a field that exists and holds nothing. That
  is the single most common way an assistant claims a JSON file says something
  it does not, so an absent field is reported as absent, by name.
- **A key that itself contains a dot is reported, not missed.** `a.b` cannot
  reach the literal key `"a.b"`, so when the dotted path resolves to nothing and
  the parent object *does* hold a key containing a dot, that key is named.
- **Malformed JSON is a parse error with its position**, never an empty
  document. "No keys" and "this is not JSON" are different answers.
- **`sum` refuses non-numbers instead of skipping them.** Counting the numeric
  subset and reporting it as the total is a wrong number that looks right.
- **A truncated list says how many rows were withheld** and how to get them,
  because a short list is indistinguishable from a complete one.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional, Tuple

from shani_chronoa import files
from shani_chronoa.skills import Skill

logger = logging.getLogger(__name__)

#: The whole document is parsed into memory at once, so this ceiling is about
#: the assistant's own footprint rather than about patience. `json` on a
#: multi-hundred-megabyte document is minutes of work for an answer nobody
#: reads aloud, and the caller can always ask about one subtree instead.
_MAX_BYTES = 64 * 1024 * 1024

#: Long enough for a config file or a slice of an API response, short enough
#: that the reply is a spoken answer rather than a data dump.
_MAX_ROWS = 40
_MAX_VALUE_CHARS = 4000

#: `_slice_frames`-style budget for the aggregate operations, so a million-entry
#: array does not turn `unique` into a million-line reply.
_MAX_ELEMENTS = 200_000

_OPERATIONS = ("get", "keys", "type", "length", "sum", "unique", "sort")

#: One dotted/indexed step. Anything else is refused by name (see module
#: docstring) rather than parsed optimistically.
_STEP = re.compile(r"^[A-Za-z0-9_\- ]+$")

#: `jq`'s own type names, because "an object" and "a dict" are two vocabularies
#: and the caller is a language model that has read both.
_TYPE_NAMES = {
    dict: "object",
    list: "array",
    str: "string",
    bool: "boolean",
    int: "number",
    float: "number",
    type(None): "null",
}

#: `bool` is a subclass of `int` in Python, so a JSON `true` would otherwise be
#: summed as 1 and a document of booleans would report a real total.
_NOT_A_NUMBER = (bool, type(None))


SCHEMA = {
    "type": "function",
    "function": {
        "name": "json_query",
        "description": (
            "Ask questions about the shape of a JSON document: what keys it "
            "has, what a field contains, how many entries an array has, or the "
            "sum/unique/sorted values of an array. Give either a file path or "
            "paste the document itself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a .json file. Omit if passing the document directly.",
                },
                "document": {
                    "type": "string",
                    "description": "The JSON text itself, when there is no file to read.",
                },
                "field": {
                    "type": "string",
                    "description": (
                        "Where in the document to look, in dotted form: "
                        "'items', 'result.rows', 'items[0].price'. Omit or use '.' "
                        "for the whole document."
                    ),
                },
                "operation": {
                    "type": "string",
                    "description": (
                        f"What to do. One of: {', '.join(_OPERATIONS)}. "
                        "Defaults to 'get', which prints the value at `field`."
                    ),
                },
                "of": {
                    "type": "string",
                    "description": (
                        "For sum/unique/sort: the key to take from each element "
                        "of the array, when the elements are objects."
                    ),
                },
            },
        },
    },
}


def _reject_unsupported(raw: str) -> Optional[str]:
    """Name the part of a path this skill does not implement, or None if fine.

    Refusing is the point. `jq`'s real language has pipes, `..`, `?`, `[]`,
    string interpolation and arithmetic; accepting the text and resolving
    something for it would answer a question the caller did not ask.

    **`-` is deliberately not in this list.** It was, and it broke two ordinary
    cases: JSON keys containing hyphens (`"content-type"`, `"x-request-id"`)
    were refused outright, and so was the negative index `rows[-1]`. Neither is
    arithmetic in a path expression, and both are things people write.
    """
    for token in ("|", "..", "?", "+", "*", "(", ")", "{", "}", ":", ",", "@", "$"):
        if token in raw:
            return token
    return None


def parse_path(raw: object) -> Tuple[List[object], Optional[str]]:
    """Split a dotted/indexed path into steps.

    Returns `(steps, problem)`. `problem` is a sentence for the user naming the
    unsupported construct; `steps` is empty whenever it is set, so a caller
    cannot use a half-parsed path by forgetting to check.
    """
    if raw is None:
        return [], None
    text = str(raw).strip()
    if not text or text == ".":
        return [], None

    bad = _reject_unsupported(text)
    if bad is not None:
        return [], (
            f"Cannot use {bad!r} in a field path. This skill understands dotted "
            f"paths and array indexes - 'items', 'result.rows', 'items[0]' - but "
            f"not jq's pipes, filters or arithmetic. Read the field with "
            f"`read_text_file` and ask about what it returned."
        )

    steps: List[object] = []
    for part in text.split("."):
        if not part:
            return [], f"Cannot parse the field path {raw!r}: it has an empty step."
        name, indexes = _split_indexes(part)
        if not _STEP.match(name):
            return [], (
                f"Cannot parse the field path {raw!r}: {name!r} is not a plain "
                f"key. Keys with dots, spaces or brackets in them cannot be "
                f"reached by a dotted path - read the file with "
                f"`read_text_file` and ask about what it returned."
            )
        steps.append(name)
        steps.extend(indexes)
    return steps, None


def _split_indexes(part: str) -> Tuple[str, List[object]]:
    """`rows[0][2]` -> `('rows', [0, 2])`.

    An unbalanced or non-numeric bracket (`rows[]`, `rows[a]`, `rows[0`) is
    returned inside `name`, which then fails the `_STEP` check in the caller
    and is refused by name. Parsing it leniently here would be the one place a
    malformed path silently became a different path.
    """
    indexes: List[object] = []
    while "[" in part:
        head, _, tail = part.partition("[")
        index, bracket, rest = tail.partition("]")
        index = index.strip()
        if bracket != "]" or not index.lstrip("-").isdigit():
            return part, []
        indexes.append(int(index))
        part = head + rest
    return part, indexes


def _type_name(value: object) -> str:
    return _TYPE_NAMES.get(type(value), type(value).__name__)


def _article(thing: str) -> str:
    """"an object", "a string". Two vocabularies meet here (jq's and Python's)."""
    return "an" if thing[:1].lower() in "aeiou" else "a"


def _a_thing(type_name: str) -> str:
    return f"{_article(type_name)} {type_name}"


def _missing(steps: List[object], upto: int, reached: object) -> str:
    """Say a field is absent - and check for a key a dotted path cannot reach.

    `jq` cannot address `{"a.b": 1}` with `.a.b` either, and a model told only
    "no such field" will conclude the document does not contain it. So when the
    requested name is the prefix of a key that really does contain a dot, that
    key is named.

    The prefix test is what keeps this from becoming noise. An earlier version
    hinted at *any* dotted key in the object, so asking about a field that was
    simply absent produced "no field 'nope' - but this object holds 'a.b'",
    which reads as though `a.b` might have been what was meant. It is only worth
    saying when the requested name could actually be the truncated form of a
    key that exists.
    """
    tail = steps[upto]
    if isinstance(tail, str) and isinstance(reached, dict):
        matches = [
            key for key in reached
            if isinstance(key, str) and key != tail and key.startswith(tail + ".")
        ]
        if matches:
            shown = ", ".join(repr(key) for key in matches[:5])
            extra = "" if len(matches) <= 5 else f" (and {len(matches) - 5} more)"
            return (
                f"No field {tail!r} - but there {_is(matches)} key "
                f"{'whose name starts' if tail else 'here'} with that as a prefix "
                f"and a dot in the rest: {shown}{extra}. A dotted path cannot "
                f"reach a key that contains a dot; read the file and look at the "
                f"key directly."
            )
    return f"No field {tail!r} in this document."


def _is(items: list) -> str:
    return "is one" if len(items) == 1 else f"are {len(items)}"


def _walk(document: object, steps: List[object], path: str) -> Tuple[object, Optional[str]]:
    """Follow `steps`, or return the reason the walk stopped."""
    reached = document
    for position, step in enumerate(steps):
        if isinstance(step, int):
            if not isinstance(reached, list):
                return None, (
                    f"Cannot index {_a_thing(_type_name(reached))} at [{step}]: "
                    f"{path or 'the document'} is not an array."
                )
            if not -len(reached) <= step < len(reached):
                return None, (
                    f"No element [{step}]: the array at {path or 'the document'} "
                    f"has {len(reached)} entr{'y' if len(reached) == 1 else 'ies'}."
                )
            reached = reached[step]
            continue
        if not isinstance(reached, dict):
            return None, (
                f"No field {step!r}: {path or 'the document'} is "
                f"{_a_thing(_type_name(reached))}, not an object."
            )
        if step not in reached:
            return None, _missing(steps, position, reached)
        reached = reached[step]
    return reached, None


def _read(path: Optional[str], document: Optional[str]) -> Tuple[Optional[object], str]:
    """Parse the document from a file or from the arguments. Returns `(value, problem)`."""
    raw_text = (document or "").strip()
    text_path = (path or "").strip()

    if text_path:
        try:
            target = files.resolve(text_path)
        except files.PathProblem as exc:
            return None, str(exc)
        if target.is_dir():
            return None, f"{target} is a directory, not a JSON file."
        try:
            size = target.stat().st_size
        except OSError as exc:
            return None, files.describe(exc, target, "read")
        if size > _MAX_BYTES:
            return None, (
                f"Refusing to read {target}: it is {files.human_size(size)}, over "
                f"the {files.human_size(_MAX_BYTES)} limit for parsing a JSON "
                f"document. Point at one part of it instead."
            )
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, files.describe(exc, target, "read")
    elif raw_text:
        text = raw_text
    else:
        return None, "No file path and no document text were given, so there is nothing to read."

    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        line = text.count("\n", 0, exc.pos) + 1
        return None, (
            f"That is not valid JSON: {exc.msg} at line {line}, column {exc.colno} "
            f"(character {exc.pos}). Nothing was read as a document."
        )


def _render(value: object) -> str:
    """A value as a spoken answer, without pretending to be complete."""
    if isinstance(value, str):
        if len(value) <= _MAX_VALUE_CHARS:
            return value
        return value[:_MAX_VALUE_CHARS] + f"\n(truncated, {len(value)} characters total)"
    try:
        text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)
    if len(text) <= _MAX_VALUE_CHARS:
        return text
    rows = text.splitlines()
    kept, withheld = files.cap_list(rows, _MAX_ROWS)
    body = "\n".join(kept)
    if withheld:
        body += "\n" + files.withheld_note("line", withheld, "Narrow `field` to see the rest.")
    return body


def _elements(value: object, of: str, path: str) -> Tuple[Optional[List[object]], Optional[str]]:
    """The array at `value`, reduced to the `of` key when one was named."""
    if not isinstance(value, list):
        return None, (
            f"{path or 'the document'} is {_a_thing(_type_name(value))}, not an array, so "
            f"there is nothing to aggregate over."
        )
    if len(value) > _MAX_ELEMENTS:
        return None, (
            f"Refusing to aggregate {len(value):,} elements at "
            f"{path or 'the document'}: the ceiling is {_MAX_ELEMENTS:,}. Point at "
            f"a smaller part of it."
        )
    if not of:
        return list(value), None
    out: List[object] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            return None, (
                f"Element {index} of {path or 'the document'} is "
                f"{_a_thing(_type_name(item))}, not an object, so it has no {of!r} to take."
            )
        if of not in item:
            return None, f"Element {index} of {path or 'the document'} has no {of!r}."
        out.append(item[of])
    return out, None


def _run(arguments: dict) -> str:
    operation = (arguments.get("operation") or "get").strip().lower()
    if operation not in _OPERATIONS:
        return f"Operation must be one of {', '.join(_OPERATIONS)}, not {operation!r}."
    path_arg = arguments.get("path")
    document_arg = arguments.get("document")

    document, problem = _read(path_arg, document_arg)
    if problem is not None:
        return problem

    where = (arguments.get("field") or "").strip()
    steps, problem = parse_path(where)
    if problem is not None:
        return problem
    if steps:
        document, problem = _walk(document, steps, where)
        if problem is not None:
            return problem

    if operation == "type":
        return f"{where or 'The document'} is {_a_thing(_type_name(document))}."

    if operation == "keys":
        if not isinstance(document, dict):
            return (
                f"{where or 'The document'} is {_a_thing(_type_name(document))}, so it has "
                f"no keys. Use operation 'length' to count its entries."
            )
        keys = list(document.keys())
        kept, withheld = files.cap_list(keys, _MAX_ROWS)
        body = "\n".join(str(key) for key in kept)
        if withheld:
            body += "\n" + files.withheld_note("key", withheld, "a narrower `field`")
        return f"Keys of {where or 'the document'} ({len(keys)}):\n{body}"

    if operation == "length":
        if isinstance(document, (dict, list, str)):
            return f"{where or 'The document'} holds {len(document):,} entr{'y' if len(document) == 1 else 'ies'}."
        return (
            f"{where or 'The document'} is {_a_thing(_type_name(document))}, which has no "
            f"length."
        )

    if operation == "sum":
        items, problem = _elements(document, arguments.get("of"), where)
        if problem is not None:
            return problem
        numbers = [
            item for item in items
            if isinstance(item, (int, float)) and not isinstance(item, _NOT_A_NUMBER)
        ]
        refused = len(items) - len(numbers)
        if not numbers:
            return (
                f"No numbers to sum in {where or 'the document'}: "
                f"all {len(items)} entr{'y' if len(items) == 1 else 'ies'} "
                f"{'is a non-number' if len(items) == 1 else 'are non-numbers'}."
            )
        # A partial total labelled as the total is a wrong number that looks
        # right, so the exclusion is stated before the figure, not after it -
        # and it names what was actually excluded. An earlier version hardcoded
        # "(true, false or null)", which is a confident wrong reason whenever
        # the odd entry out was a string, which in a mixed array it usually is.
        if refused:
            kinds = sorted({_type_name(item) for item in items
                            if not (isinstance(item, (int, float))
                                    and not isinstance(item, _NOT_A_NUMBER))})
            return (
                f"Not a complete total: {refused} of the {len(items)} entries in "
                f"{where or 'the document'} "
                f"{'is a non-number' if refused == 1 else 'are non-numbers'} "
                f"({', '.join(kinds)}) and cannot be added. Sum of the "
                f"{len(numbers)} numeric entries: {sum(numbers)}"
            )
        return f"Sum of all {len(numbers)} entries in {where or 'the document'}: {sum(numbers)}"

    if operation in ("unique", "sort"):
        items, problem = _elements(document, arguments.get("of"), where)
        if problem is not None:
            return problem
        where_text = where or "the document"
        if not items:
            return f"{where_text.capitalize()} is an empty array - no {operation} values."
        try:
            values = sorted(set(items)) if operation == "unique" else sorted(items)
        except TypeError:
            kinds = sorted({_type_name(item) for item in items})
            return (
                f"Cannot {'list' if operation == 'unique' else 'sort'} these entries: they are mixed types "
                f"({', '.join(kinds)}), which have no common order."
            )
        kept, withheld = files.cap_list(values, _MAX_ROWS)
        noun = "unique value" if operation == "unique" else "sorted value"
        body = "\n".join(_render(item) for item in kept)
        note = files.withheld_note(noun, withheld, "Narrow `field`, or drop `of`, to see the rest.")
        header = f"{len(values)} {noun}(s) in {where_text}:"
        return f"{header}\n{body}" + (f"\n{note}" if note else "")

    return _render(document)


SKILLS = [Skill(name="json_query", schema=SCHEMA, run=_run)]