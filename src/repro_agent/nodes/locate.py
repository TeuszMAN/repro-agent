"""locate: a ReAct loop where the model explores the project with read-only tools.

In each step the model answers with one JSON action. The code runs the tool and sends
the result back as the next message ("observation"). The loop ends when the model
calls "finish" or when the step limit is reached.

Tool errors (unsafe path, missing file) are not raised: they become an observation
starting with "Error:", so the model can correct itself in the next step.
"""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from repro_agent import prompts
from repro_agent.llm import ChatModel, Message, add_usage, complete_json
from repro_agent.state import ReproState
from repro_agent.tools import ProjectReader


class LocateAction(BaseModel):
    thought: str = ""
    tool: Literal["list_files", "read_file", "search_text", "finish"]
    path: str = "."
    query: str = ""
    summary: str = ""


def locate(state: ReproState, llm: ChatModel, max_steps: int) -> dict[str, Any]:
    reader = ProjectReader(Path(state["repo_path"]))
    analysis = state["analysis"]
    user = prompts.render(
        "locate.user.md",
        issue=state["issue"],
        analysis=analysis.model_dump_json(indent=2) if analysis else "(none)",
        max_steps=str(max_steps),
    )
    system = prompts.load("locate.system.md")
    messages = [Message("user", user)]
    files_read: list[str] = []
    usage = state["token_usage"]
    findings = ""

    for step in range(1, max_steps + 1):
        action, used = complete_json(llm, "locate", system, messages, LocateAction)
        usage = add_usage(usage, "locate", used)
        if action.tool == "finish":
            findings = action.summary
            break
        observation = run_tool(reader, action, files_read)
        messages += [
            Message("assistant", action.model_dump_json()),
            Message("user", f"Observation (step {step} of {max_steps}):\n{observation}"),
        ]

    if not findings:
        findings = (
            "The exploration stopped at the step limit without a summary. "
            f"Files read: {json.dumps(files_read)}."
        )
    return {"files_read": files_read, "findings": findings, "token_usage": usage}


def run_tool(reader: ProjectReader, action: LocateAction, files_read: list[str]) -> str:
    """Runs one tool and returns its result as text, errors included."""
    try:
        if action.tool == "list_files":
            return "\n".join(reader.list_files(action.path)) or "(no files)"
        if action.tool == "read_file":
            content = reader.read_file(action.path)
            if action.path not in files_read:
                files_read.append(action.path)
            return content
        return "\n".join(reader.search_text(action.query, action.path)) or "(no matches)"
    except (ValueError, OSError) as error:  # UnsafePathError is a ValueError
        return f"Error: {error}"
