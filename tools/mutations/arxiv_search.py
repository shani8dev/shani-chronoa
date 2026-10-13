"""Mutation set for arXiv search: the builder, the parser and the ladder.

Three of these exist because the corresponding test was green for a reason
that had nothing to do with the code under it, and that is the only thing a
mutation run is for.

1. **The scope goes back to `all:`.** Measured, `ti:` gave 107 on-topic hits for
   "constrained decoding" where `all:` gave 2,365 topped by a paper about NER
   taggers. Reverting it makes the builder ask a question that answers, and
   answers wrongly - so a mutation that only changed a prefix would otherwise
   leave the whole suite green with a much worse search.

2. **The operator test goes back to `.upper()`.** That is not a style change: it
   *manufactures* the operator, so every question containing the word "and" is
   handed to arXiv as a boolean query and answered with a disjunction - 406,821
   hits topped by a paper about HI absorbers. It was found by a test, after the
   suite was already green.

3. **The widening runs on every query** rather than only on an empty one. Every
   result-only assertion still passes, because both ladders return the same
   papers - so the ladder tests assert on *which questions were asked*, not on
   what came back. That is the whole reason those tests ask `self.NARROW` of the
   module's own builder instead of typing a query string.

Also: the empty-body case. arXiv answers a query matching nothing with a
**well-formed** feed carrying `totalResults 0`, so a scripted empty body is a
different event and reads as "unreadable" rather than "no such paper". Both are
covered, because conflating them is the confident wrong answer.
"""

TEST_FILE = "tests/test_arxiv_search.py"

_WS = "usr/lib/shani-chronoa/shani_chronoa/websearch.py"
_SKILL = "usr/lib/shani-chronoa/shani_chronoa/skills/web_search.py"

