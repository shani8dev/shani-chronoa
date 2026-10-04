"""What program an argv really runs: env wrappers, shell scripts, unresolvable scripts and blocked binaries."""

from __future__ import annotations


import os
import re


#: A leading `VAR=value` word, as in `FOO=bar python3 ...`.
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Programs whose meaning depends on a script string this module cannot resolve.
_SHELL_PROGRAMS = ("sh", "bash", "dash", "zsh", "ksh")

#: `env` options that take no value. From `env --help` on this machine (GNU
#: coreutils 9.4), including `-` - which env documents as "a mere - implies -i",
#: i.e. an option and emphatically not a terminator.
_ENV_FLAGS = frozenset({
    "-i", "--ignore-environment",
    "-0", "--null",
    "-v", "--debug",
    "--help", "--version",
    "--list-signal-handling",
    "-",
})

#: `env` short options that take no value, derived from `_ENV_FLAGS` rather than
#: written out again: the two lists were once separate and the short one had
#: drifted, which made `-iu` unreadable and turned a refusal into a wrong answer.
_ENV_SHORT_FLAGS = frozenset(
    name[1] for name in _ENV_FLAGS
    if len(name) == 2 and name.startswith("-") and not name.startswith("--")
)

#: `env` short options that take a value, each paired with the long form it
#: stands for: `-u`/`--unset`, `-C`/`--chdir`, `-S`/`--split-string`. They take
#: the rest of the token as the value: `-uPATH` unsets PATH and `-u PATH` unsets
#: PATH, while `-u=PATH` unsets `=PATH` (measured) - so the value is the remainder
#: of the token and never anything after an `=`.
_ENV_SHORT_VALUED = frozenset("uCS")

#: `env` long options that require a value, accepting both `--unset PATH` and
#: `--unset=PATH`. `--split-string` takes its value and splits it into several
#: arguments, but for naming the program it is one consumed token either way.
_ENV_VALUED = frozenset({"--unset", "--chdir", "--split-string"})

#: `env` long options whose value is optional, and therefore only ever accepted
#: after an `=` - GNU getopt cannot take a detached one, which is why the token
#: after a bare `--block-signal` is the command.
_ENV_OPTIONAL_VALUED = frozenset({"--block-signal", "--default-signal",
                                  "--ignore-signal"})

#: Constructs that hide the real program from any static reading of a script.
_EXPANSION = ("$(", "`", "${")


def _env_splits_its_argument(word: str) -> bool:
    """True for `env -S`, which hides the program inside a string.

    `env -S "dd of=/tmp/x"` word-splits its own argument and execs whatever
    falls out, so the program is not a token in this argv and no walk over
    tokens can reach it. Treating `-S` as an ordinary value-taking option made
    `_program(['env','-S','dd'])` consume `dd` as the option's argument and
    return `''` - a name no blocklist holds, so all four guards passed and the
    real dd ran and wrote. Measured at LEVEL_3_HOST_USER before this check.

    Refused rather than parsed, for the reason the rest of this module is built
    on: guessing where a program sits inside an arbitrary string is the failure
    mode, not the fix. `env -S` exists for shebang lines, which no skill here
    emits, so nothing legitimate is lost.
    """
    if word in ("-S", "--split-string"):
        return True
    if word.startswith("--split-string="):
        return True
    return word.startswith("-S") and len(word) > 2


