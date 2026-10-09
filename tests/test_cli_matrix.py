"""tools/cli_matrix.py: the classifiers that decide which commands become skill candidates.

Each case is a way the matrix could rank a command wrongly and look plausible:

- **the first verb decides the intent**, not the first rule that matches -
  `date` is "print or set", so it reads before it writes;
- **man section 8 is not "root only"** - lsblk, findmnt and ip are section 8 and
  answer an ordinary user, and calling them root-only hid them as candidates;
- **a summary with any changing verb is can-change**, even when it reads first,
  because a skill wrapping it needs a gate;
- the NAME line comes out of real roff (man(7) and mdoc(7)) without macro residue.
"""

import gzip
import importlib.util
import os
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "tools" / "cli_matrix.py"
_spec = importlib.util.spec_from_file_location("cli_matrix", _PATH)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


@pytest.mark.parametrize("summary, want", [
    ("print or set the system date and time", "inspect"),
    ("show / manipulate routing, network devices, interfaces and tunnels", "inspect"),
    ("transfer a URL", "transfer"),
    ("Control the systemd system and service manager", "control"),
    ("sort lines of text files", "process"),
    ("ffprobe media prober", "inspect"),
    ("compute and check SHA256 message digest", "check"),
    ("convert between image formats as well as resize an image", "convert"),
    ("something with no verb at all", "other"),
])
def test_the_first_verb_decides_the_intent(summary, want):
    assert cm.intent(summary) == want


def test_a_reader_that_can_also_write_is_can_change(tmp_path):
    exe = tmp_path / "date"
    exe.write_text("")
    assert cm.safety(str(exe), "print or set the system date and time") == "can-change"
    assert cm.safety(str(exe), "list block devices") == "read-only"


def test_section_8_alone_is_not_root_only(tmp_path):
    exe = tmp_path / "lsblk"
    exe.write_text("")
    assert cm.safety(str(exe), "list block devices") == "read-only"
    assert cm.kind("lsblk", "util-linux", "8", "list block devices") == "admin"


def test_setuid_elevates(tmp_path):
    exe = tmp_path / "sudo"
    exe.write_text("")
    os.chmod(exe, 0o4755)
    assert cm.safety(str(exe), "list things") == "elevates"


@pytest.mark.parametrize("name, section, summary, want", [
    ("sshd", "8", "OpenSSH daemon", "daemon"),
    ("agetty", "8", "alternative Linux getty", "daemon"),
    ("curl-config", "1", "Get information about a libcurl installation", "build-helper"),
    ("vim", "1", "Vi IMproved, a programmer's text editor", "interactive"),
    ("jq", "1", "Command-line JSON processor", "scriptable"),
])
def test_kind(name, section, summary, want):
    assert cm.kind(name, "pkg", section, summary) == want


def test_name_line_from_man7_roff():
    roff = ('.\\" a comment\n.TH LS 1\n.SH NAME\nls \\- list directory contents\n'
            '.\\" ***********\n.SH SYNOPSIS\n.B ls\n')
    assert cm._name_line(roff) == "list directory contents"


def test_name_line_from_mdoc():
    assert cm._name_line(".Dd x\n.Sh NAME\n.Nm ssh\n.Nd OpenSSH remote login client\n") == \
        "OpenSSH remote login client"


def test_a_so_page_is_followed(tmp_path, monkeypatch):
    man1 = tmp_path / "man1"
    man1.mkdir()
    with gzip.open(man1 / "real.1.gz", "wt") as f:
        f.write(".SH NAME\nreal \\- the real page\n.SH SYNOPSIS\n")
    with gzip.open(man1 / "alias.1.gz", "wt") as f:
        f.write(".so man1/real.1\n")
    monkeypatch.setattr(cm, "Path", lambda p: tmp_path if p == "/usr/share/man" else Path(p))
    assert cm._name_line(cm._read_page(man1 / "alias.1.gz")) == "the real page"


def test_json_output_is_detected_and_not_imagined():
    assert cm.JSON_FLAG.search(".TP\n\\fB\\-J\\fR, \\fB\\-\\-json\\fR\nUse JSON output format.")
    assert cm.JSON_FLAG.search("--output=json")
    assert not cm.JSON_FLAG.search("Use JavaScript Object Notation? no. Outputs plain text.")


def test_the_skill_scan_finds_real_callers():
    uses = cm.surface_uses()
    assert "edit_image" in uses["magick"]["skill"]
    assert "convert_media" in uses["ffmpeg"]["skill"]
    assert "unithealth" not in uses  # a word in triggers.py, not a command it runs
    assert not uses.get("definitely-not-a-command")


