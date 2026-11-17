"""judge node: applies the deterministic judge to the last run and records the attempt."""

from typing import Any

from repro_agent.judge import judge
from repro_agent.state import Attempt, ReproState

# The attempt keeps only the end of the pytest output, where the failures are summarized.
MAX_OUTPUT_CHARS = 8_000


def judge_node(state: ReproState) -> dict[str, Any]:
    run = state["last_run"]
    if run is None:
        raise ValueError("judge_node needs last_run; run_test must come first")
    analysis = state["analysis"]
    verdict = judge(run, analysis.expected_exception if analysis else None)
    attempt = Attempt(
        number=len(state["attempts"]) + 1,
        test_code=state["test_code"],
        pytest_output=tail(run.output, MAX_OUTPUT_CHARS),
        outcome=verdict.outcome,
        detail=verdict.detail,
    )
    return {"attempts": state["attempts"] + [attempt]}


def tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "[... start of output truncated ...]\n" + text[-limit:]
