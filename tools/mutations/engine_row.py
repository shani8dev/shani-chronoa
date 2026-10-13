"""Mutation set for the row that says what is answering.

`Answers with` is the line a newcomer reads first when deciding whether Chronoa
can answer them at all. For a machine that configured a cloud provider it read
**"No model"** - because `_model_word` asked the *local registry*, whose answer
is always empty on such a machine - while a provider held every turn.

These mutations aim at the three ways the fix can be got wrong, and the third
is the one the repo has recorded being got wrong in this panel repeatedly: a fix
that cannot be over-applied is not a fix yet.
"""

TEST_FILE = "tests/test_the_row_that_says_what_is_answering.py"

_WIN = "usr/lib/shani-chronoa/shani_chronoa/gui/window.py"

MUTATIONS = [
    {
        "file": _WIN,
        "old": '            if self._answering_kind() == "cloud":\n                return "a cloud provider"\n',
        "new": "",
        # **The shipped defect, restored verbatim.** With the branch gone, a
        # machine that configured a provider reads "No model" again.
        "label": "the cloud branch is gone (the shipped defect)",
        "expect": ["test_a_cloud_engine_is_not_reported_as_no_model",
                   "test_the_word_says_the_answer_leaves_this_machine",
                   "test_the_three_engines_get_three_words"],
    },
    {
        "file": _WIN,
        "old": '            if self._answering_kind() == "cloud":\n',
        "new": '            if self._answering_kind() != "none":\n',
        # **The over-fix.** Every engine that is not literally "none" - which
        # includes a perfectly good *local* model - claims its answers come from
        # a provider. That is the more alarming of the two wrong answers because
        # it is the privacy one: a user who installed a local model would be
        # told their replies left the machine.
        "label": "any engine that exists is called a cloud provider (the over-fix)",
        "expect": ["test_a_local_model_still_names_the_tier",
                   "test_the_three_engines_get_three_words"],
    },
    {
        "file": _WIN,
        "old": '                return "a cloud provider"\n',
        "new": '                return "another model"\n',
        # The branch still fires and the row still is not "No model", so the
        # first test passes - and the person is left not knowing whether their
        # question left the machine, which is the fact they chose a provider
        # for. This is the "a fix that satisfies its test while removing the
        # reason the fix exists" shape.
        "label": "cloud is named as something that does not say where it is",
        "expect": ["test_the_word_says_the_answer_leaves_this_machine"],
    },
    {
        "file": _WIN,
        "old": '        reader = getattr(self._app, "current_llm_kind", None)\n        if reader is None:\n            return ""\n',
        "new": "        reader = getattr(self._app, \"current_llm_kind\", None)\n",
        # **No degradation.** A window with an application that has no engine -
        # which is every render and every fixture - now reaches `reader()` on a
        # `None`.
        "label": "the missing-reader guard is gone",
        # **Equivalent by construction, and this is the finding.** `_answering
        # kind` has *two* independent defences on this path - `if reader is
        # None: return ""` and a `try/except` around the call - so deleting
        # either leaves the other and the file stays green. Deleting **both** is
        # caught (by `test_a_reader_that_is_absent_degrades_rather_than_raises`),
        # which is what proves the pair is doing the work rather than one of
        # them doing nothing.
        #
        # The first version of this file claimed a *coverage* gap here and had
        # to be corrected twice to reach it: the survivor turned out to be a
        # witness that could not fail (it asserted through `_model_word`, whose
        # blanket `except` answers "No model" either way), and once that was
        # fixed the survivor was real but equivalent. Both are recorded here
        # because the first one is the more expensive mistake and looks like a
        # missing test.
        "equivalent": True,
        "why": "`_answering_kind` guards this path twice - an explicit "
               "`if reader is None` and a `try/except` around the call - so "
               "removing either one leaves the other in place. Removing both is "
               "caught, which is what shows the pair is load-bearing rather than "
               "one of them being dead.",
        "expect": [],
    },
    {
        "file": _WIN,
        "old": '            return str(reader() or "")\n        except Exception:                               # noqa: BLE001\n            logger.debug("could not read which engine is answering",\n                         exc_info=True)\n            return ""\n',
        "new": "            return str(reader() or \"\")\n",
        # The raising branch rather than the missing-attribute branch: an
        # application whose `current_llm_kind` raises is the same "nobody could
        # say" case wearing a different fault, and it is the one that produced a
        # silently wrong row in the first version of this fix's fixture.
        "label": "a raising engine reader propagates",
        "expect": ["test_a_reader_that_raises_degrades_rather_than_raises"],
    },
]