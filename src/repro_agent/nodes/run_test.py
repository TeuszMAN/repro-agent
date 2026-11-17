"""run_test: runs only the generated test, with pytest, in a temporary copy of the project.

The target project is never written. Each run gets a fresh copy, so one attempt
cannot leave files behind that change the next one.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from repro_agent.judge import PytestRun
from repro_agent.state import AgentConfig, ReproState, repro_test_path

# Not copied: version control, virtualenvs and caches. They are big and never needed.
IGNORED = shutil.ignore_patterns(
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"
)


def run_test(state: ReproState, config: AgentConfig) -> dict[str, Any]:
    run = run_test_in_copy(
        Path(state["repo_path"]),
        repro_test_path(state["test_id"]),
        state["test_code"],
        python=config.python_executable,
        timeout_seconds=config.pytest_timeout_seconds,
    )
    return {"last_run": run}


def run_test_in_copy(
    project: Path, test_path: str, test_code: str, python: str, timeout_seconds: float
) -> PytestRun:
    """Copies `project`, writes `test_code` at `test_path` and runs pytest on that file only."""
    with tempfile.TemporaryDirectory(prefix="repro-agent-") as tmp:
        copy = Path(tmp) / project.name
        shutil.copytree(project, copy, ignore=IGNORED, symlinks=True)
        test_file = copy / test_path
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(test_code, encoding="utf-8")
        return run_pytest(copy, test_path, python, timeout_seconds)


def run_pytest(project: Path, test_path: str, python: str, timeout_seconds: float) -> PytestRun:
    # --tb=auto keeps the default traceback format that the judge reads, even if the
    # project configures another one. The cache plugin is off so nothing is written.
    command = [python, "-m", "pytest", "-p", "no:cacheprovider", "--tb=auto", test_path]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    try:
        result = subprocess.run(
            command, cwd=project, capture_output=True, text=True, timeout=timeout_seconds, env=env
        )
    except subprocess.TimeoutExpired as error:
        return PytestRun(exit_code=None, output=_as_text(error.stdout), timed_out=True)
    return PytestRun(exit_code=result.returncode, output=result.stdout + result.stderr)


def _as_text(output: str | bytes | None) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""
