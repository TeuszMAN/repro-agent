"""Checks that the evaluation material is sound, before any agent uses it.

For the target project in evals/fixtures/pedido:
- its own tests pass on the correct version.

For each case in evals/cases/<id>:
- issue.md exists and is not empty;
- bug.patch applies cleanly on a copy of the correct version;
- the project's own tests still pass on the buggy version (the bug slipped through,
  which is why someone had to report it);
- a reference test written by us fails on the buggy version for the right reason,
  according to the judge, and passes on the correct version (fail-to-pass).

The reference tests live here, not in evals/cases, so the agent never sees them.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CASES_DIR, apply_patch, copy_fixture

from repro_agent.judge import Outcome, PytestRun, judge

REFERENCE_TESTS = {
    "frete_gratis_no_valor_minimo": """\
from decimal import Decimal
from pedido import Item, calculate_order

def test_free_delivery_at_exactly_the_minimum():
    order = calculate_order([Item(name="burger", unit_price=Decimal("25.00"), quantity=4)])
    assert order.delivery_fee == Decimal("0.00")
""",
    "desconto_maior_que_o_esperado": """\
from decimal import Decimal
from pedido import Coupon, CouponKind, Item, calculate_order

def test_percentage_coupon_does_not_discount_the_delivery_fee():
    coupon = Coupon(code="DEZ", kind=CouponKind.PERCENTAGE, value=Decimal("10"))
    order = calculate_order([Item(name="burger", unit_price=Decimal("25.00"), quantity=2)], coupon)
    assert order.discount == Decimal("5.00")
""",
    "centavo_a_mais": """\
from decimal import Decimal
from pedido import Coupon, CouponKind, Item, calculate_order

def test_percentage_discount_rounds_half_up():
    coupon = Coupon(code="QUINZE", kind=CouponKind.PERCENTAGE, value=Decimal("15"))
    order = calculate_order([Item(name="pizza", unit_price=Decimal("33.30"), quantity=1)], coupon)
    assert order.discount == Decimal("5.00")  # 15% of 33.30 is 4.995
""",
    "cupom_acima_de_100": """\
from decimal import Decimal
import pytest
from pedido import Coupon, CouponKind

def test_percentage_coupon_above_100_is_rejected():
    with pytest.raises(ValueError):
        Coupon(code="PROMO12", kind=CouponKind.PERCENTAGE, value=Decimal("120"))
""",
    "carrinho_vazio_cobra_taxa": """\
from decimal import Decimal
from pedido import calculate_order

def test_empty_cart_costs_nothing():
    assert calculate_order([]).total == Decimal("0.00")
""",
}

CASE_IDS = sorted(path.name for path in CASES_DIR.iterdir() if path.is_dir())


def run_pytest(project: Path, *args: str) -> PytestRun:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return PytestRun(exit_code=result.returncode, output=result.stdout + result.stderr)


def test_there_are_five_cases_with_reference_tests() -> None:
    assert len(CASE_IDS) == 5
    assert set(CASE_IDS) == set(REFERENCE_TESTS)


def test_fixture_own_tests_pass(tmp_path: Path) -> None:
    project = copy_fixture(tmp_path / "pedido")

    run = run_pytest(project)

    assert run.exit_code == 0, run.output


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_has_a_non_empty_issue(case_id: str) -> None:
    issue = CASES_DIR / case_id / "issue.md"
    assert issue.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_bug_slips_through_the_fixture_tests(case_id: str, tmp_path: Path) -> None:
    buggy = copy_fixture(tmp_path / "buggy")
    apply_patch(buggy, CASES_DIR / case_id / "bug.patch")

    run = run_pytest(buggy)

    assert run.exit_code == 0, run.output


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_reference_test_is_fail_to_pass(case_id: str, tmp_path: Path) -> None:
    correct = copy_fixture(tmp_path / "correct")
    buggy = copy_fixture(tmp_path / "buggy")
    apply_patch(buggy, CASES_DIR / case_id / "bug.patch")
    for project in (correct, buggy):
        (project / "tests" / "test_reference.py").write_text(REFERENCE_TESTS[case_id])

    on_buggy = run_pytest(buggy, "tests/test_reference.py")
    on_correct = run_pytest(correct, "tests/test_reference.py")

    assert judge(on_buggy).outcome is Outcome.REPRODUCED, on_buggy.output
    assert on_correct.exit_code == 0, on_correct.output