def _env_option_width(word: str) -> "int | None":
    """How many argv entries `word` swallows as an `env` option, or None.

    None means "this is not an `env` option at all", which is the ordinary case:
    the caller has found the command. A short option is one word, so its width is
    1 or 2; a long option's width depends on whether it was given a detached
    value, because `--unset PATH` and `--unset=PATH` both appear in real argv.

    Short options cluster, and the first letter that wants a value takes the rest
    of the token - `-iu PATH` unsets PATH, `-ui PATH` unsets `i`. Both measured
    against the real `env`. That walk only ever runs on a token that begins with
    `-`: without that guard it reads the *command* as a cluster, so `env -i sudo`
    consumed `su` plus the token after it and named neither.

    An option this table does not know is reported as *not an option*, so the
    caller's next step is to refuse the whole call rather than guess. That
    asymmetry is the point: an option skipped when it should have been obeyed
    lands the walk on an argument, and an argument is a name no blocklist holds;
    the reverse mistake cannot happen. The cost is that GNU's long-option
    abbreviation (`env --ignore-e dd`, which really does run dd) is refused
    instead of understood. A spelling nobody types, refused loudly, beats a
    spelling a blocklist cannot see.
    """
    if word == "--":
        return 1
    if word.startswith("--"):
        name, sep, _ = word.partition("=")
        if name in _ENV_VALUED:
            return 1 if sep else 2
        if name in _ENV_OPTIONAL_VALUED:
            return 1
        if name in _ENV_FLAGS:
            # `env --ignore-environment=true` is an error, not a flag with a
            # value, so a flag carrying one is malformed rather than accepted.
            return None if sep else 1
        return None
    if word in _ENV_FLAGS:
        return 1
    if not word.startswith("-"):
        return None
    for position, letter in enumerate(word[1:], start=1):
        if letter in _ENV_SHORT_VALUED:
            return 1 if position < len(word) - 1 else 2
        if letter not in _ENV_SHORT_FLAGS:
            return None
    return 1


def _program(argv: "list[str]") -> "str | None":
    """The program this argv will actually exec, or None if it cannot be read.

    `env FOO=bar python3 ...` and `FOO=bar python3 ...` both run python3, so a
    guard that read only argv[0] would see `env`, match nothing, and wave the
    real program straight through. Leading assignments and a leading `env` are
    therefore skipped to find the program the kernel will exec.

    Getting past `env` means getting past its *options*, which are not
    assignments. The previous version skipped assignments and one `env` and then
    stopped at the next token, so it named the option: `_program(["env","-i",
    "dd"])` was `-i`, which matches no blocklist, and all four guards read it.
    Measured against the real executor at LEVEL_3_HOST_USER before the fix, `env
    -i dd`, `env -- dd`, `env -u PATH dd` and `env env dd` each ran the real dd
    and wrote the file it was pointed at, and `env -i sudo` and `env -i pkexec`
    each ran the real privilege escalator. So this walks `env`'s whole option
    grammar to the first token that is not one.

    Three `env` behaviours make a hand-rolled walk wrong if they are missed, all
    measured against GNU coreutils 9.4 on this machine rather than assumed:

    - An assignment ends option parsing. `env FOO=bar -i echo` execs `-i`, not
      `echo` - so options after an assignment must not be skipped, or the
      resolver names a program that never runs.
    - Short options cluster and the first value-taking letter consumes the rest
      of the token, so `-iu PATH` and `-ui PATH` mean different things.
    - A repeated `env` is a launcher too. `env env dd` really does exec dd, one
      `env` deeper, and a resolver that stopped at the outer `env` named a
      program that merely forwards to the dangerous one.

    Returns None - never a guess - when the argv's program cannot be read, which
    `execute()` treats as a refusal. That happens for a malformed option (`env
    -u` with no NAME to unset, a flag handed a value it does not take) and for an
    option this table does not know. Both are cases where the two-ended-token
    problem bites: `-u` and `-C` take an argument, so a bare `-u` is not a
    program, it is half an option; and there is no safe way to skip an option of
    unknown shape, because skipping too much walks straight past the program
    while skipping too little merely misses a blocklist that was never going to
    match anyway.

    An argv that names no program at all - `env -i`, which prints its empty
    environment and exits 0 - returns "". That is not the same answer as None and
    must not be: refusing it would be refusing working code, and the fail-closed
    rule has to stop at malformed rather than at empty.

    HONEST SCOPE. This closes the resolver, which is what commit 7af8265's four
    guards are built on; it does not describe a hole that was live. Both
    production call sites set argv[0] to a literal (`tools.py` builds
    `["python3","-c",...]`, `argfile.py` likewise), and the LLM controls the
    program *string*, not argv[0]. Before this change `env -i dd` reached the real
    dd through `execute()` in isolation, and could not have reached it from either
    production caller. It becomes live the moment some caller builds an argv an
    LLM can influence - which is why it is fixed as a structural property of the
    resolver rather than as a patch at one call site.
    """
    index = 0
    inside_env = False
    options_parse = False
    while index < len(argv):
        word = argv[index]
        if os.path.basename(word) == "env":
            # Transparent whenever it is the command of the env before it, even
            # behind a `--`: the inner env then runs its own program, so the
            # walk continues into *its* options rather than stopping here.
            inside_env = True
            options_parse = True
            index += 1
            continue
        if _ENV_ASSIGNMENT.match(word):
            if inside_env:
                options_parse = False
            index += 1
            continue
        if inside_env and options_parse:
            if _env_splits_its_argument(word):
                return None  # the program is inside a string, not in this argv
            width = _env_option_width(word)
            if width is not None:
                if word == "--":
                    # The terminator ends option parsing for good, so the next
                    # token is the program even when it is shaped like an option.
                    options_parse = False
                elif index + width > len(argv):
                    return None  # a value-taking option with nothing left to take
                index += width
                continue
            if word.startswith("-") and word != "-":
                return None  # an option of unknown or malformed shape
        return word
    return ""


