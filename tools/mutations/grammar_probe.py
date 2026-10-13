"""Mutations for the grammar probe.

**The probe is the precondition of a three-hour run.** If it cannot tell an
enforced grammar from an unenforced one, the run's headline - "the grammar bought
nothing" - is a statement about nothing having been tested. So the mutations here
aim at the *decision*, not at the plumbing, and the two worth naming are the ones
that would make it a control which cannot fail:

- **the unenforced-arm check removed**, so a model that simply chose the right
  tool would be reported as enforcement;
- **substring matching**, so the prose reply that names the tool in passing would
  be read as a tool call - which is the reply the off arm produces.

Both are the shape the file's docstring says it is written against, and both pass
on a probe that only checks the positive.
"""

TEST_FILE = 'tests/test_grammar_probe.py'

_SRC = 'tools/grammar_probe.py'

MUTATIONS = [
    {
        'label': 'the unenforced arm is never asked',
        'why': 'the control the whole probe rests on. Without it a model that '
               'simply chose get_datetime would be reported as enforcement - and '
               'the question is about the weather, so that is a real possibility.',
        'expect': ['test_both_arms_naming_the_tool_is_reported_as_undecidable',
                   'test_only_the_grammar_off_arm_naming_it_is_still_not_enforcement'],
        'file': _SRC,
        'old': '    off = chosen_tool(off_text)\n'
               '    if off == ONLY_TOOL:\n'
               '        return False, ("the grammar-off reply also named %r, so the model chose "',
        'new': '    off = None\n'
               '    if off == ONLY_TOOL:\n'
               '        return False, ("the grammar-off reply also named %r, so the model chose "',
    },
    {
        'label': 'the probe always reports the grammar as enforced',
        'why': 'a gate that cannot fail is a report, and the run depends on it '
               'being a gate.',
        'expect': ['test_the_measured_pair_is_enforced',
                   'test_an_unenforced_server_is_not_reported_as_enforced',
                   'test_a_server_that_answers_prose_under_the_grammar_is_not_enforced'],
        'file': _SRC,
        'old': '    if on != ONLY_TOOL:',
        'new': '    if False:',
    },
    {
        'label': 'a tool is recognised by the name appearing anywhere',
        'why': 'the prose reply - "You could use get_datetime for that" - would '
               'then read as a tool call, which is the very reply the '
               'unenforced arm produces.',
        'expect': ['test_prose_mentioning_the_tool_is_not_a_tool_call',
                   'test_prose_that_does_contain_the_name_still_reads_as_no_call',
                   'test_an_unenforced_server_is_not_reported_as_enforced'],
        'file': _SRC,
        'old': '    try:\n'
               '        return json.loads(text).get("tool")\n'
               '    except (ValueError, AttributeError):\n'
               '        return None',
        'new': '    if ONLY_TOOL in (text or ""):\n'
               '        return ONLY_TOOL\n'
               '    try:\n'
               '        return json.loads(text).get("tool")\n'
               '    except (ValueError, AttributeError):\n'
               '        return None',
    },
    {
        'label': 'the schema permits a second tool',
        'why': 'naming one of two permitted values proves nothing, so the '
               'probe could be satisfied by a choice rather than a constraint - '
               'and the test that says so would be testing a schema that is no '
               'longer the one in use.',
        'expect': ['test_the_enum_holds_exactly_one_value',
                   'test_the_response_format_is_the_shape_llama_server_turns_into_a_grammar'],
        'file': _SRC,
        'old': '               "properties": {"tool": {"type": "string", "enum": [ONLY_TOOL]}},',
        'new': '               "properties": {"tool": {"type": "string", "enum": [ONLY_TOOL, "get_weather"]}},',
    },
    {
        'label': 'the probe exits zero even when the grammar is off',
        'why': 'the exit code is the gate. A caller - a script, a CI job, the next '
               'person - reads the status, not the prose.',
        'expect': ['test_it_exits_non_zero_when_the_grammar_is_not_enforced'],
        'file': _SRC,
        'old': '    return 0 if ok else 1',
        'new': '    return 0',
    },
    {
        'label': 'the probe always starts its own server',
        'why': 'it would then compete with the very run it is a precondition '
               'for, and silently measure a loaded box rather than the one the '
               'run uses.',
        'expect': ['test_it_can_start_and_reuse_a_server'],
        'file': _SRC,
        'old': '    if not _server_up(args.port):',
        'new': '    if True:',
    },
    {
        'label': 'a server that cannot be asked looks like a server that answered wrong',
        'why': '**measured on this tool.** Pointed at a saturated server it raised '
               'an unhandled TimeoutError and died with a traceback - which a '
               'caller reading the exit status cannot tell from a probe that ran '
               'and found the grammar off. Both would read as "not enforced", and '
               'only one is a fact about the grammar.',
        'expect': ['test_a_crash_becomes_a_reason_not_a_traceback',
                   'test_an_unreachable_server_is_reported_as_unasked'],
        'file': _SRC,
        'old': '    except Exception as exc:  # noqa: BLE001 - any failure is "could not ask"',
        'new': '    except KeyError as exc:  # noqa: BLE001 - narrowed: now it crashes',
    },
    {
        'label': 'could not measure and grammar not enforced share an exit code',
        'why': 'the two are different facts about different things. Collapsing '
               'them tells the next person the grammar was tested when it never '
               'was - which is how a tie comes to read as a result.',
        'expect': ['test_the_exit_codes_are_three_distinct_things'],
        'file': _SRC,
        'old': 'UNMEASURED = 2',
        'new': 'UNMEASURED = 1',
    },
    {
        'label': 'the probe stops distinguishing which arm failed',
        'why': 'an operator pointing this at a busy box needs to know it got a '
               'reply the first time; "could not be asked" alone sends them to '
               'look in the wrong place.',
        'expect': ['test_the_off_arm_survives_when_the_on_arm_fails'],
        'file': _SRC,
        'old': '        return False, "the server could not be asked (grammar-on arm)", off, ""',
        'new': '        return False, "the server could not be asked", "", ""',
    },
    {
        'label': 'the question stops being one the right answer declines',
        'why': 'if get_datetime were a sensible answer to the question, the '
               'grammar-off arm would be entitled to give it and the probe '
               'cannot distinguish that from enforcement.',
        'expect': ['test_the_question_is_one_the_right_answer_declines'],
        'file': _SRC,
        'old': 'OFF_TOPIC = "What is the weather in Bengaluru right now?"',
        'new': 'OFF_TOPIC = "What time is it?"',
    },
]