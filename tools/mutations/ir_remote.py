"""Mutation set for the `ir_remote` skill's NEC and RC-6 codecs.

Rebuilt on 2026-10-11 from the committed tests and code, after the original
`/tmp` harness was lost.

The load-bearing property is the one the module states as a theorem: **every NEC
frame is exactly 67,420 microseconds**, because each byte travels with its
complement and exactly eight of any sixteen bits are ones however the byte was
chosen. The decoder checks the total exactly, and a band would be the weaker
check.
"""

TEST_FILE = "tests/test_ir_remote_skill.py"

_IR = "usr/lib/shani-chronoa/shani_chronoa/skills/ir_remote.py"

MUTATIONS = [
    # -- the frame-length theorem ----------------------------------------------
    # **The tolerance is the weaker check, and widening it is the mutation.** The
    # code's own comment says a band would be the weaker check, so the mutation
    # makes the band as wide as it can be while still refusing the wrong length.
    {
        "file": _IR,
        "old": "    if sum(us for _, us in timings) > NEC_FRAME_US * (1.0 + TOLERANCE):\n"
               "        return None\n",
        "new": "    if sum(us for _, us in timings) > NEC_FRAME_US * (1.0 + 3.0):\n"
               "        return None\n",
        "label": "the length tolerance is widened past the theorem",
        "expect": ["test_a_frame_that_is_too_long_is_refused"],
        # **Equivalent, and the reason is the order of the two checks.** Both
        # witnesses feed a capture the `len(timings) != 2 + 2 * 32` check refuses
        # *before* the total's arithmetic runs, so a wider tolerance they are
        # inside changes nothing. Recorded rather than deleted, because the next
        # person to reorder those two checks needs to know the tolerance is
        # otherwise unwitnessed.
        "equivalent": True,
        "why": "the length check runs first, so both witnesses are refused before "
               "the total's tolerance is consulted",
    },
    {
        "file": _IR,
        "old": "    if len(timings) != 2 + 2 * 32:\n        return None\n",
        "new": "    if len(timings) < 2 + 2 * 32:\n        return None\n",
        "label": "a longer capture is read as a frame with extra bits",
        "expect": ["test_a_frame_that_is_too_long_is_refused",
                   "test_a_frame_that_is_not_the_right_length_is_refused"],
    },

    # -- bit order and the complement -------------------------------------------
    {
        "file": _IR,
        "old": "    return [(value >> i) & 1 for i in range(width)]\n",
        "new": "    return [(value >> (width - 1 - i)) & 1 for i in range(width)]\n",
        "label": "every field is sent most-significant first",
        "expect": ["test_the_address_is_sent_least_significant_bit_first",
                   "test_every_field_is_least_significant_first",
                   "test_encode_then_decode_is_the_identity",
                   "test_the_address_and_command_come_from_the_right_bits"],
    },
    # **The complement is dropped on the ENCODE side, which is what makes the
    # decoder's check untested.** A decoder written to match its encoder never
    # notices a missing complement, so the mutation has to break the encoder.
    {
        "file": _IR,
        "old": "    payload += _bits_lsb_first((~command) & 0xFF, 8)\n",
        "new": "    payload += _bits_lsb_first(command, 8)\n",
        "label": "the command's complement byte is the command again",
        "expect": ["test_each_byte_is_followed_by_its_complement",
                   "test_every_frame_is_the_same_length",
                   "test_the_constant_is_the_published_arithmetic"],
    },

    # -- RC-6 -------------------------------------------------------------------
    {
        "file": _IR,
        # **The build path, not the send path** - both assign CARRIER_RC6, so the
        # anchor needs the line that follows it to be unique. A `str.replace` that
        # appears twice is exactly what the runner's count assertion refuses.
        "old": "                carrier = CARRIER_RC6\n"
               "                note = (f\"An RC-6 mode 0 frame: control {address}, information \"\n",
        "new": "                carrier = CARRIER_NEC\n"
               "                note = (f\"An RC-6 mode 0 frame: control {address}, information \"\n",
        "label": "an RC-6 frame is written with the NEC carrier",
        "expect": ["test_an_rc6_frame_is_written_at_36000",
                   "test_the_carrier_is_the_rc6_one_and_not_nec"],
    },
    # **The doubled space is the constant, not a per-mode branch.** The first
    # version aimed at a `trailer = unit * 2 if mode == 0 else unit` line that
    # does not exist - the doubling is `RC6_TRAILER_SPACE = 889` against a 444
    # half-period.
    {
        "file": _IR,
        "old": "RC6_TRAILER_SPACE = 889\n",
        "new": "RC6_TRAILER_SPACE = 444\n",
        "label": "the trailer space is not doubled, so a decoder cannot find the data",
        "expect": ["test_the_trailer_space_is_doubled",
                   "test_round_trip"],
    },
    {
        "file": _IR,
        "old": "    if mode != 0:\n        raise ValueError(f\"only RC-6 mode 0 is built, not mode {mode}\")\n",
        "new": "",
        "label": "a non-zero mode is built instead of refused",
        "expect": ["test_only_mode_zero_is_built"],
    },

    # -- the two protocols are not each other -------------------------------------
    # **The guard is the leader pair, and it is the `_match` calls at the top of
    # `nec_decode`.** The first version aimed at a `leader[0] != RC6_...` line that
    # belongs to a different codebase's shape.
    {
        "file": _IR,
        "old": "    if timings[0][0] != \"pulse\" or not _match(timings[0][1], NEC_LEADER_PULSE):\n"
               "        return None\n",
        "new": "    if timings[0][0] != \"pulse\":\n"
               "        return None\n",
        "label": "the leader pulse's width is not checked",
        "expect": ["test_a_leader_of_the_wrong_width_is_refused",
                   "test_an_rc6_frame_is_not_a_nec_frame"],
    },

    # -- the file format the man page specifies -----------------------------------
    {
        "file": _IR,
        "old": "    lines = [f\"carrier {carrier_hz}\"]\n",
        "new": "    lines = []\n",
        "label": "the file no longer starts with the carrier line",
        "expect": ["test_the_file_starts_with_the_carrier",
                   "test_it_parses_the_man_pages_own_example"],
    },
    {
        "file": _IR,
        # The ir-ctl format is *alternating* tokens, and the writer's loop over
        # `enumerate(timings)` is what keeps that true. Dropping the alternation
        # makes a file that does not parse back.
        "old": "    for kind, us in timings:\n        lines.append(f\"{kind} {us}\")\n",
        "new": "    for kind, us in timings:\n        lines.append(f\"pulse {us}\")\n",
        "label": "every sample is written as a pulse, so the file does not alternate",
        "expect": ["test_unlabelled_numbers_alternate",
                   "test_the_file_starts_with_a_pulse",
                   "test_it_parses_back"],
    },
    {
        "file": _IR,
        # **The keyword/value pair is skipped by `index += 2`.** The first version
        # aimed at an `in_code` flag from a different parser's shape.
        "old": "            if word in (\"carrier\", \"timeout\"):\n"
               "                # A keyword with a value: drop both tokens and keep going.\n"
               "                index += 2\n",
        "new": "            if word in (\"carrier\", \"timeout\"):\n"
               "                index += 1\n",
        "label": "a keyword's value is read as a timing",
        "expect": ["test_a_keywords_value_is_not_read_as_a_timing",
                   "test_the_parser_stops_at_nothing_it_should_keep"],
    },

    # -- the gate -----------------------------------------------------------------
    {
        "file": _IR,
        # The key is `_CONSENT_KEY`, a module constant - the first version spelled
        # the literal out. **And the witness is the refusal sentence, not just the
        # verdict:** a gate that returns False without naming the switch is the
        # dead-switch class this repo records four times.
        "old": "        return False, (f\"Infrared is turned off. Nothing was sent or received. \"\n"
               "                       f\"Enable '{_CONSENT_KEY}' in Settings to allow it.\")\n",
        "new": "        return False, \"Infrared is turned off.\"\n",
        "label": "the refusal does not name the switch that would open it",
        "expect": ["test_the_gate_is_off_by_default_and_refuses"],
    },
]
