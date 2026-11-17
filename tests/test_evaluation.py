"""Evaluation runner tests with the fake model, on the real cases and fixture."""

import json
from pathlib import Path

import pytest
from frete_scenario import ANALYSIS, CASE_ID, GOOD_TEST, LOCATE_STEPS, PASSING_TEST
from typer.testing import CliRunner

from repro_agent import cli
from repro_agent.evaluation import (
    CaseResult,
    EvalReport,
    markdown_table,
    run_eval,
    save_results,
)
from repro_agent.fake_llm import FakeModel
from repro_agent.llm import Settings, TokenUsage

COUPON_CASE = "cupom_acima_de_100"

# "Reproduces" on the buggy version (no ValueError for 50%), but the correct version
# does not raise either, because 50% is a valid coupon. So it is not fail-to-pass.
WRONG_EXPECTATION_TEST = """\
```python
from decimal import Decimal

import pytest

from pedido import Coupon, CouponKind


def test_coupon_is_rejected():
    with pytest.raises(ValueError):
        Coupon(code="X", kind=CouponKind.PERCENTAGE, value=Decimal("50"))
```
"""


def two_case_model(second_case_tests: list[str], reflections: list[str]) -> FakeModel:
    """Script for two cases in a row: the free delivery case, then the coupon case."""
    return FakeModel(
        {
            "understand": [ANALYSIS, ANALYSIS],
            "locate": LOCATE_STEPS + LOCATE_STEPS,
            "write_test": [GOOD_TEST, *second_case_tests],
            "reflect": reflections,
        }
    )


def price(usage: TokenUsage) -> float:
    return usage.total / 1_000_000


def test_valid_reproduction_and_not_fail_to_pass() -> None:
    fake = two_case_model([WRONG_EXPECTATION_TEST], reflections=[])

    report = run_eval(fake, price, [CASE_ID, COUPON_CASE])

    valid, wrong = report.cases
    assert (valid.case_id, valid.status, valid.attempts) == (CASE_ID, "valid", 1)
    assert valid.input_tokens > 0
    assert valid.estimated_cost_usd == pytest.approx(
        (valid.input_tokens + valid.output_tokens) / 1_000_000
    )
    assert "def test_free_delivery_at_exactly_the_minimum" in valid.test_code
    assert (wrong.status, wrong.detail) == (
        "not_fail_to_pass",
        "on the correct version: reproduced",
    )
    assert report.valid_count == 1
    assert (report.provider, report.model) == ("fake", "fake-model")
    assert fake.unused_replies() == {}


def test_not_reproduced_after_all_attempts() -> None:
    fake = FakeModel(
        {
            "understand": [ANALYSIS],
            "locate": LOCATE_STEPS,
            "write_test": [PASSING_TEST] * 3,
            "reflect": ["try again"] * 2,
        }
    )

    [case] = run_eval(fake, price, [CASE_ID]).cases

    assert (case.status, case.attempts) == ("not_reproduced", 3)
    assert "passed" in case.detail


def test_model_error_is_recorded_and_the_next_case_runs() -> None:
    fake = FakeModel(
        {
            "understand": ["not json", "still not json", ANALYSIS],
            "locate": LOCATE_STEPS,
            "write_test": [GOOD_TEST],
        }
    )

    report = run_eval(fake, price, [COUPON_CASE, CASE_ID])

    assert [c.status for c in report.cases] == ["error", "valid"]


def test_unknown_case() -> None:
    with pytest.raises(ValueError, match="unknown cases: nope"):
        run_eval(FakeModel({}), price, ["nope"])


def report_with(*cases: CaseResult) -> EvalReport:
    return EvalReport(date="2026-11-17T04:30:00", provider="gemini", model="m/x", cases=list(cases))


def test_markdown_table_and_summary() -> None:
    report = report_with(
        CaseResult(
            case_id="a",
            status="valid",
            detail="",
            attempts=1,
            input_tokens=100,
            output_tokens=10,
            estimated_cost_usd=0.001,
            duration_seconds=3.21,
        ),
        CaseResult(
            case_id="b",
            status="not_reproduced",
            detail="",
            attempts=3,
            input_tokens=300,
            output_tokens=30,
            estimated_cost_usd=0.003,
        ),
        CaseResult(case_id="c", status="error", detail="timeout"),
    )

    table = markdown_table(report)

    assert "| `a` | reprodução válida | 1 | 100 / 10 | 0.0010 | 3.2 |" in table
    assert "| `c` | erro | 0 | 0 / 0 | não estimado | 0.0 |" in table
    assert "- Taxa de reprodução válida: 1/3 (33%)" in table
    assert "- Média de tentativas: 2.0" in table  # the error case made no attempt
    assert "- Custo total estimado: não estimado" in table  # one case has no cost


def test_save_results_names_files_by_date_and_model(tmp_path: Path) -> None:
    report = report_with(CaseResult(case_id="a", status="valid", detail=""))

    json_path, markdown_path = save_results(report, tmp_path / "results")

    assert json_path.name == "2026-11-17_043000_m-x.json"
    assert markdown_path.name == "2026-11-17_043000_m-x.md"
    assert json.loads(json_path.read_text())["cases"][0]["case_id"] == "a"


def test_cli_eval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPRO_PROVIDER", "deepseek")
    monkeypatch.setenv("REPRO_MODEL", "deepseek-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    fake = FakeModel({"understand": [ANALYSIS], "locate": LOCATE_STEPS, "write_test": [GOOD_TEST]})

    def create_model(settings: Settings) -> FakeModel:
        return fake

    monkeypatch.setattr(cli, "create_model", create_model)

    result = CliRunner().invoke(
        cli.app, ["eval", "--cases", CASE_ID, "--results-dir", str(tmp_path / "out")]
    )

    assert result.exit_code == 0, result.output
    assert "Taxa de reprodução válida: 1/1 (100%)" in result.stdout
    assert len(list((tmp_path / "out").glob("*_fake-model.json"))) == 1


def test_cli_eval_unknown_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPRO_PROVIDER", "deepseek")
    monkeypatch.setenv("REPRO_MODEL", "deepseek-test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

    result = CliRunner().invoke(cli.app, ["eval", "--cases", "nope"])

    assert result.exit_code == 2
    assert "unknown cases: nope" in result.stderr
