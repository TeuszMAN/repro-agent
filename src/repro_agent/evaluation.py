"""Evaluation runner (SPEC section 6): runs the agent on every case and measures it.

For each case in evals/cases/<id>/:
1. copies the correct project (evals/fixtures/pedido) to a temporary folder and applies
   bug.patch with `git apply`;
2. runs the agent on the buggy copy;
3. checks fail-to-pass: the generated test must fail on the buggy version (for the right
   reason, according to the judge) and pass on the correct version. Only this counts as
   a valid reproduction;
4. records status, attempts, tokens, estimated cost and time.

The results go to evals/results/<date>_<model>.json and a Markdown table next to it.
"""

import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from repro_agent.graph import reproduce
from repro_agent.judge import PytestRun, judge
from repro_agent.llm import ChatModel, LLMError, TokenUsage
from repro_agent.nodes.run_test import IGNORED, run_test_in_copy
from repro_agent.state import AgentConfig, IssueAnalysis, repro_test_path

logger = logging.getLogger(__name__)

# The evals folder of this repository (src/repro_agent/evaluation.py -> repo root).
DEFAULT_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"

CaseStatus = Literal["valid", "not_reproduced", "not_fail_to_pass", "error"]

STATUS_LABELS: dict[str, str] = {
    "valid": "reprodução válida",
    "not_reproduced": "não reproduziu",
    "not_fail_to_pass": "não é fail-to-pass",
    "error": "erro",
}


class CaseResult(BaseModel):
    case_id: str
    status: CaseStatus
    detail: str
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None
    duration_seconds: float = 0.0
    test_code: str = ""


class EvalReport(BaseModel):
    date: str
    provider: str
    model: str
    cases: list[CaseResult]

    @property
    def valid_count(self) -> int:
        return sum(1 for case in self.cases if case.status == "valid")

    @property
    def mean_attempts(self) -> float:
        """Average over the cases where the agent made at least one attempt."""
        ran = [case.attempts for case in self.cases if case.attempts > 0]
        return sum(ran) / len(ran) if ran else 0.0

    @property
    def total_cost_usd(self) -> float | None:
        costs = [case.estimated_cost_usd for case in self.cases]
        if any(cost is None for cost in costs):
            return None
        return sum(cost for cost in costs if cost is not None)


def available_cases(evals_dir: Path) -> list[str]:
    return sorted(path.name for path in (evals_dir / "cases").iterdir() if path.is_dir())


def run_eval(
    llm: ChatModel,
    estimate_cost: Callable[[TokenUsage], float | None],
    case_ids: list[str] | None = None,
    evals_dir: Path = DEFAULT_EVALS_DIR,
    config: AgentConfig | None = None,
) -> EvalReport:
    """Runs the given cases (all of them by default), one after the other."""
    config = config or AgentConfig()
    known = available_cases(evals_dir)
    case_ids = case_ids or known
    unknown = sorted(set(case_ids) - set(known))
    if unknown:
        raise ValueError(f"unknown cases: {', '.join(unknown)}. Available: {', '.join(known)}")

    results = []
    for case_id in case_ids:
        logger.info("case %s: start", case_id)
        result = run_case(case_id, llm, estimate_cost, evals_dir, config)
        logger.info("case %s: %s (%s)", case_id, result.status, result.detail)
        results.append(result)
    return EvalReport(
        date=datetime.now().isoformat(timespec="seconds"),
        provider=llm.provider,
        model=llm.model,
        cases=results,
    )


