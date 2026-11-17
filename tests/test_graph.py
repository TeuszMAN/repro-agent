"""Whole-graph tests with the scripted fake model, on `pedido` with a real bug.

The fake model replaces only the LLM. Everything else is real: the tools read the
project, pytest runs in a temporary copy and the judge reads the real output.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from frete_scenario import (
    ANALYSIS,
    CASE_ID,
    GOOD_TEST,
    ISSUE,
    LOCATE_STEPS,
    PASSING_TEST,
    REFLECTION,
    WRONG_IMPORT_TEST,
)

from repro_agent.fake_llm import FakeModel
from repro_agent.graph import reproduce
from repro_agent.judge import Outcome
from repro_agent.state import AgentConfig


@pytest.fixture
def buggy_project(buggy_pedido: Callable[[str], Path]) -> Path:
    return buggy_pedido(CASE_ID)


def test_reproduces_on_the_first_attempt(buggy_project: Path) -> None:
    fake = FakeModel({"understand": [ANALYSIS], "locate": LOCATE_STEPS, "write_test": [GOOD_TEST]})

    result = reproduce(ISSUE, buggy_project, "frete", fake, AgentConfig())

    assert result.reproduced
    assert [a.outcome for a in result.attempts] == [Outcome.REPRODUCED]
    assert "def test_free_delivery_at_exactly_the_minimum" in result.test_code
    assert result.state["files_read"] == ["tests/test_order.py", "pedido/delivery.py"]
    assert result.state["reflections"] == []
    assert set(result.state["token_usage"]) == {"understand", "locate", "write_test"}
    assert result.total_usage.total > 0
    assert fake.unused_replies() == {}
    assert not (buggy_project / "tests" / "test_repro_frete.py").exists()


def test_wrong_import_then_reflects_and_reproduces(buggy_project: Path) -> None:
    fake = FakeModel(
        {
            "understand": [ANALYSIS],
            "locate": LOCATE_STEPS,
            "write_test": [WRONG_IMPORT_TEST, GOOD_TEST],
            "reflect": [REFLECTION],
        }
    )

    result = reproduce(ISSUE, buggy_project, "frete", fake, AgentConfig())

    assert result.reproduced
    assert [a.outcome for a in result.attempts] == [Outcome.IMPORT_ERROR, Outcome.REPRODUCED]
    assert result.state["reflections"] == [REFLECTION]
    # The reflect node saw the real ModuleNotFoundError from pytest...
    reflect_prompt = next(c for c in fake.calls if c.node == "reflect").messages[0].content
    assert "ModuleNotFoundError: No module named 'pedido.frete'" in reflect_prompt
    # ...and the second write_test saw the failed attempt and the reflection.
    second_write = [c for c in fake.calls if c.node == "write_test"][1].messages[0].content
    assert "from pedido.frete import calcular_frete" in second_write
    assert REFLECTION in second_write
    assert set(result.state["token_usage"]) == {"understand", "locate", "write_test", "reflect"}
    assert fake.unused_replies() == {}


def test_stops_after_three_attempts(buggy_project: Path) -> None:
    fake = FakeModel(
        {
            "understand": [ANALYSIS],
            "locate": LOCATE_STEPS,
            "write_test": [PASSING_TEST] * 3,
            "reflect": ["The test does not use a subtotal of exactly 100."] * 2,
        }
    )

    result = reproduce(ISSUE, buggy_project, "frete", fake, AgentConfig())

    assert not result.reproduced
    assert [a.outcome for a in result.attempts] == [Outcome.PASSED] * 3
    assert len(result.state["reflections"]) == 2  # no reflection after the last attempt
    assert fake.unused_replies() == {}


def test_max_attempts_is_configurable(buggy_project: Path) -> None:
    fake = FakeModel(
        {"understand": [ANALYSIS], "locate": LOCATE_STEPS, "write_test": [PASSING_TEST]}
    )

    result = reproduce(ISSUE, buggy_project, "frete", fake, AgentConfig(max_attempts=1))

    assert [a.outcome for a in result.attempts] == [Outcome.PASSED]


def test_recursion_limit_fits_many_attempts(buggy_project: Path) -> None:
    """7 attempts need 2 + 7 * 3 + 6 = 29 node runs, all within the explicit limit."""
    fake = FakeModel(
        {
            "understand": [ANALYSIS],
            "locate": LOCATE_STEPS,
            "write_test": [PASSING_TEST] * 7,
            "reflect": ["try again"] * 6,
        }
    )

    result = reproduce(ISSUE, buggy_project, "frete", fake, AgentConfig(max_attempts=7))

    assert len(result.attempts) == 7
