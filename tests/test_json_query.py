"""json_query: asking about a JSON document's shape, and refusing to guess.

The tests that matter here are the negative ones. A missing field reported as
`null`, a partial sum reported as the total, or an unsupported `jq` construct
silently misresolved would all produce a *plausible* reply that is simply
wrong - so each is asserted against the specific sentence that distinguishes
it, and each has a control proving the assertion can fail.
"""

import json

import pytest

from shani_chronoa.skills import json_query as jq

DOC = {
    "name": "shanios",
    "version": 3,
    "tags": ["blue", "green", "immutable"],
    "result": {"rows": [{"price": 10, "sku": "a"}, {"price": 20, "sku": "b"}], "ok": True},
    "counts": [3, 1, 4, 1, 5],
    "mixed": [1, True, None, "x"],
    "a.b": "a key with a dot in it",
}


@pytest.fixture
def doc(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(DOC))
    return str(path)


def test_reads_a_document_and_answers_each_question(doc):
    assert jq._run({"path": doc, "operation": "keys"}).splitlines()[0].endswith("(7):")
    assert jq._run({"path": doc, "operation": "type"}) == "The document is an object."
    assert jq._run({"path": doc, "field": "result.rows[1].sku"}) == "b"
    assert "3 entries" in jq._run({"path": doc, "field": "tags", "operation": "length"})
    assert "14" in jq._run({"path": doc, "field": "counts", "operation": "sum"})
    assert "30" in jq._run({"path": doc, "field": "result.rows", "of": "price", "operation": "sum"})


def test_unique_and_sort_are_distinct_answers(doc):
    uniq = jq._run({"path": doc, "field": "counts", "operation": "unique"})
    assert uniq.splitlines()[0] == "4 unique value(s) in counts:"
    assert uniq.splitlines()[1:] == ["1", "3", "4", "5"]
    ordered = jq._run({"path": doc, "field": "tags", "operation": "sort"})
    assert ordered.splitlines()[1:] == ["blue", "green", "immutable"]


def test_a_document_can_be_passed_directly():
    assert jq._run({"document": '{"x": {"y": 42}}', "field": "x.y"}) == "42"


# --- the honesty rules -------------------------------------------------------


def test_a_missing_field_is_reported_absent_not_as_null(doc):
    """The defect this rule exists for: `jq` prints `null` for an absent key."""
    out = jq._run({"path": doc, "field": "nope"})
    assert out == "No field 'nope' in this document."
    assert "null" not in out, "an absent field must not be rendered as a value"
    # Control: the control must actually differ from the missing-field answer,
    # or the assertion above proves nothing.
    present = jq._run({"path": doc, "field": "name"})
    assert present == "shanios"


def test_the_dotted_key_hint_only_fires_when_it_is_relevant(doc):
    """A hint on every missing field would be noise that reads as a lead."""
    unrelated = jq._run({"path": doc, "field": "nope"})
    assert "dot" not in unrelated, "must not hint at 'a.b' when 'nope' was asked for"

    unreachable = jq._run({"path": doc, "field": "a.b"})
    assert "No field 'a'" in unreachable
    assert "'a.b'" in unreachable, "the unreachable key must be named"


def test_malformed_json_is_a_parse_error_not_an_empty_document():
    for text in ('{"a": 1,}', '{"a": ', "hello world", ""):
        out = jq._run({"document": text})
        if text == "":
            assert "nothing to read" in out
        else:
            assert "not valid JSON" in out, text
            assert "line" in out, "the position must be given"


def test_unsupported_jq_constructs_are_refused_by_name(doc):
    for path in ("result.rows | length", "..rows", "tags[]", "a+b", "items[0]?"):
        out = jq._run({"path": doc, "field": path})
        assert "Cannot" in out, path
        assert "read_text_file" in out, "the refusal must say what to use instead"


def test_sum_refuses_to_present_a_partial_total_as_the_total(doc):
    out = jq._run({"path": doc, "field": "mixed", "operation": "sum"})
    assert "Not a complete total" in out
    assert "3 of the 4 entries" in out
    # The excluded kinds are named from what was actually excluded: mixed
    # holds a string as well as a boolean and a null.
    assert "boolean, null, string" in out
    # Control: a clean array has no such caveat, so the caveat is not boilerplate.
    clean = jq._run({"path": doc, "field": "counts", "operation": "sum"})
    assert "Not a complete total" not in clean
    assert clean == "Sum of all 5 entries in counts: 14"