def test_pacman_fields_join_wrapped_lines():
    block = ("Name            : glib2\nRequired By     : gtk4  pango\n                  shani-desktop-gnome\n"
             "Install Reason  : Installed as a dependency for another package\n")
    f = cm._fields(block)
    assert f["Required By"].split() == ["gtk4", "pango", "shani-desktop-gnome"]


def test_pulled_in_by_finds_the_nearest_explicit_ancestor():
    pkgs = {
        "libx": {"explicit": False, "required_by": ["liby"]},
        "liby": {"explicit": False, "required_by": ["shani-multimedia", "other-lib"]},
        "other-lib": {"explicit": False, "required_by": []},
        "shani-multimedia": {"explicit": True, "required_by": []},
        "orphan": {"explicit": False, "required_by": []},
    }
    via = cm.pulled_in_by(pkgs)
    assert via["libx"] == ["shani-multimedia"]
    assert via["shani-multimedia"] == ["shani-multimedia"]
    assert via["orphan"] == []


def test_synopsis_from_man7_and_mdoc():
    man7 = ".SH NAME\nls \\- list\n.SH SYNOPSIS\n.B ls\n[\\fIOPTION\\fR]... [\\fIFILE\\fR]...\n.SH DESCRIPTION\n"
    assert cm._synopsis(man7) == "ls [OPTION]... [FILE]..."
    mdoc = ".Sh NAME\n.Sh SYNOPSIS\n.Nm ssh\n.Op Fl 46\n.Ar destination\n.Sh DESCRIPTION\n"
    # mdoc uses .Sh, which the man(7) pattern does not claim: empty, not garbage
    assert cm._synopsis(mdoc) == "ssh -46 destination"


