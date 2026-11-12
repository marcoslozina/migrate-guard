"""Reporter: JUnit XML, one testcase per case, so any CI shows the run as tests (SPEC R7).

A difference is a `<failure>`, a case that could not be compared is an `<error>`: CI tools
count them apart, and an error must never look like a pass.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET  # only builds XML, never parses untrusted input

from migrate_guard.domain.entities.case_result import CaseResult, CaseStatus, RunResult
from migrate_guard.domain.services.redaction import RedactionPolicy
from migrate_guard.infrastructure.adapters.reporters.plain_text import (
    case_lines,
    describe_rule,
    summary_text,
)

SUITE_NAME = "migrate-guard"
CLASS_NAME = "migrate_guard"

# Characters XML 1.0 can't hold at all, not even escaped. Responses can contain them.
_NOT_XML = re.compile("[^\t\n\r\x20-퟿-�\U00010000-\U0010ffff]")


def render_junit_report(run: RunResult, policy: RedactionPolicy) -> str:
    counts = {
        "tests": str(len(run.cases)),
        "failures": str(run.count(CaseStatus.DIFFERENCE)),
        "errors": str(run.count(CaseStatus.ERROR)),
        "skipped": "0",
    }
    root = ET.Element("testsuites", {"name": SUITE_NAME, **counts})
    suite = ET.SubElement(root, "testsuite", {"name": SUITE_NAME, **counts})
    for case in run.cases:
        _testcase(suite, case, policy)
    notes = [f"rule never applied: {describe_rule(rule)}" for rule in run.unused_rules]
    ET.SubElement(suite, "system-out").text = _xml_safe("\n".join([*notes, summary_text(run)]))
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _testcase(suite: ET.Element, case: CaseResult, policy: RedactionPolicy) -> None:
    element = ET.SubElement(
        suite, "testcase", {"classname": CLASS_NAME, "name": _xml_safe(case.name)}
    )
    if case.status is CaseStatus.MATCH:
        return
    lines = [_xml_safe(line) for line in case_lines(case, policy)]
    if case.status is CaseStatus.ERROR:
        tag, message = "error", lines[0]
    else:
        count = len(lines)
        tag, message = "failure", f"{count} difference{'s' if count != 1 else ''}"
    ET.SubElement(element, tag, {"type": tag, "message": message}).text = "\n".join(lines)


def _xml_safe(text: str) -> str:
    return _NOT_XML.sub("�", text)