def _name_matches(name: str, blocked_names) -> bool:
    """Whether `name` is one of `blocked_names`.

    Basename first, so `/sbin/dd` is `dd`. Still no substring matching, so `add`
    and `ddrescue` are not `dd`; and a dotted variant (`mkfs.ext4`,
    `mount.fuse`) counts as the binary it is named after, which is the form
    anyone actually types.
    """
    base = os.path.basename(name)
    return any(base == blocked or base.startswith(blocked + ".") for blocked in blocked_names)


def _blocked_binary(argv: "list[str]", blocked_names) -> "str | None":
    r"""The blocklisted program this argv would run, if any.

    argv[0] and nothing else. The previous version split a *shell string* on
    whitespace and compared tokens, which meant the check was defeated by
        # writing the name in any form a shell would expand: measured against the
        # real executor, `dd status=...` was refused with 126 while `$(echo dd)
        # status=...` and `d\d status=...` both reached the real system `dd`, which
        # then complained about its own arguments.

    Every spelling of `dd` is now either the name `_program()` reports, or an
    argv that was refused before anything ran. The `env` option spellings were
    the missing middle case: a resolver that stopped at the token after `env`
    named `-i`, `--`, `-u` and `env` for them, and none of those is in any
    blocklist, so `env -i dd` and `env -u PATH dd` reached the real dd. The two
    ways a spelling still escapes naming are both deliberate and both tested: the
    argv was refused as unreadable (`env -h dd`, `env --un PATH dd`), or real
    `env` is not running `dd` either because an assignment ended its option
    parsing (`env FOO=bar -i dd` execs `-i`). The earlier version of this
    sentence claimed there was no such spelling at all, which was false.
    """
    program = _program(argv)
    if program and _name_matches(program, blocked_names):
        return os.path.basename(program)
    return None


def _shell_script(argv: "list[str]") -> "str | None":
    """The script of an explicit `sh -c ...` argv, or None if this is not one.

    The shell stays reachable on purpose, because a blocklist never could police
    a shell string - so the honest move is to make the shell *visible* in the argv
    rather than keep a filter that appears to work. Everything below exists to
    stop that visibility from being a hole.
    """
    program = _program(argv)
    if not program or os.path.basename(program) not in _SHELL_PROGRAMS:
        return None
    try:
        return argv[argv.index("-c") + 1]
    except (ValueError, IndexError):
        return None


def _unresolvable_script(script: str) -> "str | None":
    """Why a literal shell script cannot be policed, or None if it can be.

    An expansion construct makes the program unknowable by reading the text: that
    is precisely how the previous string filter was walked past, since
    `$(echo dd) ...` names no blocked binary anywhere in the string. Rather than
    guess, the script is refused. The cost is real and deliberate - `sh -c "echo
    $(date)"` is refused too - and the alternative is a check that silently fails
    on the input it exists to catch.
    """
    for construct in _EXPANSION:
        if construct in script:
            return (
                f"an explicit shell script contains {construct!r}, so the program it "
                "would run is not knowable from the argv; pass the program directly "
                "instead of a shell string"
            )
    return None
