"""Scripted replies for the free delivery case (frete_gratis_no_valor_minimo).

Shared by the graph, CLI and evaluation tests, so each test only says which replies it
uses. Each write_test reply is what a model would answer: the file in a code block.
"""

import json

from repro_agent.state import IssueAnalysis

CASE_ID = "frete_gratis_no_valor_minimo"

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


def script(write_tests: list[str], reflections: list[str] | None = None) -> dict[str, list[str]]:
    """Full fake model script: one understand reply, the locate steps and the given tests."""
    return {
        "understand": [ANALYSIS],
        "locate": list(LOCATE_STEPS),
        "write_test": write_tests,
        "reflect": reflections or [],
    }
