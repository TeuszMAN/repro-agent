"""Judge tests with real pytest output.

Each test writes a small test file (and sometimes a small module "with a bug") into a
temporary folder, runs the real pytest in a subprocess and gives the captured output
to the judge. This way the judge is checked against what pytest actually prints, in
the version installed in the project, instead of hand-written strings.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from repro_agent.judge import Outcome, PytestRun, judge

# A tiny "project under test" with a real bug: the discount divides by the quantity.
BUGGY_MODULE = """\
def total(price, quantity):
    return price * quantity


def unit_discount(discount, quantity):
    return discount / quantity
"""


def run_pytest(folder: Path, test_source: str, timeout: float = 30) -> PytestRun:
    """Writes test_repro.py into `folder` and runs pytest on it, like run_test will."""
    (folder / "calc.py").write_text(BUGGY_MODULE)
    (folder / "test_repro.py").write_text(test_source)
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "test_repro.py"]
    try:
        result = subprocess.run(
            command,
            cwd=folder,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        partial = error.stdout if isinstance(error.stdout, str) else ""
        return PytestRun(exit_code=None, output=partial, timed_out=True)
    return PytestRun(exit_code=result.returncode, output=result.stdout + result.stderr)


# Success


def test_assertion_error_is_a_reproduction(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import total

def test_total():
    assert total(10, 3) == 31
""",
    )
    verdict = judge(run)
    assert verdict.outcome is Outcome.REPRODUCED
    assert verdict.reproduced


def test_explicit_assertion_error_is_a_reproduction(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
def test_total():
    raise AssertionError("wrong total")
""",
    )
    assert judge(run).outcome is Outcome.REPRODUCED


def test_did_not_raise_is_a_reproduction(tmp_path: Path) -> None:
    """A missing validation is proven with pytest.raises, which fails with 'DID NOT RAISE'."""
    run = run_pytest(
        tmp_path,
        """\
import pytest
from calc import total

def test_negative_quantity_is_rejected():
    with pytest.raises(ValueError):
        total(10, -1)
""",
    )
    assert judge(run).outcome is Outcome.REPRODUCED


def test_exception_described_in_the_report_is_a_reproduction(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import unit_discount

def test_discount_with_zero_quantity():
    assert unit_discount(5, 0) == 0
""",
    )
    verdict = judge(run, expected_exception="ZeroDivisionError")
    assert verdict.outcome is Outcome.REPRODUCED


# Failure for the wrong reason


def test_passing_test_is_not_a_reproduction(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import total

def test_total():
    assert total(10, 3) == 30
""",
    )
    verdict = judge(run)
    assert verdict.outcome is Outcome.PASSED
    assert not verdict.reproduced


def test_exception_not_described_in_the_report(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import unit_discount

def test_discount_with_zero_quantity():
    assert unit_discount(5, 0) == 0
""",
    )
    verdict = judge(run)  # the report did not mention any exception
    assert verdict.outcome is Outcome.UNEXPECTED_EXCEPTION
    assert "ZeroDivisionError" in verdict.detail


def test_other_exception_than_the_one_in_the_report(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import total

def test_total():
    assert total(None, 3) == 0
""",
    )
    verdict = judge(run, expected_exception="ZeroDivisionError")
    assert verdict.outcome is Outcome.UNEXPECTED_EXCEPTION
    assert "TypeError" in verdict.detail


def test_import_error_of_missing_module(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calculator import total

def test_total():
    assert total(10, 3) == 31
""",
    )
    assert judge(run).outcome is Outcome.IMPORT_ERROR


def test_import_error_of_missing_name(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import grand_total

def test_total():
    assert grand_total(10, 3) == 31
""",
    )
    assert judge(run).outcome is Outcome.IMPORT_ERROR


def test_import_error_inside_the_test_function(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
def test_total():
    from calc import grand_total
    assert grand_total(10, 3) == 31
""",
    )
    assert judge(run).outcome is Outcome.IMPORT_ERROR


def test_syntax_error(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import total

def test_total(:
    assert total(10, 3) == 31
""",
    )
    assert judge(run).outcome is Outcome.SYNTAX_ERROR


def test_indentation_error(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
def test_total():
assert True
""",
    )
    assert judge(run).outcome is Outcome.SYNTAX_ERROR


def test_other_collection_error(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
raise RuntimeError("module crashed at import time")

def test_total():
    assert True
""",
    )
    assert judge(run).outcome is Outcome.COLLECTION_ERROR


def test_fixture_not_found(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
def test_total(order_with_items):
    assert order_with_items.total == 31
""",
    )
    verdict = judge(run)
    assert verdict.outcome is Outcome.FIXTURE_NOT_FOUND
    assert "order_with_items" in verdict.detail


def test_fixture_that_raises(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
import pytest

@pytest.fixture
def order():
    raise RuntimeError("could not build the order")

def test_total(order):
    assert order == 31
""",
    )
    assert judge(run).outcome is Outcome.SETUP_ERROR


def test_no_tests_collected(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
def check_total():
    assert False
""",
    )
    assert judge(run).outcome is Outcome.NO_TESTS


def test_timeout(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
import time

def test_total():
    time.sleep(60)
""",
        timeout=3,
    )
    assert run.timed_out
    assert judge(run).outcome is Outcome.TIMEOUT


def test_one_good_failure_and_one_wrong_failure_is_not_a_reproduction(tmp_path: Path) -> None:
    run = run_pytest(
        tmp_path,
        """\
from calc import total, unit_discount

def test_total():
    assert total(10, 3) == 31

def test_discount():
    assert unit_discount(5, 0) == 0
""",
    )
    assert judge(run).outcome is Outcome.UNEXPECTED_EXCEPTION


@pytest.mark.parametrize("exit_code", [3, 4])
def test_internal_or_usage_error(exit_code: int) -> None:
    """Exit codes 3 and 4 are pytest's own problems; the output here does not matter."""
    run = PytestRun(exit_code=exit_code, output="ERROR: usage error")
    assert judge(run).outcome is Outcome.PYTEST_ERROR
