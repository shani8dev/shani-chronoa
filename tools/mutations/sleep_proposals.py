"""Mutation set for accepting - or declining - a sentence the sleep pass noticed.

**The defect.** `consolidation.sleep_now()` reads a finished conversation
overnight and writes a digest of `Candidate`s. That boundary is right - a
background pass must not decide what the assistant believes about somebody - and
on its own it was also a dead end: the daemon wrote digests to `sleep/` and
**nothing anywhere read them**, so every night's work proposed facts to nobody.
`decide_candidate` is the acceptor.

**Three things this set is really about, each of which was a bug that shipped
while the feature was being written:**

1. **The lister asked the opposite question.** `pending_proposals` was first
   built on `sessions_needing_sleep`, which names conversations with *no* digest
   yet. On a machine holding a full `sleep/` directory it returned an empty
   proposal list, which reads as "nothing was ever proposed" - the
   absence-shaped wrong answer this repository keeps paying for.
   `consolidation.list_digests` exists so that question has one answer, in the
   module that owns the path convention `digest_path` unified.

2. **`_covered_by` could not fire on any capital.** It compared the stored
   supersession key - which `store_fact` lowercases **before** writing - against
   the caller's raw key, case-sensitively. Nothing shipped was broken, because
   the other two callers pass keys that are already lowercase by construction;
   the acceptor passes the *sentence*, and a sentence has capitals in it. A
   guard that cannot fire, wearing the shape of one that can.

3. **`kept` and `kept-unrecorded` are the same answer twice.** The decision is
   written into the digest *before* the store write, so a write that fails has
   already consumed the proposal. Reporting it as `kept` tells the person a
   memory exists that does not, and it will not be offered again either.

**Both directions are carried on every guard that can be over-applied.** A
non-bool `accept` that is coerced instead of refused turns the string `"no"`
into yes; a `_covered_by` that fires on *any* key rather than the matching one
turns a near-duplicate into a refusal to update a fact. A digest that cannot be
written back must refuse and roll the flags back rather than silently consume
the answer.
"""

TEST_FILE = "tests/test_sleep_proposals_can_be_answered.py"

_CONSOLIDATION = "usr/lib/shani-chronoa/shani_chronoa/consolidation.py"
_MEMORY = "usr/lib/shani-chronoa/shani_chronoa/senses/memory.py"

# --- the lister, which is the opposite question wearing the right name ------

_OPPOSITE_LISTING = (
    "def list_digests(directory: Optional[Path] = None) -> List[str]:",
    "def sessions_needing_sleep(root: Optional[Path] = None,\n"
    "                           minimum_age: float = 3600.0,\n"
    "                           directory: Optional[Path] = None) -> List[str]:\n"
    "    return sessions_needing_sleep(directory, minimum_age=0.0, "
    "directory=directory)",
)

_NO_DIRECTORY_CHECK = (
    "        if not home.is_dir():\n"
    "            return []\n",
    "        if not home.is_dir():\n"
    "            home.mkdir(parents=True, exist_ok=True)\n",
)

_NO_ID_FILTER = (
    "    fresh = [pair for pair in entries\n"
    "             if conversation_store.valid_id(pair[1])]\n",
    "    fresh = list(entries)\n",
)

# --- the acceptor's own refusals -------------------------------------------

_NO_BOOL_CHECK = (
    "    if not isinstance(accept, bool):",
    "    if False:",
)

_COERCED_BOOL = (
    "    if not isinstance(accept, bool):\n"
    "        return (\"refused\",\n"
    "                f\"accept must be true or false, not {accept!r} - refusing rather \"\n"
    "                f\"than guessing, because this argument arrives from a button, a \"\n"
    "                f\"command line and a model\")\n",
    "    accept = bool(accept)\n",
)

_NO_CONSENT_CHECK = (
    "    denied = _consent_error()\n"
    "    if denied:\n"
    "        return (\"refused\", denied)\n",
    "    denied = \"\"\n",
)

_NO_ALREADY_DECIDED = (
    "    if candidate.accepted or candidate.rejected:\n"
    "        return (\"already-decided\",\n"
    "                f\"proposal {position} was already \"\n"
    "                f\"{'accepted' if candidate.accepted else 'rejected'}\")\n",
    "    if False:\n"
    "        return (\"already-decided\", \"\")\n",
)

_ROLLBACK_DROPPED = (
    "    except (OSError, ValueError) as exc:\n"
    "        candidate.accepted = False\n"
    "        candidate.rejected = False\n",
    "    except (OSError, ValueError) as exc:\n",
)

