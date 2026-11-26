"""AGENTS.md is a short index; the dated record lives in docs/AGENTS-HISTORY.md.

The split moves text, it does not delete it. A reader of the index follows a
link to a verbatim section, so both halves are checked: the current-instruction
headings that must stay in `AGENTS.md`, and every moved heading that must still
be present in the archive. The archive's own index is the manifest; reading it
rather than keeping a second hand-written list here means a section cannot go
missing from one list while the test still passes.

The controls matter as much as the assertions. An empty archive index satisfies
"every indexed section exists", so the population size is asserted first, and
every top-level archived section must carry its own anchor. Level-3 headings
stay inside their moved parent; the archive's own index heading and level-1
title are not moved sections and are skipped by design.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
ARCHIVE = ROOT / "docs" / "AGENTS-HISTORY.md"

# The current-instructions index. Adding a section here is a deliberate edit to
# the index, so it must be accompanied by this list rather than happen by drift.
CURRENT_HEADINGS = (
    "## Skill source of truth",
    "## Start here (fast path)",
    "## What this repo is",
    "## Package layout and renames (2026-10-02) - read before grepping",
    "## What counts as done",
    "## Empirical verification (mandatory)",
    "## Test harness: shani-testbed (use it - and improve it, never invent around it)",
    "## Rule: verify by actually running it, not by reading it",
    "## Commit discipline",
    "## Boundaries",
    "## Required verification for a change",
    "## Model choice (as of 2026-09-16)",
    "## Packaging: two different naming conventions, don't mix them",
    "## Cross-repo impact",
    "## Deliberately not done (as of 2026-09-16)",
    "## History archive",
    "## Shared architecture (all repos)",
)

INDEX_LINE = re.compile(r"^- \[(s\d{4})\]\(#\1\) — (.+)$")
ANCHOR = re.compile(r'^<a id="s\d{4}"></a>$')
ARCHIVE_OWN_HEADING = "## Index of moved sections"


def _level_two(text):
    """Exact level-2 heading lines, so a mention of one in prose is not counted."""
    return [line for line in text.splitlines() if line.startswith("## ")]


class TestBothHalvesSurviveTheSplit:
    def test_every_current_heading_is_still_in_agents_md(self):
        present = set(_level_two(AGENTS.read_text(encoding="utf-8")))
        assert set(CURRENT_HEADINGS) <= present

    def test_no_history_section_crept_back_into_the_index(self):
        present = set(_level_two(AGENTS.read_text(encoding="utf-8")))
        # Equality in both directions: a lost current heading and a restored
        # history section are the two ways this index stops being an index.
        assert present == set(CURRENT_HEADINGS)

    def test_the_index_names_the_generated_skill_source_of_truth(self):
        text = AGENTS.read_text(encoding="utf-8")
        assert "CAPABILITIES.md" in text
        assert "tools/gen_capabilities.py" in text

    def test_the_index_links_to_the_archive(self):
        text = AGENTS.read_text(encoding="utf-8")
        assert "[docs/AGENTS-HISTORY.md](docs/AGENTS-HISTORY.md)" in text
        archive = ARCHIVE.read_text(encoding="utf-8")
        anchors = set(re.findall(r'^<a id="(s\d{4})"></a>$', archive, re.M))
        links = set(re.findall(r"docs/AGENTS-HISTORY\.md#(s\d{4})", text))
        assert links  # a control: no links would satisfy the check below
        assert links <= anchors, sorted(links - anchors)


class TestTheArchiveManifest:
    def _entries(self):
        found = []
        for line in ARCHIVE.read_text(encoding="utf-8").splitlines():
            match = INDEX_LINE.match(line)
            if match:
                found.append(match.groups())
        return found

    def test_the_manifest_is_not_empty(self):
        # Without this, an empty index satisfies the per-section checks below.
        assert len(self._entries()) >= 300

    def test_anchors_are_unique(self):
        anchors = [anchor for anchor, _ in self._entries()]
        assert len(anchors) == len(set(anchors))

    def test_every_indexed_section_exists_verbatim_in_the_archive(self):
        lines = ARCHIVE.read_text(encoding="utf-8").splitlines()
        for anchor, heading in self._entries():
            marker = f'<a id="{anchor}"></a>'
            assert lines.count(marker) == 1, marker
            at = lines.index(marker)
            assert lines[at + 1] in (f"## {heading}", f"### {heading}"), heading

    def test_every_archived_section_is_in_the_manifest(self):
        lines = ARCHIVE.read_text(encoding="utf-8").splitlines()
        indexed = {f'<a id="{anchor}"></a>' for anchor, _ in self._entries()}
        for at, line in enumerate(lines):
            if not line.startswith("## "):
                continue  # level-3 headings live inside their moved parent
            if line == ARCHIVE_OWN_HEADING:
                continue  # the archive's own preamble, not a moved section
            assert at > 0 and lines[at - 1] in indexed, line

    def test_no_archived_heading_is_still_a_heading_in_the_index(self):
        agents = AGENTS.read_text(encoding="utf-8")
        present = set(agents.splitlines())
        for _, heading in self._entries():
            assert f"## {heading}" not in present, heading
            assert f"### {heading}" not in present, heading

    def test_the_archive_still_holds_the_bulk_of_the_text(self):
        # A link with no text behind it is the failure this guards against.
        assert len(ARCHIVE.read_text(encoding="utf-8")) > 1_000_000
