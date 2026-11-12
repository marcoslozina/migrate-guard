from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from migrate_guard.cli.app import app
from tests.conftest import Services

runner = CliRunner()


def test_run_when_config_is_missing_then_exit_code_is_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "--config", str(tmp_path / "nope.yaml")])

    assert result.exit_code == 2
    assert "refused: can't read" in result.output


def test_run_when_target_is_remote_then_refuses_without_allow_remote(tmp_path: Path) -> None:
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "a.yaml").write_text("name: a\nrequest: {method: GET, path: /}\n")
    config = tmp_path / "migrate-guard.yaml"
    config.write_text(
        "version: 1\n"
        "baseline: {target: http, url: 'http://127.0.0.1:5001'}\n"
        "candidate: {target: http, url: 'https://prod.example.com'}\n"
        "corpus: cases/\n"
    )

    result = runner.invoke(app, ["run", "--config", str(config)])

    assert result.exit_code == 2
    assert "--allow-remote" in result.output


# --- reports (SPEC R5, R7), against two real services on loopback -------------------------


def test_run_json_report_alone_goes_to_stdout_as_valid_json(
    services: Services, tmp_path: Path
) -> None:
    config = services.write_config(tmp_path, "/same", "/diff")

    result = runner.invoke(app, ["run", "-c", str(config), "--report", "json"])

    assert result.exit_code == 1
    report = json.loads(result.stdout, parse_float=Decimal)
    assert report["summary"]["difference"] == 1
    diff = report["cases"][0]["differences"][0]
    assert (diff["baseline"], diff["candidate"]) == (Decimal("2.675"), Decimal("2.67"))
    assert "hunter2" not in result.stdout


def test_run_junit_report_to_a_file_keeps_the_terminal_summary(
    services: Services, tmp_path: Path
) -> None:
    config = services.write_config(tmp_path, "/same")
    out = tmp_path / "report.xml"

    result = runner.invoke(app, ["run", "-c", str(config), "--report", "junit", "-o", str(out)])

    assert result.exit_code == 0
    assert "1 case: 1 match" in result.stdout
    assert ET.fromstring(out.read_text()).get("tests") == "1"
    assert out.stat().st_mode & 0o077 == 0  # it can hold test data: owner only
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".report")] == []


def test_run_when_the_report_can_not_be_written_then_exit_code_is_2(
    services: Services, tmp_path: Path
) -> None:
    config = services.write_config(tmp_path, "/same")
    out = tmp_path / "missing-dir" / "report.json"

    result = runner.invoke(app, ["run", "-c", str(config), "--report", "json", "-o", str(out)])

    assert result.exit_code == 2
    assert "can't write the report" in result.stderr


def test_run_refusal_goes_to_stderr_so_stdout_stays_a_report(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "-c", str(tmp_path / "nope.yaml"), "--report", "json"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "refused" in result.stderr


def test_run_output_without_a_file_format_is_refused(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", "-c", str(tmp_path / "x.yaml"), "-o", "r.txt"])

    assert result.exit_code == 2
    assert "--output needs --report json" in result.stderr
