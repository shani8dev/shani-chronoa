"""Mutations for the three-arm decoder run's control arm.

**The whole experiment is a claim about one variable**, so every mutation here
breaks a different way of breaking that claim, and each one is aimed at a test
that could fail. A mutation set for this machinery is mostly a way of asking
"if the arms quietly stopped being comparable, would anything notice?" - and the
answer has to be yes, or the 220-case numbers beside them are decoration.

The four that matter most are the last four: a control arm that quietly enforces,
a grammar arm that quietly does not, a dispatch that ignores which arm it is on,
and a flag that is set from the wrong config string.
"""

TEST_FILE = 'tests/test_three_arm_control.py'

_SRC = 'usr/lib/shani-chronoa/shani_chronoa/local_llm.py'
_EVAL = 'tools/task_eval.py'

MUTATIONS = [
    {
        'label': 'the grammar arm is also given the schema in prose',
        'why': 'then the two arms differ in two places - wording and enforcement '
               '- and a difference between them could be either. This is the '
               'failure the experiment exists to make impossible.',
        'expect': ['test_the_grammar_arm_carries_no_prose_shape'],
        'file': _SRC,
        'old': '    return base if enforce else base + _shape_request(schema)',
        'new': '    return base + _shape_request(schema)',
    },
    {
        'label': 'the control arm is given no shape at all',
        'why': 'the control becomes "ask for nothing in particular", which is a '
               'weaker request rather than the same request unenforced. That is '
               'a different experiment wearing the control arm\'s name.',
        'expect': ['test_the_decision_prompt_differs_only_by_the_shape',
                   'test_the_argument_prompt_differs_only_by_the_shape'],
        'file': _SRC,
        'old': '    return base if enforce else base + _shape_request(schema)',
        'new': '    return base',
    },
    {
        'label': 'the shape request stops naming the fields',
        'why': 'an unenforced prompt that does not say what to produce is not '
               'the same request minus the grammar; it is a different request.',
        'expect': ['test_it_names_every_field_and_marks_the_optional_ones',
                   'test_the_tool_choice_lists_the_offered_names_and_none'],
        'file': _SRC,
        'old': '    return ("\\n\\nReply with JSON only, no other text: {%s}"\n'
               '            % ", ".join(fields))',
        'new': '    return "\\n\\nReply with JSON only."',
    },
    {
        'label': 'the control arm enforces anyway',
        'why': '**the mutation that would have invalidated the run silently.** '
               'Both arms would carry a grammar, the control would stop being a '
               'control, and the scores would agree for the wrong reason.',
        'expect': ['test_unenforced_sends_nothing_at_all',
                   'test_the_request_arm_sends_no_grammar_and_still_makes_a_call'],
        'file': _SRC,
        'old': '                if enforce:\n'
               '                    more["response_format"] = _json_format(schema)',
        'new': '                if True:\n'
               '                    more["response_format"] = _json_format(schema)',
    },
    {
        'label': 'the grammar arm stops enforcing',
        'why': 'the mirror of the one above: `+greedy+constrained` would report '
               'a grammar run that sent no grammar, and a tie against it would '
               'read as "the grammar bought nothing" when nothing was tested.',
        'expect': ['test_enforced_sends_a_json_schema_response_format',
                   'test_the_grammar_arm_sends_a_grammar_on_both_steps'],
        'file': _SRC,
        'old': '                if enforce:\n'
               '                    more["response_format"] = _json_format(schema)',
        'new': '                if False:\n'
               '                    more["response_format"] = _json_format(schema)',
    },
    {
        'label': 'enforcement stops being the default',
        'why': 'every existing caller - the title generator included - would '
               'silently become the unenforced arm. No config would show it.',
        'expect': ['test_the_default_is_enforced'],
        'file': _SRC,
        'old': '            async def _json(self, messages, schema, temperature=None, enforce=True):',
        'new': '            async def _json(self, messages, schema, temperature=None, enforce=False):',
    },
    {
        'label': 'the dispatch ignores which arm it is on',
        'why': 'the flags would be right, the tests over them would stay green, '
               'and both arms would run the grammar. This is why the dispatch is '
               'driven end to end and not merely inspected.',
        'expect': ['test_the_request_arm_sends_no_grammar_and_still_makes_a_call'],
        'file': _SRC,
        'old': '                    called = await self._constrained(messages, tools, enforce=CONSTRAINED)',
        'new': '                    called = await self._constrained(messages, tools, enforce=True)',
    },
    {
        'label': 'the control arm is never switched on',
        'why': 'the arm named in CONFIGS would run the grammar, because the '
               'flags it sets would be False and the dispatch falls through to '
               'the unconstrained path - a different failure from "never ran".',
        'expect': ['test_the_control_arm_does_not_also_enforce'],
        'file': _EVAL,
        'old': '    local_llm.REQUEST_ONLY = config == "+greedy+request"',
        'new': '    local_llm.REQUEST_ONLY = False',
    },
    {
        'label': 'the grammar arm switches on for every greedy run',
        'why': 'the baseline would be contaminated: `+greedy` is the reference '
               'point every difference is measured against, and a grammar in it '
               'makes the comparison meaningless.',
        'expect': ['test_plain_greedy_has_neither'],
        'file': _EVAL,
        'old': '    local_llm.CONSTRAINED = config == "+greedy+constrained"',
        'new': '    local_llm.CONSTRAINED = config.startswith("+greedy")',
    },
    {
        'label': 'both arms are on at once',
        'why': '`+greedy+constrained` would also ask in prose, which is neither '
               'arm: the control\'s prompt with the grammar\'s payload.',
        'expect': ['test_the_grammar_arm_does_not_also_ask_in_prose'],
        'file': _EVAL,
        'old': '    local_llm.REQUEST_ONLY = config == "+greedy+request"',
        'new': '    local_llm.REQUEST_ONLY = config in ("+greedy+request", "+greedy+constrained")',
    },
]