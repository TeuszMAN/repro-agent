"""Command line: `repro run` reproduces one bug report, `repro eval` runs the evaluation suite.

Exit codes: 0 reproduced, 1 not reproduced, 2 bad configuration, 3 error during the run.
The JSON report goes to stdout (and to --report, if given). Logs go to stderr.
"""

import logging
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from repro_agent.evaluation import DEFAULT_EVALS_DIR, run_eval, save_results
from repro_agent.graph import reproduce
from repro_agent.llm import ConfigError, LLMError, Settings, create_model, load_settings
from repro_agent.report import RunReport, build_report
from repro_agent.state import AgentConfig, repro_test_path

app = typer.Typer(no_args_is_help=True, add_completion=False)
logger = logging.getLogger("repro_agent")


@app.callback()
def main() -> None:
    """repro-agent: turns a bug report into a failing pytest test."""


@app.command()
def run(
    repo: Annotated[
        Path, typer.Option(help="Target project folder.", exists=True, file_okay=False)
    ],
    issue: Annotated[
        Path, typer.Option(help="Bug report in Markdown.", exists=True, dir_okay=False)
    ],
    model: Annotated[str | None, typer.Option(help="Overrides REPRO_MODEL.")] = None,
    provider: Annotated[str | None, typer.Option(help="Overrides REPRO_PROVIDER.")] = None,
    test_id: Annotated[
        str | None, typer.Option(help="Test file suffix. Default: the issue file name.")
    ] = None,
    max_attempts: Annotated[int, typer.Option(min=1)] = 3,
    python: Annotated[
        str | None, typer.Option(help="Python that runs the test. Default: this one.")
    ] = None,
    write: Annotated[
        bool, typer.Option(help="Write the test into the project when the bug is reproduced.")
    ] = True,
    report: Annotated[Path | None, typer.Option(help="Also save the JSON report here.")] = None,
) -> None:
    """Reproduces the bug described in the issue file with a pytest test for the project."""
    configure_logging()
    settings = settings_or_exit(provider, model)
    test_id = test_id or slugify(issue.stem)
    config = AgentConfig(max_attempts=max_attempts, python_executable=python or sys.executable)

    try:
        result = reproduce(
            issue.read_text(encoding="utf-8"), repo, test_id, create_model(settings), config
        )
    except LLMError as error:
        logger.error("run failed: %s", error)
        failed = RunReport(
            status="error",
            provider=settings.provider,
            model=settings.model,
            repo=str(repo.resolve()),
            test_id=test_id,
            error=str(error),
        )
        emit(failed, report)
        raise typer.Exit(3) from error

    test_file = None
    if result.reproduced and write:
        target = repo / repro_test_path(test_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.test_code, encoding="utf-8")
        test_file = str(target.resolve())
        logger.info("test written to %s", test_file)

    cost = settings.estimate_cost(result.total_usage)
    run_report = build_report(result, settings.provider, settings.model, test_id, cost, test_file)
    emit(run_report, report)
    raise typer.Exit(0 if result.reproduced else 1)


@app.command("eval")
def evaluate(
    model: Annotated[str | None, typer.Option(help="Overrides REPRO_MODEL.")] = None,
    provider: Annotated[str | None, typer.Option(help="Overrides REPRO_PROVIDER.")] = None,
    cases: Annotated[
        str | None, typer.Option(help="Comma separated case ids. Default: all cases.")
    ] = None,
    evals_dir: Annotated[
        Path, typer.Option(help="Folder with fixtures/ and cases/.", exists=True, file_okay=False)
    ] = DEFAULT_EVALS_DIR,
    results_dir: Annotated[
        Path | None, typer.Option(help="Where to save results. Default: <evals-dir>/results.")
    ] = None,
) -> None:
    """Runs the agent on the evaluation cases and checks fail-to-pass for each one."""
    configure_logging()
    settings = settings_or_exit(provider, model)
    case_ids = [case.strip() for case in cases.split(",") if case.strip()] if cases else None
    try:
        eval_report = run_eval(
            create_model(settings), settings.estimate_cost, case_ids, evals_dir=evals_dir
        )
    except ValueError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(2) from error

    json_path, markdown_path = save_results(eval_report, results_dir or evals_dir / "results")
    typer.echo(markdown_path.read_text(encoding="utf-8"))
    typer.echo(f"Results saved to {json_path} and {markdown_path}", err=True)


def settings_or_exit(provider: str | None, model: str | None) -> Settings:
    """Reads .env from the current folder, then the environment. Exits with code 2 on error."""
    load_dotenv(Path.cwd() / ".env")
    try:
        return load_settings(os.environ, provider=provider, model=model)
    except ConfigError as error:
        typer.echo(f"Configuration error: {error}", err=True)
        raise typer.Exit(2) from error


def emit(run_report: RunReport, path: Path | None) -> None:
    text = run_report.model_dump_json(indent=2)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    typer.echo(text)


def configure_logging() -> None:
    # force=True: each command (and each test with CliRunner) gets a fresh stderr handler.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True
    )
    # Keep the output about the agent; HTTP libraries are noisy at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def slugify(text: str) -> str:
    """'Frete grátis!' -> 'frete_gratis'. The result is safe in a Python module name."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_") or "issue"