def run_case(
    case_id: str,
    llm: ChatModel,
    estimate_cost: Callable[[TokenUsage], float | None],
    evals_dir: Path,
    config: AgentConfig,
) -> CaseResult:
    case_dir = evals_dir / "cases" / case_id
    issue = (case_dir / "issue.md").read_text(encoding="utf-8")
    fixture = evals_dir / "fixtures" / "pedido"

    with tempfile.TemporaryDirectory(prefix="repro-eval-") as tmp:
        buggy = Path(tmp) / "buggy" / fixture.name
        correct = Path(tmp) / "correct" / fixture.name
        shutil.copytree(fixture, buggy, ignore=IGNORED)
        shutil.copytree(fixture, correct, ignore=IGNORED)
        apply_patch(buggy, case_dir / "bug.patch")

        try:
            result = reproduce(issue, buggy, case_id, llm, config)
        except LLMError as error:
            return CaseResult(case_id=case_id, status="error", detail=str(error))

        usage = result.total_usage
        base = CaseResult(
            case_id=case_id,
            status="not_reproduced",
            detail=result.attempts[-1].detail if result.attempts else "no attempt",
            attempts=len(result.attempts),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=estimate_cost(usage),
            duration_seconds=round(result.duration_seconds, 2),
            test_code=result.test_code,
        )
        if not result.reproduced:
            return base

        status, detail = check_fail_to_pass(
            result.test_code, case_id, buggy, correct, config, result.state["analysis"]
        )
        return base.model_copy(update={"status": status, "detail": detail})


def check_fail_to_pass(
    test_code: str,
    case_id: str,
    buggy: Path,
    correct: Path,
    config: AgentConfig,
    analysis: IssueAnalysis | None,
) -> tuple[CaseStatus, str]:
    """Runs the generated test on both versions, each in a fresh copy."""
    test_path = repro_test_path(case_id)
    expected_exception = analysis.expected_exception if analysis else None

    def run(project: Path) -> PytestRun:
        return run_test_in_copy(
            project, test_path, test_code, config.python_executable, config.pytest_timeout_seconds
        )

    on_buggy = judge(run(buggy), expected_exception)
    if not on_buggy.reproduced:
        return "not_fail_to_pass", f"on the buggy version: {on_buggy.outcome.value}"
    on_correct = run(correct)
    if on_correct.exit_code != 0:
        verdict = judge(on_correct, expected_exception)
        return "not_fail_to_pass", f"on the correct version: {verdict.outcome.value}"
    return "valid", "fails on the buggy version and passes on the correct one"


def apply_patch(project: Path, patch: Path) -> None:
    result = subprocess.run(
        ["git", "apply", str(patch)], cwd=project, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not apply {patch}: {result.stderr}")


# Output files


def save_results(report: EvalReport, results_dir: Path) -> tuple[Path, Path]:
    """Writes <date>_<model>.json and .md. Returns both paths."""
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report.date).strftime("%Y-%m-%d_%H%M%S")
    model = re.sub(r"[^A-Za-z0-9._-]+", "-", report.model)
    base = results_dir / f"{stamp}_{model}"
    json_path = base.with_suffix(".json")
    markdown_path = base.with_suffix(".md")
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(markdown_table(report), encoding="utf-8")
    return json_path, markdown_path


def markdown_table(report: EvalReport) -> str:
    lines = [
        f"# Avaliação: {report.provider} / {report.model}",
        "",
        f"Data: {report.date}",
        "",
        "| Caso | Resultado | Tentativas | Tokens (entrada / saída) | Custo (US$) | Tempo (s) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in report.cases:
        lines.append(
            f"| `{case.case_id}` | {STATUS_LABELS[case.status]} | {case.attempts} "
            f"| {case.input_tokens} / {case.output_tokens} "
            f"| {format_cost(case.estimated_cost_usd)} | {case.duration_seconds:.1f} |"
        )
    total = len(report.cases)
    rate = 100 * report.valid_count / total if total else 0
    input_tokens = sum(case.input_tokens for case in report.cases)
    output_tokens = sum(case.output_tokens for case in report.cases)
    lines += [
        "",
        f"- Taxa de reprodução válida: {report.valid_count}/{total} ({rate:.0f}%)",
        f"- Média de tentativas: {report.mean_attempts:.1f}",
        f"- Tokens no total: {input_tokens} de entrada, {output_tokens} de saída",
        f"- Custo total estimado: {format_cost(report.total_cost_usd)}",
        "",
    ]
    return "\n".join(lines)


def format_cost(cost: float | None) -> str:
    return "não estimado" if cost is None else f"{cost:.4f}"
