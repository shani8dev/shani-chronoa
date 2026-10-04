"""analyze_table: read-only SQL over a CSV/XLSX/JSON, enforced by SQLite's authorizer, and SVG charts."""

import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from shani_chronoa import office
from shani_chronoa.skills import analyze_table as at

CSV = """Date,Category,Amount,Code
2026-09-01,Food,"1,200.50",007
2026-09-02,Rent,9000,008
2026-09-15,Food,300,009
2026-10-01,Travel,₹ 450,010
"""


@pytest.fixture
def home():
    return Path(os.environ["HOME"])


@pytest.fixture
def spend(home):
    p = home / "spend.csv"
    p.write_text(CSV)
    return p


def test_describe_types_columns(spend):
    out = at._run({"action": "describe", "path": str(spend)})
    assert "Table data: 4 rows" in out
    assert "- amount (was \"Amount\"): number, min 300, max 9000" in out
    assert "- code (was \"Code\"): text" in out, "leading-zero codes stay text"


def test_queries_answer_real_questions(spend):
    out = at._run({"action": "query", "path": str(spend),
                   "sql": "SELECT category, SUM(amount) AS total FROM data GROUP BY category ORDER BY total DESC"})
    assert out.splitlines()[:3] == ["| category | total |", "| Rent | 9000 |", "| Food | 1500.5 |"]
    month = at._run({"action": "query", "path": str(spend),
                     "sql": "SELECT substr(date,1,7) m, SUM(amount) FROM data GROUP BY m ORDER BY 2 DESC LIMIT 1"})
    assert "| 2026-09 | 10500.5 |" in month


@pytest.mark.parametrize("sql", [
    "DELETE FROM data", "DROP TABLE data", "INSERT INTO data VALUES (1,2,3,4)",
    "ATTACH DATABASE '/tmp/x.db' AS x", "PRAGMA table_info(data)", "CREATE TABLE t(x)",
    "SELECT * FROM data; DROP TABLE data",
])
def test_nothing_but_reading_runs(spend, sql):
    out = at._run({"action": "query", "path": str(spend), "sql": sql})
    assert out.startswith("Could not do that"), out
    assert spend.read_text() == CSV


def test_a_runaway_query_is_stopped(spend, monkeypatch):
    monkeypatch.setattr(at, "QUERY_SECONDS", 0.5)
    out = at._run({"action": "query", "path": str(spend), "sql":
                   "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT count(*) FROM n"})
    assert "was stopped" in out


def test_xlsx_sheets_are_tables_and_results_can_be_saved(home):
    book = home / "b.xlsx"
    book.write_bytes(office.make_xlsx({"Sales": [["Region", "Units"], ["North", 5], ["South", 7]],
                                       "Targets": [["Region", "Goal"], ["North", 4], ["South", 9]]}))
    out = at._run({"action": "query", "path": str(book), "save_as": "~/hit.xlsx", "sql":
                   "SELECT d.region, d.units >= t.goal AS hit FROM data d JOIN targets t USING(region)"})
    assert "| North | 1 |" in out and "| South | 0 |" in out and "Saved all 2" in out
    assert office.read_xlsx_rows(home / "hit.xlsx")["Result"][1] == ["North", "1"]


def test_json_records(home):
    p = home / "r.json"
    p.write_text('[{"name": "a", "n": 2}, {"name": "b", "n": 5, "extra": true}]')
    assert "| 7 |" in at._run({"action": "query", "path": str(p), "sql": "SELECT SUM(n) FROM data"})


def test_charts_are_valid_svg(spend, home):
    for kind in ("bar", "line", "pie"):
        out = at._run({"action": "chart", "path": str(spend), "kind": kind, "output": f"~/c-{kind}.svg",
                       "sql": "SELECT category, SUM(amount) FROM data GROUP BY category"})
        assert out.startswith(f"Saved a {kind} chart of 3 point"), out
        root = ET.parse(home / f"c-{kind}.svg").getroot()
        assert root.tag.endswith("svg")
    assert "already exists" in at._run({"action": "chart", "path": str(spend), "output": "~/c-bar.svg",
                                        "sql": "SELECT category, amount FROM data"})
    assert "must be numbers" in at._run({"action": "chart", "path": str(spend), "output": "~/x.svg",
                                         "sql": "SELECT date, category FROM data"})
