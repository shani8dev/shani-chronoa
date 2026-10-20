"""Mutation set for the phone keyboard being opt-in.

**`phone-remote-enabled` is off by default.** While this computer
advertises "Chronoa Remote", the Find Me alert is registered as a plain
write-without-response characteristic with no encryption flag - so a device
nearby that has not paired with it can write the alert level and make
this computer ring. A key that advertises a keyboard is worth having; a
key that lets anything nearby ring the machine is not something to hand
out without being asked.

The set is the two ways that decision can be undone: the default
itself, and the sentence that tells a person what the default is.

**A mutation of the XML leaves the shipped `gschemas.compiled` stale
against it**, which is exactly the silent failure the AGENTS.md records -
every setting then takes its Python default - and the guard in
`tests/test_the_shipped_schema_matches_its_source.py` is what says so.
"""

TEST_FILE = "tests/test_the_shipped_schema_matches_its_source.py"

_SCHEMA = "usr/share/glib-2.0/schemas/org.shani.chronoa.gschema.xml"

MUTATIONS = [
    {
        "file": _SCHEMA,
        "old": '    <key name="phone-remote-enabled" type="b">\n      <default>false</default>\n',
        "new": '    <key name="phone-remote-enabled" type="b">\n      <default>true</default>\n',
        # The shipped default. A device nearby that has not paired can
        # make this computer ring while this computer advertises, and
        # nobody asked for that.
        "label": "the phone keyboard advertises by default again",
        "expect": ["test_the_default_is_off"],
    },
    {
        "file": _SCHEMA,
        "old": "Off by default, and the reason is measured rather than assumed:",
        "new": "On by default, and the reason is measured rather than assumed:",
        # **A description that justifies a default the schema no longer
        # has is a stale claim** - and it is the sentence a person reads
        # to decide whether the switch is on.
        "label": "the description says the switch is on by default",
        "expect": ["test_the_description_does_not_claim_it_is_on"],
    },
]
