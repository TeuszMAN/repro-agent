"""Shared fixtures: copies of the target project `pedido`, correct or with a case's bug."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "evals" / "fixtures" / "pedido"
CASES_DIR = REPO_ROOT / "evals" / "cases"


def copy_fixture(destination: Path) -> Path:
    return shutil.copytree(FIXTURE, destination, ignore=shutil.ignore_patterns("__pycache__"))


def apply_patch(project: Path, patch: Path) -> None:
    subprocess.run(["git", "apply", str(patch)], cwd=project, check=True, capture_output=True)


@pytest.fixture
def pedido(tmp_path: Path) -> Path:
    """A copy of the correct `pedido` project."""
    return copy_fixture(tmp_path / "pedido")


@pytest.fixture
def buggy_pedido(tmp_path: Path) -> Callable[[str], Path]:
    """Factory: buggy_pedido("centavo_a_mais") returns a copy with that case's bug."""

    def make(case_id: str) -> Path:
        project = copy_fixture(tmp_path / f"buggy_{case_id}")
        apply_patch(project, CASES_DIR / case_id / "bug.patch")
        return project

    return make
