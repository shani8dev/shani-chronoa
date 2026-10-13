"""`lan_link.py` drove the KDE Connect handshake twice, and one copy was dead.

An earlier version carried `identity_exchange()` - **119 lines** of hand-written
TCP + TLS + identity exchange, driving TLS over a `ssl.MemoryBIO` exactly as
`Link.connect()` does, both with `server_side=True`. It had **no caller and no
test**: the only mention anywhere was prose in a retired test file.

It was removed rather than given a caller, and this file holds that line.

## Why removal, not wiring

The one thing that made it look like a missing capability is in its docstring:
*"the capability list the phone advertises is real and is returned by
`identity_exchange`."* That is true — and irrelevant, because **discovery already
carries the capability list, with no handshake at all.**

The official KDE Connect field order puts `incomingCapabilities` and
`outgoingCapabilities` in the **plaintext** identity, `identity_packet` emits
them, and `parse_identity` returns them. `tests/test_lan_link.py` asserts
`"kdeconnect.battery" in peer["incomingCapabilities"]` on a discovery datagram.
So the advertised capabilities were always one `rglob` away from being readable.

And `Link` subsumes the rest. It keeps the peer's **whole** identity body in
`_peer_reply_body` (an earlier reading of `connect()` suggested it kept only
`deviceId` — it does not), and it carries the `expect_peer` pinning defence that
the standalone copy had no way to express:

> "every later connection must match the pinned fingerprint exactly; on mismatch,
> hard-fail"

## Why two copies were the real hazard

This module's own docstring says the handshake's non-obvious parts were **"got
wrong first"** — the roles are inverted from the obvious reading, and using
`wrap_socket` instead of a memory BIO consumes the ClientHello. That knowledge is
expensive to acquire and trivially cheap to lose. Two hand-written copies drift:
one gets fixed, the other does not, and the broken one is the one nothing calls.
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import lan_link  # noqa: E402

SOURCE = ROOT / "usr/lib/shani-chronoa/shani_chronoa/lan_link.py"


def _bio_sites():
    """Every function that constructs a `ssl.MemoryBIO` - i.e. hand-drives TLS.

    **Module level and methods, kept apart.** `Link.connect` is a method, and
    treating it as a public *module* function made the first version of this test
    fail against correct code: `connect` does not start with an underscore, but it
    is not something a caller reaches without constructing a `Link` first.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    module_level = [n for n in tree.body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def builds(node):
        return any(isinstance(x, ast.Call)
                   and getattr(getattr(x, "func", None), "attr", "") == "MemoryBIO"
                   for x in ast.walk(node))

    return [("function", n.name) for n in module_level if builds(n)] + \
           [("method", f"{c.name}.{m.name}")
            for c in tree.body if isinstance(c, ast.ClassDef)
            for m in c.body
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and builds(m)]


def test_no_public_function_drives_a_handshake():
    """**The public surface drives none.** `Link` is the only way in.

    Counting the two *sites* in the module and demanding one was my first
    version, and it failed against correct code: there are two, and the second is
    `_exchange`, a private helper whose own docstring says it exists so that the
    first plugin packet is a one-liner rather than a re-derivation of the TLS
    dance — and that "none of them have been written yet". That one is a declared
    template and stays.

    What must not come back is a *public* function doing the same job a second
    way. `identity_exchange` was exactly that, and it had no caller.
    """
    public = [n for kind, n in _bio_sites() if kind == "function"
              and not n.startswith("_")]
    assert public == [], (
        f"public function(s) {public} hand-drive a TLS handshake; Link already "
        f"does this, keeps the whole peer identity, and carries the expect_peer "
        f"pinning defence a standalone copy has no way to express")
    assert not hasattr(lan_link, "identity_exchange"), (
        "the duplicate handshake is back; if it has a caller now, that caller "
        "needs a reason to exist rather than the capabilities")


def test_every_handshake_site_is_accounted_for():
    """So the assertion above cannot be satisfied by accident as the module grows.

    Two sites, both named: the live one and the documented template. A third would
    be a new copy of a handshake whose roles this module's docstring records were
    "got wrong first".
    """
    assert sorted(_bio_sites()) == [("function", "_exchange"),
                                   ("method", "Link.connect")], _bio_sites()


def test_the_removed_function_is_really_gone():
    """Not merely uncalled - gone, so it cannot be found and used by accident."""
    names = [n.name for n in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8")))
             if isinstance(n, ast.FunctionDef)]
    assert "identity_exchange" not in names, names


def test_capabilities_reach_the_caller_without_a_handshake():
    """**Why the removal was safe.** The capability list the deleted function was
    famous for is in the plaintext discovery datagram.

    This runs `parse_identity` over a datagram `identity_packet` built - the same
    path discovery uses, no socket and no TLS.
    """
    packet = lan_link.identity_packet(name="a phone", device_type="phone")
    import json

    parsed = lan_link.parse_identity((json.dumps(packet) + "\n").encode())

    assert parsed is not None, "a well-formed identity was refused"
    for field in ("incomingCapabilities", "outgoingCapabilities"):
        assert field in parsed, f"{field} missing from the discovery identity: {parsed}"


def test_the_real_test_still_reads_capabilities_out_of_one():
    """The claim above is not re-derived here - it is the control that says the
    thing it replaced really did answer that question."""
    real = (ROOT / "tests/test_lan_link.py").read_text(encoding="utf-8")
    assert 'incomingCapabilities' in real and "kdeconnect.battery" in real, (
        "tests/test_lan_link.py no longer asserts a capability out of an "
        "identity; this file's central claim would be resting on nothing")
