"""The workbench: a command's man page turned into a skill, and the drop-ins
that result - next to the whitelist they join.

Chronoa has had this capability since `tools/cli_matrix.py --scaffold CMD`
existed and **no panel reached it**. The generator reads a command's own
OPTIONS section, guards with `which`, refuses operands that look like flags,
gates anything that changes behind a generated `ENABLED = False`, and writes a
module that satisfies the skills contract - so the hard part was never the
scaffolding, it was that the only way in was a terminal. This panel is that
capability with three things around it:

1. **the whitelist, searchable** - the built-ins read from the live registry
   (`discover_skills()`, the same call the model is handed) and the user's
   drop-ins read from the loader's own directory, in a **separate group**. The
   separation is the point: a drop-in is code the user wrote and a built-in is
   code this package shipped, and one list interleaving them would let a
   user-supplied name look like a shipped one.
2. **a row per drop-in saying what `verify()` returns** - `verification.verify`
   is the same post-condition check `tools.py` runs after every skill call, so
   "verified" on this page means what it means in the tool loop, and "no
   post-condition declared" is stated rather than drawn as a pass.
3. **the scaffold row** - a command name in, the generated module shown *before*
   anything is written, written only into the user's own skills directory.

**Three refusals, in these words, because each names a different mistake.** A
command the matrix does not know is `MATRIX_UNKNOWN`: the generator's
classifiers ran and had no row for it, so there is no man page, no package and
no safety class - and a scaffold built from a guess is a wrapper around
whatever that name happens to be. A command that would change something is
still generated, but as `GENERATED_AND_DISABLED`, which is the tool's own
`ENABLED = False` read back out of the source it just wrote rather than a second
guess at the same question. A generated module that already exists is not
overwritten: that file is the user's own edit, and a scaffolder that silently
clobbers it is one nobody trusts twice.

**Where a write can land: one directory, and the code proves it.**
`_write_skill()` re-derives the destination's parent from `user_skills_dir()`
and refuses anything that is not a plain `.py` file directly inside it, so a
name that escaped (`..`, a slash, an absolute path) is refused rather than
resolved into somebody's checkout. The destination never comes from the
generator's own `--scaffold-dir` default, never from a command line, and never
from the repository.

**The generator is not packaged, so importing it is lazy and optional.**
`tools/cli_matrix.py` is a development tool: it is not installed, it is not in
the payload, and importing it at module scope would make this panel
unimportable on every real install. `matrix()` therefore looks for it at first
use, loads it by file path, and returns the reason it could not be found when
it is absent - which the page states as a state, not as a crash and not as an
empty list of commands. Nothing about *building* this page touches the
generator, so a panel build never depends on it. `SHANI_CHRONOA_CLI_MATRIX`
names a copy kept elsewhere.

Two private names of the generator are used on purpose, and both are in the
generator's own interest: `_ident()` gives the module filename, so a file
written here and a file written by `--scaffold` are the same name rather than
two skills for one command, and `_with_subcommands()` is what turns one tool
with both reading and changing subcommands into the `mixed` class the rest of
the row is built on. Re-deriving either rule here is how two implementations of
one rule drift.

**Removing a drop-in asks.** `Adw.AlertDialog`, `cancel` as both the default
and the close response, the destructive response marked
`Adw.ResponseAppearance.DESTRUCTIVE`, and the file removed only from the
`remove` response - so the button itself cannot delete anything, and a `cancel`
that reaches the handler deletes nothing either. Without libadwaita there is no
dialog to ask with, and the same button becomes the second step instead: two
activations, the first of which writes nothing.
`remove_drop_in()` unlinks exactly the one path it is given and never lists the
directory.

**Markup is escaped, not disabled.** `Adw.PreferencesRow.use-markup` defaults
to True (measured, libadwaita 1.5), so a title and a subtitle are Pango markup:
a description holding a bare `&` is a parse error that renders the row *empty*
and says nothing but a warning on stderr. `_plain()` escapes when libadwaita
built the row and leaves the text alone otherwise, because the plain-GTK answer
is a `Gtk.Label` that takes no markup and would print the entities themselves.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
import sys
import types
from pathlib import Path
from typing import Any, List, NamedTuple, Optional, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from shani_chronoa import markdown_lite, skills, verification  # noqa: E402
from shani_chronoa.gui.surfaces import common  # noqa: E402

logger = logging.getLogger(__name__)

TITLE = "Workbench"
ICON = "object-select-symbolic"
SECTION = "What Chronoa did"

SUBTITLE = "Turn a command into a skill of your own, and see what that joined."

#: Markers a test can find in the built tree, so no assertion has to read a
#: count back out of the object being counted.
BUILTIN_ROW = "builtin-skill-row"
DROPIN_ROW = "user-skill-row"

#: The refusals, as words. Declared here because the tests that pin them should
#: pin *these* strings, and because a refusal nobody can quote is a refusal
#: nobody can act on.
MATRIX_UNKNOWN = "the matrix does not know it"
GENERATOR_MISSING = "the generator is not installed"
NOT_A_COMMAND = "that is not a command name"
CANNOT_GENERATE = "the generator would not scaffold that"
ALREADY_THERE = "there is already a drop-in at that path"

#: The wording for a scaffold of a command that can change something. Read back
#: out of the generated source (`ENABLED = False`), so this label and the module
#: the user then reviews cannot disagree about whether it is armed.
GENERATED_AND_DISABLED = "generated and disabled"

__all__ = [
    "TITLE", "ICON", "SECTION", "build",
    "GENERATED_AND_DISABLED", "MATRIX_UNKNOWN", "GENERATOR_MISSING",
    "DropIn", "Generated", "drop_ins", "generate", "remove_drop_in",
    "user_skills_dir",
]


# --- the generator, lazily --------------------------------------------------

#: Where a checkout keeps the generator, searched upwards from this file: an
#: installed package has no `tools/` above it, which is the point.
_MATRIX_RELATIVE = Path("tools") / "cli_matrix.py"

#: For a generator kept outside a checkout, since it is not packaged.
MATRIX_ENV = "SHANI_CHRONOA_CLI_MATRIX"

_matrix_cache: "dict[str, Any]" = {}


def _matrix_path() -> Optional[Path]:
    """The generator's file, or None when there is none to find."""
    override = os.environ.get(MATRIX_ENV, "")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    here = Path(__file__).resolve()
    for parent in here.parents[:8]:
        candidate = parent / _MATRIX_RELATIVE
        if candidate.is_file():
            return candidate
    return None


