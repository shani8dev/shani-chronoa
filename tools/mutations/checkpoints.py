"""Mutation set for "the checkpoint row was red on a machine that said nothing".

`_checkpoint_count` has two words to answer one question and answered red to
every case where the answer was *nothing*, including a machine that had never
saved a conversation - which its own docstring named as *"the normal 'nothing
done yet' state, not a fault"*. The section above the row said it in prose a
second time.

**The mutation that matters most is the over-fix.** Zero checkpoints is two
facts, and the obvious colour change deletes the second: a conversation that was
saved and has no checkpoint would be rewound to nothing, silently, and nothing
would say so. That is a real alarm, and this panel's section - *"A missing
session dir is not an error"* - is about the other fact only. The set carries
both direction, and the witnesses that keep them apart.

**Three more the fix found in its own docstring**, each recorded here rather than
left for the next reader:

- it named `conversation_store.list_checkpoints` as *"the one place that knows
  how to enumerate them"*, and that function is **per-session** - it needs the
  conversation id the probe did not have, which is *why* the glob existed. So the
  probe now asks `list_sessions` for the ids and `list_checkpoints` for each.
- `list_sessions` goes through `index()`, which **makes the directory**. A
  read-only diagnostic asking about a store nobody used was about to be the
  thing that created it - and the next run would then see a different machine.
- an unreadable store answered *"0 checkpoint files"*, a clean count the old code
  could not know and nothing caught. It is `could not determine` now, which is
  the panel's third word and the one `STATUS_UNKNOWN` exists for.

**A fixture that described a state the store cannot produce** is why the counting
mutations exist: the old test wrote two `.ckpt.N.jsonl` files into a directory
with no index and asserted *"in 1 conversation(s)"* - one, while writing two. It
passed only because the old glob counted files and called them conversations.
`list_sessions` reads the index, so a directory of checkpoint files with no index
holds **no conversations at all**. Fixtures go through `new_session` / `append` /
`checkpoint` now, and two tests carry two conversations, because the first
mutation run left "only the first counted" and "count conversations, not files"
fully green against a file where everything else built exactly one.
"""

TEST_FILE = "tests/test_surface_diagnostics.py"

_CKPT = "usr/lib/shani-chronoa/shani_chronoa/gui/surfaces/diagnostics.py"

# The two empty-store rows: what the shipped defect restored verbatim.
_NO_DIR = _CKPT
_BRANCH = "        if not root.exists():"

_ROW = "test_an_empty_store_is_nothing_to_rewind_not_a_fault"

