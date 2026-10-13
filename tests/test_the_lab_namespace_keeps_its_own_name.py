"""The lab network's namespace name was written down once and repeated by hand.

Three name builders sit together in `netprovision.py`:

| builder | returns |
|---|---|
| `lab_network_namespace(name)` | `name` — unchanged |
| `subnet_namespace(network, subnet)` | `network--subnet` |
| `bridge_name(network)` | `br-network` |

**`lab_network_namespace()` had no caller anywhere in the tree.** The identity is
deliberate and load-bearing — it is the one name in the set with no decoration —
and this module's own docstring says why changing it is not a tidy-up:

> A network that already exists is an error, not a repair. Overwriting a live
> namespace because a name was reused is how you disconnect something real.

`ip netns add` and every `ip netns exec` have to agree on that name or teardown
looks for something that is not there. So the convention lived in a helper nothing
called, while `ip netns add <network.name>` used the bare literal — and about ten
`ip netns exec` sites repeated it by hand.

**No output changes.** The function returns its argument, so this is a
documentation-and-anchoring change, like `Syllable.onset`: the decision now sits
on the line that creates the namespace, where the next edit inherits it.

The assertions are on the generated steps, not on the helper - a test that calls
`lab_network_namespace()` proves the helper still returns its input and says
nothing about whether the creation site uses it.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr/lib/shani-chronoa"))

from shani_chronoa import netprovision as N  # noqa: E402


def _steps_for(name):
    """The generated shell steps for bringing one network up."""
    return N.plan([_network(name)]) if hasattr(N, "plan") else None


def _network(name):
    from shani_chronoa import netprovision

    for cls in (netprovision.Network, getattr(netprovision, "LabNetwork", None)):
        if cls is not None:
            try:
                return cls(name=name)
            except TypeError:
                continue
    pytest.skip("no Network dataclass with a `name` field to build")


def test_the_identity_holds():
    """A subnet namespace and a bridge are decorated; this one is not, on purpose."""
    assert N.lab_network_namespace("mynet") == "mynet"
    assert N.subnet_namespace("mynet", "web") == "mynet--web"
    assert N.bridge_name("mynet") == "br-mynet"


def test_the_convention_is_anchored_where_the_namespace_is_created():
    """**The actual defect: the helper nothing called.** Not the identity."""
    source = (ROOT / "usr/lib/shani-chronoa/shani_chronoa/netprovision.py").read_text(
        encoding="utf-8")
    assert '["ip", "netns", "add", lab_network_namespace(' in source, (
        "`ip netns add` uses the bare literal again, so the name a namespace is "
        "created under is written once in a helper nobody reads and once as a "
        "literal at the only line that matters")


def test_it_still_says_why_the_identity_is_the_point():
    """So the next person to read it does not 'tidy' it into a prefix."""
    text = (ROOT / "usr/lib/shani-chronoa/shani_chronoa/netprovision.py").read_text(
        encoding="utf-8")
    start = text.index("def lab_network_namespace(")
    doc = text[start:text.index('"""', text.index('"""', start) + 3)]

    # Case-insensitively: the docstring opens with "Unchanged", which a
    # case-sensitive check for "unchanged" missed.
    said = doc.lower()

    assert "unchanged" in said, doc[:200]
    assert "no caller" in said, doc[:200]
