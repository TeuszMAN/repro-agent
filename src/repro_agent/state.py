"""The data that flows through the graph, and the agent settings that are not about the LLM."""

import sys
from dataclasses import dataclass, field
from typing import TypedDict

from pydantic import BaseModel

from repro_agent.judge import Outcome, PytestRun
from repro_agent.llm import TokenUsage


class IssueAnalysis(BaseModel):
    """Structured output of the understand node."""

    expected_behavior: str
    observed_behavior: str
    trigger: str
    suspect_modules: list[str] = []
    # Filled only when the report describes an exception as the bug itself.
    # The judge then accepts a test that fails with this exception.
    expected_exception: str | None = None


@dataclass(frozen=True)
class Attempt:
    """One write_test, run_test and judge round."""

    number: int
    test_code: str
    pytest_output: str  # possibly truncated, see nodes/judge.py
    outcome: Outcome
    detail: str


class ReproState(TypedDict):
    """Graph state. Each node reads what it needs and returns only the keys it changes."""

    issue: str  # bug report text
    repo_path: str  # target project; only read, never written
    test_id: str  # the test file is tests/test_repro_<test_id>.py
    analysis: IssueAnalysis | None
    files_read: list[str]
    findings: str  # summary written by locate
    test_code: str  # code of the current attempt
    last_run: PytestRun | None
    attempts: list[Attempt]
    reflections: list[str]
    token_usage: dict[str, TokenUsage]


def initial_state(issue: str, repo_path: str, test_id: str) -> ReproState:
    return ReproState(
        issue=issue,
        repo_path=repo_path,
        test_id=test_id,
        analysis=None,
        files_read=[],
        findings="",
        test_code="",
        last_run=None,
        attempts=[],
        reflections=[],
        token_usage={},
    )


@dataclass(frozen=True)
class AgentConfig:
    max_locate_steps: int = 8
    max_attempts: int = 3
    pytest_timeout_seconds: float = 60
    # Interpreter that runs the generated test. It must be able to import the target
    # project and pytest. Default: the interpreter running the agent.
    python_executable: str = field(default=sys.executable)


def repro_test_path(test_id: str) -> str:
    """Where the generated test goes, relative to the project root."""
    return f"tests/test_repro_{test_id}.py"