MUTATIONS = [
    {
        "file": _CKPT,
        # **The shipped defect, restored verbatim.** A store that was never
        # created goes back to reporting the history store is empty and nothing
        # to count - which is what put red on a fresh install.
        "old": "    if not root.exists():\n"
               "        return STATUS_WORKING, (",
        "new": "    if not root.exists():\n"
               "        return STATUS_NOT_WORKING, (",
        "label": "a never-created store is a fault",
        "expect": [_ROW, "test_a_store_that_was_never_created_reads_the_same_way"],
    },
    {
        "file": _CKPT,
        # **The second branch of the same defect** - the state the render
        # actually hit: the directory exists and holds nothing.
        "old": "        return STATUS_WORKING, (\n"
               "            \"no checkpoints, and no conversations either - there is nothing \"",
        "new": "        return STATUS_NOT_WORKING, (\n"
               "            \"no checkpoints, and no conversations either - there is nothing \"",
        "label": "an empty store is a fault",
        "expect": [_ROW, "test_the_two_answers_really_are_different"],
    },
    {
        "file": _CKPT,
        # **The over-fix, and the reason the fix is two states rather than a
        # colour.** A saved conversation with no checkpoint cannot be rewound -
        # rewinding it would silently do nothing - and that is an alarm.
        "old": "            return STATUS_NOT_WORKING, (\n"
               "                f\"{plural(len(sessions), 'conversation')} are saved but no checkpoint \"",
        "new": "            return STATUS_WORKING, (\n"
               "                f\"{plural(len(sessions), 'conversation')} are saved but no checkpoint \"",
        "label": "a conversation with no checkpoint is fine too",
        "expect": [
            "test_a_conversation_with_no_checkpoint_is_still_a_fault",
            "test_the_two_answers_really_are_different",
        ],
    },
    {
        "file": _CKPT,
        # A store that could not be read answered a clean count of zero - the
        # confident wrong answer, and the one the old code could not produce.
        "old": "        return STATUS_UNKNOWN, (\n"
               "            f\"the conversation store at {root} could not be read \"",
        "new": "        return STATUS_WORKING, (\n"
               "            f\"the conversation store at {root} could not be read \"",
        "label": "an unreadable store reads as a clean zero",
        "expect": [
            "test_a_store_that_cannot_be_read_says_so_rather_than_guessing",
        ],
    },
    {
        "file": _CKPT,
        # `list_sessions` goes through `index()`, which MAKES the directory. A
        # read-only diagnostic must not be the thing that creates the store it
        # is asking about, and the next run would then see a different machine.
        "old": "    if not root.exists():\n"
               "        return STATUS_WORKING, (\n"
               "            f\"{root} does not exist yet",
        "new": "    root.mkdir(parents=True, exist_ok=True)\n"
               "    if not root.exists():\n"
               "        return STATUS_WORKING, (\n"
               "            f\"{root} does not exist yet",
        "label": "the probe creates the store it asks about",
        "expect": [
            "test_the_probe_does_not_create_the_directory_it_asks_about",
        ],
    },
    {
        "file": _CKPT,
        # Only the first conversation counted - a survivor of the first mutation
        # run, because every other case in the file built exactly one.
        "old": "    for row in sessions:\n"
               "        sid = str(row.get(\"id\") or \"\")",
        "new": "    for row in sessions[:1]:\n"
               "        sid = str(row.get(\"id\") or \"\")",
        "label": "only the first conversation's checkpoints are counted",
        "expect": ["test_two_conversations_are_both_counted"],
    },
    {
        "file": _CKPT,
        # Files counted as conversations, and the two agree at one of each - so
        # the fixture gives one conversation three checkpoints.
        "old": "        total += len(nums)",
        "new": "        total += 1",
        "label": "the count is the number of conversations, not of files",
        # `test_a_saved_checkpoint_is_reported_with_its_conversation` was named
        # here first and removed: it builds one conversation with one
        # checkpoint, where `total += 1` and `total += len(nums)` are the same
        # number. Naming it claimed coverage it does not have - the
        # over-claiming this file already records for `asleep_state`.
        "expect": [
            "test_the_count_is_of_files_not_of_conversations",
        ],
    },
    {
        "file": _CKPT,
        # The docstring's claim about which function knows how to count, made
        # true again: `list_checkpoints` is per-session, so the probe asks
        # `list_sessions` for the ids first. Re-inlining the glob would restore
        # a second implementation of the same question.
        "old": "        nums = conversation_store.list_checkpoints(root, sid)",
        "new": "        nums = [1 for _ in root.glob(f'{sid}.ckpt.*.jsonl')]",
        "label": "the store's own reader is quietly re-inlined",
        "expect": [],
        # **An equivalent mutant, kept and documented rather than counted as
        # uncovered.** `list_checkpoints(root, sid)` *is* `root.glob(f"{sid}
        # .ckpt.*.jsonl")` plus the same `int(p.stem.split(".")[-1])` parse, so
        # substituting it changes nothing the tests can see. The guard worth
        # having is that the probe does not grow its own glob when the store
        # already has one, and **nothing observable can detect that** - so it
        # stays here as the reason the two cannot be told apart, rather than as
        # a survivor implying a missing witness.
        "equivalent": True,
        "why": "list_checkpoints is that glob plus the same int() parse, so "
               "swapping the store's reader for it is behaviour-preserving",
    },
]
