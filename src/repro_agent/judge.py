"""Deterministic judge: decides, without an LLM, whether a generated test reproduced the bug.

The input is the raw result of running pytest on the generated test file. The judge
looks at three things, in this order:

1. whether the run hit the timeout;
2. the pytest exit code (0 = all passed, 1 = some test failed or errored,
   2 = interrupted, usually by a collection error, 5 = no tests collected);
3. the text of the report, to find out *why* it failed.

It relies on pytest's default traceback style (`--tb=auto`), where every failure
section ends with a line like `test_x.py:12: AssertionError`. That line tells us the
type of the exception that made the test fail. Do not run pytest with `--tb=no`,
`--tb=line` or `-q -q` when the output is meant for the judge.
"""

import re
from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    # Success: the test failed for the right reason.
    REPRODUCED = "reproduced"
    # Wrong reasons: the test does not prove anything about the bug.
    PASSED = "passed"
    NO_TESTS = "no_tests"
    TIMEOUT = "timeout"
    SYNTAX_ERROR = "syntax_error"
    IMPORT_ERROR = "import_error"
    COLLECTION_ERROR = "collection_error"
    FIXTURE_NOT_FOUND = "fixture_not_found"
    SETUP_ERROR = "setup_error"
    UNEXPECTED_EXCEPTION = "unexpected_exception"
    PYTEST_ERROR = "pytest_error"


@dataclass(frozen=True)
class PytestRun:
    """What the run_test node captured from the pytest subprocess."""

    exit_code: int | None  # None when the process was killed by the timeout
    output: str  # stdout and stderr together
    timed_out: bool = False


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    detail: str  # short explanation, useful for the reflect node and for logs

    @property
    def reproduced(self) -> bool:
        return self.outcome is Outcome.REPRODUCED


# Exceptions that mean "the assertion about the behavior did not hold".
# `Failed` is raised by pytest.fail() and by pytest.raises() when nothing is raised
# ("DID NOT RAISE"), which is how a test proves that a validation is missing.
ASSERTION_EXCEPTIONS = {"AssertionError", "Failed"}
IMPORT_EXCEPTIONS = {"ImportError", "ModuleNotFoundError"}
SYNTAX_EXCEPTIONS = {"SyntaxError", "IndentationError", "TabError"}

# Last line of a failure section: "path/to/file.py:12: ZeroDivisionError".
FAILURE_LOCATION = re.compile(r"^\S+\.py:\d+: (?P<exception>[\w.]+)$", re.MULTILINE)
# An exception line inside a traceback: "E   ModuleNotFoundError: No module named 'x'".
EXCEPTION_LINE = re.compile(r"^E\s+(?P<exception>[\w.]+(?:Error|Exception)):", re.MULTILINE)


def judge(run: PytestRun, expected_exception: str | None = None) -> Verdict:
    """Classifies a pytest run.

    `expected_exception` is the name of an exception that the bug report itself
    describes as the bug (for example "ZeroDivisionError"). A test that fails with
    it also counts as a reproduction.
    """
    if run.timed_out:
        return Verdict(Outcome.TIMEOUT, "pytest did not finish before the timeout")
    if run.exit_code == 0:
        return Verdict(Outcome.PASSED, "the test passed, so it does not reproduce the bug")
    if run.exit_code == 5:
        return Verdict(Outcome.NO_TESTS, "pytest did not collect any test")
    if "ERROR collecting" in run.output:
        return _collection_error(run.output)
    if "ERROR at setup" in run.output or "ERROR at teardown" in run.output:
        return _setup_error(run.output)
    if run.exit_code == 1:
        return _test_failures(run.output, expected_exception)
    return Verdict(Outcome.PYTEST_ERROR, f"pytest exited with code {run.exit_code}")


def _collection_error(output: str) -> Verdict:
    """The test module could not even be imported."""
    exceptions = _short_names(EXCEPTION_LINE.findall(output))
    if exceptions & SYNTAX_EXCEPTIONS:
        return Verdict(Outcome.SYNTAX_ERROR, "the test file has a syntax error")
    if exceptions & IMPORT_EXCEPTIONS:
        return Verdict(Outcome.IMPORT_ERROR, "an import in the test file failed")
    return Verdict(Outcome.COLLECTION_ERROR, "pytest could not collect the test file")


def _setup_error(output: str) -> Verdict:
    """The test body never ran because a fixture failed."""
    match = re.search(r"fixture '(?P<name>[^']+)' not found", output)
    if match:
        return Verdict(Outcome.FIXTURE_NOT_FOUND, f"fixture '{match['name']}' does not exist")
    return Verdict(Outcome.SETUP_ERROR, "a fixture raised an error before the test ran")


def _test_failures(output: str, expected_exception: str | None) -> Verdict:
    """The test body ran and failed. Now check that every failure is the right kind."""
    exceptions = _short_names(FAILURE_LOCATION.findall(output))
    if not exceptions:
        return Verdict(Outcome.PYTEST_ERROR, "could not find why the test failed")

    accepted = set(ASSERTION_EXCEPTIONS)
    if expected_exception:
        accepted.add(expected_exception.rsplit(".", 1)[-1])

    wrong = sorted(exceptions - accepted)
    if not wrong:
        return Verdict(Outcome.REPRODUCED, f"the test failed with {', '.join(sorted(exceptions))}")
    if set(wrong) <= IMPORT_EXCEPTIONS:
        return Verdict(Outcome.IMPORT_ERROR, "an import inside the test failed")
    return Verdict(
        Outcome.UNEXPECTED_EXCEPTION,
        f"the test failed with {', '.join(wrong)}, which the report does not describe as the bug",
    )


def _short_names(names: list[str]) -> set[str]:
    """'decimal.InvalidOperation' -> 'InvalidOperation', so names compare the same way."""
    return {name.rsplit(".", 1)[-1] for name in names}