_ROLLBACK_PARTIAL = (
    "        candidate.accepted = False\n"
    "        candidate.rejected = False\n",
    "        candidate.rejected = False\n",
)

_NO_POSITION_CHECK = (
    "    if not 0 <= position < len(digest.candidates):",
    "    if False:",
)

# --- the two facts the acceptor must not collapse -------------------------

_UNRECORDED_AS_KEPT = (
    "    if stored is None:\n"
    "        return (\"kept-unrecorded\",\n",
    "    if stored is None:\n"
    "        return (\"kept\", \"\"),\n"
    "        return (\"kept-unrecorded\",\n",
)

#: The **decline** half. The first version of this wrote `if not accept:` - a
#: condition that is true when declining, so it skipped the write on *keep* and
#: the label said decline. The runner reported five unrelated failures and named
#: none of my witnesses, which is how a mutation aimed at the wrong branch
#: looks: not a survivor, a mess.
_DECISION_NOT_RECORDED = (
    "    candidate.accepted = accept\n"
    "    candidate.rejected = not accept\n"
    "    try:\n"
    "        consolidation.save_digest(digest, directory)\n",
    "    candidate.accepted = accept\n"
    "    candidate.rejected = not accept\n"
    "    try:\n"
    "        if accept:\n"
    "            consolidation.save_digest(digest, directory)\n",
)

_EXTRACTED_CONFIDENCE = (
    "    return Fact(text, text, text, f\"sleep:{session_id}#{index}\",\n"
    "                CONFIDENCE_STATED)",
    "    return Fact(text, text, text, f\"sleep:{session_id}#{index}\",\n"
    "                CONFIDENCE_EXTRACTED)",
)

_NO_SOURCE = (
    "    return Fact(text, text, text, f\"sleep:{session_id}#{index}\",",
    "    return Fact(text, text, text, \"turn-extract\",",
)

# --- the covering guard, in both directions -------------------------------

_COVERING_CASE_SENSITIVE = (
    '        if " ".join(str(held_key).split()).casefold() != wanted_key:\n'
    "            continue\n",
    "        if held_key != fact.key:\n"
    "            continue\n",
)

_COVERING_ANY_KEY = (
    '        if " ".join(str(held_key).split()).casefold() != wanted_key:\n'
    "            continue\n",
    "        if held_key is None:\n"
    "            continue\n",
)

_COVERING_GONE = (
    "    covering = _covered_by(fact, target)\n",
    "    covering = None\n",
)

# --- the listing the panel and the skill both read ------------------------

_LIST_LIMIT_DROPPED = (
    "    return [proposal for _, proposal in rows[:max(1, int(limit or 20))]]",
    "    return [proposal for _, proposal in rows]",
)

_LIST_IGNORES_DECIDED = (
    "            if candidate.accepted or candidate.rejected:\n"
    "                continue\n",
    "            if False:\n"
    "                continue\n",
)

_LIST_MTIME_ORDER = (
    "    rows.sort(key=lambda pair: pair[0], reverse=True)",
    "    rows.sort(key=lambda pair: pair[0])",
)

