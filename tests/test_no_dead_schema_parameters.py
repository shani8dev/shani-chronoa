"""No skill advertises a parameter its handler never reads.

A schema property is a **control the model can use**. Declaring one the handler
never looks at is the same defect as a button wired to nothing - the caller is
offered a lever, it moves, and nothing happens, which is worse than not having
the lever, because the model repeats the argument and the answer never changes.

This is the same class `files_by_name`'s unused flags and
`capabilities.GATED`'s uncited rows come from, and it is checked over the whole
registry rather than by the guy who added the skill.

**How a parameter is recognised as read.** An early version of this audit
collected `ast.Name` and `ast.Attribute` nodes and reported **220 of 257**
skills as advertising a dead parameter. That was the check failing for its own
reasons: in this package a handler reads `arguments.get("path")` or
`arguments["path"]`, so the key is a **string constant** in a slot that no Name
node covers. A second version also collected every string literal, which passes
a parameter whose name merely appears in its own description prose.

So the reader is the tight one - a string in a position that *reads* it:

    arguments["path"]           a subscript key
    arguments.get("path")       a positional argument of a call
    f(arguments, "text", 200)   a helper's argument
    f(..., key=name)            a keyword name

plus `Name`/`Attribute` identifiers for the positional-destructuring case.
**Docstrings are stripped before any of it**, because a description that names
its own field is prose about the field and not a read of it.

`TestTheCheckItself` runs the same detector over two synthetic modules - one
reading its parameter, one not - because a detector that cannot flag a dead
parameter is the same failure as a skill that never reads it.
"""

from __future__ import annotations

import ast
import inspect
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, "usr/lib/shani-chronoa")

from shani_chronoa import skills  # noqa: E402


def _without_docstrings(tree: ast.AST) -> "set[int]":
    """The ids of every docstring node in the tree."""
    docstrings: "set[int]" = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return docstrings


def read_identifiers(source: str) -> "set[str]":
    """Every identifier this source reads, with its docstrings removed.

    **A string is only counted where it is read, not where it is written.**
    That is the whole distinction between this function and a text search: a
    description mentioning `path` does not read `path`.
    """
    tree = ast.parse(source)
    docstrings = _without_docstrings(tree)
    found: "set[str]" = set()

    def add(node) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)

    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Subscript):
            # arguments["path"]
            add(node.slice)
        elif isinstance(node, ast.Call):
            # arguments.get("path"), _text(arguments, "text", 200)
            for argument in node.args:
                add(argument)
            for keyword in node.keywords:
                if keyword.arg:
                    found.add(keyword.arg)
                add(keyword.value)
    return found


class TestEveryAdvertisedParameterIsRead:
    def _modules(self):
        """`(tool name, source path)` for every skill, via its handler.

        **Indexed by the handler's own module, not by a source grep.** A grep
        for `Skill(name="notes"` misses the five skills that carry the name in a
        `_NAME` variable, and the first version of this audit silently skipped
        exactly those.
        """
        _, handlers = skills.discover_skills()
        out = {}
        for name, handler in handlers.items():
            try:
                path = inspect.getsourcefile(handler)
            except TypeError:
                continue
            if not path:
                continue
            out[name] = Path(path)
        return out

    def test_no_declared_parameter_is_unread(self):
        modules = self._modules()
        tools, _ = skills.discover_skills()
        dead = []
        unchecked = []
        for tool in tools:
            name = tool["function"]["name"]
            properties = (tool["function"].get("parameters", {}) or {}).get(
                "properties", {})
            if not properties:
                continue
            path = modules.get(name)
            if path is None or not path.exists():
                unchecked.append(name)
                continue
            read = read_identifiers(path.read_text(errors="replace"))
            for prop in properties:
                if prop not in read:
                    dead.append(f"{name}.{prop}")

        # **Not a silent skip.** A skill whose handler file cannot be found is a
        # fact about this audit, and skipping it quietly is how a control
        # becomes decoration - so it fails and names them.
        assert not unchecked, (
            f"the handler's source could not be located for {len(unchecked)} "
            f"skill(s), so this check could not read them: {sorted(unchecked)}")

        assert not dead, (
            f"{len(dead)} advertised parameter(s) are never read by their "
            f"handler - each is a control the model can use and nothing "
            f"changes: {dead}")


class TestTheCheckItself:
    """The detector must be able to fail, in both directions.

    A detector that passes everything is the same failure as a skill whose
    schema lists a lever nothing reads, which is the thing this file exists to
    catch - so the detector gets the treatment the skills do.
    """

    LIVE = textwrap.dedent("""
        '''A docstring that mentions path so the naive version would pass it.'''
        def _run(arguments):
            target = arguments.get("path")
            other = arguments["depth"]
            return f"{target} {other}"
        SCHEMA = {"function": {"name": "live",
                    "parameters": {"properties": {
                        "path": {"type": "string"},
                        "depth": {"type": "integer"}}}}}
    """)

    DEAD = textwrap.dedent("""
        '''A docstring that also mentions path, and mentions it to nothing.'''
        def _run(arguments):
            target = arguments.get("path")
            return f"{target}"
        SCHEMA = {"function": {"name": "dead",
                    "parameters": {"properties": {
                        "path": {"type": "string"},
                        "depth": {"type": "integer"}}}}}
    """)

    def test_a_parameter_that_is_read_is_not_flagged(self):
        read = read_identifiers(self.LIVE)
        assert "path" in read, "arguments.get('path') was not counted as a read"
        assert "depth" in read, "arguments['depth'] was not counted as a read"

    def test_a_parameter_no_handler_reads_is_flagged(self):
        read = read_identifiers(self.DEAD)
        assert "path" in read
        assert "depth" not in read, (
            "the detector counted a parameter that no handler reads as read")

    def test_a_docstring_meme_does_not_count_as_a_read(self):
        """`depth` appears in `DEAD`'s docstring as a word, and never as a key.

        A detector that collects every string literal passes this module, which
        is exactly the version of this audit that reported 220 of 257 skills as
        clean while being unable to tell a read from a mention.
        """
        assert "depth" not in read_identifiers(self.DEAD)
        assert "mentions" not in read_identifiers(self.DEAD)

    def test_the_docstring_alone_is_stripped(self):
        """A module whose only mention of a name is its docstring."""
        source = ('"""path is described here and never read."""\n'
                  "def _run(arguments):\n"
                  "    return 'nothing'\n")
        assert "path" not in read_identifiers(source)
