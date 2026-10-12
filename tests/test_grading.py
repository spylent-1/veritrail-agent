from evals.grading import grade_location
from evals.run import load_cases, summarize
from repopilot.models import EvidenceRef, RunReport
from repopilot.workspace import digest


def test_location_grade_requires_actual_current_cited_evidence(tmp_path):
    file = tmp_path / "shop.py"
    file.write_text("def remaining_stock(stock,quantity): return stock-quantity\n")
    case = {"file": "shop.py", "symbol": "remaining_stock"}
    report = RunReport(
        run_id="fixture",
        session_id="fixture",
        status="completed",
        stop_reason="ANSWER",
        answer="remaining_stock is in shop.py:1 [E1]",
        evidence=[
            EvidenceRef(
                id="E1", path="shop.py", start_line=1, end_line=1, sha256=digest(file.read_bytes())
            )
        ],
    )
    assert grade_location(case, report, tmp_path)
    report.answer = "remaining_stock is in shop.py:1 [E999]"
    assert not grade_location(case, report, tmp_path)
    report.answer = "remaining_stock is in shop.py:1 [E1]"
    file.write_text("changed")
    assert not grade_location(case, report, tmp_path)


def test_dataset_balance_and_partial_reports_are_not_release_claims():
    cases = load_cases()
    assert len(cases) == 12
    assert sum(c["kind"] == "edit" for c in cases) == 6
    assert sum(c["kind"] == "locate" and c["language"] == "java" for c in cases) == 4
    summary = summarize([], 3, 12)
    assert summary["gate"] == "partial" and summary["counts"]["edit"]["total"] == 0
