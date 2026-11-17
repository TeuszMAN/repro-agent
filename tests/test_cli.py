"""CLI tests: `repro run` end to end, with the fake model in place of the provider."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from frete_scenario import CASE_ID, GOOD_TEST, ISSUE, PASSING_TEST, script
from typer.testing import CliRunner

from repro_agent import cli
from repro_agent.fake_llm import FakeModel
from repro_agent.llm import Settings

runner = CliRunner()


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty current folder (no .env) and a valid provider configuration."""
    folder = tmp_path / "work"
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setenv("REPRO_PROVIDER", "gemini")
    monkeypatch.setenv("REPRO_MODEL", "gemini-test")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("REPRO_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("REPRO_PRICE_OUTPUT_PER_MTOK", "2")
    (folder / "frete gratis.md").write_text(ISSUE, encoding="utf-8")
    return folder


def use_fake_model(monkeypatch: pytest.MonkeyPatch, fake: FakeModel) -> None:
    def create_model(settings: Settings) -> FakeModel:
        assert settings.model == "gemini-test"
        return fake

    monkeypatch.setattr(cli, "create_model", create_model)


def test_run_reproduces_writes_the_test_and_reports(
    workdir: Path, buggy_pedido: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = buggy_pedido(CASE_ID)
    use_fake_model(monkeypatch, FakeModel(script([GOOD_TEST])))

    result = runner.invoke(
        cli.app,
        ["run", "--repo", str(repo), "--issue", "frete gratis.md", "--report", "out/report.json"],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report == json.loads((workdir / "out" / "report.json").read_text())
    assert report["status"] == "reproduced"
    assert (report["provider"], report["model"]) == ("gemini", "gemini-test")
    assert report["test_id"] == "frete_gratis"
    assert report["attempts"] == 1
    assert report["attempt_details"][0]["outcome"] == "reproduced"
    assert set(report["tokens_by_node"]) == {"understand", "locate", "write_test"}
    total = report["total_tokens"]
    expected_cost = (total["input_tokens"] * 1 + total["output_tokens"] * 2) / 1_000_000
    assert report["estimated_cost_usd"] == pytest.approx(expected_cost)
    assert report["duration_seconds"] > 0
    written = repo / "tests" / "test_repro_frete_gratis.py"
    assert report["test_file"] == str(written)
    assert written.read_text() == report["test_code"]


def test_run_not_reproduced_does_not_touch_the_project(
    workdir: Path, buggy_pedido: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = buggy_pedido(CASE_ID)
    use_fake_model(monkeypatch, FakeModel(script([PASSING_TEST], reflections=[])))

    result = runner.invoke(
        cli.app,
        ["run", "--repo", str(repo), "--issue", "frete gratis.md", "--max-attempts", "1"],
    )

    assert result.exit_code == 1, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "not_reproduced"
    assert report["test_file"] is None
    assert not list((repo / "tests").glob("test_repro_*"))


def test_run_with_no_write(
    workdir: Path, buggy_pedido: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = buggy_pedido(CASE_ID)
    use_fake_model(monkeypatch, FakeModel(script([GOOD_TEST])))

    result = runner.invoke(
        cli.app,
        ["run", "--repo", str(repo), "--issue", "frete gratis.md", "--no-write", "--test-id", "x"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["test_file"] is None
    assert not (repo / "tests" / "test_repro_x.py").exists()


def test_run_reports_llm_errors(
    workdir: Path, pedido: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_model(monkeypatch, FakeModel({}))  # no replies: the first call fails

    result = runner.invoke(cli.app, ["run", "--repo", str(pedido), "--issue", "frete gratis.md"])

    assert result.exit_code == 3
    report = json.loads(result.stdout)
    assert report["status"] == "error"
    assert "no scripted reply" in report["error"]


def test_run_without_configuration(
    workdir: Path, pedido: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPRO_PROVIDER")

    result = runner.invoke(cli.app, ["run", "--repo", str(pedido), "--issue", "frete gratis.md"])

    assert result.exit_code == 2
    assert "REPRO_PROVIDER" in result.stderr


def test_run_reads_the_env_file(
    workdir: Path, pedido: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("REPRO_PROVIDER", "REPRO_MODEL", "GEMINI_API_KEY"):
        monkeypatch.delenv(name)
    (workdir / ".env").write_text("REPRO_PROVIDER=deepseek\nREPRO_MODEL=gemini-test\n")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    seen: list[Settings] = []

    def create_model(settings: Settings) -> FakeModel:
        seen.append(settings)
        return FakeModel({})

    monkeypatch.setattr(cli, "create_model", create_model)
    runner.invoke(cli.app, ["run", "--repo", str(pedido), "--issue", "frete gratis.md"])

    assert [s.provider for s in seen] == ["deepseek"]


def test_default_test_id(tmp_path: Path) -> None:
    assert cli.default_test_id(tmp_path / "frete gratis.md") == "frete_gratis"
    case = tmp_path / "centavo_a_mais"
    assert cli.default_test_id(case / "issue.md") == "centavo_a_mais"


@pytest.mark.parametrize(
    ("text", "slug"),
    [("frete gratis", "frete_gratis"), ("Cupom acima de 100%!", "cupom_acima_de_100"), ("ç", "c")],
)
def test_slugify(text: str, slug: str) -> None:
    assert cli.slugify(text) == slug
