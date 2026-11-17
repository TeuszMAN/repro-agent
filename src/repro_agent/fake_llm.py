"""Scripted fake model for tests: no network, no API key, fully predictable.

The script maps each node to the replies it will receive, in order:

    FakeModel({
        "understand": ['{"expected_behavior": "...", ...}'],
        "write_test": ["```python\\n...\\n```", "```python\\n...\\n```"],
    })

Every call is recorded in `calls`, so tests can check what each node sent.
Token counts are word counts, enough to test the accounting.
"""

from dataclasses import dataclass

from repro_agent.llm import LLMError, Message, Reply, TokenUsage


@dataclass(frozen=True)
class FakeCall:
    node: str
    system: str
    messages: list[Message]
    json_mode: bool


class FakeModel:
    provider = "fake"
    model = "fake-model"

    def __init__(self, script: dict[str, list[str]]) -> None:
        self._script = {node: list(replies) for node, replies in script.items()}
        self.calls: list[FakeCall] = []

    def complete(
        self, node: str, system: str, messages: list[Message], json_mode: bool = False
    ) -> Reply:
        self.calls.append(FakeCall(node, system, list(messages), json_mode))
        replies = self._script.get(node, [])
        if not replies:
            raise LLMError(f"fake model has no scripted reply left for node '{node}'")
        text = replies.pop(0)
        input_words = len(system.split()) + sum(len(m.content.split()) for m in messages)
        return Reply(text, TokenUsage(input_words, len(text.split())))

    def unused_replies(self) -> dict[str, int]:
        """Replies left in the script, per node. Tests use it to check the whole script ran."""
        return {node: len(replies) for node, replies in self._script.items() if replies}