def test_sum_over_booleans_alone_is_refused_rather_than_counted(doc):
    """`bool` subclasses `int`, so `[true, true]` must not sum to 2."""
    out = jq._run({"document": '{"flags": [true, false, true]}',
                  "field": "flags", "operation": "sum"})
    assert "No numbers to sum" in out
    assert "3 entries are non-numbers" in out
    assert ": 2" not in out, "three booleans must never be summed into 2"


def test_indexing_and_walking_report_the_type_that_blocked_them(doc):
    assert "not an array" in jq._run({"path": doc, "field": "name[0]"})
    assert "has 3 entries" in jq._run({"path": doc, "field": "tags[9]"})
    assert "is a string, not an object" in jq._run({"path": doc, "field": "name.inner"})
    assert "has no 'qty'" in jq._run(
        {"path": doc, "field": "result.rows", "of": "qty", "operation": "sum"})


def test_mixed_types_are_refused_rather_than_ordered_arbitrarily(doc):
    out = jq._run({"path": doc, "field": "mixed", "operation": "unique"})
    assert "mixed types" in out
    assert "no common order" in out


def test_bad_arguments_are_refused_by_name(doc):
    assert "Operation must be one of" in jq._run({"path": doc, "operation": "delete"})
    assert "is a directory" in jq._run({"path": str(jq.__file__).rsplit("/", 1)[0]})
    assert "does not exist" in jq._run({"path": "/nonexistent/nope.json"})
    assert "nothing to read" in jq._run({})


# --- truncation has to disclose itself ---------------------------------------


def test_a_capped_list_says_how_many_were_withheld_and_how_to_get_them():
    document = {"rows": [{"n": i} for i in range(200)]}
    out = jq._run({"document": json.dumps(document), "field": "rows",
                   "of": "n", "operation": "sort"})
    assert "200 sorted value(s)" in out
    assert "not shown" in out, "a short list must disclose that it is short"
    assert "Narrow" in out, "the disclosure must say how to get the rest"
    # Control: the full count is present even though the list is capped, so the
    # cap is visible rather than mistaken for the whole answer.
    assert len(out.splitlines()) < 200


def test_a_list_at_the_cap_is_not_reported_as_withheld():
    document = {"rows": [{"n": i} for i in range(3)]}
    out = jq._run({"document": json.dumps(document), "field": "rows",
                   "of": "n", "operation": "sort"})
    assert "not shown" not in out


def test_path_parser_round_trips_and_refuses_junk():
    assert jq.parse_path(None) == ([], None)
    assert jq.parse_path(".") == ([], None)
    assert jq.parse_path("a.b[0].c")[0] == ["a", "b", 0, "c"]
    assert jq.parse_path("rows[0][2]")[0] == ["rows", 0, 2]
    assert jq.parse_path("rows[-1]")[0] == ["rows", -1]
    for bad in ("rows[0", "rows[a]", "a..b", "a|b", "x?"):
        steps, problem = jq.parse_path(bad)
        assert problem is not None, bad
        assert steps == [], "a refused path must never be half-parsed"


def test_the_schema_is_registerable():
    assert jq.SKILLS[0].name == "json_query"
    from shani_chronoa.skills import is_valid_schema
    assert is_valid_schema(jq.SKILLS[0].schema)


# --- the mutation controls: these prove the tests can fail -------------------


def test_control_removing_the_missing_field_rule_breaks_the_test(doc, monkeypatch):
    """Emptying `_missing` must break the absent-field assertions."""
    monkeypatch.setattr(jq, "_missing", lambda *a, **k: "")
    assert jq._run({"path": doc, "field": "nope"}) == ""
    assert jq._run({"path": doc, "field": "nope"}) != "No field 'nope' in this document."


def test_control_dropping_the_sum_caveat_breaks_the_test(doc, monkeypatch):
    """Coercing every entry to a number must remove the caveat.

    The first version of this control converted only the booleans, so `null`
    and a string were still excluded and the caveat still printed - the control
    could not fail and proved nothing.
    """
    real = jq._elements

    def everything_is_a_number(value, of, path):
        items, problem = real(value, of, path)
        if items is not None and not problem:
            items = [0 if item is True else (1 if item is False else
                    (0 if item is None else
                     (len(item) if isinstance(item, str) else item)))
                     for item in items]
        return items, problem

    monkeypatch.setattr(jq, "_elements", everything_is_a_number)
    out = jq._run({"path": doc, "field": "mixed", "operation": "sum"})
    assert "Not a complete total" not in out, "the caveat must be load-bearing"
    assert "Sum of all 4 entries" in out
    # The unpatched behaviour is asserted in full above; coercing the array is
    # what changed the answer, so the caveat was caused by the non-numbers and
    # nothing else. Nothing to assert here: the assertion is the absence.
