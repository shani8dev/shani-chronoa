"""Mutation set for `EventSink` refusing a non-callable field at construction.

`harness-study/laya-decision`'s `laya/hooks.py` states the rule:

> a plain callable is only accepted on the single-event `on_predict_start=`
> parameters, because `hooks=` reads for method names and a bare function has
> none.

An API that introspects must refuse the shape it cannot introspect. Chronoa already
learned this in `skills/__init__.py`, where `run=lambda a: ...` passed `callable()`
and failed on every call in a subprocess - the fix there was at the loader.

Before this change, `EventSink(on_text="not a function")` built cleanly and the
TypeError arrived at the call site: mid-turn, after the model had run. Every
construction site in the tree passes a real callable, so this is defence at the
seam rather than a behaviour change.

Three ways the guard is written wrong, each of which lets a real defect through
while looking correct:

- **`if value:` rather than `if value is not None and not callable(value):`** -
  0 and "" are falsy, so they pass as None and the exception moves back to the
  call site.
- **a single field checked rather than all six** - the one nobody checked is the
  one that fails.
- **`raise ValueError` or a silent `setattr(None)`** rather than a TypeError -
  a caller catching TypeError gets nothing, and a silent clear turns a wiring
  mistake into a turn with no streamed reply.
"""

TEST_FILE = "tests/test_sink_shape.py"

_EV = "usr/lib/shani-chronoa/shani_chronoa/events.py"

MUTATIONS = [
    # -- the guard exists at all --------------------------------------------
    {
        "file": _EV,
        "old": "            if value is not None and not callable(value):\n",
        "new": "            if False:\n",
        "label": "**the finding, restored**: a non-callable is accepted and the "
                 "TypeError arrives mid-turn",
        "expect": ["test_a_non_callable_is_refused_for_every_field"],
    },

    # -- three ways the guard is written wrong ------------------------------
    {
        "file": _EV,
        "old": "            if value is not None and not callable(value):\n",
        "new": "            if value and not callable(value):\n",
        "label": "the guard tests truthiness, so 0 and '' pass as None",
        "expect": ["test_a_zero_is_not_a_callable_and_not_none",
                   "test_a_false_bool_is_not_a_callable"],
    },
    {
        "file": _EV,
        "old": "        for field_name in (\"on_tool_start\", \"on_tool_finish\", \"on_text\",\n"
               "                          \"on_state\", \"on_compaction\", \"on_cloud_turn\"):\n",
        "new": "        for field_name in (\"on_tool_start\", \"on_tool_finish\", \"on_text\",\n"
               "                          \"on_state\", \"on_compaction\", \"on_cloud_turn\")[:1]:\n",
        "label": "only the first field is checked, so a wiring mistake on the "
                 "one nobody checked fails mid-turn",
        "expect": ["test_a_non_callable_is_refused_for_every_field"],
    },
    {
        "file": _EV,
        "old": "            if value is not None and not callable(value):\n"
               "                raise TypeError(\n"
               "                    f\"EventSink.{field_name} must be a callable or None, not \"\n"
               "                    f\"{type(value).__name__}: {value!r:.40}. A sink is built \"\n"
               "                    f\"once, before the turn runs; a value that is not callable \"\n"
               "                    f\"would raise here instead of in the middle of a reply.\")\n",
        "new": "            pass\n",
        "label": "the guard returns instead of raising, so a bad sink is built "
                 "and the turn runs with a dead field",
        "expect": ["test_a_non_callable_is_refused_for_every_field",
                   "test_the_message_names_the_field_and_the_type"],
    },

    # -- a valid sink must not be refused -----------------------------------
    {
        "file": _EV,
        "old": "            if value is not None and not callable(value):\n",
        "new": "            if not callable(value):\n",
        "label": "**the over-fix**: `None` is refused too, so every sink that "
                 "leaves a field unset cannot be built",
        "expect": ["test_an_empty_sink_builds",
                   "test_a_sink_with_no_fields_at_all_fires_nothing"],
    },
    {
        "file": _EV,
        "old": "        for field_name in (\"on_tool_start\", \"on_tool_finish\", \"on_text\",\n"
               "                          \"on_state\", \"on_compaction\", \"on_cloud_turn\"):\n",
        "new": "        for field_name in (\"on_text\",):\n",
        "label": "only one field is checked, so the other five lose their guard",
        "expect": ["test_a_non_callable_is_refused_for_every_field"],
    },
    {
        "file": _EV,
        "old": "                    f\"{type(value).__name__}: {value!r:.40}. A sink is built \"\n"
               "                    f\"once, before the turn runs; a value that is not callable \"\n"
               "                    f\"would raise here instead of in the middle of a reply.\")\n",
        "new": "                    f\"{type(value).__name__}.\")\n",
        # **A whole-block replacement, because changing only the first line left
        # the continuation dangling - a SyntaxError, which the runner now reports
        # as `MutationDidNotParse` rather than counting as a survivor.** The
        # message keeps the type and loses the reason: a refusal that says what is
        # wrong and nothing about why it is where it is.
        "label": "the refusal stops naming the type it was given",
        "expect": ["test_the_message_names_the_field_and_the_type"],
    },

    # -- a bound method, a lambda and a callable object still build ----------
    {
        "file": _EV,
        "old": "        for field_name in (\"on_tool_start\", \"on_tool_finish\", \"on_text\",\n"
               "                          \"on_state\", \"on_compaction\", \"on_cloud_turn\"):\n"
               "            value = getattr(self, field_name)\n",
        "new": "        for field_name in (\"on_tool_start\", \"on_tool_finish\", \"on_text\",\n"
               "                          \"on_state\", \"on_compaction\", \"on_cloud_turn\"):\n"
               "            value = getattr(self, field_name)\n"
               "            if value is not None and not isinstance(value, type(lambda: None)):\n"
               "                raise TypeError(\"a sink field must be a plain function\")\n",
        "label": "**the over-fix with a type check**: a bound method or a "
                 "callable object is refused, and the GUI sink is one of those",
        "expect": ["test_a_bound_method_and_a_lambda_build",
                   "test_a_callable_object_builds"],
    },
]
