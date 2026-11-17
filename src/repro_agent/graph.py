"""The agent graph (SPEC section 4), built with LangGraph.

    START -> understand -> locate -> write_test -> run_test -> judge
    judge -> END        when the bug was reproduced or the attempts ran out
    judge -> reflect    otherwise, and reflect -> write_test

Nodes are plain functions (see nodes/). Here they get their dependencies (the model
and the config) through small lambdas, and LangGraph only sees `state -> update`.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from repro_agent.judge import Outcome
from repro_agent.llm import ChatModel, TokenUsage
from repro_agent.nodes.judge import judge_node
from repro_agent.nodes.locate import locate
from repro_agent.nodes.reflect import reflect
from repro_agent.nodes.run_test import run_test
from repro_agent.nodes.understand import understand
from repro_agent.nodes.write_test import write_test
from repro_agent.state import AgentConfig, Attempt, ReproState, initial_state


def build_graph(llm: ChatModel, config: AgentConfig) -> CompiledStateGraph:
    graph = StateGraph(ReproState)
    graph.add_node("understand", lambda state: understand(state, llm))
    graph.add_node("locate", lambda state: locate(state, llm, config.max_locate_steps))
    graph.add_node("write_test", lambda state: write_test(state, llm))
    graph.add_node("run_test", lambda state: run_test(state, config))
    graph.add_node("judge", judge_node)
    graph.add_node("reflect", lambda state: reflect(state, llm))

    graph.add_edge(START, "understand")
    graph.add_edge("understand", "locate")
    graph.add_edge("locate", "write_test")
    graph.add_edge("write_test", "run_test")
    graph.add_edge("run_test", "judge")
    graph.add_conditional_edges(
        "judge",
        lambda state: next_step(state, config.max_attempts),
        {"reflect": "reflect", "end": END},
    )
    graph.add_edge("reflect", "write_test")
    return graph.compile()


def next_step(state: ReproState, max_attempts: int) -> Literal["reflect", "end"]:
    """After judge: stop on success or when the attempts ran out, otherwise reflect."""
    last = state["attempts"][-1]
    if last.outcome is Outcome.REPRODUCED or len(state["attempts"]) >= max_attempts:
        return "end"
    return "reflect"


def recursion_limit(max_attempts: int) -> int:
    """Upper bound of node runs for one invocation, passed to LangGraph explicitly.

    One run needs understand + locate, then write_test, run_test, judge (and reflect)
    per attempt. A tight explicit bound stops a routing bug from looping for a long time.
    """
    return 2 + 4 * max_attempts + 5  # 5 to spare


@dataclass(frozen=True)
class ReproResult:
    state: ReproState
    duration_seconds: float

    @property
    def attempts(self) -> list[Attempt]:
        return self.state["attempts"]

    @property
    def reproduced(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].outcome is Outcome.REPRODUCED

    @property
    def test_code(self) -> str:
        """Code of the last attempt (the one that reproduced the bug, on success)."""
        return self.attempts[-1].test_code if self.attempts else ""

    @property
    def total_usage(self) -> TokenUsage:
        return sum(self.state["token_usage"].values(), TokenUsage())


def reproduce(
    issue: str, repo: Path, test_id: str, llm: ChatModel, config: AgentConfig
) -> ReproResult:
    """Runs the whole graph once. Never writes to `repo`."""
    start = time.monotonic()
    graph = build_graph(llm, config)
    final_state = graph.invoke(
        initial_state(issue, str(repo.resolve()), test_id),
        {"recursion_limit": recursion_limit(config.max_attempts)},
    )
    return ReproResult(cast(ReproState, final_state), time.monotonic() - start)
