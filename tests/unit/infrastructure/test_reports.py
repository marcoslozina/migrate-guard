from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from decimal import Decimal

from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.entities.difference import MISSING, Difference, DiffKind
from migrate_guard.domain.services.redaction import REDACTED, RedactionPolicy
from migrate_guard.domain.value_objects.json_pointer import JsonPointer
from migrate_guard.domain.value_objects.rules import IgnoreRule
from migrate_guard.infrastructure.adapters.reporters.json_reporter import (
    REPORT_VERSION,
    render_json_report,
)
from migrate_guard.infrastructure.adapters.reporters.junit_reporter import render_junit_report

POLICY = RedactionPolicy()


def diff(path: str, baseline: object, candidate: object, kind: DiffKind) -> Difference:
    return Difference(kind, JsonPointer.parse(path), baseline, candidate)  # type: ignore[arg-type]


RUN = RunResult(
    (
        CaseResult("cart", CaseStatus.MATCH),
        CaseResult(
            "one unit",
            CaseStatus.DIFFERENCE,
            differences=(
                diff("/line_total", Decimal("2.675"), Decimal("2.67"), DiffKind.VALUE_CHANGED),
                diff("/promo", "LOYAL2", MISSING, DiffKind.MISSING_IN_CANDIDATE),
                diff("/password", "hunter2", "hunter3", DiffKind.VALUE_CHANGED),
            ),
            state_differences=(
                diff("/prices/sku=B2/last_priced_at", None, "2026-11-09", DiffKind.TYPE_CHANGED),
            ),
        ),
        CaseResult.failed("lookup", "candidate: timed out\x00"),
    ),
    unused_rules=(IgnoreRule(path="/stale"),),
)


def parse(text: str) -> dict[str, object]:
    data: dict[str, object] = json.loads(text, parse_float=Decimal)
    return data


# --- JSON -------------------------------------------------------------------------------


def test_json_report_has_a_versioned_schema_and_a_summary() -> None:
    report = parse(render_json_report(RUN, POLICY))

    assert report["report_version"] == REPORT_VERSION == 1
    assert report["summary"] == {
        "cases": 3,
        "match": 1,
        "difference": 1,
        "error": 1,
        "exit_code": 1,
    }
    assert report["unused_rules"] == ["ignore /stale"]


def test_json_report_writes_numbers_exactly() -> None:
    case = parse(render_json_report(RUN, POLICY))["cases"][1]  # type: ignore[index]

    assert case["differences"][0] == {
        "kind": "value_changed",
        "path": "/line_total",
        "baseline": Decimal("2.675"),
        "candidate": Decimal("2.67"),
    }


def test_json_report_tells_a_missing_value_from_a_null() -> None:
    case = parse(render_json_report(RUN, POLICY))["cases"][1]  # type: ignore[index]

    missing = case["differences"][1]
    null = case["state_differences"][0]
    assert "candidate" not in missing
    assert "baseline" in null
    assert null["baseline"] is None


def test_json_report_redacts_sensitive_fields() -> None:
    text = render_json_report(RUN, POLICY)

    assert "hunter2" not in text
    assert parse(text)["cases"][1]["differences"][2]["baseline"] == REDACTED  # type: ignore[index]


def test_json_report_keeps_the_error_of_a_case_that_could_not_be_compared() -> None:
    cases = parse(render_json_report(RUN, POLICY))["cases"]

    assert cases[2]["status"] == "error"  # type: ignore[index]
    assert cases[2]["error"].startswith("candidate: timed out")  # type: ignore[index]
    assert cases[0]["error"] is None  # type: ignore[index]


# --- JUnit ------------------------------------------------------------------------------


def test_junit_report_has_one_testcase_per_case() -> None:
    root = ET.fromstring(render_junit_report(RUN, POLICY))

    suite = root.find("testsuite")
    assert suite is not None
    assert (suite.get("tests"), suite.get("failures"), suite.get("errors")) == ("3", "1", "1")
    assert [tc.get("name") for tc in suite.iter("testcase")] == ["cart", "one unit", "lookup"]


def test_junit_report_a_difference_is_a_failure_and_an_error_is_an_error() -> None:
    match, difference, error = ET.fromstring(render_junit_report(RUN, POLICY)).iter("testcase")

    assert list(match) == []
    failure = difference.find("failure")
    assert failure is not None
    assert failure.get("message") == "4 differences"
    assert "state /prices/sku=B2/last_priced_at  null" in (failure.text or "")
    assert error.find("error") is not None


def test_junit_report_redacts_and_stays_valid_xml_with_control_characters() -> None:
    text = render_junit_report(RUN, POLICY)

    assert "hunter2" not in text
    ET.fromstring(text)  # a raw \x00 from a response would make it unreadable


def test_junit_report_lists_unused_rules_in_the_suite_output() -> None:
    out = ET.fromstring(render_junit_report(RUN, POLICY)).find("testsuite/system-out")

    assert out is not None
    assert "rule never applied: ignore /stale" in (out.text or "")
