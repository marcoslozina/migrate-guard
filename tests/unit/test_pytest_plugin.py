"""The plugin, run in an inner pytest session (pytester) against two real services."""

from __future__ import annotations

import pytest

from tests.conftest import Services


def test_plugin_turns_each_case_into_a_test(pytester: pytest.Pytester, services: Services) -> None:
    config = services.write_config(pytester.path, "/same", "/diff")

    result = pytester.runpytest(f"--migrate-guard={config}", "-v")

    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines(
        [
            r".*migrate_guard\[diff\] FAILED",  # corpus order: by file name
            r".*migrate_guard\[same\] PASSED",
            r"/total  2\.675 → 2\.67  value_changed$",
        ]
    )


def test_plugin_failure_text_is_redacted(pytester: pytest.Pytester, services: Services) -> None:
    rules = "rules:\n  response:\n    - ignore: /total\n"
    config = services.write_config(pytester.path, "/diff", rules=rules)
    (pytester.path / "cases" / "diff.yaml").write_text(
        "name: diff\nrequest: {method: GET, path: /diff}\n"
    )

    result = pytester.runpytest(f"--migrate-guard={config}")

    result.assert_outcomes(passed=1)
    assert "hunter2" not in result.stdout.str()


def test_plugin_cases_can_be_selected_with_k(pytester: pytest.Pytester, services: Services) -> None:
    config = services.write_config(pytester.path, "/same", "/diff")

    result = pytester.runpytest(f"--migrate-guard={config}", "-k", "same")

    result.assert_outcomes(passed=1, deselected=1)


def test_plugin_an_unreachable_target_fails_the_case_instead_of_passing(
    pytester: pytest.Pytester, services: Services
) -> None:
    config = services.write_config(pytester.path, "/same")
    config.write_text(config.read_text().replace(services.candidate, "http://127.0.0.1:9"))

    result = pytester.runpytest(f"--migrate-guard={config}")

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["refused: target unreachable: candidate*"])


def test_plugin_a_bad_config_is_a_usage_error(pytester: pytest.Pytester) -> None:
    result = pytester.runpytest(f"--migrate-guard={pytester.path / 'nope.yaml'}")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*migrate-guard: can't read*"])


def test_plugin_does_nothing_without_the_option(pytester: pytest.Pytester) -> None:
    pytester.makepyfile("def test_ok():\n    pass\n")

    pytester.runpytest().assert_outcomes(passed=1)
