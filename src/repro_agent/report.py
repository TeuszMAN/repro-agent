"""The JSON report of one run (SPEC section 2): status, attempts, tokens, cost and time."""

from typing import Literal

from pydantic import BaseModel

from repro_agent.graph import ReproResult

Status = Literal["reproduced", "not_reproduced", "error"]


class AttemptReport(BaseModel):
    number: int
    outcome: str
    detail: str


class Tokens(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class RunReport(BaseModel):
    status: Status
    provider: str
    model: str
    repo: str
    test_id: str
    test_file: str | None = None  # where the test was written, if it was
    test_code: str = ""
    attempts: int = 0
    attempt_details: list[AttemptReport] = []
    tokens_by_node: dict[str, Tokens] = {}
    total_tokens: Tokens = Tokens()
    estimated_cost_usd: float | None = None  # None when prices are not configured
    duration_seconds: float = 0.0
    error: str | None = None


def build_report(
    result: ReproResult,
    provider: str,
    model: str,
    test_id: str,
    estimated_cost_usd: float | None,
    test_file: str | None = None,
) -> RunReport:
    state = result.state
    total = result.total_usage
    return RunReport(
        status="reproduced" if result.reproduced else "not_reproduced",
        provider=provider,
        model=model,
        repo=state["repo_path"],
        test_id=test_id,
        test_file=test_file,
        test_code=result.test_code,
        attempts=len(result.attempts),
        attempt_details=[
            AttemptReport(number=a.number, outcome=a.outcome.value, detail=a.detail)
            for a in result.attempts
        ],
        tokens_by_node={
            node: Tokens(input_tokens=u.input_tokens, output_tokens=u.output_tokens)
            for node, u in state["token_usage"].items()
        },
        total_tokens=Tokens(input_tokens=total.input_tokens, output_tokens=total.output_tokens),
        estimated_cost_usd=estimated_cost_usd,
        duration_seconds=round(result.duration_seconds, 2),
    )
