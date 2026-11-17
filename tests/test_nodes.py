"""Node tests, one node at a time, with the scripted fake model and the real `pedido` project."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from repro_agent import prompts
from repro_agent.fake_llm import FakeModel
from repro_agent.judge import Outcome, PytestRun
from repro_agent.nodes.judge import judge_node, tail
from repro_agent.nodes.locate import locate
from repro_agent.nodes.reflect import reflect
from repro_agent.nodes.run_test import run_test
from repro_agent.nodes.understand import understand
from repro_agent.nodes.write_test import extract_python_code, write_test
from repro_agent.state import AgentConfig, Attempt, IssueAnalysis, ReproState, initial_state

ISSUE = "Pedido de exatamente R$ 100,00 cobrou taxa de entrega de R$ 7,90."
ANALYSIS = IssueAnalysis(
    expected_behavior="Free delivery when the subtotal is exactly 100.00",
    observed_behavior="Delivery fee of 7.90 is charged",
    trigger="Subtotal of exactly 100.00",
    suspect_modules=["delivery"],
)
GOOD_TEST = """\
from decimal import Decimal

from pedido import Item, calculate_order


def test_free_delivery_at_exactly_the_minimum():
    order = calculate_order([Item(name="burger", unit_price=Decimal("25.00"), quantity=4)])
    assert order.delivery_fee == Decimal("0.00")
