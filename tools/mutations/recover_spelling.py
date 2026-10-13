"""Mutations for the `function` spelling in `recover_tool_calls`.

Found by running the balanced-adapter ladder, not by reading code: the model
answered "make a reminder" with `{"arguments": {...}, "function": "reminders"}`
and the call was left as text. Same class as the XML spelling this file already
documents - one more key a small model converges on - and it is gated by the
offered-set rule, which is the property that must survive the change.
"""

TEST_FILE = 'tests/test_text_tool_calls_recovered.py'

_SRC = 'usr/lib/shani-chromoa/shani_chronoa/local_llm.py'
_SRC = 'usr/lib/shani-chronoa/shani_chronoa/local_llm.py'

MUTATIONS = [
    {
        'label': 'the flat `function` spelling is not read',
        'why': 'the measured defect. Correct tool and args, left as text, scored '
               'as "no tool" - which reads as the model being unable to call '
               'tools when it was a key name.',
        'expect': ['test_the_flat_function_key_names_the_tool'],
        'file': _SRC,
        'old': '                nested = obj.get("function")\n'
               '                name = nested.get("name") if isinstance(nested, dict) \\\n'
               '                    else nested',
        'new': '                name = None',
    },
    {
        'label': 'the nested OpenAI wrapper is not unwrapped',
        'why': 'half the spellings. A fixture offering only the flat form would '
               'pass, so the flat and nested cases are separate tests.',
        'expect': ['test_the_nested_openai_wrapper_is_unwrapped'],
        'file': _SRC,
        'old': '                nested = obj.get("function")\n'
               '                name = nested.get("name") if isinstance(nested, dict) \\\n'
               '                    else nested',
        'new': '                name = None',
    },
    {
        'label': 'a non-string name is allowed through to the offered set',
        'why': '`name in offered` raises on an unhashable, so a reply carrying '
               'a list or a dict takes the turn down after the model has already '
               'answered. This was the first version of the fix, caught by a test '
               'built for it rather than by reading it.',
        'expect': ['test_a_function_key_that_is_not_a_tool_name_stays_text'],
        'file': _SRC,
        'old': '        if not isinstance(name, str) or not isinstance(args, dict):\n'
               '            name, args = None, {}',
        'new': '        if not isinstance(args, dict):\n            name, args = None, {}',
    },
    {
        'label': 'the offered-set gate stops applying to the new spelling',
        'why': 'a second key must not become a second way to call a tool nobody '
               'offered. This is the safety property of the whole function.',
        'expect': [
            'test_a_function_key_naming_a_tool_that_was_not_offered_stays_text',
        ],
        'file': _SRC,
        'old': '        if name in offered and isinstance(args, dict):',
        'new': '        if name in offered or True and isinstance(args, dict):',
    },
]