def matrix() -> Tuple[Optional[Any], Optional[Path], str]:
    """`(module, path, reason)` - the generator loaded now, or why not.

    Lazy and cached by path: a panel *build* never calls this, so building the
    panel on a machine with no generator checkout is not a failure - the
    scaffold row reports the absence instead of failing to appear.

    Loaded by file path under a private module name rather than imported,
    because it is a script outside the package and not on `sys.path`.
    """
    path = _matrix_path()
    if path is None:
        return None, None, GENERATOR_MISSING
    key = str(path)
    if key in _matrix_cache:
        cached = _matrix_cache[key]
        return cached["module"], path, cached["reason"]
    spec = importlib.util.spec_from_file_location("shani_chronoa_cli_matrix", path)
    module = None
    reason = ""
    if spec is None or spec.loader is None:
        reason = f"{GENERATOR_MISSING}: {path} is not an importable file"
    else:
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - a broken tool is a state, not a crash
            module, reason = None, f"{GENERATOR_MISSING}: {exc}"
            logger.warning("could not load the generator at %s", path, exc_info=True)
    _matrix_cache[key] = {"module": module, "reason": reason}
    return module, path, reason


def matrix_reason() -> str:
    """Why there is no generator, or "" when one loaded."""
    return matrix()[2]


# --- the user's own skills directory ---------------------------------------

def user_skills_dir() -> Path:
    """Where user drop-in skills live: the loader's own path, read per call.

    `skills._USER_SKILLS_DIR` is read at call time and never recomputed from
    `files.config_home()`, so this panel lists exactly the directory
    `discover_skills()` loads from and the two cannot disagree about which
    files are drop-ins. The fallback is for a build where that name is gone:
    `config_home()/shani-chronoa/skills` is the expression the loader builds it
    from, so the fallback is the same directory.
    """
    declared = getattr(skills, "_USER_SKILLS_DIR", None)
    if isinstance(declared, Path):
        return declared
    from shani_chronoa import files

    return files.config_home() / "shani-chronoa" / "skills"


def _target_for(module: Any, command: str) -> Path:
    """Where a generated module for `command` may be written.

    The name comes from the generator's own `_ident()` so a file written here
    and a file written by `--scaffold` are the same name; two files for one
    command would load as two skills and neither would be the one meant. The
    name is also checked to be a plain identifier before it is joined to the
    directory, so a name that escaped cannot escape.
    """
    directory = user_skills_dir()
    name = module._ident(command)  # noqa: SLF001 - see the module docstring
    if not re.fullmatch(r"[a-z0-9_]+", name or ""):
        raise ValueError(f"the generator produced an unusable module name from {command!r}")
    return directory / f"{name}.py"


def _write_skill(path: Path, source: str) -> Optional[str]:
    """Write `source` to `path`, or return the reason it was not written.

    Refuses to overwrite: that file would be the user's own edit, and this is
    the only write in the panel, so this is the only place that has to say so.
    `O_EXCL` as well as the existence check, so a file that appeared between
    the two is still not clobbered.
    """
    directory = user_skills_dir()
    if path.parent != directory or path.suffix != ".py":
        return f"refusing to write outside {directory}"
    if path.exists():
        return f"{ALREADY_THERE} ({path}); remove it first if you meant to replace it"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return f"{ALREADY_THERE} ({path}); remove it first if you meant to replace it"
    except OSError as exc:
        return f"could not write {path}: {exc}"
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(source)
    return None


