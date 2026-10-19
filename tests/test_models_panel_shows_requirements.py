"""The Models panel says what each tier needs from *this* machine.

The request behind this was "show cpu + ram + gpu/vram per model, not just
size", and the reason it was not already there is in the code: `_cards()` read
`for spec, label, _ram in local_llm.TIERS:` and threw the RAM figure away, and
nothing else from `local_llm.REQUIRES` reached a card. So a person choosing a
model saw a download size and nothing about whether their machine could use it.

**Every number on the card is read from `local_llm`, never written here.**
That is the whole claim of the test: a card is rendered from the same table and
the same functions that choose the default, so the panel cannot say a tier is
fine while the code refuses it.

Measured against the tier table on this box (i7-1165G7, 4 cores, no GPU), the
panel renders:

    Medium (1.1 GB)      Needs 2 CPU cores, reads 1.1 GB per token, 9.8 tok/s here.
                         Fast enough to be the default on this machine.
    Very large (4.7 GB)  Needs 16 GB RAM, 2 CPU cores, reads 5.0 GB per token,
                         2.2 tok/s here. Not the default here: 5 tok/s (this
                         machine decodes at 2.2)
    Mixture of experts   Needs 20 GB RAM, 4 CPU cores, reads 2.2 GB per token,
                         about 4.9 tok/s here. Not the default here: a measured
                         decode rate (this tier's bytes-per-token is estimated,
                         not measured)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "usr" / "lib" / "shani-chronoa"))

from shani_chronoa import local_llm  # noqa: E402
from shani_chronoa.gui.surfaces import models as surface  # noqa: E402


def _llm_cards():
    return [c for c in surface._cards() if c["kind"] == "llm"]


#: **This whole file was machine-dependent, and failed on any box that had not
#: been benchmarked.** `_requirement_text` is driven by `local_llm
#: .expected_decode_tps(key)`, which returns nothing when this machine has no
#: recorded decode rate. With no `tps` the card omits the tok/s clause, and with
#: no rate the "missing" reason is set, so the card says
#:
#:     Needs 2 CPU cores, reads 1.1 GB per token. Not the default here: a
#:     measured decode rate (no local measurement)
#:
#: - which is **correct, and says why**. Three assertions here wanted
#: `"tok/s here"` and `"default on this machine"`, and failed on a box that had
#: simply never been measured. The test and the code were committed together in
#: 43687a1, so it never passed anywhere.
#:
#: The fix is to pin the input rather than skip the output: the claim under test
#: is that a card names its units, qualifies an estimate, and says why a tier is
#: not the default - not what this particular laptop decodes at. `PINNED_TPS`
#: makes the first two decidable everywhere.
PINNED_TPS = 9.8


@pytest.fixture(autouse=True)
def _pinned_decode_rate(monkeypatch):
    """Give the panel a decode rate, so the card's wording is what is measured.

    The stub takes ``*args`` rather than naming one parameter. The panel now
    passes the machine's measured bandwidth through - one figure for the whole
    card list instead of an `/props` round trip per card - so a stub written
    against the old one-argument call raised `TypeError` on every card. A stand-in
    shaped like the call rather than like the thing being called is this
    repository's own recorded failure (`meeting.notes`'s `_Engine`, written to
    match a broken signature).
    """
    monkeypatch.setattr(local_llm, "expected_decode_tps",
                        lambda *a, **k: PINNED_TPS, raising=True)


def _card(key):
    for card in _llm_cards():
        if card["key"] == key:
            return card
    raise AssertionError(f"no LLM card for {key!r}")


class TestEveryCardStatesWhatItNeeds:
    def test_every_llm_card_carries_a_requirements_row(self):
        """A tier with no row in `REQUIRES` would render a card that says
        nothing about the machine - which is the shape the request was about."""
        cards = _llm_cards()
        assert cards, "the panel has no LLM cards at all"
        for card in cards:
            assert card.get("requires"), f"{card['key']} has no requirements row"

    def test_the_row_names_ram_cpu_and_what_one_token_reads(self):
        for card in _llm_cards():
            req = card["requires"]
            assert "ram" in req and "cpu" in req and "reads" in req, (
                f"{card['key']}: {sorted(req)}")
            assert req["reads"] > 0, f"{card['key']} reads nothing per token"

    def test_a_moe_tier_reads_less_than_it_stores(self):
        """**The fact that makes a big tier worth considering at all.** The 30B
        stores 17.3 GB and activates a fraction of it, so one token reads ~2.2 GB
        - less than the 8B's 4.7 GB. A card that printed only the download size
        would rank them the wrong way round."""
        moe = _card("qwen3-30b-a3b")
        dense = _card("qwen3-8b")
        assert moe["requires"]["reads"] < dense["requires"]["reads"]
        assert moe["requires"]["reads"] < moe["size"]

    def test_the_card_text_names_the_units_and_the_shortfall(self):
        """The units, and - when a tier is refused - why.

        **The refusal half is machine-dependent, twice over, and was red from
        the commit that introduced it.** It picked `qwen3-14b` and assumed this
        machine refuses it; on a box with enough RAM and cores it does not, so
        there is no shortfall to name. Pinning the decode rate cannot fix that
        either - it is RAM and cores that decide, not speed.

        So the refused tier is *found* rather than assumed, and the test says
        plainly that it had nothing to check when none exists. An assertion with
        no subject is not a control; skipping with the reason is what makes the
        green meaningful.
        """
        refused = [c for c in _llm_cards() if c["requires"]["missing"]]
        if not refused:
            pytest.skip(
                "no tier is refused on this machine, so there is no shortfall "
                "for a card to name - every tier meets its RAM, core and speed "
                "requirements. The units below are still asserted; run this on a "
                "machine short of RAM to cover the refusal half.")

        text = surface._requirement_text(refused[0])
        assert "GB RAM" in text or "CPU cores" in text
        assert "per token" in text
        assert "tok/s here" in text
        assert "Not the default here:" in text, (
            f"a refused tier must say why on the card, not just omit a check "
            f"mark. The card reads: {text!r}")

    def test_a_usable_tier_says_so_rather_than_saying_nothing(self):
        """The other direction: silence reads as a defect, and a panel where
        every line ends in a complaint trains people to ignore them."""
        text = surface._requirement_text(_card("qwen3-1.7b"))
        assert "default on this machine" in text
        assert "Not the default" not in text

    def test_the_shortfall_is_the_number_not_a_verdict(self):
        """`requirements_met`'s own reasons, so the card cannot disagree with the
        code that picks the default."""
        met, missing = local_llm.requirements_met("qwen3-30b-a3b")
        assert not met
        text = surface._requirement_text(_card("qwen3-30b-a3b"))
        assert missing[0][:24] in text, (
            f"card says {text!r}, the code's reason is {missing[0]!r}")

    def test_an_estimated_speed_is_never_written_without_saying_so(self):
        text = surface._requirement_text(_card("qwen3-30b-a3b"))
        assert "about " in text, (
            "the 30B's bytes-per-token comes from its architecture, not a "
            "decode here; the card must not print that number bare")


class TestOnlyModelsSayAnything:
    def test_the_card_decides_to_draw_the_line_from_the_same_function(self):
        """**Why `_needs_label_text` exists.** The renderer used to decide for
        itself, and the mutation that stopped the label being appended left
        every test in this file green - they all called the *text* function and
        none called the card. The decision is now a pure function the card calls,
        so it can be asserted without a display."""
        for card in _llm_cards():
            assert surface._needs_label_text(card) == surface._requirement_text(card)

    def test_no_other_kind_grows_a_requirement_line(self):
        """A speech card does not become more demanding because a text model is,
        and 'Needs 2 CPU cores' under a Piper voice would be a claim nobody
        checked."""
        for card in surface._cards():
            if card["kind"] != "llm":
                assert surface._requirement_text(card) == "", (
                    f"{card['kind']}/{card['key']} renders a requirement line")


# **What this file does not do, and the mutations that proved it.**
#
# It never builds the card. A first version tried, in-process, and segfaulted -
# GDK picks its display backend at GTK init and no fixture can change it
# afterwards, which is why this repository drives real windows through a
# subprocess rather than in pytest.
#
# That is not stylistic: with `_card` unbuilt, these mutations all left this
# file green.
#
#   * the label is never appended to the box
#   * the label is constructed with an empty string
#   * the shortfall clause is dropped
#
# **All three are now caught by `tests/test_models_card_really_shows_the_line.py`**,
# which builds the card on a private Broadway display in a subprocess. What this
# file verifies is the **text** and the **decision to render**, both pure
# functions; what that one verifies is that GTK puts the result on screen.
# Together they cover the whole path, and neither file pretends to cover the
# other's half.