def test_dependency_audit_splits_guarded_from_unguarded(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    (tmp_path / "senses").mkdir()
    (skills / "demo.py").write_text(
        'import shutil, subprocess\n'
        'if shutil.which("present-tool"): pass\n'
        'if shutil.which("guarded-missing"): pass\n'
        'subprocess.run(["unguarded-missing", "x"])\n')
    monkeypatch.setattr(cm, "SKILLS_DIR", tmp_path)
    d = cm.chronoa_dependencies({"present-tool": "/usr/bin/present-tool"})["demo"]
    assert d["missing"] == ["guarded-missing", "unguarded-missing"]
    assert d["unguarded_missing"] == ["unguarded-missing"]


# --- safety classes, as measured against Chronoa's own modules ----------------------

@pytest.mark.parametrize("summary, verb, want", [
    ("Control the systemd system and service manager", "control", "mixed"),
    ("query or control network driver and hardware settings", "search", "mixed"),
    ("Simple management tool for pods, containers and images", "control", "mixed"),
    ("GSettings configuration tool", "other", "mixed"),
    ("ffmpeg media converter", "convert", "writes-new"),
    ("send ICMP ECHO_REQUEST to network hosts", "transfer", "read-only"),
    ("Portable Document Format (PDF) to text converter", "convert", "writes-new"),
    ("print or set the system date and time", "inspect", "can-change"),
    ("list block devices", "inspect", "read-only"),
])
def test_safety_classes(tmp_path, summary, verb, want):
    exe = tmp_path / "x"
    exe.write_text("")
    assert cm.safety(str(exe), summary, verb) == want


def test_format_as_a_noun_is_not_a_change():
    assert cm.intent("Portable Document Format (PDF) to text converter") == "convert"
    assert cm.can_change("format a disk with a new filesystem")


def test_setgid_alone_does_not_elevate(tmp_path):
    exe = tmp_path / "plocate"
    exe.write_text("")
    os.chmod(exe, 0o2755)
    assert cm.safety(str(exe), "find files by name") == "read-only"


def test_a_command_page_beats_a_syscall_page(tmp_path, monkeypatch):
    for sec in ("man2", "man8"):
        (tmp_path / sec).mkdir()
    (tmp_path / "man2" / "shutdown.2.gz").write_bytes(gzip.compress(b".SH NAME\nshutdown \\- syscall\n.SH X\n"))
    (tmp_path / "man8" / "shutdown.8.gz").write_bytes(gzip.compress(b".SH NAME\nshutdown \\- power off\n.SH X\n"))
    real = cm.Path
    monkeypatch.setattr(cm, "Path", lambda p: tmp_path if p == "/usr/share/man" else real(p))
    assert cm._man_pages()["shutdown"][0] == "8"


def test_a_mixed_case_name_header():
    assert cm._name_line(".SH Name\ngio \\- GIO commandline tool\n.SH Synopsis\n") == "GIO commandline tool"


# --- options -------------------------------------------------------------------------

def test_options_man7_tp_with_values():
    page = (".SH OPTIONS\n.TP\n\\fB\\-a\\fR, \\fB\\-\\-all\\fR\ndo not ignore entries\n"
            ".TP\n\\fB\\-\\-block\\-size\\fR=\\fI\\,SIZE\\/\\fR\nscale sizes by SIZE\n.TP\n0\nexit status, not a flag\n.SH X\n")
    opts = cm._options(page)
    assert [(o["flags"], o["arg"]) for o in opts] == [(["-a", "--all"], ""), (["--block-size"], "SIZE")]


def test_options_ip_inline_and_docbook():
    curl = '.IP "\\-\\-alt\\-svc <file name>"\nEnable alt-svc parser.\n.IP "\\-\\-anyauth"\nPick any method.\n.SH X\n'
    assert [(o["flags"], o["arg"]) for o in cm._options(curl)] == [(["--alt-svc"], "<file name>"), (["--anyauth"], "")]
    systemd = ".PP\n\\fB\\-t\\fR, \\fB\\-\\-type=\\fR\n.RS 4\nThe argument is a list of unit types.\n.RE\n"
    assert [(o["flags"], o["arg"]) for o in cm._options(systemd)] == [(["-t", "--type"], "VALUE")]


# --- scaffold, diff, check, calibration ------------------------------------------------

def _row(**kw):
    row = {"command": "lscpu", "package": "util-linux", "package_description": "", "summary":
           "display information about the CPU architecture", "intent": "inspect", "safety": "read-only",
           "fits": ["sense", "skill"], "json": True, "synopsis": "lscpu [options]",
           "option_list": [{"flags": ["-J", "--json"], "arg": "", "desc": "Use JSON output format."},
                           {"flags": ["-o", "--output"], "arg": "list", "desc": "Define columns."},
                           {"flags": ["-h", "--help"], "arg": "", "desc": "help"}]}
    row.update(kw)
    return row


def _load(tmp_path, source):
    import importlib.util
    path = tmp_path / "gen.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("gen_skill", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scaffold_builds_a_loadable_skill(tmp_path):
    from shani_chronoa.skills import is_valid_schema
    mod = _load(tmp_path, cm.scaffold(_row()))
    (skill,) = mod.SKILLS
    assert is_valid_schema(skill.schema)
    props = skill.schema["function"]["parameters"]["properties"]
    assert set(props) == {"json", "output", "operands"}, "--help is never a parameter"
    assert mod.build_argv({"json": True, "output": "CPU,CORE", "operands": []}) == ["lscpu", "--json", "--output", "CPU,CORE"]


def test_scaffold_refuses_flag_smuggling(tmp_path):
    mod = _load(tmp_path, cm.scaffold(_row()))
    for bad in ({"operands": ["--delete"]}, {"output": "-x"}, {"operands": [""]}):
        with pytest.raises(ValueError):
            mod.build_argv(bad)
    assert "Not run" in mod.SKILLS[0].run({"operands": ["-rf"]})


def test_scaffold_of_an_actuator_is_opt_in_and_disabled(tmp_path):
    row = _row(command="systemctl", safety="mixed")
    with pytest.raises(ValueError):
        cm.scaffold(row)
    mod = _load(tmp_path, cm.scaffold(row, allow_actuator=True))
    assert mod.ENABLED is False
    assert "disabled" in mod.SKILLS[0].run({})


def test_diff_reports_added_removed_and_newly_verified():
    old = {"commands": [{"command": "a"}, {"command": "b"}], "summary": {},
           "chronoa": {"tools": [{"name": "t", "missing": ["x"], "post_condition": False}], "senses": [], "events": []}}
    new = {"commands": [{"command": "b"}, {"command": "c"}], "summary": {},
           "chronoa": {"tools": [{"name": "t", "missing": [], "post_condition": True}], "senses": [],
                       "events": [{"type": "schedule"}]}}
    out = cm.diff(old, new)
    assert "## Commands added (1)" in out and "`c`" in out and "## Commands removed (1)" in out
    assert "No longer missing one: `t`" in out and "Newly verified: `t`" in out and "`schedule`" in out


def test_check_flags_a_raw_error_but_not_an_explained_one():
    data = {"chronoa": {"tools": [{"name": "press_key", "handling": {"xdotool": "caught (raw error)"}},
                                  {"name": "type_text", "handling": {"wtype": "checked first"}}], "senses": []}}
    problems = cm.check(data)
    assert len(problems) == 1 and "press_key" in problems[0]
    assert cm.check({"chronoa": {"error": "ImportError: x"}})


def test_calibration_counts_mixed_as_honest():
    rows = [
        {"command": "systemctl", "used_by": {"sense": ["services"]}, "safety": "mixed", "fits": ["sense"], "summary": ""},
        {"command": "rm", "used_by": {"actuator": ["delete_file"]}, "safety": "read-only", "fits": [], "summary": ""},
        {"command": "lsblk", "used_by": {"sense": ["storage"]}, "safety": "read-only", "fits": [], "summary": ""},
    ]
    c = cm.calibration(rows)
    assert (c["safety_agree"], c["safety_disagree"]) == (2, 1)
    assert c["safety_wrong"][0]["command"] == "rm"
    # The key was `sense_missed`, which read as "senses Chronoa lacks". It is the
    # opposite: a command a sense already runs where the heuristic did not say
    # "sense". Renamed because I read it as a gap list, checked all six entries
    # against the tree, and found every one already covered.
    assert c["sense_recall"] == 0.5
    disagree = c["sense_classifier_disagreements"]
    assert [d["command"] for d in disagree] == ["lsblk"], disagree
    # ...and it carries who runs it, so it can be checked in a second.
    assert "run_by" in disagree[0] and "classified_as" in disagree[0], disagree
    assert "sense_missed" not in c, (
        "the old key is back, and a reader will take it for a list of "
        "capabilities Chronoa lacks rather than a list of heuristic blind spots")


def test_a_guard_in_an_imported_helper_counts(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    (tmp_path / "senses").mkdir()
    (skills / "list_things.py").write_text(
        'import shutil\ndef session_problem():\n    if shutil.which("xdotool") is None:\n        return "missing"\n')
    (skills / "press.py").write_text(
        'import subprocess\nfrom shani_chronoa.skills.list_things import session_problem\n'
        'def _run(a):\n    try:\n        subprocess.run(["xdotool", "key", "x"])\n    except OSError:\n        pass\n')
    (skills / "bare.py").write_text('import subprocess\ntry:\n    subprocess.run(["xdotool", "key"])\nexcept OSError:\n    pass\n')
    monkeypatch.setattr(cm, "SKILLS_DIR", tmp_path)
    deps = cm.chronoa_dependencies({})
    assert deps["press"]["handling"] == {"xdotool": "checked first"}
    assert deps["bare"]["handling"] == {"xdotool": "caught (raw error)"}, "the control: no helper, no guard"


# --- subcommands -------------------------------------------------------------------------

@pytest.mark.parametrize("name, desc, want", [
    ("list-units", "List units that systemd currently has in memory.", "reads"),
    ("is-active", "Check whether any of the specified units are active", "reads"),
    ("start", "Start (activate) one or more units", "changes"),
    ("checkout", "Switch branches or restore working tree files.", "changes"),  # not "check"
    ("branch", "List, create, or delete branches.", "changes"),             # one changing verb wins
    ("am", "Apply a series of patches from a mailbox.", "changes"),
    ("log", "Show commit logs.", "reads"),
    ("frobnicate", "", "changes"),                                            # unknown stays gated
])
def test_classify_subcommand(name, desc, want):
    assert cm.classify_subcommand(name, desc) == want


def test_subcommands_from_a_docbook_commands_section():
    page = (".SH \"COMMANDS\"\n.PP\n\\fBlist\\-units\\fR [\\fIPATTERN\\fR\\&...]\n.RS 4\nList units.\n.RE\n"
            ".PP\n\\fBstop\\fR \\fIUNIT\\fR\\&...\n.RS 4\nStop one or more units.\n.RE\n.SH \"OPTIONS\"\n")
    subs = cm._subcommands(page, command="systemctl")
    assert [(x["name"], x["effect"]) for x in subs] == [("list-units", "reads"), ("stop", "changes")]


def test_a_mixed_tool_scaffolds_as_its_reading_subcommands_only(tmp_path):
    row = _row(command="systemctl", safety="mixed", json=False, change_subcommands=1,
               subcommands=[{"name": "status", "effect": "reads", "desc": "", "args": ""},
                            {"name": "list-units", "effect": "reads", "desc": "", "args": ""},
                            {"name": "stop", "effect": "changes", "desc": "", "args": ""}],
               option_list=[{"flags": ["--all"], "arg": "", "desc": "Show all units."},
                            {"flags": ["--force"], "arg": "", "desc": "Force it."},
                            {"flags": ["--now"], "arg": "", "desc": "Also start the unit."}])
    mod = _load(tmp_path, cm.scaffold(row))
    assert mod.ENABLED is True and mod.SUBCOMMANDS == ("status", "list-units")
    assert mod.build_argv({"subcommand": "status", "all": True, "operands": ["sshd"]}) == \
        ["systemctl", "status", "--all", "sshd"]
    for bad in ({"subcommand": "stop"}, {}, {"subcommand": "status", "operands": ["--now"]}):
        with pytest.raises(ValueError):
            mod.build_argv(bad)
    assert "force" not in mod.FLAGS and "now" not in mod.FLAGS


@pytest.mark.parametrize("name, desc, want", [
    ("container-list", "List containers", "reads"),
    ("container-checkpoint", "Checkpoint one or more running containers", "changes"),
    ("container-rm", "Remove one or more containers", "changes"),
])
def test_nested_subcommands_use_their_last_segment(name, desc, want):
    assert cm.classify_subcommand(name, desc) == want


def test_a_getter_that_sets_with_an_argument_is_a_change():
    page = (".SH \"COMMANDS\"\n.PP\n\\fBservice\\-log\\-level\\fR \\fISERVICE\\fR [\\fILEVEL\\fR]\n.RS 4\n"
            "If the LEVEL argument is not given, print the current log level as reported by service SERVICE.\n"
            ".sp\nIf the optional argument LEVEL is provided, then change the current log level of the service.\n.RE\n"
            ".SH \"OPTIONS\"\n")
    (sub,) = cm._subcommands(page, command="systemctl")
    assert sub["effect"] == "changes"


# --- packaging audit -----------------------------------------------------------------

PKGBUILD = """pkgname=demo
pkgver=1.2
pkgrel=3
depends=(
    'python'
    'bluez'   # the daemon (a comment with (parens) and a 'quote')
)
optdepends=(
    'geoclue: where this computer is (GNOME’s location service)'
    "ollama: a # inside quotes is text"
)
makedepends=('cmake')
"""


def test_bash_array_is_quote_and_comment_aware(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "PKGBUILD").write_text(PKGBUILD)
    b = cm.read_pkgbuilds(tmp_path)["demo"]
    assert b["depends"] == ["bluez", "python"]
    assert b["optdepends"] == ["geoclue", "ollama"]
    assert b["makedepends"] == ["cmake"] and b["version"] == "1.2-3"


def _mat(profile, packages, commands=(), tools=(), version="20261001"):
    return {"profile": profile, "image_version": version, "generated": "t", "host": "h",
            "packages": {n: {"version": "1-1", "explicit": True, "required_by": [], "provides": [],
                             "description": ""} for n in packages},
            "commands": [{"command": c, "package": p, "has_man": True, "kind": "scriptable", "safety": "read-only",
                          "shani": False} for c, p in commands],
            "chronoa": {"tools": list(tools), "senses": []}, "os_surfaces": {"polkit": []}}


def test_audit_problems_flags_undeclared_and_missing_hard_deps(tmp_path):
    builds = {"shani-chronoa": {"depends": ["whisper-cpp"], "optdepends": ["ffmpeg"], "makedepends": [], "version": ""}}
    m = _mat("gnome", ["shani-chronoa", "imagemagick", "ffmpeg"],
             commands=[("magick", "imagemagick"), ("ffmpeg", "ffmpeg")],
             tools=[{"name": "edit_image", "needs": ["magick"]}, {"name": "convert_media", "needs": ["ffmpeg"]}])
    problems = cm.audit_problems([m], builds, tmp_path / "no-profiles")
    assert any("without its dependency whisper-cpp" in p for p in problems)
    assert any("edit_image runs magick from imagemagick" in p for p in problems)
    assert not any("convert_media" in p for p in problems), "a declared optdepend is not a problem"


def test_a_stale_image_is_not_blamed_on_its_lists(tmp_path):
    import subprocess
    prof = tmp_path / "profiles"
    for d in ("shared", "plasma"):
        (prof / d).mkdir(parents=True)
    (prof / "plasma" / "Packages-Desktop").write_text("shani-chronoa\n")
    subprocess.run(["git", "init", "-q", str(prof)], check=True)
    subprocess.run(["git", "-C", str(prof), "add", "."], check=True)
    subprocess.run(["git", "-C", str(prof), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x",
                    "--date=2026-09-25T00:00:00"], check=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": "2026-09-25T00:00:00"})
    builds = {"shani-chronoa": {"depends": [], "optdepends": [], "makedepends": [], "version": ""}}
    stale = _mat("plasma", ["x"], version="20260922")
    fresh = _mat("plasma", ["x"], version="20261001")
    assert not cm.audit_problems([stale], builds, prof)
    assert any("not on the image built from them" in p for p in cm.audit_problems([fresh], builds, prof))


# --- suggestions -------------------------------------------------------------------------

def test_suggestions_rank_measured_needs_and_keep_opinion_apart():
    pk = {"gtk4": {"explicit": True, "required_by": [], "provides": [], "description": "",
                   "optional": [{"name": "evince", "why": "print preview", "installed": False}]},
          "gtk3": {"explicit": True, "required_by": [], "provides": [], "description": "",
                   "optional": [{"name": "evince", "why": "print preview", "installed": False},
                                {"name": "python-gobject-doc", "why": "documentation", "installed": False}]},
          "exim": {"explicit": False, "required_by": ["arpwatch"], "provides": [], "description": "", "optional": []}}
    m = {"profile": "gnome", "packages": pk, "broken": [], "python_imports": [{"module": "sympy", "first_used_in": "solve_math"}],
         "commands": [{"command": "exim", "package": "exim", "safety": "elevates"}],
         "chronoa": {"tools": [{"name": "press_key", "missing": ["xdotool"]}, {"name": "check_updates",
                                                                              "missing": ["checkupdates"]}], "senses": []}}
    items = cm.suggestions([m], {})
    by = {(x["kind"], x["package"]): x for x in items}
    assert "wanted by 2: gtk3, gtk4" in by[("add", "evince")]["evidence"]
    assert ("skip", "python-gobject-doc") in by, "documentation is not a distro feature"
    assert ("add", "pacman-contrib") in by and ("add", "python-sympy") in by
    assert ("note", "xdotool") in by and ("add", "xdotool") not in by, "an X11 tool is not a Wayland gap"
    assert "arpwatch" in by[("review", "exim")]["evidence"]
    assert any(x["kind"] == "consider" for x in items)
    kinds = [x["kind"] for x in items]
    assert kinds.index("add") < kinds.index("review") < kinds.index("consider")


# --- footprint, units, owners -------------------------------------------------------------

def test_footprint_is_what_only_that_package_pulls_in():
    pk = {
        "meta-a": {"explicit": True, "depends": ["big", "shared"], "provides": [], "size": 0},
        "meta-b": {"explicit": True, "depends": ["shared", "sh"], "provides": [], "size": 0},
        "big": {"explicit": False, "depends": ["bigdep"], "provides": [], "size": 300},
        "bigdep": {"explicit": False, "depends": [], "provides": [], "size": 200},
        "shared": {"explicit": False, "depends": [], "provides": [], "size": 1000},
        "bash": {"explicit": False, "depends": [], "provides": ["sh"], "size": 5},
    }
    fp = {x["package"]: x for x in cm.footprint(pk)}
    assert fp["meta-a"]["exclusive_bytes"] == 500, "big + bigdep, not the shared package"
    assert fp["meta-b"]["exclusive_bytes"] == 5, "sh resolves through bash's Provides"
    assert cm.footprint(pk)[0]["package"] == "meta-a"


def test_sizes_and_dates_parse_in_the_c_locale():
    assert cm._size("9.59 MiB") == int(9.59 * 1024 ** 2) and cm._size("0.00 B") == 0 and cm._size("") == 0
    assert cm._when("Tue Sep 15 19:47:19 2026") > 0 and cm._when("garbage") == 0


def test_resolve_owners_reads_pacman_files_machine_output(tmp_path, monkeypatch):
    stub = tmp_path / "pacman"
    stub.write_text('#!/bin/sh\ncase "$3" in usr/bin/xdotool) printf "extra\\0xdotool\\0 3-1\\0usr/bin/xdotool\\n";; esac\n')
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert cm.resolve_owners(["xdotool", "nonexistent"]) == {"xdotool": "xdotool (extra)"}