# --- the whitelist ----------------------------------------------------------

def builtin_schemas(exclude: Optional["set[str]"] = None) -> Tuple[List[Any], Optional[str]]:
    """The *shipped* whitelist - the registry, minus what a drop-in owns.

    Same shape as `surfaces/skills.py`, and for the same reason: a registry that
    cannot be read, rendered as an empty list, states "Chronoa has no skills"
    about a registry nobody managed to look at.

    `exclude` is the set of names the user's own modules provide.
    `discover_skills()` registers the built-ins first and then lets a drop-in of
    the same name take its place, so the registry a drop-in has joined is not a
    list of what shipped - and listing the user's own code under a heading that
    says *built-in* is the one thing this panel exists to prevent. Taking those
    names out is what makes the two groups mean what they say.
    """
    try:
        found, _handlers = skills.discover_skills()
    except Exception as exc:  # noqa: BLE001 - the window must still open
        logger.warning("the skill registry could not be read", exc_info=True)
        return [], f"{type(exc).__name__}: {exc}"
    try:
        schemas = list(found or ())
    except TypeError:
        return [], "the registry returned something that is not a list of schemas"
    if not exclude:
        return schemas, None
    kept = []
    for schema in schemas:
        fields = _schema_fields(schema)
        if fields is not None and fields[0] in exclude:
            continue
        kept.append(schema)
    return kept, None


def _declared_names(module: types.ModuleType) -> Tuple[str, ...]:
    """The skill names a module registers - `volume.py` registers `set_volume`,
    so the filename answers a different question."""
    entries = getattr(module, "SKILLS", None)
    if not isinstance(entries, (list, tuple)):
        return ()
    names = []
    for entry in entries:
        fields = _schema_fields(getattr(entry, "schema", None))
        if fields is not None:
            names.append(fields[0])
    return tuple(names)


_packaged_names: Optional["set[str]"] = None


def _packaged_names_cache() -> "set[str]":
    """Every name the shipped skills package registers.

    Read off the package's own modules, which `discover_skills()` has already
    imported by the time anything asks: `importlib.import_module` returns the
    cached module, so this runs no new code. It is what tells a drop-in row
    that it *replaces* a shipped skill rather than merely adding one - a
    question the directory listing cannot answer, because a module's names are
    not its filename.
    """
    global _packaged_names
    if _packaged_names is not None:
        return _packaged_names
    names: "set[str]" = set()
    directory = Path(skills.__file__).resolve().parent
    for path in sorted(directory.glob("*.py")):
        if path.stem == "__init__":
            continue
        try:
            module = importlib.import_module(f"{skills.__name__}.{path.stem}")
        except Exception:  # noqa: BLE001 - a module that will not import registers nothing
            continue
        names.update(_declared_names(module))
    _packaged_names = names
    return names


def _schema_fields(schema: Any) -> Optional[Tuple[str, str]]:
    """`(name, description)` from one registry schema, or None to skip it."""
    try:
        if not skills.is_valid_schema(schema):
            return None
        function = schema.get("function") or {}
        name = function.get("name", "")
        description = function.get("description", "")
    except Exception:  # noqa: BLE001 - an entry nobody can read is skipped
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(description, str):
        description = "" if description is None else str(description)
    return name.strip(), description.strip()


def _load_module(path: Path) -> Optional[types.ModuleType]:
    """Import one drop-in by path, the way the loader does, or None.

    Registered in `sys.modules` under the loader's own name for this file
    because that is the name `verification.verify()` looks a post-condition up
    under - it takes an importable module name, not a path, so without this the
    one honest answer available ("unverified") would be a claim about a module
    name that does not exist rather than about the drop-in.
    """
    name = f"shani_chronoa_user_skill_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - a broken drop-in costs a row, not the window
        logger.warning("drop-in %s did not import: %s", path.name, exc)
        return None
    sys.modules[name] = module
    return module


def _describe(module: types.ModuleType) -> str:
    """The first skill's own description, or "" - the words the model is given."""
    entries = getattr(module, "SKILLS", None)
    if not isinstance(entries, (list, tuple)):
        return ""
    for entry in entries:
        fields = _schema_fields(getattr(entry, "schema", None))
        if fields is not None:
            return fields[1]
    return ""


class DropIn(NamedTuple):
    """One user drop-in, and everything its row says about it."""

    name: str
    path: Path
    description: str
    verdict: str
    evidence: str
    error: str
    skills: Tuple[str, ...] = ()
    replaces: str = ""

    @property
    def verdict_line(self) -> str:
        """What `verify()` returned, in its own words."""
        if self.error:
            return f"will not load: {self.error}"
        detail = self.evidence.strip()
        return f"verifies: {self.verdict}" + (f" - {detail}" if detail else "")

    @property
    def line(self) -> str:
        """The row's subtitle: its own description, the verdict, what it replaces.

        The description is the words the model is handed for this skill, so it
        is worth showing verbatim - and it is user-written text going onto a row
        that renders markup, which is why it goes through `_plain()`.
        """
        parts = [self.description.strip() or "(no description)", self.verdict_line]
        if self.replaces:
            parts.append(f"replaces the built-in skill: {self.replaces}")
        return "\n".join(part for part in parts if part)


