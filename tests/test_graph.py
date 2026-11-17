"""Whole-graph tests with the scripted fake model, on `pedido` with a real bug.

The fake model replaces only the LLM. Everything else is real: the tools read the
project, pytest runs in a temporary copy and the judge reads the real output.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from repro_agent.fake_llm import FakeModel
from repro_agent.graph import reproduce
from repro_agent.judge import Outcome
from repro_agent.state import AgentConfig, IssueAnalysis

ISSUE = """\
# Frete grátis não vale para pedido de exatamente R$ 100,00

Fiz um pedido de 4 hambúrgueres de R$ 25,00 e foi cobrada a taxa de entrega de R$ 7,90.
"""

ANALYSIS = IssueAnalysis(
    expected_behavior="Free delivery when the subtotal is exactly 100.00",
    observed_behavior="A delivery fee of 7.90 is charged",
    trigger="Four items of 25.00, subtotal exactly 100.00",
    suspect_modules=["delivery"],
).model_dump_json()

LOCATE_STEPS = [
    json.dumps({"thought": "see the tests", "tool": "read_file", "path": "tests/test_order.py"}),
    json.dumps({"thought": "fee rule", "tool": "read_file", "path": "pedido/delivery.py"}),
    json.dumps(
        {
            "tool": "finish",
            "summary": "delivery_fee in pedido/delivery.py uses > instead of >=. "
            "Tests import with `from pedido import Item, calculate_order`.",
        }
    ),
]

GOOD_TEST = """\
```python
from decimal import Decimal

from pedido import Item, calculate_order


def test_free_delivery_at_exactly_the_minimum():
    \"\"\"A subtotal equal to the minimum gets free delivery.\"\"\"
    order = calculate_order([Item(name="burger", unit_price=Decimal("25.00"), quantity=4)])
    assert order.delivery_fee == Decimal("0.00")
```
"""

WRONG_IMPORT_TEST = """\
```python
from decimal import Decimal

from pedido.frete import calcular_frete


def test_free_delivery_at_exactly_the_minimum():
    assert calcular_frete(Decimal("100.00")) == Decimal("0.00")
```
"""

PASSING_TEST = """\
```python
from decimal import Decimal

from pedido import Item, calculate_order


def test_free_delivery_above_the_minimum():
    order = calculate_order([Item(name="burger", unit_price=Decimal("25.00"), quantity=5)])
    assert order.delivery_fee == Decimal("0.00")
```
"""

REFLECTION = (
    "The module pedido.frete does not exist. Import Item and calculate_order from pedido, "
    "as tests/test_order.py does."
)


@pytest.fixture
def buggy_project(buggy_pedido: Callable[[str], Path]) -> Path:
    return buggy_pedido("frete_gratis_no_valor_minimo")


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
