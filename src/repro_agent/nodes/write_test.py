"""write_test: asks the model for the test file, using everything gathered so far."""

import re
from pathlib import Path
from typing import Any

from repro_agent import prompts
from repro_agent.llm import ChatModel, Message, add_usage
from repro_agent.state import ReproState, repro_test_path
from repro_agent.tools import ProjectReader

# Upper bound for the source code pasted in the prompt.
MAX_SOURCE_CHARS = 60_000

CODE_BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)


def write_test(state: ReproState, llm: ChatModel) -> dict[str, Any]:
    analysis = state["analysis"]
    test_path = repro_test_path(state["test_id"])
    user = prompts.render(
        "write_test.user.md",
        issue=state["issue"],
        analysis=analysis.model_dump_json(indent=2) if analysis else "(none)",
        findings=state["findings"] or "(none)",
        source_files=source_files_section(Path(state["repo_path"]), state["files_read"]),
        test_path=test_path,
        previous_attempt=previous_attempt_section(state),
    )
    reply = llm.complete(
        "write_test", prompts.load("write_test.system.md"), [Message("user", user)]
    )
    return {
        "test_code": extract_python_code(reply.text),
        "token_usage": add_usage(state["token_usage"], "write_test", reply.usage),
    }


def extract_python_code(text: str) -> str:
    """Returns the first code block of the reply, or the whole reply if there is none."""
    match = CODE_BLOCK.search(text)
    code = match.group(1) if match else text
    return code.strip() + "\n"


def source_files_section(repo: Path, files_read: list[str]) -> str:
    if not files_read:
        return "(no files were read)"
    reader = ProjectReader(repo)
    parts: list[str] = []
    size = 0
    for path in files_read:
        content = reader.read_file(path)
        if size + len(content) > MAX_SOURCE_CHARS:
            parts.append(f"(other files omitted to keep the prompt small: {path} and later)")
            break
        parts.append(f"### {path}\n\n```\n{content}\n```")
        size += len(content)
    return "\n\n".join(parts)


def previous_attempt_section(state: ReproState) -> str:
    if not state["attempts"]:
        return ""
    last = state["attempts"][-1]
    reflection = state["reflections"][-1] if state["reflections"] else "(no analysis)"
    return prompts.render(
        "write_test.previous.md",
        number=str(last.number),
        outcome=last.outcome.value,
        detail=last.detail,
        test_code=last.test_code.rstrip(),
        reflection=reflection,
    )
