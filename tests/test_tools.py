from pathlib import Path

import pytest

from repro_agent.tools import ProjectReader, UnsafePathError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A folder with a small project inside and a secret file next to it (outside)."""
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("")
    (project / "pkg" / "order.py").write_text("def total():\n    return 42\n")
    (project / "README.md").write_text("Order project\n")
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "order.cpython-312.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "secret.txt").write_text("API_KEY=do-not-leak\n")
    return tmp_path


@pytest.fixture
def reader(workspace: Path) -> ProjectReader:
    return ProjectReader(workspace / "project")


# Normal use


def test_list_files_returns_relative_paths_and_skips_caches(reader: ProjectReader) -> None:
    assert reader.list_files() == ["README.md", "pkg/__init__.py", "pkg/order.py"]


def test_list_files_of_a_subfolder(reader: ProjectReader) -> None:
    assert reader.list_files("pkg") == ["pkg/__init__.py", "pkg/order.py"]


def test_read_file(reader: ProjectReader) -> None:
    assert reader.read_file("pkg/order.py") == "def total():\n    return 42\n"


def test_read_missing_file(reader: ProjectReader) -> None:
    with pytest.raises(FileNotFoundError):
        reader.read_file("pkg/missing.py")


def test_search_text_returns_path_line_and_text(reader: ProjectReader) -> None:
    assert reader.search_text("return 42") == ["pkg/order.py:2: return 42"]


def test_search_text_without_matches(reader: ProjectReader) -> None:
    assert reader.search_text("nothing here") == []


# Refusals: "..", absolute paths and symbolic links


@pytest.mark.parametrize("path", ["../secret.txt", "pkg/../../secret.txt", "..", "pkg/../order"])
def test_refuses_dot_dot(reader: ProjectReader, path: str) -> None:
    with pytest.raises(UnsafePathError, match=r"'\.\.'"):
        reader.read_file(path)
    with pytest.raises(UnsafePathError):
        reader.list_files(path)
    with pytest.raises(UnsafePathError):
        reader.search_text("API_KEY", path)


def test_refuses_absolute_path(reader: ProjectReader, workspace: Path) -> None:
    absolute = str(workspace / "secret.txt")
    with pytest.raises(UnsafePathError, match="absolute"):
        reader.read_file(absolute)
    with pytest.raises(UnsafePathError, match="absolute"):
        reader.list_files("/")
    with pytest.raises(UnsafePathError, match="absolute"):
        reader.search_text("API_KEY", "/")


def test_refuses_absolute_path_that_points_inside_the_project(
    reader: ProjectReader, workspace: Path
) -> None:
    with pytest.raises(UnsafePathError, match="absolute"):
        reader.read_file(str(workspace / "project" / "README.md"))


def test_refuses_symlink_to_file_outside(reader: ProjectReader, workspace: Path) -> None:
    (workspace / "project" / "notes.txt").symlink_to(workspace / "secret.txt")

    with pytest.raises(UnsafePathError, match="outside"):
        reader.read_file("notes.txt")
    # The link must not leak through listing or searching either.
    assert "notes.txt" not in reader.list_files()
    assert reader.search_text("API_KEY") == []


def test_refuses_symlink_to_folder_outside(reader: ProjectReader, workspace: Path) -> None:
    (workspace / "project" / "outside").symlink_to(workspace, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="outside"):
        reader.read_file("outside/secret.txt")
    with pytest.raises(UnsafePathError, match="outside"):
        reader.list_files("outside")
    assert all(not path.startswith("outside") for path in reader.list_files())
    assert reader.search_text("API_KEY") == []


def test_allows_symlink_that_stays_inside(reader: ProjectReader) -> None:
    (reader.root / "order_link.py").symlink_to(reader.root / "pkg" / "order.py")

    assert reader.read_file("order_link.py") == "def total():\n    return 42\n"


def test_root_must_be_a_directory(workspace: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        ProjectReader(workspace / "secret.txt")
