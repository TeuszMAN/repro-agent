"""Read-only tools used by the locate node.

Every path received from the model is relative to the target project root.
Before touching the file system, each path goes through `ProjectReader.resolve`,
which refuses anything that could leave the project folder:

1. absolute paths (`/etc/passwd`);
2. any `..` component (`../secret.txt`), even if it would end up inside;
3. paths whose real location, after following symbolic links, is outside the root.

Rule 3 is the one that actually guarantees safety. Rules 1 and 2 exist to give the
model a clear error message instead of a generic "outside the project".
"""

from pathlib import Path, PurePosixPath

# Folders that only add noise for the model (caches, virtualenvs, VCS metadata).
IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

MAX_LISTED_FILES = 200
MAX_READ_CHARS = 20_000
MAX_SEARCH_RESULTS = 50


class UnsafePathError(ValueError):
    """Raised when a path would escape the target project folder."""


class ProjectReader:
    """Lists, reads and searches files inside a single project folder."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError(f"project root is not a directory: {root}")

    def resolve(self, relative_path: str) -> Path:
        """Turns a path given by the model into a real path inside the project."""
        candidate = PurePosixPath(relative_path)
        if candidate.is_absolute() or Path(relative_path).is_absolute():
            raise UnsafePathError(f"absolute paths are not allowed: {relative_path}")
        if ".." in candidate.parts:
            raise UnsafePathError(f"'..' is not allowed in paths: {relative_path}")

        real_path = (self.root / candidate).resolve()
        if not real_path.is_relative_to(self.root):
            raise UnsafePathError(f"path points outside the project: {relative_path}")
        return real_path

    def list_files(self, relative_dir: str = ".") -> list[str]:
        """Returns all files under `relative_dir`, recursively, as project-relative paths."""
        start = self.resolve(relative_dir)
        if not start.is_dir():
            raise FileNotFoundError(f"not a directory: {relative_dir}")

        files = [self._relative(path) for path in self._walk_files(start)]
        return sorted(files)[:MAX_LISTED_FILES]

    def read_file(self, relative_path: str) -> str:
        """Returns the text of a file, truncated to MAX_READ_CHARS characters."""
        path = self.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"not a file: {relative_path}")

        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + "\n[... truncated ...]"
        return text

    def search_text(self, query: str, relative_dir: str = ".") -> list[str]:
        """Finds lines containing `query` (case sensitive). Returns 'path:line: text' entries."""
        if not query:
            raise ValueError("query must not be empty")
        start = self.resolve(relative_dir)

        results: list[str] = []
        for path in sorted(self._walk_files(start)):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue  # binary file
            for number, line in enumerate(lines, start=1):
                if query in line:
                    results.append(f"{self._relative(path)}:{number}: {line.strip()}")
                    if len(results) >= MAX_SEARCH_RESULTS:
                        return results
        return results

    def _walk_files(self, start: Path) -> list[Path]:
        """Collects regular files under `start`, skipping ignored folders and unsafe links."""
        found: list[Path] = []
        for directory, dir_names, file_names in start.walk():  # does not follow dir links
            dir_names[:] = [name for name in dir_names if name not in IGNORED_DIRS]
            for name in file_names:
                path = directory / name
                # A symbolic link to a file outside the project must not leak its content.
                if path.resolve().is_relative_to(self.root) and path.is_file():
                    found.append(path)
        return found

    def _relative(self, path: Path) -> str:
        """Project-relative path with forward slashes, as the model sees it."""
        return path.relative_to(self.root).as_posix()
