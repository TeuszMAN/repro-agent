"""understand: turns the bug report into a structured analysis."""

from typing import Any

from repro_agent import prompts
from repro_agent.llm import ChatModel, Message, add_usage, complete_json
from repro_agent.state import IssueAnalysis, ReproState


def understand(state: ReproState, llm: ChatModel) -> dict[str, Any]:
    user = prompts.render("understand.user.md", issue=state["issue"])
    analysis, usage = complete_json(
        llm,
        "understand",
        prompts.load("understand.system.md"),
        [Message("user", user)],
        IssueAnalysis,
    )
    return {
        "analysis": analysis,
        "token_usage": add_usage(state["token_usage"], "understand", usage),
    }
