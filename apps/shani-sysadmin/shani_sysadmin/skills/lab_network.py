"""Skills: build an AWS-shaped network out of real Linux network namespaces.

A network is one namespace holding a bridge with forwarding on. A subnet is its own
namespace with a veth to that bridge, holding a gateway address and an instance
address. **A public subnet gets a default route out through an nftables
masquerade; a private one gets no default route at all** - so it can reach the
network and nothing beyond. That single difference is AWS's public/private split,
and here it is a real route table you can read back rather than a tag.

Everything is RFC1918 and stays inside kernel namespaces on this machine. No
cloud account, no credentials, no egress. The one thing that can reach the
host's uplink is the NAT rule, and only for public subnets.

**Nothing happens without `apply=true`.** A call with `apply` absent or false
returns the exact plan - every command, in order - and changes nothing. This is
the default because the failure this tool could cause is not a bad answer but a
half-built network on the user's machine, and a plan a person can read before
saying yes is the cheapest guard there is.

**The privilege split is the security boundary, and it is worth stating plainly.**
Creating a namespace needs `CAP_NET_ADMIN`, so the commands run as root through
`pkexec`. The only thing on that command line is a *path*:

    pkexec /usr/bin/shani-chronoa-lab-network apply /path/to/plan.json

The request an LLM wrote is never a command line. It is validated here, turned
into argv arrays by `netprovision`, written to a 0600 `O_EXCL` file, and the
root helper then **re-derives the whole plan from the request inside that file
and refuses it if a single step differs**. So an edited plan file cannot become
an arbitrary root command, and there is no shell anywhere in the path.

**Honesty rules, each of which is a way to be confidently wrong:**

- **A plan that fails part-way reports failure and records nothing.** The
  namespaces it did create are named, so they can be removed. Reporting a
  partial build as a built network is the one outcome worth refusing.
- **"Reachable" is measured by actually reaching it, not by a route existing.**
  `lab_network_status` pings from inside the namespaces, so a NAT rule that did not take
  shows up as unreachable instead of as a healthy-looking route table.
- **A private subnet having no route out is the design, not a fault**, and it is
  reported as such - otherwise "why can't my private subnet reach anything" reads
  as a broken network rather than the thing that was asked for.
- **Namespaces this tool did not build are never removed by it**, even when they
  look like network leftovers. `destroy-all` only touches what it recorded.
- **Creating a network needs root; destroying one the tool did not build is not
  something it will do at all**, and says so instead of failing obscurely.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from shani_chronoa import files
from shani_chronoa.config import ChronoaConfig
from shani_chronoa.netprovision import Rejected, build_plan, build_teardown
from shani_chronoa.netprovision import load_record, make_plan, parse_request
from shani_chronoa.netprovision import record_path, render_plan, write_envelope
from shani_chronoa.skills import Skill

#: A new switch, because this is its own kind of thing: it changes the machine's
#: network configuration as root. It is not any of the existing keys, and it is
#: not `service-control-enabled`, because that one is about systemd units and a
#: person who allowed it has not agreed to have namespaces appear.
CONSENT_KEY = "network-provision-enabled"

_HELPER = "shani-chronoa-lab-network"
_APPLY_TIMEOUT = 240


def _refuse(reason: str) -> str:
    return (f"Not done: {reason} Chronoa can build and remove lab networks on "
            f"this machine, but needs you to allow it first.")


def _consent(config: ChronoaConfig) -> tuple[bool, str]:
    """Return (allowed, reason).

    Takes `config` and returns a pair because that is the shape every other
    gated skill in this package uses, and
    `test_question_presenter.py::test_every_gated_tool_names_its_consent_key_with_nobody_to_ask`
    walks them all through exactly that signature - calling
    `_consent(config)` and requiring the refusal to name the consent key. An
    earlier zero-argument version returning `str | None` satisfied its own call
    sites and broke every other gated skill's probe with
    `TypeError: _consent() takes 0 positional arguments but 1 was given`.
    """
    if not config.get_bool(CONSENT_KEY, False):
        return False, (
            f"creating or removing networks is switched off ('{CONSENT_KEY}'). "
            f"Turn on 'Let Chronoa build lab networks' in Settings to allow it.")
    return True, ""


def _helper_missing() -> str | None:
    return None if shutil.which(_HELPER) else files.tool_missing(
        _HELPER, "build a lab network")


def _root_precondition() -> str | None:
    """Why the privileged step cannot run here, or None if it can.

    Two separate reasons, deliberately not merged: the helper being absent is a
    packaging problem, and this process already being root is a design fact -
    the skill is a subprocess of a chat turn and must never itself be root. The
    helper is what elevates, through pkexec, so that the turn which asked for a
    network never does.
    """
    missing = _helper_missing()
    if missing:
        return missing
    if os.geteuid() == 0:
        return (
            "Refusing to run: this skill is already root, and it is not supposed "
            "to be. The helper runs as root through pkexec so the boundary is the "
            "helper, not the chat turn. Running both in one process removes it."
        )
    return None


def _request_from_record(name: str, entry: dict) -> dict:
    """The request that rebuilds a recorded network, for a plan or a teardown.

    **Every field the record holds is passed through, and dropping one is not a
    simplification.** An earlier version of `destroy`'s plan path rebuilt this
    by hand and omitted `uplink`, which the record has carried since the helper
    began writing it (`usr/bin/shani-chronoa-lab-network:186`). `parse_request`
    then rejected the network - *"nat is on but no uplink was given"* - so
    **`destroy` without `apply` refused to show a plan for any network created
    with NAT on**, which is the default. Measured on this machine against a real
    interface:

        helper's shape (uplink present) -> parses OK
        destroy's shape (uplink DROPPED) -> REJECTED: nat is on but no uplink

    The `apply` path never noticed, because it hands the name to the helper,
    which does its own rebuild from the same record. So the two halves of one
    call disagreed about what the record means, and only the read-only half was
    broken - which is why it survived: the destructive path is the one anybody
    notices.

    `usr/bin/shani-chronoa-lab-network`'s `_recorded_network` builds the same
    shape. Two copies of this is the duplication that produced the bug, so it is
    named here and both are expected to keep agreeing; a test asserts the field
    set, because a *missing* field cannot fail an equality check written against
    a list that already omits it.
    """
    return {
        "name": name,
        "cidr": entry.get("cidr"),
        "nat": bool(entry.get("nat", True)),
        "uplink": entry.get("uplink") or "",
        "subnets": entry.get("subnets") or [],
    }


def _subnet_arguments(arguments: dict):
    """The subnets a caller asked for.

    A list of objects is the schema's shape. A string is also accepted, because
    `name=cidr[:public]` per line is how a person says it out loud and it is
    what a small model most often produces - refusing it would produce a
    confusing "not built" rather than a plan.
    """
    raw = arguments.get("subnets")
    if not isinstance(raw, str):
        return raw
    parsed = []
    for line in raw.replace(",", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, cidr = line.partition("=")
        if not cidr:
            raise Rejected(
                f"{line!r} is not a subnet. Write it as name=cidr, e.g. "
                f"'public=10.77.1.0/24', or add ':private' for one with no route "
                f"out: 'private=10.77.2.0/24:private'.")
        tail, marker, _ = cidr.rpartition(":")
        public = marker != "private" if marker else True
        parsed.append({"name": name.strip(), "cidr": (tail if marker else cidr).strip(),
                       "public": public})
    return parsed


def _request_from(arguments: dict) -> dict:
    request = {
        "name": arguments.get("name"),
        "cidr": arguments.get("cidr"),
        "nat": arguments.get("nat", True),
        "uplink": arguments.get("uplink") or "",
        "subnets": _subnet_arguments(arguments),
    }
    if arguments.get("internet") is False:
        request["nat"] = False
    return request


def _invoke(subcommand: str, path: str = "") -> tuple:
    """Run the helper through pkexec. Returns (returncode, output)."""
    argv = ["pkexec", shutil.which(_HELPER) or _HELPER, subcommand]
    if path:
        argv.append(path)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_APPLY_TIMEOUT, check=False)
    except FileNotFoundError:
        return 127, ("pkexec is not installed, so the privileged step could not "
                     "be run. On Arch it comes from the `polkit` package.")
    except subprocess.TimeoutExpired:
        return 124, (f"The network change did not finish within {_APPLY_TIMEOUT}s. "
                     f"Whether it took effect is unknown - run `lab_network_list` to see.")
    except OSError as exc:
        return 126, f"The privileged step could not be started: {exc}."
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _lab_network_list(_arguments: dict) -> str:
    record = load_record()
    if not record:
        return (
            "No lab networks have been built on this machine. `lab_network_create` "
            "builds one from a name, a CIDR and its subnets."
        )
    lines = [f"{len(record)} lab network(s) built:"]
    for name in sorted(record):
        entry = record[name]
        lines.append(f"  network {name}  {entry.get('cidr')}  NAT "
                     f"{'on' if entry.get('nat') else 'off'}  created "
                     f"{entry.get('created', 'at an unrecorded time')}")
        for subnet in entry.get("subnets") or []:
            lines.append(
                f"      {subnet.get('name')}  {subnet.get('cidr')}  "
                f"{'public' if subnet.get('public') else 'private'}  "
                f"instance {subnet.get('instance')}")
    lines.append(" `lab_network_status` says what each one can actually reach.")
    return "\n".join(lines)


def _lab_network_create(arguments: dict) -> str:
    allowed, blocked = _consent(ChronoaConfig())
    if not allowed:
        return blocked

    try:
        network = parse_request(_request_from(arguments))
        steps = build_plan(network)
    except Rejected as exc:
        return f"Not built: {exc}"

    # Consent and validation are enough for a plan. The helper is only needed
    # once something is actually going to be run, so a missing one must not stop
    # somebody reading what would happen.
    if not arguments.get("apply"):
        return "\n".join([
            render_plan(network, steps, f"Plan for network {network.name} - nothing has been changed."),
            "",
            "This has changed nothing. Pass apply=true to build it; that needs "
            "your system password, because it creates network namespaces as root."])

    blocked = _root_precondition()
    if blocked:
        return blocked
    if network.name in load_record():
        return (f"Not built: a network called {network.name!r} already exists here. "
                f"Refusing rather than replacing it - use a different name, or "
                f"`lab_network_destroy` first.")

    path = write_envelope(make_plan(network, "create", steps))
    try:
        code, output = _invoke("apply", str(path))
    finally:
        # The plan is consumed either way; a plan file left behind is a root
        # action sitting on disk waiting for something to find it.
        try:
            os.unlink(path)
        except OSError:
            pass
    if code != 0:
        return ("Not built. " + (output.strip() or f"the helper exited {code}."))
    return output.strip()


def _lab_network_destroy(arguments: dict) -> str:
    allowed, reason = _consent(ChronoaConfig())
    if not allowed:
        return reason
    # **This used to be one line, and it refused every call.**
    # `blocked = (reason if not allowed else _refuse("")) or _root_paths("")`:
    # with consent *granted*, `reason` is "" and the `or` moved on to
    # `_refuse("")`, which returns a truthy string - so `destroy` returned
    # "needs you to allow it first" to a person who had just allowed it, and
    # never reached the name check, the record lookup or the teardown at all.
    #
    # The undefined `_root_paths` behind it hid the real fault: the truthy
    # `_refuse("")` short-circuited the `or` every time, so pyflakes' report of
    # an undefined name here was true and unreachable at the same moment. It is
    # `_root_precondition()` - the helper this module already defines and
    # `create` does not need because the helper refuses for itself.
    blocked = _root_precondition()
    if blocked:
        return blocked
    name = str(arguments.get("name") or "").strip()
    if not name:
        return "Which network? Give the name."
    if name not in load_record():
        known = ", ".join(sorted(load_record())) or "none"
        return (f"No network called {name!r} is recorded, so nothing was removed. "
                f"Recorded lab networks: {known}. A namespace this tool did not build is "
                f"never removed by it.")
    if not arguments.get("apply"):
        try:
            network = parse_request(_request_from_record(name, load_record()[name]))
            steps = build_teardown(network)
        except Rejected as exc:
            return (f"Not removed: the record for {name!r} no longer validates "
                    f"({exc}). Refusing rather than deleting something whose "
                    f"shape I cannot confirm.")
        return "\n".join([
            render_plan(network, steps, f"Plan to remove network {name} - nothing changed:"),
            "", "Pass apply=true to remove it."])

    code, output = _invoke("destroy", name)
    if code != 0:
        return "Not removed. " + (output.strip() or f"the helper exited {code}.")
    return output.strip()


def _reach(ns: str, target: str, count: int = 2) -> str:
    """Can this namespace actually reach `target`? Measured, not inferred."""
    if shutil.which("ping") is None:
        return "unknown (ping is not installed)"
    argv = ["ip", "netns", "exec", ns, "ping", "-c", str(count), "-W", "2",
            "-n", target]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown (ping could not be run)"
    if proc.returncode == 0:
        return "reachable"
    if "unreachable" in (proc.stdout + proc.stderr).lower():
        return "no route"
    return "did not answer"


def _lab_network_status(arguments: dict) -> str:
    name = str(arguments.get("name") or "").strip()
    record = load_record()
    if not record:
        return "No lab networks have been built on this machine."
    if not name:
        return _lab_network_list(arguments)
    entry = record.get(name)
    if entry is None:
        return (f"No network called {name!r} is recorded. Recorded: "
                f"{', '.join(sorted(record)) or 'none'}.")

    out = [describe_actual(name, entry)]
    if _helper_missing():
        out.append("I could not check what it can reach, because the network "
                   "helper is not installed.")
        return "\n".join(out)

    out.append("Measured, not inferred - each line is a real ping from inside "
               "that namespace:")
    for subnet in entry.get("subnets") or []:
        ns = f"{name}--{subnet.get('name')}"
        inside = _reach(ns, str(subnet.get("instance")))
        beyond = _reach(ns, str(entry.get("cidr", "")).split("/")[0])
        out.append(f"  {subnet.get('name')} ({'public' if subnet.get('public') else 'private'})"
                   f" in {ns}: can reach its own instance ({inside}), "
                   f"and the network's gateway address ({beyond}).")
        if not subnet.get("public"):
            out.append("    No route out is installed, so anything beyond the network "
                       "is unreachable - that is what private means here, not a fault.")
        else:
            outside = _reach(ns, "1.1.1.1")
            out.append(f"    Beyond this network, out through the uplink: {outside}.")
    return "\n".join(out)


def describe_actual(name: str, entry: dict) -> str:
    subnets = entry.get("subnets") or []
    return (f"network {name}  {entry.get('cidr')}  NAT "
            f"{'enabled' if entry.get('nat') else 'disabled'}  "
            f"{len(subnets)} subnet(s)  created {entry.get('created', 'at an unrecorded time')}")


def _state_path() -> str:
    """Where this module's record lives.

    It called a bare `state_dir()`, which is never imported here - a
    `NameError` on every call. Nothing calls this function today, so the
    fault was invisible; it is fixed rather than deleted because the record's
    location is `netprovision`'s business and duplicating the path is how the
    two drifted apart in the first place.
    """
    return str(record_path())


SCHEMAS = [
    ("lab_network_list", "List the isolated lab networks built on this machine, "
                 "with their subnets. Read-only.",
     {"type": "object", "properties": {}}),
    ("lab_network_create", "Build an isolated lab network on this machine: a "
                   "network namespace holding a bridge, with named subnets in "
                   "namespaces of their own. A public subnet can reach the "
                   "internet through the uplink; a private one can reach this "
                   "network and nothing beyond it. Returns the exact plan and "
                   "changes nothing unless apply=true, which needs your system "
                   "password.",
     {"type": "object", "properties": {
         "name": {"type": "string", "description": "Short name, lower-case letters, digits and dashes, e.g. 'lab1'."},
         "cidr": {"type": "string", "description": "The network's private address range, e.g. '10.77.0.0/16'."},
         "subnets": {"type": "array", "description": "The subnets inside it.",
                     "items": {"type": "object", "properties": {
                         "name": {"type": "string"},
                         "cidr": {"type": "string"},
                         "public": {"type": "boolean", "description": "True to give it a route out through the uplink."}}}},
         "internet": {"type": "boolean", "description": "False to build with no uplink at all, so nothing can leave."},
         "apply": {"type": "boolean", "description": "Actually build it. Without this, only the plan is returned and nothing changes."}},
      "required": ["name", "cidr", "subnets"]}),
    ("lab_network_destroy", "Remove a lab network built by lab_network_create, and "
                    "everything in it. Only ever removes networks this tool "
                    "built. Returns the plan unless apply=true.",
     {"type": "object", "properties": {
         "name": {"type": "string", "description": "The network's name."},
         "apply": {"type": "boolean", "description": "Actually remove it. Without this, only the plan is returned."}},
      "required": ["name"]}),
    ("lab_network_status", "What a lab network can actually reach: a real ping "
                   "from inside each subnet, not just what the routing table "
                   "says.",
     {"type": "object", "properties": {
         "name": {"type": "string", "description": "The network's name. Omit to list them all."}},
      "required": []}),
]

SKILLS = [
    Skill(name="lab_network_list", run=_lab_network_list,
          schema={"type": "function", "function": {"name": "lab_network_list", "description": SCHEMAS[0][1], "parameters": SCHEMAS[0][2]}}),
    Skill(name="lab_network_create", run=_lab_network_create,
          schema={"type": "function", "function": {"name": "lab_network_create", "description": SCHEMAS[1][1], "parameters": SCHEMAS[1][2]}}),
    Skill(name="lab_network_destroy", run=_lab_network_destroy,
          schema={"type": "function", "function": {"name": "lab_network_destroy", "description": SCHEMAS[2][1], "parameters": SCHEMAS[2][2]}}),
    Skill(name="lab_network_status", run=_lab_network_status,
          schema={"type": "function", "function": {"name": "lab_network_status", "description": SCHEMAS[3][1], "parameters": SCHEMAS[3][2]}}),
]
