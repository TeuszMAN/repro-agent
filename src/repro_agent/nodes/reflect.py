"""reflect: explains why the last attempt failed for the wrong reason, for the next write_test."""

from typing import Any

from repro_agent import prompts
from repro_agent.llm import ChatModel, Message, add_usage
from repro_agent.state import ReproState


def reflect(state: ReproState, llm: ChatModel) -> dict[str, Any]:
    last = state["attempts"][-1]
    user = prompts.render(
        "reflect.user.md",
        issue=state["issue"],
        findings=state["findings"] or "(none)",
        test_code=last.test_code.rstrip(),
        outcome=last.outcome.value,
        detail=last.detail,
        pytest_output=last.pytest_output.rstrip(),
    )
    reply = llm.complete("reflect", prompts.load("reflect.system.md"), [Message("user", user)])
    return {
        "reflections": state["reflections"] + [reply.text.strip()],
        "token_usage": add_usage(state["token_usage"], "reflect", reply.usage),
    }