def _drop_in(path: Path) -> DropIn:
    """One drop-in, loaded and asked to verify itself."""
    module = _load_module(path)
    if module is None:
        return DropIn(path.stem, path, "", "", "", "it did not import")
    names = _declared_names(module)
    # `replaces` is the name this drop-in took over from a shipped skill, which
    # is what the loader's own "overrides an existing skill" warning is about -
    # so the row says so rather than leaving two rows with the same name and no
    # way to tell which one is running.
    replaces = ", ".join(sorted(set(names) & _packaged_names_cache()))
    try:
        result = verification.verify(f"shani_chronoa_user_skill_{path.stem}", {}, None)
    except Exception as exc:  # noqa: BLE001 - verify never raises, and neither may this
        return DropIn(path.stem, path, _describe(module), "unverified", "",
                      f"verify() could not be asked: {exc}", names, replaces)
    return DropIn(path.stem, path, _describe(module), result.verdict.value,
                  result.evidence, "", names, replaces)


def drop_ins() -> List[DropIn]:
    """Every `*.py` in the user's skills directory, loaded and verified.

    The same glob `discover_skills()` uses, so a file listed here is a file that
    loader will try to load - and it is loaded here too, which is the same
    execution `discover_skills()` already performs on these files at startup,
    not a new trust boundary.
    """
    directory = user_skills_dir()
    try:
        paths = sorted(directory.glob("*.py")) if directory.is_dir() else []
    except OSError as exc:  # noqa: BLE001 - an unreadable directory is a state
        logger.warning("could not list %s: %s", directory, exc)
        return []
    return [_drop_in(path) for path in paths]


def remove_drop_in(path: Path) -> Optional[str]:
    """Delete one drop-in file, or return the reason it was not deleted.

    Takes a path and unlinks exactly that path: it never lists the directory,
    never globs, and takes no second argument, so "remove this one" cannot turn
    into "remove the ones beside it".
    """
    directory = user_skills_dir()
    try:
        if path.parent != directory:
            return f"refusing to remove {path}: it is not in {directory}"
        path.unlink()
    except FileNotFoundError:
        return f"{path.name} was already gone"
    except OSError as exc:
        return f"could not remove {path}: {exc}"
    return None


# --- scaffolding ------------------------------------------------------------

class Generated(NamedTuple):
    """The outcome of asking for a command's skill: the module, or the reason.

    `disabled` is read out of `source` rather than decided again here: the
    generator decides whether a scaffold is armed (a *mixed* tool becomes a
    read-only subset of its reading subcommands, and that is not a disabled
    skill), and a second implementation of that rule in this file is how the two
    would drift.
    """

    command: str
    source: str
    path: Optional[Path]
    disabled: bool
    refusal: str
    detail: str
    note: str

    @property
    def refused(self) -> bool:
        return bool(self.refusal)

    @property
    def status(self) -> str:
        """The one line the scaffold row says about this attempt."""
        if self.refused:
            return f"{self.command or '(nothing typed)'} - {self.refusal}."
        words = GENERATED_AND_DISABLED if self.disabled else "generated and read-only"
        return f"{self.command} - {words}; it would be written to {self.path}."


def _owning_package(module: Any, path: str) -> str:
    """The package that owns `path`, or "" - `pacman -Qoq`, nothing else.

    The generator reads `row["package"]` into both the generated docstring and
    the `which` guard's "it comes from the X package" message, and writes "?"
    there when it cannot tell. This asks the one question that answers it, and
    returns "" rather than a guess on a system with no pacman.
    """
    if not shutil.which("pacman"):
        return ""
    out = module.run(["pacman", "-Qoq", path], timeout=10).strip()
    return out.splitlines()[0].strip() if out else ""