MUTATIONS = [
    # --- the lister, and the question it used to ask -----------------------
    {
        "label": "the proposal list asks for the sessions that have NO digest yet",
        "file": _CONSOLIDATION,
        "old": _OPPOSITE_LISTING[0],
        "new": _OPPOSITE_LISTING[1],
        "why": "`sessions_needing_sleep` is the opposite question. Measured: on "
               "a directory full of digests it yields every session *except* the "
               "ones holding a proposal, so the listing is empty on the machine "
               "with the most to answer.",
        "expect": ["test_the_proposals_are_listed_with_the_context_they_were_"
                   "drawn_from",
                   "test_only_undecided_proposals_are_listed"],
    },
    {
        "label": "asking what sleep produced creates the store it is asking about",
        "file": _CONSOLIDATION,
        "old": _NO_DIRECTORY_CHECK[0],
        "new": _NO_DIRECTORY_CHECK[1],
        "why": "`digest_path` documents that asking where a file is does not "
               "make it. A read-only listing that creates an unused store "
               "changes what the *next* run answers.",
        "expect": ["test_listing_creates_nothing"],
    },
    {
        "label": "a file whose stem is not a session id is offered as a proposal",
        "file": _CONSOLIDATION,
        "old": _NO_ID_FILTER[0],
        "new": _NO_ID_FILTER[1],
        "why": "The id is a filename, and `digest_path` refuses one that could "
               "escape, so listing it hands out a `session_id` no decision could "
               "ever act on.",
        "expect": ["test_a_file_naming_an_invalid_session_is_not_a_proposal"],
    },

    # --- the refusals, each with its over-fix ----------------------------
    {
        "label": "a non-boolean answer is coerced instead of refused",
        "file": _MEMORY,
        "old": _COERCED_BOOL[0],
        "new": _COERCED_BOOL[1],
        "why": "`bool('no')` is True. This is the one argument that arrives from "
               "a button, a command line and a model, and all three spell no "
               "differently.",
        "expect": ["test_a_non_boolean_answer_is_refused_rather_than_guessed"],
    },
    {
        "label": "the acceptor's own consent gate is gone",
        "file": _MEMORY,
        "old": _NO_CONSENT_CHECK[0],
        "new": _NO_CONSENT_CHECK[1],
        "why": "The panel is not the only door and neither door checks its own "
               "consent; this function is where both of them are refused.",
        "expect": ["test_consent_is_the_acceptors_own_gate_too"],
    },
    {
        "label": "a proposal that is out of range is refused with no check",
        "file": _MEMORY,
        "old": _NO_POSITION_CHECK[0],
        "new": _NO_POSITION_CHECK[1],
        "why": "Without it a stale index addresses whatever is there, and the "
               "answer to a second press would be a different sentence than the "
               "one the person pressed about.",
        "expect": ["test_a_proposal_that_is_not_there_is_refused_with_the_count"],
    },
    {
        "label": "the position is coerced rather than refused",
        "file": _MEMORY,
        "old": "    try:\n        position = int(index)\n"
               "    except (TypeError, ValueError):\n",
        "new": "    try:\n        position = int(index)\n"
               "    except (TypeError, ValueError):\n        position = 0\n"
               "    if False:\n",
        "why": "A position that cannot be read becomes proposal 0, so a caller "
               "passing a stale index decides about somebody else's sentence.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_an_index_that_is_not_a_number_is_refused_rather_than_guessed",
                   "test_an_index_that_is_not_a_number_is_refused_rather_than_guessed"],
    },

    # --- the two facts that must not be collapsed ------------------------
    {
        "label": "a failed store write is reported as kept (the shipped shape)",
        "file": _MEMORY,
        "old": _UNRECORDED_AS_KEPT[0],
        "new": _UNRECORDED_AS_KEPT[1],
        "why": "The decision is already in the digest, so this tells a person a "
               "memory exists that does not *and* the proposal is gone. The same "
               "shape as `stop_service()` returning '' for a unit that was "
               "already inactive.",
        "expect": ["test_an_accepted_answer_survives_a_write_it_could_not_perform"],
    },
    {
        "label": "a declined proposal is consumed without the decision recorded",
        "file": _MEMORY,
        "old": _DECISION_NOT_RECORDED[0],
        "new": _DECISION_NOT_RECORDED[1],
        "why": "The in-memory flag still answers this session, so the listing "
               "looks right until the digest is reloaded - which is what a "
               "restart, or the panel, does.",
        "expect": ["test_declining_records_the_answer_and_stores_nothing",
                   "test_only_undecided_proposals_are_listed"],
    },
    {
        "label": "a digest that cannot be written back is silently consumed",
        "file": _MEMORY,
        "old": _ROLLBACK_DROPPED[0],
        "new": _ROLLBACK_DROPPED[1],
        "equivalent": True,
        "why": "**Equivalent by construction, and the reason is about the "
               "call graph rather than the code.** `decide_candidate` loads the "
               "digest with `load_digest`, mutates the object it loaded, and on a "
               "failed write returns the refusal and **discards the object**. No "
               "caller holds it - there is no caller to hand it to - so the flags "
               "left set on it are read by nobody, and the digest on disk is "
               "untouched either way. Every observable outcome is identical with "
               "and without the rollback. The rollback is defence against a "
               "future caller that keeps the object, and no test can witness a "
               "guard against a caller that does not exist. The *observable* half "
               "of the same failure - the refusal, and the proposal still on offer "
               "- is witnessed by "
               "`test_a_digest_that_cannot_be_written_back_is_refused_and_"
               "nothing_decided`, which was reported as covering this mutation "
               "and is the reason this one is marked equivalent instead of "
               "counted as a gap.",
    },
    {
        "label": "the rollback restores only one of the two flags",
        "file": _MEMORY,
        "old": _ROLLBACK_PARTIAL[0],
        "new": _ROLLBACK_PARTIAL[1],
        "equivalent": True,
        "why": "The same object as the entry above, and for the same reason: it "
               "is loaded inside `decide_candidate`, mutated, and dropped when "
               "the function returns. Restoring one flag or both is invisible to "
               "every caller, because the value they cannot reach is the only one "
               "that changed. Two equivalent mutants for one unobservable line is "
               "the honest count - **a guard against a caller nobody has yet is "
               "not a coverage gap, it is a bet**, and this file names it as one "
               "rather than leaving it looking tested.",
    },

    # --- provenance and confidence ---------------------------------------
    {
        "label": "a fact a person agreed to is stored as an extracted one",
        "file": _MEMORY,
        "old": _EXTRACTED_CONFIDENCE[0],
        "new": _EXTRACTED_CONFIDENCE[1],
        "why": "Sleep read the sentence out of a conversation it was not present "
               "for, so *it* should not be believed. The person agreed to it, "
               "which is the same provenance a typed fact carries.",
        "expect": ["test_an_agreed_fact_is_believed_as_much_as_a_typed_one"],
    },
    {
        "label": "the stored memory does not say which proposal it came from",
        "file": _MEMORY,
        "old": _NO_SOURCE[0],
        "new": _NO_SOURCE[1],
        "why": "`sleep:<session>#<index>` is what makes \"why does it think "
               "that\" answerable, and it is the only place the proposal's own "
               "identity survives.",
        "expect": ["test_the_stored_memory_says_where_it_came_from"],
    },

    # --- the covering guard, both directions ------------------------------
    {
        "label": "the covering guard compares keys case-sensitively again",
        "file": _MEMORY,
        "old": _COVERING_CASE_SENSITIVE[0],
        "new": _COVERING_CASE_SENSITIVE[1],
        "why": "`store_fact` lowercases the key before writing it, so a plain "
               "`!=` makes the guard answer \"not covered\" for every sentence. "
               "Nothing shipped was broken - the other callers pass keys that "
               "are already lowercase - which is exactly why it survived.",
        "expect": ["test_the_covering_guard_compares_keys_the_way_they_are_"
                   "stored"],
    },
    {
        "label": "the covering guard fires on any key at all",
        "file": _MEMORY,
        "old": _COVERING_ANY_KEY[0],
        "new": _COVERING_ANY_KEY[1],
        "why": "The over-fix: the identical-text check still applies, so a fact "
               "about a *different* subject with the same words would be treated "
               "as already stored. Dropping the key comparison has to be caught "
               "separately from dropping the guard.",
        # The runner named a different test than this one and reported
        # "failed for another reason" - its rule for a witness that names
        # the wrong test. Corrected by name; no new test was written,
        # because the coverage was already there and mine pointed beside it.
        "expect": ["test_the_covering_guard_needs_the_matching_key_and_not_only_the_text",
                   "test_the_covering_guard_needs_the_matching_key_and_not_only_the_text"],
    },
    {
        "label": "agreeing to something already remembered double-writes it",
        "file": _MEMORY,
        "old": _COVERING_GONE[0],
        "new": _COVERING_GONE[1],
        "why": "The over-fix the guard exists to prevent, in the other direction: "
               "one press, one memory.",
        "expect": ["test_agreeing_to_what_is_already_remembered_does_not_"
                   "duplicate"],
    },

    # --- the listing both surfaces read -----------------------------------
    {
        "label": "the listing's limit is dropped",
        "file": _MEMORY,
        "old": _LIST_LIMIT_DROPPED[0],
        "new": _LIST_LIMIT_DROPPED[1],
        "why": "A long-running install accumulates one digest per conversation, "
               "and an unbounded listing reads all of them to show the twenty "
               "nearest - on the GUI thread.",
        "expect": ["test_the_limit_bounds_the_listing_and_survives_a_nonsense_"
                   "one"],
    },
    {
        "label": "the listing offers proposals that were already answered",
        "file": _MEMORY,
        "old": _LIST_IGNORES_DECIDED[0],
        "new": _LIST_IGNORES_DECIDED[1],
        "why": "The decision is written into the digest precisely so a nightly "
               "pass does not ask again; a listing that ignores it puts the "
               "same sentence back in front of a person every morning.",
        "expect": ["test_only_undecided_proposals_are_listed",
                   "test_agreeing_marks_it_in_the_digest_so_it_is_not_proposed_"
                   "again"],
    },
    {
        "label": "the listing is ordered by when the conversation ended, oldest first",
        "file": _MEMORY,
        "old": _LIST_MTIME_ORDER[0],
        "new": _LIST_MTIME_ORDER[1],
        "why": "Deciding a proposal rewrites its digest, so it becomes the "
               "newest file - which is why the order is the digest's own "
               "`created` and not the filesystem's.",
        "expect": ["test_the_newest_conversation_is_listed_first"],
    },
]