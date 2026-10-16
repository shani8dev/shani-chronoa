"""Mutation set for the shared `LD_PRELOAD` re-exec, and for the two launchers.

**The defect this set exists for is that only one of two callers had the fix.**
`gtk4-layer-shell` refuses to initialise unless it is linked *before*
`libwayland-client`, its own warning says so, and it cannot be repaired from inside a
running process - the loader decides before `main()`. `usr/bin/shani-chronoa-overlay`
re-execs for that link order. `usr/bin/shani-chronoa` did not - and the mascot's
layer surface is created **in the app's own process** (`application.py` constructs
`Mascot(self)` rather than launching it), so the mascot was the one surface that
silently rendered as an ordinary window while the cue layer worked. Same library,
same requirement, one copy of the fix, and only one of the two callers had it.

So this set is deliberately about **the wiring**, not the arithmetic. Mutations 13-16
are the four shapes that drift: the app launcher drops the call, the overlay drops
the call, the call moves after the package import, and a launcher grows its own
`os.execv`.

**Four of these are guards with no observable otherwise**, which is why each is
written as *removing the guard* rather than *inverting the condition*. Inverting
`if os.environ.get(MARKER): return` to `if not os.environ.get(MARKER): return`
would make the second process re-exec again - and nothing in this repository's tests
runs two processes, so the mutation would be reported caught by a test that passes
either way. The mutation that bites is the one that leaves the loop guard in place
with nothing under it.

**Every witness is a bare method name.** `tools/mutate.py` builds its verdict set
from `line.split("::")[-1]`, so a `Class::method` compares a full id against a bare
one and marks the witness stale.

**Every anchor was re-read from the live file and asserted `count == 1` before the
first run.** One of these was written from the plan and matched nothing -
`if os.path.exists(candidate):` is indented four spaces inside the `for`, not at
column zero - which the runner reports as a survivor with a misleading reason.

**One mutation is deliberately absent.** Dropping `os.path.realpath()` from the
`LD_PRELOAD` entry is **equivalent by construction**: the loader resolves the
soname itself, so `/usr/lib/x86_64-linux-gnu/libgtk4-layer-shell.so.0` and the
`...so.1.3.0` it points at preload the same library. That is precisely why
`test_the_library_is_preloaded_and_the_process_re_execed` accepts either spelling -
it pins *"ours is first and it is the library"*, not *"ours is spelled this way"*.
"""

TEST_FILE = "tests/test_layer_shell_preload.py"

_LP = "usr/lib/shani-chronoa/shani_chronoa/layer_shell_preload.py"
_APP = "usr/bin/shani-chronoa"
_OVL = "usr/bin/shani-chronoa-overlay"