MUTATIONS = [
    {
        "file": _WS,
        "old": 'def arxiv_query(text, scope="ti"):',
        "new": 'def arxiv_query(text, scope="all"):',
        "label": "the default scope is full text rather than the title",
        "expect": ["test_the_scope_is_the_title_by_default",
                   "test_a_phrase_of_ordinary_words_is_and_ed_not_quoted",
                   "test_the_narrow_scope_is_tried_first"],
    },
    {
        "file": _WS,
        "old": '            or any(" %s " % op in raw for op in _ARXIV_OPERATORS)):',
        "new": '            or any(" %s " % op in raw.upper() for op in _ARXIV_OPERATORS)):',
        "label": "the word 'and' is read as arXiv's operator",
        "expect": ["test_lower_case_and_is_not_the_operator"],
    },
    {
        "file": _WS,
        "old": "        if not cleaned or cleaned in _ARXIV_STOPWORDS or "
               "stem in _ARXIV_STOPWORDS:\n",
        "new": "        if not cleaned or cleaned in _ARXIV_STOPWORDS:\n",
        "label": "a contraction is no longer read as its stem",
        "expect": ["test_a_contraction_is_its_stem"],
    },
    {
        "file": _WS,
        "old": '        cleaned = _RESERVED.sub("", term).strip(\'"\').strip(_EDGE).lower()',
        "new": '        cleaned = _RESERVED.sub("", term).strip(\'"\')',
        "label": "a term keeps its trailing punctuation and its case",
        "expect": ["test_trailing_punctuation_is_stripped"],
    },
    {
        "file": _WS,
        "old": "        if term.startswith('\"') and term.endswith('\"') and len(term) > 2:",
        "new": "        if False:",
        "label": "a phrase the caller quoted is split into loose words",
        "expect": ["test_a_phrase_the_caller_quoted_is_kept_as_one_term"],
    },
    {
        "file": _WS,
        "old": "            if link.get(\"rel\") == \"alternate\" and link.get(\"href\"):",
        "new": "            if link.get(\"rel\") == \"related\" and link.get(\"href\"):",
        "label": "the PDF link is answered where the page link was meant",
        "expect": ["test_the_url_is_the_abstract_page_and_not_the_pdf",
                   "test_every_result_has_a_title_and_a_url"],
    },
    {
        "file": _WS,
        "old": '    for entry in root.iter("%sentry" % _ATOM):',
        "new": '    for entry in root.iter("item"):',
        "label": "Atom is read as RSS, so every real feed parses to nothing",
        "expect": ["test_the_parser_does_not_assume_rss",
                   "test_the_real_feed_gives_real_results",
                   "test_the_capture_has_the_entries_the_parser_claims"],
    },
    {
        "file": _WS,
        "old": "            if link.get(\"rel\") == \"alternate\" and link.get(\"href\"):\n"
               "                url = link.get(\"href\")\n"
               "                break\n",
        "new": "            pass\n",
        "label": "the alternate link is no longer preferred over the entry id",
        "expect": ["test_the_url_is_the_alternate_link_over_https_not_the_http_id"],
    },
    {
        "file": _WS,
        "old": '    widened = None if scoped == alternate else alternate',
        "new": '    widened = alternate',
        "label": "the widening runs on every query instead of only an empty one",
        "expect": ["test_a_pass_through_matching_nothing_is_also_asked_once"],
    },
    {
        "file": _WS,
        "old": '    widened = None if scoped == alternate else alternate',
        "new": '    widened = None',
        "label": "the widening never runs, so a title-only answer is final",
        "expect": ["test_the_widening_runs_only_when_the_first_found_nothing",
                   "test_an_empty_answer_quotes_both_questions_it_asked"],
    },
    {
        "file": _WS,
        "old": "    for search_query in ([scoped, widened] if widened else [scoped]):",
        "new": "    for search_query in ([scoped] if widened else [scoped]):",
        "label": "the widened question is built but never asked",
        "expect": ["test_the_widening_runs_only_when_the_first_found_nothing",
                   "test_an_empty_answer_quotes_both_questions_it_asked"],
    },
    {
        "file": _WS,
        "old": "        if found:\n            return found, \"\", []\n",
        "new": "        if False:\n            return found, \"\", []\n",
        "label": "an answer is discarded and the search reported as a refusal",
        "expect": ["test_the_narrow_scope_is_tried_first",
                   "test_the_widening_runs_only_when_the_first_found_nothing",
                   "test_an_empty_answer_quotes_both_questions_it_asked"],
    },
    {
        "file": _WS,
        "old": "    scoped = arxiv_query(query, scope=\"ti\")",
        "new": "    scoped = arxiv_query(query, scope=\"all\")",
        "label": "the search asks the wide scope first",
        "expect": ["test_the_narrow_scope_is_tried_first",
                   "test_an_empty_answer_quotes_both_questions_it_asked"],
    },
    {
        "file": _WS,
        "old": '    refused = gate("Searching arXiv")',
        "new": '    refused = None',
        "label": "the web consent gate is not consulted for arXiv",
        "expect": ["test_the_web_sense_gate_is_consulted_first"],
    },
    {
        "file": _WS,
        "old": "    if not scoped:\n        return [], (\"Nothing to search arXiv for:",
        "new": "    if False:\n        return [], (\"Nothing to search arXiv for:",
        "label": "a question with no content words is sent anyway",
        "expect": ["test_a_question_with_no_content_words_is_refused_before_any_request"],
    },
    {
        "file": _WS,
        "old": '    if (any("%s:" % field in raw for field in _ARXIV_FIELDS)\n',
        "new": '    if (False\n',
        "label": "a query already in arXiv syntax is rebuilt instead of passed through",
        "expect": ["test_a_query_already_in_arxiv_syntax_passes_through_unchanged",
                   "test_a_pass_through_query_is_asked_once"],
    },
    {
        "file": _SKILL,
        "old": '    if (arguments.get("source") or "").strip().lower() == "arxiv":',
        "new": '    if False:',
        "label": "the skill never reaches arXiv however it is asked",
        "expect": ["test_source_arxiv_reaches_the_arxiv_search",
                   "test_the_arxiv_answer_says_what_arxiv_is",
                   "test_a_url_is_refused_for_arxiv_by_name"],
    },
    {
        "file": _SKILL,
        "old": '        return websearch.format_results(query, results, notes) + "\\n\\n" + _ARXIV_SCOPE',
        "new": '        return websearch.format_results(query, results, notes)',
        "label": "the answer no longer says arXiv holds preprints",
        "expect": ["test_the_arxiv_answer_says_what_arxiv_is"],
    },
    {
        "file": _SKILL,
        "old": '        if url:\n            return ("arXiv searches papers, not pages',
        "new": '        if False:\n            return ("arXiv searches papers, not pages',
        "label": "a URL is sent to arXiv as if its characters were a query",
        "expect": ["test_a_url_is_refused_for_arxiv_by_name"],
    },
    {
        "file": _SKILL,
        "old": '                    "enum": ["web", "arxiv"],',
        "new": '                    "enum": ["web"],',
        "label": "the model is never offered arxiv as a source",
        "expect": ["test_the_schema_offers_source_with_arxiv_in_it"],
    },
    {
        'label': 'the abstract is truncated with the description',
        'why': 'the defect the field was added for: capped at 420 characters the '
               'paper is unreadable, and the one paper naming a research answer '
               'was cut by the tool that had found it.',
        'expect': ['test_the_abstract_travels_whole'],
        'file': _WS,
        'old': '            description = ("%s — %s" % (description, abstract)).lstrip(" —")',
        'new': '            description = ("%s — %s" % (description, abstract)).lstrip(" —")\n'
               '            abstract = abstract[:_ARXIV_DESC_CHARS]',
    },
    {
        'label': 'the abstract field is never filled',
        'why': 'carried and dropped, which is the dead-code class one level below '
               'a module.',
        'expect': ['test_the_abstract_travels_whole'],
        'file': _WS,
        'old': 'out.append(Result(title, url, description, "arXiv", abstract))',
        'new': 'out.append(Result(title, url, description, "arXiv", ""))',
    },
    {
        'label': 'format_results stops showing the abstract',
        'why': 'a field nobody prints is a field that did nothing. The whole '
               'point is that the paper reaches the answer the model reads.',
        'expect': ['test_format_results_shows_the_abstract'],
        'file': _WS,
        'old': '        if result.abstract and result.abstract not in result.description:',
        'new': '        if False:',
    },
]
