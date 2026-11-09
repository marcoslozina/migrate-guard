from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from migrate_guard.cli.app import app

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