_MUTATIONS = [

    # --- the four guards ------------------------------------------------------
    {
        "label": "the loop guard has an empty body, so the re-exec never stops",
        "file": _LP,
        "old": '    if os.environ.get(MARKER):\n        return',
        "new": '    if os.environ.get(MARKER):\n        pass',
        "why": "The guard against an execv that cannot loop. Removed rather than "
               "inverted on purpose: inverting it would make the *second* process "
               "restart again, and nothing here runs two processes, so an inverted "
               "mutation is caught by nothing.",
        "expect": [
            "test_the_second_process_does_not_restart_again",
        ],
    },
    {
        "label": "the marker is never set, so the second process cannot see the first one's work",
        "file": _LP,
        "old": '    os.environ[MARKER] = "1"',
        "new": '    pass',
        "why": "The marker travels through execv's environment, so a re-exec "
               "without it is the loop this guard exists to prevent. Unobservable "
               "from inside one process, which is why the fix's own test asserts the "
               "marker's value in the recorded environment rather than trusting the "
               "code.",
        "expect": [
            "test_the_library_is_preloaded_and_the_process_re_execed",
        ],
    },
    {
        "label": "a machine with no layer shell is told it has one, so every launch pays a restart",
        "file": _LP,
        "old": "    return None",
        "new": '    return ""',
        "why": "The ordinary machine. `None` is what every caller treats as "
               "\"nothing to do\"; an empty string is truthy-looking to a naive "
               "caller and would have the preload realpath the process's own cwd. "
               "**Measured cost of getting this wrong: 7.2 ms on every launch of "
               "every machine, for a library none of them has.**",
        "expect": [
            "test_it_reports_none_rather_than_guessing",
            "test_a_machine_without_the_library_is_never_restarted",
        ],
    },
    {
        "label": "the search stops asking whether the file is there",
        "file": _LP,
        "old": "        if os.path.exists(candidate):\n            return candidate",
        "new": "        if True:\n            return candidate",
        "why": "The existence check, and with it the whole meaning of `None`. "
               "Dropping it makes the *first* prefix the answer on every machine, so "
               "a machine with the library somewhere else in the list looks exactly "
               "like a machine that does not have it - the failure this module's "
               "docstring calls out by name.",
        "expect": [
            "test_it_reports_none_rather_than_guessing",
            "test_it_finds_the_library_where_it_is_installed",
        ],
    },
    {
        "label": "an inherited preload is not recognised, so the library is loaded twice",
        "file": _LP,
        "old": '    if LIBRARY[: -len(".so.0")] in os.environ.get("LD_PRELOAD", ""):\n        return',
        "new": '    if False:\n        return',
        "why": "A launcher that already preloaded it, or a shell that exports one, "
               "would get a second copy prepended. At best wasted; at worst a "
               "duplicate-initialisation crash, and neither raises anything a person "
               "would recognise.",
        "expect": [
            "test_an_inherited_preload_is_not_duplicated",
        ],
    },

    # --- what gets installed, and in what order -------------------------------
    {
        "label": "the library goes last in LD_PRELOAD instead of first",
        "file": _LP,
        "old": "        part for part in (os.path.realpath(library),\n"
               "                          os.environ.get(\"LD_PRELOAD\", \"\")) if part)",
        "new": "        part for part in (os.environ.get(\"LD_PRELOAD\", \"\"),\n"
               "                          os.path.realpath(library)) if part)",
        "why": "The loader searches in order, so putting ours last makes the fix "
               "conditional on nothing else preloading us. On a machine with an "
               "inherited preload this is inert; on a clean one it is the same answer, "
               "which is why the witness builds the inherited case rather than the "
               "simple one.",
        "expect": [
            "test_an_existing_preload_is_kept_after_ours",
        ],
    },
    {
        "label": "an inherited LD_PRELOAD is discarded instead of kept",
        "file": _LP,
        "old": "        part for part in (os.path.realpath(library),\n"
               "                          os.environ.get(\"LD_PRELOAD\", \"\")) if part)",
        "new": "        part for part in (os.path.realpath(library),) if part)",
        "why": "The other half of the same line, and the destructive direction: a "
               "launcher that preloads something of its own loses it silently, and "
               "nothing about *this* module would report it.",
        "expect": [
            "test_an_existing_preload_is_kept_after_ours",
        ],
    },
    {
        "label": "the arguments are not carried across the re-exec",
        "file": _LP,
        "old": "    os.execv(sys.executable, [sys.executable] + sys.argv)",
        "new": "    os.execv(sys.executable, [sys.executable])",
        "why": "The app is started with `--hidden`, `--model=`, `--voice=` and "
               "friends. A re-exec that drops argv restarts the app with no "
               "arguments at all - the start-hidden app appearing, the override gone - "
               "with nothing saying why.",
        "expect": [
            "test_the_library_is_preloaded_and_the_process_re_execed",
        ],
    },

    # --- which library, and where from ----------------------------------------
    {
        "label": "the GTK 3 build is preloaded into the GTK 4 app",
        "file": _LP,
        "old": 'LIBRARY = "libgtk4-layer-shell.so.0"',
        "new": 'LIBRARY = "libgtk-layer-shell.so.0"',
        "why": "`libgtk-layer-shell.so.0` is a *different file*, measured: it links "
               "against `libgtk-3.so.0`, and loading it into a GTK 4 process "
               "registers a second GObject type system - which is worse than having "
               "no layer shell at all. Both names look like the right one.",
        "expect": [
            "test_the_gtk_3_build_is_not_mistaken_for_the_gtk_4_one",
        ],
    },
    {
        "label": "only the first of the three measured install prefixes is searched",
        "file": _LP,
        "old": 'SEARCH_PATH = ("/usr/lib/x86_64-linux-gnu", "/usr/lib64", "/usr/lib")',
        "new": 'SEARCH_PATH = ("/usr/lib/x86_64-linux-gnu",)',
        "why": "Dropping a prefix is **invisible on whichever layout you happen to "
               "be standing on**, which is the trap this set has no way around from "
               "inside the code: Debian and Ubuntu install under `x86_64-linux-gnu` "
               "(this box), Arch and Fedora under `/usr/lib`. So the witness is a "
               "pin on the tuple rather than a behavioural test, and it is the only "
               "one of its kind here.",
        "expect": [
            "test_every_measured_install_location_is_searched",
        ],
    },

    # --- the shape a launcher depends on --------------------------------------
    {
        "label": "the helper gains a heavy import, so a launcher must start GTK to fix a link order",
        "file": _LP,
        "old": "import os\nimport sys",
        "new": "import os\nimport sys\nimport gi",
        "why": "`automation_overlay` imports `gi` and `Gtk` at module scope, which is "
               "why it is the wrong home for this. The property that decides the "
               "placement is *\"a launcher can call this before it imports anything "
               "from the package\"*, so it is the only mutation that can be aimed at "
               "the reason the module exists at all.",
        "expect": [
            "test_the_module_imports_nothing_but_os_and_sys",
        ],
    },

    # --- the wiring, which is the whole point ---------------------------------
    {
        "label": "the app launcher does not preload at all (the shipped defect)",
        "file": _APP,
        "old": "preload_layer_shell()\n\nfrom shani_chronoa.app import main",
        "new": "from shani_chronoa.app import main",
        "why": "**The defect this extraction exists to fix**, restored verbatim: the "
               "mascot's layer surface is created in this process and this process "
               "does not re-exec for the link order. The symptom is not an error - the "
               "library logs a warning and returns a *working* ordinary window.",
        "expect": [
            "test_the_launcher_preloads_before_it_imports_the_package",
        ],
    },
    {
        "label": "the app launcher re-implements the re-exec instead of calling the helper",
        "file": _APP,
        "old": "from shani_chronoa.layer_shell_preload import preload_layer_shell",
        "new": "def preload_layer_shell():\n    os.execv(sys.executable, [sys.executable])",
        "why": "One copy of the fix, enforced. A second `execv` in a launcher is "
               "exactly the drift the extraction removed, and it is invisible to any "
               "test that only checks *whether* the launcher restarts.",
        "expect": [
            "test_no_launcher_carries_its_own_copy_of_the_fix",
        ],
    },
    {
        "label": "the app launcher preloads after importing the package, so the loader has already decided",
        "file": _APP,
        "old": "from shani_chronoa.layer_shell_preload import preload_layer_shell\n\n"
               "preload_layer_shell()\n\nfrom shani_chronoa.app import main",
        "new": "from shani_chronoa.layer_shell_preload import preload_layer_shell\n\n"
               "from shani_chronoa.app import main\n\npreload_layer_shell()",
        "why": "**Order is the whole mechanism.** `execv` does not return, so a call "
               "placed after the package import re-execs a process that has already "
               "loaded GTK - which is precisely the state the link order has to be "
               "fixed *before*. The launcher still preloads and still re-execs, so "
               "only the position is observable.",
        "expect": [
            "test_the_launcher_preloads_before_it_imports_the_package",
        ],
    },
    {
        "label": "the overlay launcher does not preload, so the cue layer is the half that breaks",
        "file": _OVL,
        "old": "preload_layer_shell()\n\nfrom shani_chronoa.automation_overlay import main",
        "new": "from shani_chronoa.automation_overlay import main",
        "why": "The same drift in the other direction. Kept because the two launchers "
               "are separate files with separate histories: the reason the app needed "
               "the fix is that this one **had** it, so a future edit that removes "
               "it from here is the original defect's mirror.",
        "expect": [
            "test_the_launcher_preloads_before_it_imports_the_package",
        ],
    },
]

MUTATIONS = _MUTATIONS