"""LLM layer tests. No network: httpx.MockTransport answers in place of the providers."""

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from repro_agent.fake_llm import FakeModel
from repro_agent.llm import (
    ConfigError,
    DeepSeekModel,
    GeminiModel,
    LLMError,
    Message,
    TokenUsage,
    add_usage,
    complete_json,
    create_model,
    load_settings,
    strip_code_fence,
)

GEMINI_ANSWER = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": '{"ok": true}'}]}}],
    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 5, "thoughtsTokenCount": 3},
}
DEEPSEEK_ANSWER = {
    "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 7},
}


class Recorder:
    """A fake HTTP server: returns the queued responses and keeps the requests."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def body(self, index: int = 0) -> Any:
        return json.loads(self.requests[index].content)


CONVERSATION = [Message("user", "hello"), Message("assistant", "hi"), Message("user", "json?")]


# Gemini


def test_gemini_request_and_response() -> None:
    server = Recorder(httpx.Response(200, json=GEMINI_ANSWER))
    model = GeminiModel("gemini-test", "secret", timeout_seconds=12, client=server.client())

    reply = model.complete("understand", "be brief", CONVERSATION, json_mode=True)

    request = server.requests[0]
    assert str(request.url).endswith("/models/gemini-test:generateContent")
    assert request.headers["x-goog-api-key"] == "secret"
    assert request.extensions["timeout"]["read"] == 12
    body = server.body()
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"] == {"responseMimeType": "application/json"}

    assert reply.text == '{"ok": true}'
    assert reply.usage == TokenUsage(input_tokens=12, output_tokens=8)  # 5 + 3 thinking


def test_gemini_without_json_mode_sends_no_generation_config() -> None:
    server = Recorder(httpx.Response(200, json=GEMINI_ANSWER))
    GeminiModel("m", "k", client=server.client()).complete("n", "s", CONVERSATION)

    assert "generationConfig" not in server.body()


def test_gemini_without_candidates_is_an_error() -> None:
    server = Recorder(httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMError, match="no answer"):
        GeminiModel("m", "k", client=server.client()).complete("n", "s", CONVERSATION)


# DeepSeek


def test_deepseek_request_and_response() -> None:
    server = Recorder(httpx.Response(200, json=DEEPSEEK_ANSWER))
    model = DeepSeekModel(
        "deepseek-test", "secret", client=server.client(), base_url="https://example.test/"
    )

    reply = model.complete("locate", "be brief", CONVERSATION, json_mode=True)

    request = server.requests[0]
    assert str(request.url) == "https://example.test/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    assert request.extensions["timeout"]["read"] == 60
    body = server.body()
    assert body["model"] == "deepseek-test"
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert [m["role"] for m in body["messages"][1:]] == ["user", "assistant", "user"]
    assert body["response_format"] == {"type": "json_object"}
    assert reply.usage == TokenUsage(input_tokens=20, output_tokens=7)


# Retries and errors


def test_retries_rate_limit_then_succeeds() -> None:
    waits: list[float] = []
    server = Recorder(
        httpx.Response(429, headers={"retry-after": "7"}),
        httpx.Response(503),
        httpx.Response(200, json=DEEPSEEK_ANSWER),
    )
    model = DeepSeekModel("m", "k", client=server.client(), sleep=waits.append)

    reply = model.complete("n", "s", CONVERSATION)

    assert reply.text == '{"ok": true}'
    assert waits == [7.0, 4.0]  # Retry-After header first, then exponential backoff


def test_retries_timeout_and_gives_up() -> None:
    server = Recorder(*[httpx.ReadTimeout("slow")] * 3)
    model = DeepSeekModel("m", "k", client=server.client(), sleep=lambda _: None)

    with pytest.raises(LLMError, match="request failed"):
        model.complete("n", "s", CONVERSATION)
    assert len(server.requests) == 3


def test_client_error_is_not_retried() -> None:
    server = Recorder(httpx.Response(401, text="invalid api key"))
    model = DeepSeekModel("m", "k", client=server.client(), sleep=lambda _: None)

    with pytest.raises(LLMError, match="HTTP 401: invalid api key"):
        model.complete("n", "s", CONVERSATION)
    assert len(server.requests) == 1


# Structured output


class Answer(BaseModel):
    ok: bool


def test_complete_json_parses_fenced_json() -> None:
    fake = FakeModel({"node": ['```json\n{"ok": true}\n```']})

    answer, usage = complete_json(fake, "node", "system", [Message("user", "go")], Answer)

    assert answer.ok is True
    assert usage.output_tokens > 0
    assert fake.calls[0].json_mode is True


def test_complete_json_sends_the_error_back_and_retries() -> None:
    fake = FakeModel({"node": ['{"ok": "maybe"}', '{"ok": false}']})

    answer, usage = complete_json(fake, "node", "system", [Message("user", "go")], Answer)

    assert answer.ok is False
    retry_messages = fake.calls[1].messages
    assert retry_messages[-2] == Message("assistant", '{"ok": "maybe"}')
    assert "did not match" in retry_messages[-1].content
    assert usage.output_tokens == 4  # both tries are counted (2 words each in the fake)


def test_complete_json_gives_up() -> None:
    fake = FakeModel({"node": ["not json", "still not json"]})

    with pytest.raises(LLMError, match="valid JSON"):
        complete_json(fake, "node", "system", [Message("user", "go")], Answer)


def test_strip_code_fence() -> None:
    assert strip_code_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fence('  {"a": 1}  ') == '{"a": 1}'


def test_add_usage_accumulates_per_node() -> None:
    usage = add_usage({}, "locate", TokenUsage(10, 2))
    usage = add_usage(usage, "locate", TokenUsage(5, 1))
    usage = add_usage(usage, "understand", TokenUsage(1, 1))

    assert usage == {"locate": TokenUsage(15, 3), "understand": TokenUsage(1, 1)}


# Fake model


def test_fake_model_follows_the_script_per_node() -> None:
    fake = FakeModel({"a": ["first", "second"], "b": ["only"]})

    assert fake.complete("a", "s", []).text == "first"
    assert fake.complete("b", "s", []).text == "only"
    assert fake.unused_replies() == {"a": 1}
    with pytest.raises(LLMError, match="no scripted reply"):
        fake.complete("b", "s", [])


# Settings


def test_load_settings_for_gemini() -> None:
    env = {
        "REPRO_PROVIDER": "Gemini",
        "REPRO_MODEL": "gemini-x",
        "GEMINI_API_KEY": "k",
        "REPRO_LLM_TIMEOUT": "30",
        "REPRO_PRICE_INPUT_PER_MTOK": "0.5",
        "REPRO_PRICE_OUTPUT_PER_MTOK": "2",
    }
    settings = load_settings(env)

    assert (settings.provider, settings.model, settings.timeout_seconds) == (
        "gemini",
        "gemini-x",
        30,
    )
    assert settings.estimate_cost(TokenUsage(1_000_000, 500_000)) == pytest.approx(1.5)
    assert isinstance(create_model(settings), GeminiModel)
    assert settings.estimate_cost(TokenUsage(7000, 700)) == 0.0049  # rounded, no float noise


def test_command_line_overrides_environment() -> None:
    env = {"REPRO_PROVIDER": "gemini", "REPRO_MODEL": "gemini-x", "DEEPSEEK_API_KEY": "k"}
    settings = load_settings(env, provider="deepseek", model="deepseek-chat")

    assert (settings.provider, settings.model) == ("deepseek", "deepseek-chat")
    assert settings.estimate_cost(TokenUsage(10, 10)) is None  # prices not set
    assert isinstance(create_model(settings), DeepSeekModel)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "REPRO_PROVIDER"),
        ({"REPRO_PROVIDER": "openai"}, "REPRO_PROVIDER"),
        ({"REPRO_PROVIDER": "gemini"}, "REPRO_MODEL"),
        ({"REPRO_PROVIDER": "gemini", "REPRO_MODEL": "m"}, "GEMINI_API_KEY"),
        (
            {
                "REPRO_PROVIDER": "gemini",
                "REPRO_MODEL": "m",
                "GEMINI_API_KEY": "k",
                "REPRO_LLM_TIMEOUT": "soon",
            },
            "REPRO_LLM_TIMEOUT",
        ),
    ],
)
def test_load_settings_errors(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(env)