def _matrix_row(module: Any, command: str) -> Tuple[Optional[dict], str, str]:
    """The matrix's own row for `command`: `(row, refusal, detail)`.

    Built exactly as `cli_matrix.main()` builds one - the same man page, the
    same `intent`, the same `safety` refined by its subcommands - because
    `scaffold()` reads those keys, and a row built any other way would generate
    a module the CLI would not have generated.

    Two things make a command unknown, and both are refusals rather than
    guesses: a name that is not on `PATH` (there is nothing to wrap) and a name
    with no man page under `/usr/share/man` (there is no summary, so `safety`
    would fall back to "can-change" for lack of evidence and every command
    would be classed as one that changes things).
    """
    name = (command or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name):
        return None, NOT_A_COMMAND, f"{command!r} is not a command name"
    found = shutil.which(name)
    if found is None:
        return None, MATRIX_UNKNOWN, f"{name} is not on PATH, so there is no matrix row"
    # `man_info` walks every page under /usr/share/man and shells out to
    # `whatis`, so it is asked once here and read twice - not three times.
    info = module.man_info([name]).get(name) or {}
    summary = info.get("summary") or ""
    if not summary:
        return None, MATRIX_UNKNOWN, f"{name} has no man page, so the matrix has no summary for it"
    verb = module.intent(summary)
    section = info.get("section") or ""
    subcommands = info.get("subcommands") or []
    package = _owning_package(module, found)
    row = {
        "command": name, "path": found,
        "package": package,
        "package_description": "", "explicit": False, "shani": False,
        "category": module.category(package, ""),
        "kind": module.kind(name, package, section, summary),
        "intent": verb,
        "safety": module._with_subcommands(  # noqa: SLF001 - see the module docstring
            module.safety(found, summary, verb), subcommands),
        "man_section": section, "summary": summary,
        "has_man": True, "json": bool(info.get("json")),
        "follows": bool(info.get("follows")), "dry_run": bool(info.get("dry_run")),
        "app": "", "synopsis": info.get("synopsis") or "",
        "options": info.get("options") or 0,
        "option_list": info.get("option_list") or [],
        "subcommands": subcommands, "used_by": {},
    }
    row["fits"] = module.fits(row)
    return row, "", ""


def generate(command: str) -> Generated:
    """The skill for `command`, as source, without writing anything.

    Refuses in `MATRIX_UNKNOWN` words for a command the matrix does not know,
    and generates a command that can change something **disabled** -
    `allow_actuator=True`, the generator's own flag for exactly this - rather
    than refusing it: the generated module refuses to run until a person reads
    it, so showing it is how a person gets to read it.
    """
    module, _path, reason = matrix()
    if module is None:
        return Generated(str(command or ""), "", None, False, GENERATOR_MISSING, reason, "")
    row, refusal, detail = _matrix_row(module, str(command or ""))
    if refusal:
        return Generated(str(command or ""), "", None, False, refusal, detail, "")
    try:
        source = module.scaffold(row, allow_actuator=True)
    except ValueError as exc:
        # The generator's own refusal, in its own words - passed on rather than
        # rewritten, because a second wording for one refusal is one more thing
        # to keep in step.
        return Generated(row["command"], "", None, False, CANNOT_GENERATE, str(exc), "")
    try:
        target = _target_for(module, row["command"])
    except ValueError as exc:
        return Generated(row["command"], "", None, False, CANNOT_GENERATE, str(exc), "")
    disabled = "ENABLED = False" in source
    note = (f"intent={row['intent']}, safety={row['safety']}, "
            f"surfaces={', '.join(row['fits']) or 'none'}, "
            f"flags={len(row['option_list'])}, subcommands={len(row['subcommands'])}")
    if disabled:
        note += (" - a command that can change things, so this skill refuses to run "
                 "until someone reads it and sets ENABLED")
    return Generated(row["command"], source, target, disabled, "", "", note)


# --- the page ---------------------------------------------------------------

def _plain(text: str) -> str:
    """`text` as an Adw row's title or subtitle, escaped if that row is one.

    See the module docstring: a bare `&` in a skill's own description is a Pango
    parse error that renders the row empty, silently, and the plain-GTK row
    takes no markup at all and would print the entities themselves.
    """
    return markdown_lite.escape(text) if common.adw_ready() else text


def _matches(haystack: str, query: str) -> bool:
    """True when every whitespace-separated token of `query` is in `haystack`.

    Read as a module global at call time so a test can break it on purpose; a
    filter that cannot fail is not a filter.
    """
    tokens = [token for token in (query or "").lower().split() if token]
    if not tokens:
        return True
    low = (haystack or "").lower()
    return all(token in low for token in tokens)


def _add(group: Gtk.Widget, child: Gtk.Widget) -> None:
    """Put a row in a group, whichever kind `common.group()` built."""
    adder = getattr(group, "add", None)
    if callable(adder):
        adder(child)
    else:
        group.append(child)


def _clear(group: Gtk.Widget, rows: List[Gtk.Widget]) -> None:
    """Remove rows from a group by reference, not by walking it.

    `Adw.PreferencesGroup` keeps its rows inside its own boxes, so a walk finds
    boxes that are not its children and cannot be removed from it.
    """
    for row in rows:
        group.remove(row)
    rows.clear()


def _dim(text: str) -> Gtk.Label:
    """A plain-text label. `set_text` takes no markup at all, which is the one
    call that cannot be half-done for a string a skill module wrote."""
    label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
    label.set_text(text)
    label.add_css_class("dim-label")
    return label


