"""Skill: run and drive containers, and start or stop nspawn machines.

The container half of "automate the machine like a browser is automated":
podman gives a container a lifecycle (run, exec into it, stop, read its logs),
which is what makes it useful rather than merely listed. The machine half is
`machinectl`, which is systemd-nspawn's front door - the same lifecycle for a
whole-system container.

**Why every podman call goes through a transient `systemd-run --user` unit.**
This is measured, not stylistic: every skill call runs at `LEVEL_3_HOST_USER`
under the `PROFILE_DEFAULT` ceiling, which is 512 MB of *address space*
(`RLIMIT_AS`, soft equal to hard, set in `sandbox/limits.py`). Measured on this
box with the real podman 5.7.0:

    ulimit -v 524288; podman run --rm alpine echo hi
      -> runtime.schedinit() ... traceback: the Go runtime will not start
    ulimit -v 1048576; podman run --rm alpine echo hi
      -> OCI runtime error: runc: pthread_create failed: Resource temporarily
         unavailable
    podman run --rm alpine echo hi (no ceiling)
      -> works

A skill's own subprocess inherits that ceiling and cannot raise it, so the
podman client cannot be a child of the skill process at any sane limit. A
transient user unit is spawned by the user manager instead, which is outside
the skill child: measured, a unit launched from a capped shell reports
`Max address space unlimited unlimited`. The invocation contract was measured
too - exit status, stdout and stderr all pass through `--pipe`, and a detached
container outlives the unit that started it (`conmon` is its parent, not the
unit). One unit per call, `--collect` removes it on exit.

Honesty rules:
- a podman exit code of 0 is reported as the tool's own account; the actions
  that can be read back read back (`run` detached and `stop` have a
  post-condition), the rest say they are unverified.
- `podman run` **pulls a missing image by itself** (measured: redis:alpine
  pulled and ran in 14.5 s), so a run can be slow and network-bound. That is
  why this is in `tools._SLOW_TOOLS`.
- no `machinectl` is "no machines"; an empty `machinectl list` is an empty
  list. `machinectl start` on a stock desktop asks machine1 for a privileged
  operation, and this development box cannot complete that authentication, so
  the machine-start path reports whatever machine1 says rather than claiming
  the boot worked.
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.skills import Skill

_CONSENT_KEY = "container-control-enabled"
#: Long enough for an image pull (measured: 14.5 s for redis:alpine, ~66 MB).
_TIMEOUT = 300
#: Lifecycle only. Driving what a container's app draws on this desktop is
#: `ui_elements` (its `act`/`state`/`drive` actions): one way in, one gate, and
#: a container request no longer pays ~1.3k chars of schema for a vocabulary it
#: will never use. Measured: this schema was the *largest* in the registry
#: (3481 chars, ahead of `browse` at 3462) while carrying both halves.
_ACTIONS = (
    "run", "exec", "stop", "rm", "logs", "list", "images", "pull",
    "machines", "machine-start", "machine-stop",
)
#: Container names: podman's own rule is a leading alphanumeric, then
#: alphanumerics, underscore, period, hyphen.
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
#: Image references carry a registry path, tag and digest, so they allow
#: slashes and colons that a container *name* does not.
_IMAGE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,255}$")
_PUBLISH = re.compile(r"^\d{1,5}:\d{1,5}(/(tcp|udp))?$")
_UNIT_SEQ = itertools.count(1)


def _consent(config: ChronoaConfig) -> "tuple[bool, str]":
    """Return (allowed, reason). Fail-closed: an undeclared key denies."""
    if not config.get_bool(_CONSENT_KEY, False):
        return False, (
            f"running containers is turned off (enable '{_CONSENT_KEY}' in "
            f"Settings). Listing what already exists needs no such permission "
            f"- only starting, stopping and running commands inside a "
            f"container does, because a container is a whole computer as far "
            f"as anything it does is concerned."
        )
    return True, ""


def _unit_name() -> str:
    """A unique transient unit name; `--collect` removes it on exit."""
    return f"chronoa-container-{int(time.time() * 1000) % 10**11}-{next(_UNIT_SEQ)}"


def _podman(argv: "list[str]", timeout: int = _TIMEOUT, keep_output: bool = False):
    """Run a podman command in a transient user unit.

    Returns `(proc, "")` or `(None, reason)`. See the module docstring for why
    the unit is load-bearing rather than cosmetic: the podman client cannot
    start under the skill child's 512 MB address-space ceiling.

    `keep_output` writes the unit's stdout and stderr to files instead of the
    `--pipe` streams. Measured reason it exists: with `--pipe`, the *invoking*
    process holds the read end, and a detached container's `conmon` inherits
    the write end - so when `systemd-run` returns and the pipe closes, conmon
    takes a SIGPIPE and dies, leaving a container the runtime cannot manage
    ("conmon exited prematurely"). The container it started even keeps running,
    which is what makes the failure quiet: `podman ps` shows it Up.
    """
    for binary, verb in (("systemd-run", "run a container"), ("podman", "run a container")):
        if shutil.which(binary) is None:
            return None, files.tool_missing(binary, verb)
    unit = ["systemd-run", "--user", "--quiet", "--collect", f"--unit={_unit_name()}"]
    if keep_output:
        handle, out_path = tempfile.mkstemp(prefix="chronoa-container-out-")
        os.close(handle)
        handle, err_path = tempfile.mkstemp(prefix="chronoa-container-err-")
        os.close(handle)
        unit += [f"--property=StandardOutput=file:{out_path}",
                 f"--property=StandardError=file:{err_path}"]
    else:
        unit.append("--pipe")
    try:
        proc = subprocess.run([*unit, "podman", *argv],
                              capture_output=True, text=True, timeout=timeout, check=False)
        if keep_output:
            with open(out_path, encoding="utf-8", errors="replace") as h:
                proc = subprocess.CompletedProcess(
                    proc.args, proc.returncode, h.read(), _readfile(err_path))
    except subprocess.TimeoutExpired:
        return None, (f"the container command did not finish within {timeout}s "
                      f"(an image pull can take that long), so the outcome is unknown.")
    except OSError as exc:
        return None, f"could not run the container command: {exc}"
    finally:
        if keep_output:
            for path in (out_path, err_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return proc, ""


def _readfile(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as h:
            return h.read()
    except OSError:
        return ""


def _detail(proc: subprocess.CompletedProcess) -> str:
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    return lines[-1] if lines else ""


def _running_names(all_containers: bool = False):
    """Names of containers, or None if podman could not be asked.

    The distinction is the point: an empty list means nothing is running (the
    success case for `stop`), while None means the question was not answered.
    Returning `[]` for both is how a post-condition ends up reporting
    UNVERIFIED on the one input where it could have been certain.
    """
    argv = ["ps", "--format", "{{.Names}}"]
    if all_containers:
        argv.append("--all")
    proc, _ = _podman(argv, timeout=30)
    if proc is None or proc.returncode != 0:
        return None
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _name_or_all(arguments: dict) -> "str | None":
    name = str(arguments.get("name") or "").strip()
    if arguments.get("all"):
        return None if not name else name
    if not name:
        return "NO-NAME"
    if not _NAME.match(name):
        return "BAD-NAME"
    return name


def _display_setup(display: str, share_bus: bool = False) -> "tuple[list[str], list[str], str]":
    """What a container needs to draw on this desktop: (extra argv, env, refusal).

    The mechanism is `shani-testbed`'s, copied rather than reinvented: it binds
    the display's socket into the container, hands over the variable that names
    it, and pins the toolkits to their X11 backends with software rendering,
    because a container has no GPU of its own and a Wayland-native client cannot
    be driven by the X pointer. Measured on this box: `/tmp/.X11-unix/X0`
    (XWayland's socket) plus `DISPLAY` is enough for a container's X client to
    connect **once the host has allowed it** - see `_X_AUTH_HINT`.
    """
    if display in ("", "none"):
        return [], [], ""
    if display == "x11":
        if not os.path.isdir(_X11_SOCKET):
            return [], [], (
                "there is no X11 socket at /tmp/.X11-unix, so nothing on this "
                "machine can be given an X display to draw on.")
        host_display = os.environ.get("DISPLAY", "")
        if not host_display:
            return [], [], (
                "no DISPLAY is set for this session, so there is no X display "
                "to give the container.")
        extra = ["-v", f"{_X11_SOCKET}:{_X11_SOCKET}:ro"]
        env = [f"DISPLAY={host_display}", "GDK_BACKEND=x11",
               "QT_QPA_PLATFORM=xcb", "GSK_RENDERER=cairo",
               "LIBGL_ALWAYS_SOFTWARE=1"]
        if share_bus:
            # The two things measured to be needed before a GTK app in a
            # container is anything more than a picture:
            #
            # `--userns=keep-id` - the session bus and the accessibility bus
            # authenticate by *peer credentials*, and a rootless container's
            # process has a different host-side uid, so both refuse it until the
            # container runs as the host's own uid. Measured: without this the
            # a11y bus answers "Exhausted all available authentication
            # mechanisms (tried: EXTERNAL)"; with it, zero errors and the app
            # appears in the host's AT-SPI tree.
            #
            # a dbus session - a GTK4 app with no bus opens **no window at
            # all** while reporting nothing wrong (measured: gtk4-demo ran
            # silently for 45 seconds with no window until it was given a
            # session bus, which is what `shani-desktop-plasma`'s session test
            # does with `dbus-run-session`).
            extra.append("--userns=keep-id")
            bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
            if bus:
                env.append(f"DBUS_SESSION_BUS_ADDRESS={bus}")
            a11y = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "at-spi")
            if os.path.isdir(a11y):
                env.append(f"AT_SPI_BUS_ADDRESS=unix:path={a11y}/bus")
                extra += ["-v", f"{a11y}:{a11y}"]
        return extra, env, ""
    if display == "wayland":
        wayland = os.environ.get("WAYLAND_DISPLAY", "")
        runtime = os.environ.get("XDG_RUNTIME_DIR", "")
        if not (wayland and runtime) or not os.path.exists(
                os.path.join(runtime, wayland)):
            return [], [], (
                "this session has no Wayland socket to give a container, so "
                "there is no display to bind.")
        # The socket must sit at the same path inside the container, because a
        # Wayland client connects to $XDG_RUNTIME_DIR/$WAYLAND_DISPLAY and
        # nothing resolves the name for it.
        return (["-v", f"{runtime}:{runtime}:ro"],
                [f"WAYLAND_DISPLAY={wayland}", f"XDG_RUNTIME_DIR={runtime}",
                 "GDK_BACKEND=wayland"], "")
    return [], [], f"display must be none, x11 or wayland, not {display!r}."


#: What a person runs when X refuses the container, and why they must run it
#: themselves. Measured on this box: with the socket bound, a container's X
#: client is told *"Authorization required, but no authorization protocol
#: specified"*, and `xhost +local:` on the host makes the same call succeed.
#: Deliberately **not** run by the skill: it opens the host's X server to every
#: local process for as long as it is left on, which is a change to the person's
#: machine and theirs to make.
_X_AUTH_HINT = "run 'xhost +local:' on this machine to allow local connections"
_X11_SOCKET = "/tmp/.X11-unix"


def _do_run(arguments: dict) -> str:
    image = str(arguments.get("image") or "").strip()
    if not image:
        return "No image was named. Use the images action to see what is on disk."
    if not _IMAGE.match(image):
        return f"{image!r} is not a plausible image reference; refusing to pass it to podman."
    name = str(arguments.get("name") or "").strip()
    if name and not _NAME.match(name):
        return f"{name!r} is not a plausible container name (start with a letter or digit; no slashes)."
    command = arguments.get("command") or []
    if isinstance(command, str):
        # A model often writes one string. Splitting it into argv is what the
        # person meant; wrapping it whole would make podman try to exec a file
        # literally named "echo hello", which fails in a way that explains
        # nothing.
        command = shlex.split(command)
    if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
        return "command must be a list of strings, e.g. ['echo', 'hello']."
    publish = arguments.get("publish") or []
    if not isinstance(publish, list) or not all(
            isinstance(p, str) and _PUBLISH.match(p) for p in publish):
        return "publish must be a list of 'host:container' pairs, e.g. ['8080:80']."
    detach = bool(arguments.get("detach", False))

    argv = ["run"]
    if not detach:
        # A one-shot command is cleaned up when it exits; a detached container
        # is a service the person will stop by hand, so it stays.
        argv.append("--rm")
    if name:
        argv += ["--name", name]
    for port in publish:
        argv += ["-p", port]
    # The display is bound *before* the image, so a GUI app in the container
    # draws on this desktop rather than failing for want of a socket.
    display = str(arguments.get("display") or "none").strip().lower()
    binds, env, refusal = _display_setup(display, share_bus=bool(arguments.get("share_bus")))
    if refusal:
        return f"Not started: {refusal}"
    argv += binds
    for pair in env:
        argv += ["-e", pair]
    if detach:
        argv.append("-d")
    argv.append(image)
    argv += command

    proc, why = _podman(argv, keep_output=detach)
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        hint = ""
        if "authorization" in " ".join(detail).lower() or "display" in " ".join(detail).lower():
            hint = f" If it could not open the display, {_X_AUTH_HINT}."
        return (f"podman refused to run {image} (exit {proc.returncode}). "
                f"Nothing was started." + (f" podman said: {_detail(proc)}" if _detail(proc) else "")
                + hint)
    if not detach:
        out = (proc.stdout or "").strip()
        return f"Ran {image} to completion" + (f": {out}" if out else " (it printed nothing).")
    # A detached run prints the full container id, which is not the name the
    # person will use next time; resolve it so the reply names the container.
    cid = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
    shown = name or _resolve_name(cid)
    if not shown:
        return "Started the container in the background, but podman did not say which name."
    return f"Started {shown} from {image} in the background. Its logs are available with logs."


def _resolve_name(cid: str) -> str:
    if not cid:
        return ""
    proc, _ = _podman(["ps", "--filter", f"id={cid}", "--format", "{{.Names}}"], timeout=30)
    if proc is None or proc.returncode != 0:
        return ""
    names = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    return names[0] if names else ""


def _do_exec(arguments: dict) -> str:
    name = _name_or_all(arguments)
    if name in ("NO-NAME", "BAD-NAME"):
        return ("No running container was named" if name == "NO-NAME"
                else f"{arguments.get('name')!r} is not a plausible container name.")
    command = arguments.get("command") or []
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        return "command must be a non-empty list of strings, e.g. ['ls', '-la']."
    proc, why = _podman(["exec", name, *command], timeout=120)
    if proc is None:
        return why
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return (f"The command inside {name} exited {proc.returncode}, so it did not "
                f"succeed." + (f" It said: {err.splitlines()[-1]}" if err else ""))
    body = out or ("(it printed nothing)" if not err else err)
    return f"Ran the command inside {name}: {body}"


def _do_stop(arguments: dict) -> str:
    name = _name_or_all(arguments)
    argv = ["stop", "--time", "10"]
    if name is None:
        argv.append("--all")
        what = "the running containers"
    elif name == "NO-NAME":
        return "No container was named to stop."
    elif name == "BAD-NAME":
        return f"{arguments.get('name')!r} is not a plausible container name."
    else:
        argv.append(name)
        what = name
    proc, why = _podman(argv, timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"podman did not stop {what} (exit {proc.returncode}); it is unknown "
                f"whether it is still running."
                + (f" podman said: {_detail(proc)}" if _detail(proc) else ""))
    return f"Stopped {what}. The container still exists - remove it with the rm action."


def _do_rm(arguments: dict) -> str:
    name = _name_or_all(arguments)
    argv = ["rm", "--force"]
    if name is None:
        argv.append("--all")
        what = "every container"
    elif name == "NO-NAME":
        return "No container was named to remove."
    elif name == "BAD-NAME":
        return f"{arguments.get('name')!r} is not a plausible container name."
    else:
        argv.append(name)
        what = name
    proc, why = _podman(argv, timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"podman did not remove {what} (exit {proc.returncode})."
                + (f" podman said: {_detail(proc)}" if _detail(proc) else ""))
    return f"Removed {what}."


def _do_logs(arguments: dict) -> str:
    name = _name_or_all(arguments)
    if name in ("NO-NAME", "BAD-NAME", None):
        return "No container was named to read the logs of."
    tail = arguments.get("tail", 50)
    try:
        tail = max(1, min(int(tail), 2000))
    except (TypeError, ValueError):
        return "tail must be a whole number of lines."
    proc, why = _podman(["logs", "--tail", str(tail), name], timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"podman could not read {name}'s logs (exit {proc.returncode}); it may "
                f"never have run." + (f" podman said: {_detail(proc)}" if _detail(proc) else ""))
    body = (proc.stdout or proc.stderr or "").strip()
    return f"Last lines from {name}: {body}" if body else f"{name} has printed nothing."


def _do_list(arguments: dict) -> str:
    argv = ["ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"]
    if arguments.get("all"):
        argv.append("--all")
    proc, why = _podman(argv, timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"podman could not list containers (exit {proc.returncode}), so what is "
                f"running is unknown." + (f" podman said: {_detail(proc)}" if _detail(proc) else ""))
    rows = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not rows:
        return "No containers are running." if not arguments.get("all") else "No containers exist."
    out = [f"{len(rows)} container(s):"]
    for row in rows:
        cells = row.split("\t")
        while len(cells) < 3:
            cells.append("")
        out.append(f"  {cells[0]:<24} {cells[1]:<24} {cells[2]}")
    return "\n".join(out)


def _do_images(_arguments: dict) -> str:
    proc, why = _podman(["images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"], timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"podman could not list images (exit {proc.returncode})."
                + (f" podman said: {_detail(proc)}" if _detail(proc) else ""))
    rows = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    if not rows:
        return "No images are on this machine yet."
    return f"{len(rows)} image(s) on disk:\n" + "\n".join(f"  {row}" for row in rows)


def _do_pull(arguments: dict) -> str:
    image = str(arguments.get("image") or "").strip()
    if not image:
        return "No image was named to pull."
    if not _IMAGE.match(image):
        return f"{image!r} is not a plausible image reference."
    proc, why = _podman(["pull", image], timeout=_TIMEOUT)
    if proc is None:
        return why
    if proc.returncode != 0:
        return (f"podman could not pull {image} (exit {proc.returncode}); nothing new "
                f"was downloaded." + (f" podman said: {_detail(proc)}" if _detail(proc) else ""))
    return f"Pulled {image} onto this machine."


def _machinectl(argv: "list[str]", timeout: int):
    if shutil.which("machinectl") is None:
        return None, files.tool_missing("machinectl", "manage nspawn machines")
    try:
        return subprocess.run(["machinectl", *argv], capture_output=True, text=True,
                              timeout=timeout, check=False), ""
    except subprocess.TimeoutExpired:
        return None, f"machinectl did not answer within {timeout}s."
    except OSError as exc:
        return None, f"could not run machinectl: {exc}"


def _do_machines(_arguments: dict) -> str:
    proc, why = _machinectl(["list", "--no-legend"], timeout=30)
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return ("machinectl could not list machines (exit {proc.returncode}), so what "
                "exists is unknown." + (f" systemd said: {detail[-1]}" if detail else ""))
    rows = [line.split() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not rows:
        return "No machines are registered on this machine."
    out = [f"{len(rows)} machine(s):"]
    for cells in rows:
        out.append("  " + "  ".join(cells[:3]))
    return "\n".join(out)


def _do_machine_start(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named. Use the machines action to see what is registered."
    if not _NAME.match(name):
        return f"{name!r} is not a plausible machine name."
    proc, why = _machinectl(["start", name], timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (f"systemd did not start the machine {name} (exit {proc.returncode}), so "
                f"it is not running." + (f" systemd said: {detail[-1]}" if detail else ""))
    return f"Asked systemd to start the machine {name}; it reported success."


def _do_machine_stop(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "No machine was named to stop."
    if not _NAME.match(name):
        return f"{name!r} is not a plausible machine name."
    proc, why = _machinectl(["poweroff", name], timeout=60)
    if proc is None:
        return why
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return (f"systemd did not power off {name} (exit {proc.returncode}); it is "
                f"unknown whether it is still running."
                + (f" systemd said: {detail[-1]}" if detail else ""))
    return f"Asked the machine {name} to power off."


def _run(arguments: dict) -> str:
    action = str(arguments.get("action") or "list").strip().lower()
    if action not in _ACTIONS:
        return f"Action must be one of {', '.join(_ACTIONS)}, not {action!r}."

    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return f"Refusing: {reason}"

    handlers = {
        "run": _do_run,
        "exec": _do_exec,
        "stop": _do_stop,
        "rm": _do_rm,
        "logs": _do_logs,
        "list": _do_list,
        "images": _do_images,
        "pull": _do_pull,
        "machines": _do_machines,
        "machine-start": _do_machine_start,
        "machine-stop": _do_machine_stop,
    }
    return handlers[action](arguments)


def _post_condition(arguments: dict):
    """Read back the actions whose effect is observable: detached run, stop, rm."""
    action = str(arguments.get("action") or "").strip().lower()
    name = str(arguments.get("name") or "").strip()
    if action == "run" and not name:
        return None  # the detached name was generated by podman, so re-reading
        # it from here would need the id the run printed; the run itself
        # reports it.
    if action not in ("run", "stop", "rm") or not _NAME.match(name):
        return None
    running = _running_names(all_containers=(action == "rm"))
    if running is None:
        return None
    present = name in running
    if action == "run":
        return (present, f"{name} is {'running' if present else 'not running'}")
    if action == "stop":
        return (not present, f"{name} is {'still running' if present else 'stopped'}")
    return (not present, f"{name} is {'still there' if present else 'removed'}")


# Declared for `verification.verify`; the LLM never supplies this.
POST_CONDITION = _post_condition


SCHEMA = {
    "type": "function",
    "function": {
        "name": "container",
        "description": (
            "Run containers on this machine with podman (run an image, run a "
            "command inside a running container, stop it, read its logs, list "
            "what is running, list local images, pull one) and start/stop nspawn "
            "machines with machinectl. A container started with display=x11 draws "
            "on this desktop, and the ui_elements tool is what drives it by name "
            "with a visible cursor; this tool starts it, and stops it. Needs "
            "'container-control-enabled', off by default."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": f"One of: {', '.join(_ACTIONS)}. Defaults to list.",
                },
                "image": {
                    "type": "string",
                    "description": "Image reference for run/pull, e.g. 'alpine' or 'docker.io/library/nginx:alpine'.",
                },
                "name": {
                    "type": "string",
                    "description": "Container or machine name, e.g. 'web'.",
                },
                "command": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command for run/exec as a list of strings, e.g. ['echo','hello'].",
                },
                "detach": {
                    "type": "boolean",
                    "description": "For run: start it in the background instead of waiting for the command to finish.",
                },
                "display": {
                    "type": "string",
                    "description": "For run: none (default), x11 or wayland - bind this desktop's display into the container so a GUI app in it draws on the screen you can see. X11 is the one that can be driven by pointer and keyboard.",
                },
                "share_bus": {
                    "type": "boolean",
                    "description": "For run with a display: also share this desktop's session and accessibility buses, which is what turns a container's GTK app into something Chronoa can drive by name and role instead of by clicking coordinates. Requires a GUI app, and the container runs as your own user for it to work.",
                },
                "publish": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "For run: ports to publish, as 'host:container' pairs, e.g. ['8080:80'].",
                },
                "all": {
                    "type": "boolean",
                    "description": "For stop/rm/list: act on every container rather than a named one.",
                },
                "tail": {
                    "type": "integer",
                    "description": "For logs: how many lines to show (default 50).",
                },
            },
            "required": ["action"],
        },
    },
}


SKILLS = [Skill(name="container", schema=SCHEMA, run=_run)]