"""


def make_state(repo: Path, **changes: Any) -> ReproState:
    state = initial_state(ISSUE, str(repo), "frete")
    state.update(changes)  # type: ignore[typeddict-item]
    return state


def action(tool: str, **fields: str) -> str:
    return json.dumps({"thought": "", "tool": tool, **fields})


# understand


def test_understand_returns_structured_analysis(pedido: Path) -> None:
    reply = ANALYSIS.model_dump_json()
    fake = FakeModel({"understand": [reply]})

    result = understand(make_state(pedido), fake)

    assert result["analysis"] == ANALYSIS
    assert result["analysis"].expected_exception is None
    assert result["token_usage"]["understand"].total > 0
    call = fake.calls[0]
    assert call.json_mode
    assert ISSUE in call.messages[0].content


def test_understand_keeps_the_exception_that_is_the_bug(pedido: Path) -> None:
    reply = ANALYSIS.model_copy(update={"expected_exception": "ZeroDivisionError"})
    fake = FakeModel({"understand": [reply.model_dump_json()]})

    result = understand(make_state(pedido), fake)

    assert result["analysis"].expected_exception == "ZeroDivisionError"


# locate


def test_locate_runs_tools_and_turns_errors_into_observations(pedido: Path) -> None:
    fake = FakeModel(
        {
            "locate": [
                action("list_files", path="."),
                action("read_file", path="../secret.txt"),
                action("read_file", path="pedido/delivery.py"),
                action("search_text", query="FREE_DELIVERY_MINIMUM", path="pedido"),
                action("finish", summary="The bug is in pedido/delivery.py"),
            ]
        }
    )

    result = locate(make_state(pedido, analysis=ANALYSIS), fake, max_steps=8)

    assert result["findings"] == "The bug is in pedido/delivery.py"
    assert result["files_read"] == ["pedido/delivery.py"]
    assert result["token_usage"]["locate"].total > 0
    observations = [call.messages[-1].content for call in fake.calls[1:]]
    assert "pedido/delivery.py" in observations[0]  # list_files
    assert observations[1].startswith("Observation (step 2 of 8):\nError: '..' is not allowed")
    assert "def delivery_fee" in observations[2]  # read_file
    assert "pedido/delivery.py:8: FREE_DELIVERY_MINIMUM =" in observations[3]  # search_text
    assert fake.unused_replies() == {}


def test_locate_stops_at_the_step_limit(pedido: Path) -> None:
    fake = FakeModel({"locate": [action("read_file", path="pedido/order.py")] * 5})

    result = locate(make_state(pedido, analysis=ANALYSIS), fake, max_steps=3)

    assert len(fake.calls) == 3
    assert "step limit" in result["findings"]
    assert result["files_read"] == ["pedido/order.py"]  # read only once in the list


# write_test


def test_write_test_extracts_the_code_and_sends_the_sources(pedido: Path) -> None:
    fake = FakeModel({"write_test": [f"Here it is:\n```python\n{GOOD_TEST}```\nDone."]})
    state = make_state(
        pedido, analysis=ANALYSIS, findings="look at delivery", files_read=["pedido/delivery.py"]
    )

    result = write_test(state, fake)

    assert result["test_code"] == GOOD_TEST
    prompt = fake.calls[0].messages[0].content
    assert "FREE_DELIVERY_MINIMUM = Decimal" in prompt  # source of the file read by locate
    assert "tests/test_repro_frete.py" in prompt
    assert "Previous attempt" not in prompt
    assert result["token_usage"]["write_test"].total > 0


def test_write_test_shows_the_previous_attempt_and_reflection(pedido: Path) -> None:
    attempt = Attempt(1, "from pedido.frete import x\n", "...", Outcome.IMPORT_ERROR, "an import")
    fake = FakeModel({"write_test": [f"```python\n{GOOD_TEST}```"]})
    state = make_state(
        pedido, analysis=ANALYSIS, attempts=[attempt], reflections=["There is no pedido.frete"]
    )

    write_test(state, fake)

    prompt = fake.calls[0].messages[0].content
    assert "Attempt 1 did not reproduce the bug" in prompt
    assert "import_error" in prompt
    assert "from pedido.frete import x" in prompt
    assert "There is no pedido.frete" in prompt


def test_extract_python_code_without_a_code_block() -> None:
    assert extract_python_code("def test_x():\n    assert False") == (
        "def test_x():\n    assert False\n"
    )


# run_test


def test_run_test_runs_in_a_copy_and_leaves_the_project_untouched(
    buggy_pedido: Callable[[str], Path],
) -> None:
    project = buggy_pedido("frete_gratis_no_valor_minimo")
    before = sorted(p.relative_to(project) for p in project.rglob("*"))

    result = run_test(make_state(project, test_code=GOOD_TEST), AgentConfig())

    run = result["last_run"]
    assert run.exit_code == 1
    assert "tests/test_repro_frete.py:8: AssertionError" in run.output
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before


def test_run_test_passes_on_the_correct_project(pedido: Path) -> None:
    result = run_test(make_state(pedido, test_code=GOOD_TEST), AgentConfig())

    assert result["last_run"].exit_code == 0


def test_run_test_timeout(pedido: Path) -> None:
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"

    result = run_test(make_state(pedido, test_code=slow), AgentConfig(pytest_timeout_seconds=2))

    assert result["last_run"].timed_out


# judge node


def test_judge_node_records_the_attempt(pedido: Path) -> None:
    run = PytestRun(exit_code=0, output="1 passed")
    state = make_state(pedido, analysis=ANALYSIS, test_code=GOOD_TEST, last_run=run)

    result = judge_node(state)

    [attempt] = result["attempts"]
    assert (attempt.number, attempt.outcome, attempt.test_code) == (1, Outcome.PASSED, GOOD_TEST)


def test_judge_node_needs_a_run(pedido: Path) -> None:
    with pytest.raises(ValueError, match="last_run"):
        judge_node(make_state(pedido))


def test_tail_keeps_the_end() -> None:
    assert tail("abcdef", 10) == "abcdef"
    assert tail("abcdef", 2).endswith("\nef")


# reflect


def test_reflect_sees_the_failure_and_stores_the_explanation(pedido: Path) -> None:
    attempt = Attempt(
        1, "from pedido.frete import x\n", "E   ModuleNotFoundError", Outcome.IMPORT_ERROR, "boom"
    )
    fake = FakeModel({"reflect": ["Use `from pedido import calculate_order`."]})

    result = reflect(make_state(pedido, attempts=[attempt], findings="f"), fake)

    assert result["reflections"] == ["Use `from pedido import calculate_order`."]
    prompt = fake.calls[0].messages[0].content
    assert "E   ModuleNotFoundError" in prompt
    assert "import_error (boom)" in prompt
    assert result["token_usage"]["reflect"].total > 0


# prompts


def test_prompt_templates_refuse_missing_values() -> None:
    with pytest.raises(KeyError):
        prompts.render("understand.user.md")