def _margined(widget: Gtk.Widget) -> Gtk.Widget:
    """The margins every panel in this package puts around its content."""
    widget.set_margin_top(12)
    widget.set_margin_bottom(12)
    widget.set_margin_start(12)
    widget.set_margin_end(12)
    return widget


class _Workbench:
    """The page's logic, and the page it fills.

    `build()` hands back the `Adw.NavigationPage` itself, because that is what
    a window puts in its `Adw.NavigationView`; this object keeps the state, and
    `build()` publishes its read-only API onto the page.
    """

    def __init__(self, app: Any, page: Gtk.Widget, set_content: Any) -> None:
        # `app` is kept and never asked for anything: this panel needs no
        # settings, no tracker and no model - it reads two directories and the
        # generator. That is a claim the tests hold to by passing `None`.
        self._app = app
        self._page = page
        self._builtin_rows: List[Gtk.Widget] = []
        self._dropin_rows: List[Gtk.Widget] = []
        self._placeholder: Optional[Gtk.Widget] = None
        self._pending_remove: Optional[Path] = None
        self._dialog: Optional[Any] = None
        self._generated: Optional[Generated] = None
        self.registry_error: Optional[str] = None

        body = common.page_body()
        _margined(body)

        self.search = common.search_entry("Filter skills by name or description")
        # `changed`, not the debounced `search-changed` `common.search_entry`
        # wires: the latter fires 150 ms after the last keystroke and
        # `set_text()` emits neither, so a filter bound to it would not narrow
        # as the user types, and a test that set the text would be counting a
        # render the surface itself never shows.
        self.search.connect("changed", lambda entry: self.apply_filter(entry.get_text()))
        body.append(self.search)

        self.summary = _dim("")
        body.append(self.summary)

        self._builtins_group = common.group(
            "Built-in skills",
            "What this package ships: the whitelist the model is handed, minus "
            "anything your own modules below take over.")
        body.append(self._builtins_group)

        self._dropins_group = common.group(
            "Your drop-in skills",
            "Modules you, or the generator below, put in this directory. A "
            "drop-in is your own code - not something this package shipped.")
        body.append(self._dropins_group)

        self._scaffold_group = common.group(
            "Build a skill from a command",
            "Flags come from the command's own man page. Anything that changes "
            "something is generated disabled.")
        body.append(self._scaffold_group)
        self._build_scaffold_rows()

        self.note = _dim("")
        body.append(self.note)

        set_content(common.scrolled(body))
        self._body = body
        self._load()

    # -- building ---------------------------------------------------------
    def _build_scaffold_rows(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        self.command_entry = Gtk.Entry(hexpand=True)
        self.command_entry.set_placeholder_text("a command, e.g. uptime")
        self.command_entry.update_property([Gtk.AccessibleProperty.LABEL],
                                           ["A command to build a skill from"])
        self.command_entry.connect("activate", lambda _e: self.on_generate())
        self.generate_button = Gtk.Button(label="Generate")
        self.generate_button.set_tooltip_text(
            "Read the command's man page and show the skill it would generate. "
            "Nothing is written until you save it.")
        self.generate_button.update_property([Gtk.AccessibleProperty.LABEL],
                                             ["Generate a skill from this command"])
        self.generate_button.connect("clicked", lambda _b: self.on_generate())
        box.append(self.command_entry)
        box.append(self.generate_button)
        _add(self._scaffold_group, box)

        self.preview_status = _dim("")
        _add(self._scaffold_group, self.preview_status)

        self.preview = common.monospace("")
        self._preview_window = common.scrolled(self.preview)
        self._preview_window.set_max_content_height(360)
        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.preview_box.set_visible(False)
        self.preview_box.append(self._preview_window)

        self.save_button = Gtk.Button(label="Save to my skills")
        self.save_button.add_css_class("destructive-action")
        self.save_button.set_tooltip_text(
            f"Writes one file into {user_skills_dir()} and nowhere else.")
        self.save_button.update_property(
            [Gtk.AccessibleProperty.LABEL],
            ["Save this generated skill to my skills directory"])
        self.save_button.connect("clicked", lambda _b: self.on_save())
        self.save_button.set_sensitive(False)
        self.preview_box.append(self.save_button)
        _add(self._scaffold_group, self.preview_box)

        reason = matrix_reason()
        if reason:
            # Said in place, rather than as a failure: the panel cannot build a
            # skill without the generator, and an installed system does not
            # ship it, so this is the expected state there.
            self.preview_status.set_text(
                f"{reason}. It lives in a checkout at tools/cli_matrix.py and is "
                f"not installed; set {MATRIX_ENV} to point at a copy.")
            self.generate_button.set_sensitive(False)
            self.command_entry.set_sensitive(False)
        else:
            self.preview_status.set_text(
                "Generated from the command's man page and shown here before it "
                "is written. Writing goes to your own skills directory only.")

    def _load(self) -> None:
        # Drop-ins are read first, because the shipped list is the registry
        # minus what a drop-in owns: `discover_skills()` registers the built-ins
        # and then lets a user module of the same name take its place, so
        # reading them the other way round would list the user's own code under
        # a heading that says *built-in*.
        entries = drop_ins()
        owned = {name for entry in entries for name in entry.skills}
        schemas, self.registry_error = builtin_schemas(exclude=owned)
        for schema in schemas:
            fields = _schema_fields(schema)
            if fields is not None:
                self._add_builtin(fields[0], fields[1])

        if not self._builtin_rows:
            # "No skills" and "we could not look" are different answers, and a
            # group with nothing in it reads as the first of them.
            self._body.remove(self._builtins_group)
            title = ("Could not read the skill registry" if self.registry_error
                     else "No skills loaded")
            reason = (f"{self.registry_error}. This is not an empty whitelist - it "
                      "is one that could not be read.") if self.registry_error else (
                      "The registry returned nothing, which is not the same as having "
                      "no capabilities: the modules may all have failed to import.")
            self._body.append(common.empty_state(ICON, title, _plain(reason)))

        self._rebuild_dropins(entries)
        self.refresh_summary()
        self.apply_filter(self.search.get_text())

    def _add_builtin(self, name: str, description: str) -> Gtk.Widget:
        row = common.row(_plain(name), _plain(description or "(no description)"))
        row.add_css_class(BUILTIN_ROW)
        row.set_tooltip_text(f"{name}\n{description or '(no description)'}")
        row.update_property([Gtk.AccessibleProperty.LABEL], [f"Built-in skill {name}"])
        _add(self._builtins_group, row)
        self._builtin_rows.append(row)
        return row

    def _add_drop_in(self, drop_in: DropIn) -> Gtk.Widget:
        button = Gtk.Button(label="Remove")
        button.set_tooltip_text(
            f"Asks first, then deletes {drop_in.path.name} from your skills directory.")
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [f"Remove the drop-in skill {drop_in.name}"])
        button.connect("clicked", lambda _b, d=drop_in: self.ask_remove(d))
        row = common.row(_plain(drop_in.name), _plain(drop_in.line), suffix=button)
        row.add_css_class(DROPIN_ROW)
        row.set_tooltip_text(f"{drop_in.path}\n{drop_in.description or '(no description)'}")
        row.update_property([Gtk.AccessibleProperty.LABEL],
                             [f"Drop-in skill {drop_in.name}, {drop_in.line}"])
        _add(self._dropins_group, row)
        self._dropin_rows.append(row)
        return row

    # -- the whitelist ----------------------------------------------------
    def apply_filter(self, query: str) -> int:
        """Show the rows matching `query`; returns how many are visible.

        Driven by the search entry and called directly by tests, so the count
        asserted is the count the widget itself reports.
        """
        visible = 0
        for row in self._builtin_rows + self._dropin_rows:
            show = _matches(self._row_text(row), query)
            row.set_visible(show)
            visible += 1 if show else 0
        self.refresh_summary()
        return visible

    @staticmethod
    def _row_text(row: Gtk.Widget) -> str:
        """What a row says, for the filter: its title and its subtitle."""
        parts = []
        for getter in ("get_title", "get_subtitle"):
            if hasattr(row, getter):
                try:
                    parts.append(str(getattr(row, getter)() or ""))
                except Exception:  # noqa: BLE001 - a row nobody can read matches nothing
                    continue
        return " ".join(parts)

    def refresh_summary(self) -> None:
        """Both counts, each shown against its own total.

        One number for two lists would be a count of nothing: "showing 1 of 133"
        reads as one skill existing, and the two lists are the whole point of
        this panel being split in two.
        """
        shown_builtin = sum(1 for row in self._builtin_rows if row.get_visible())
        shown_dropins = sum(1 for row in self._dropin_rows if row.get_visible())
        self.summary.set_text(
            f"Showing {shown_builtin} of {len(self._builtin_rows)} built-in skills "
            f"and {shown_dropins} of {len(self._dropin_rows)} of yours, in "
            f"{user_skills_dir()}")

    # -- scaffolding ------------------------------------------------------
    def on_generate(self) -> Optional[Generated]:
        """Generate from whatever is in the entry, and show the result."""
        self._generated = generate(self.command_entry.get_text())
        self._show_generated()
        return self._generated

    def _show_generated(self) -> None:
        generated = self._generated
        if generated is None:
            return
        self.preview_status.set_text(generated.status)
        if generated.refused:
            self.preview_box.set_visible(False)
            self.save_button.set_sensitive(False)
            return
        # Rebuilt through `common.monospace()` rather than re-set on the old
        # label: that helper is where the escaping of a whole generated module
        # lives, and reaching past it would be a second, unescaped path.
        window = common.scrolled(common.monospace(generated.source))
        window.set_max_content_height(360)
        self._preview_window.set_child(window)
        self.preview_box.set_visible(True)
        self.save_button.set_sensitive(True)

    def on_save(self) -> Optional[str]:
        """Write the shown skill into the user's own skills directory.

        Returns the reason it was not written, or None when it was. The only
        write in this panel, and it re-checks the destination rather than
        trusting the preview.
        """
        generated = self._generated
        if generated is None or generated.refused or generated.path is None:
            return "there is nothing generated to save"
        problem = _write_skill(generated.path, generated.source)
        if problem:
            self.preview_status.set_text(problem)
            self.save_button.set_sensitive(False)
            return problem
        self._generated = None
        self.preview_box.set_visible(False)
        self.save_button.set_sensitive(False)
        self.preview_status.set_text(
            f"Written to {generated.path}. It loads as a user skill the next time "
            "Chronoa starts, and is listed under your drop-ins above.")
        self.reload_drop_ins()
        return None

    # -- removing a drop-in -----------------------------------------------
    def ask_remove(self, drop_in: DropIn) -> None:
        """The Remove button's whole job: ask. Nothing is deleted here.

        The only removal in this panel is in `_on_remove_response`, and only for
        the `remove` response. Without libadwaita there is no dialog to ask
        with, so the same button becomes the second step - two activations, the
        first of which writes nothing - and the question goes in the note line.
        """
        path = drop_in.path
        if not common.adw_ready():
            if self._pending_remove != path:
                self._pending_remove = path
                self.note.set_text(
                    f"Remove {drop_in.name}? Press Remove again to confirm. Nothing "
                    "has been deleted yet.")
                return
            self._pending_remove = None
            self._do_remove(path)
            return

        dialog = Adw.AlertDialog(
            heading=f"Remove {drop_in.name}?",
            body=(f"{path} will be deleted from your own skills directory.\n\n"
                  "The built-in skills are not affected. A generated module can "
                  "always be built again from this panel; anything you wrote by "
                  "hand cannot."),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove it")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_response, path)
        self._dialog = dialog
        dialog.present(self._page.get_root() or self._page)

    def _on_remove_response(self, _dialog: Any, response: str, path: Path) -> None:
        if response != "remove":
            return
        self._do_remove(path)

    def _do_remove(self, path: Path) -> None:
        problem = remove_drop_in(path)
        if problem:
            self.note.set_text(problem)
            return
        self.note.set_text(f"Removed {path.name} from {user_skills_dir()}.")
        self.reload_drop_ins()

    def reload_drop_ins(self) -> int:
        """Re-read the drop-in directory and rebuild only that group."""
        return self._rebuild_dropins(drop_ins())

    def _rebuild_dropins(self, entries: List[DropIn]) -> int:
        """Put `entries` in the drop-in group, replacing whatever was there."""
        _clear(self._dropins_group, self._dropin_rows)
        if self._placeholder is not None:
            self._dropins_group.remove(self._placeholder)
            self._placeholder = None
        for drop_in in entries:
            self._add_drop_in(drop_in)
        if not self._dropin_rows:
            self._placeholder = _dim(
                f"No drop-in skills in {user_skills_dir()}. Chronoa loads "
                "nothing from that directory yet.")
            _add(self._dropins_group, self._placeholder)
        self.refresh_summary()
        return len(self._dropin_rows)

    # -- read-only, for the caller and for tests --------------------------
    def visible_row_count(self) -> int:
        return sum(1 for row in self._builtin_rows + self._dropin_rows
                   if row.get_visible())

    def builtin_rows(self) -> List[Gtk.Widget]:
        return list(self._builtin_rows)

    def drop_in_rows(self) -> List[Gtk.Widget]:
        return list(self._dropin_rows)

    def drop_in_entries(self) -> List[DropIn]:
        return drop_ins()


def build(app: Any) -> Gtk.Widget:
    """Build the workbench page. `app` is anything at all, including nothing.

    Returns the `Adw.NavigationPage` a window puts in its
    `Adw.NavigationView`, with this surface's read-only API attached to it, so a
    caller (or a test) can ask what the page is showing without the page having
    been wrapped in something else first.
    """
    page, set_content = common.surface(TITLE, SUBTITLE)
    workbench = _Workbench(app, page, set_content)
    page.workbench = workbench
    page.search = workbench.search
    page.summary = workbench.summary
    page.note = workbench.note
    page.preview_status = workbench.preview_status
    page.command_entry = workbench.command_entry
    page.generate_button = workbench.generate_button
    page.save_button = workbench.save_button
    page.builtin_rows = workbench.builtin_rows
    page.drop_in_rows = workbench.drop_in_rows
    page.visible_row_count = workbench.visible_row_count
    page.drop_in_entries = workbench.drop_in_entries
    page.apply_filter = workbench.apply_filter
    page.generate = workbench.on_generate
    page.save_generated = workbench.on_save
    page.ask_remove = workbench.ask_remove
    page.reload_drop_ins = workbench.reload_drop_ins
    page.registry_error = workbench.registry_error
    return page