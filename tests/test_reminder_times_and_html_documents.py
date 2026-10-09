"""Two skill bugs a recorded trip-planning demo showed on screen.

- `reminders` refused "tomorrow 9am" - the example in its own schema - and,
  for a time it could read, built "tomorrow" on today's date, so "tomorrow
  09:00" was set for this morning, already past.
- `create_document` in HTML put a markdown body into <p> blocks as-is, so an
  itinerary showed "## Flight - Boston..." as one run-on paragraph.
"""

from datetime import datetime, timedelta

import pytest

from shani_chronoa.skills import create_document, reminders


@pytest.mark.parametrize("words,hour,minute", [
    ("tomorrow 9am", 9, 0), ("tomorrow 09:00", 9, 0), ("tomorrow", 9, 0),
    ("tomorrow at 6:30 pm", 18, 30), ("Tomorrow 12am", 0, 0), ("tomorrow 12pm", 12, 0),
])
def test_tomorrow_is_tomorrow(words, hour, minute):
    due, problem = reminders._parse_due(words)
    assert not problem, problem
    tomorrow = datetime.now().date() + timedelta(days=1)
    assert (due.date(), due.hour, due.minute) == (tomorrow, hour, minute), due


def test_the_schema_example_parses():
    example = reminders.SCHEMA["function"]["parameters"]["properties"]["due"]["description"]
    assert "tomorrow 9am" in example
    assert reminders._parse_due("tomorrow 9am")[1] == ""


@pytest.mark.parametrize("words", ["tomorrow 25:00", "tomorrow 13pm", "tomorrow teatime", "someday"])
def test_an_unclear_time_is_refused_not_guessed(words):
    due, problem = reminders._parse_due(words)
    assert due is None and "Could not read" in problem, (due, problem)


def test_a_bare_clock_time_is_the_next_one():
    due, problem = reminders._parse_due("9pm")
    assert not problem and due > datetime.now() and due.hour == 21


def test_html_documents_render_the_markdown_body():
    html = create_document.markdown_to_html(
        "## Flight\n- Boston **to** London\n- second\n\nA paragraph\nthat wraps.")
    assert "<h2>Flight</h2>" in html
    assert "<ul><li>Boston <strong>to</strong> London</li><li>second</li></ul>" in html
    assert "<p>A paragraph that wraps.</p>" in html
    assert "##" not in html


def test_html_documents_escape_before_adding_tags():
    html = create_document.markdown_to_html("## <img src=x onerror=alert(1)>\n- <script>x</script> **<b>**")
    assert "<script>" not in html and "<img" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "<strong>&lt;b&gt;</strong>" in html
