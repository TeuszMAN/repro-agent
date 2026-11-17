"""Prompt files of the agent nodes.

Each node has a `<node>.system.md` file (fixed instructions) and a `<node>.user.md`
template. Templates use `string.Template` placeholders like `$issue`, so JSON braces
in the text need no escaping. A literal dollar sign is written as `$$`.
"""

from importlib.resources import files
from string import Template


def load(name: str) -> str:
    """Returns the text of a prompt file in this folder."""
    return files(__package__).joinpath(name).read_text(encoding="utf-8")


def render(name: str, **values: str) -> str:
    """Fills the placeholders of a template. A missing value raises KeyError."""
    return Template(load(name)).substitute(values)
