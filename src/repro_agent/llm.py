"""LLM layer: one small interface, two real providers and the settings that pick one.

Every node talks to a `ChatModel`:

    reply = llm.complete(node="understand", system=..., messages=[...], json_mode=True)

`reply.text` is the answer and `reply.usage` has the tokens of that call. The `node`
argument only labels the call (logs, token accounting and the scripted fake model).

The real providers are plain HTTP calls with httpx, with no provider SDK:
- Gemini, through the Google AI Studio REST API;
- DeepSeek, through its OpenAI-compatible chat completions API.

Provider, model name, API key, timeout and prices come from environment variables
(see `.env.example`). Nothing about the model is fixed in the code.
"""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60.0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
# Status codes worth retrying: rate limit and temporary server problems.
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT_SECONDS = 60.0


class LLMError(RuntimeError):
    """The model could not give a usable answer."""


class ConfigError(ValueError):
    """The environment does not describe a usable provider and model."""


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Reply:
    text: str
    usage: TokenUsage


class ChatModel(Protocol):
    """What the nodes need from a model. Real providers and the fake one implement it."""

    provider: str
    model: str

    def complete(
        self, node: str, system: str, messages: list[Message], json_mode: bool = False
    ) -> Reply: ...


def add_usage(
    usage_by_node: dict[str, TokenUsage], node: str, usage: TokenUsage
) -> dict[str, TokenUsage]:
    """Returns a new dict with `usage` added to the count of `node`."""
    updated = dict(usage_by_node)
    updated[node] = updated.get(node, TokenUsage()) + usage
    return updated


# Structured output


def complete_json[SchemaT: BaseModel](
    llm: ChatModel,
    node: str,
    system: str,
    messages: list[Message],
    schema: type[SchemaT],
    max_tries: int = 2,
) -> tuple[SchemaT, TokenUsage]:
    """Asks for JSON and validates it with a Pydantic model.

    If the answer does not validate, the error goes back to the model once, so it
    can fix the format. Tokens of every try are counted.
    """
    conversation = list(messages)
    usage = TokenUsage()
    for _ in range(max_tries):
        reply = llm.complete(node, system, conversation, json_mode=True)
        usage += reply.usage
        try:
            return schema.model_validate_json(strip_code_fence(reply.text)), usage
        except ValidationError as error:
            logger.warning("invalid JSON from model node=%s error=%s", node, error)
            conversation += [
                Message("assistant", reply.text),
                Message(
                    "user",
                    f"Your reply did not match the expected JSON format:\n{error}\n"
                    "Reply again with only the corrected JSON object.",
                ),
            ]
    raise LLMError(f"node '{node}' did not get valid JSON after {max_tries} tries")


def strip_code_fence(text: str) -> str:
    """Removes a surrounding ```json ... ``` fence, which some models add anyway."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        first_newline = stripped.find("\n")
        return stripped[first_newline + 1 : -3].strip()
    return stripped


# Real providers


class HttpChatModel:
    """Shared HTTP logic: timeout, retries and logging. Subclasses build and parse requests."""

    provider = "http"

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client or httpx.Client()
        self._max_attempts = max_attempts
        self._sleep = sleep

    def complete(
        self, node: str, system: str, messages: list[Message], json_mode: bool = False
    ) -> Reply:
        url, headers, body = self._build_request(system, messages, json_mode)
        data = self._post_with_retries(url, headers, body)
        reply = self._parse_response(data)
        logger.info(
            "llm call node=%s provider=%s model=%s input_tokens=%d output_tokens=%d",
            node,
            self.provider,
            self.model,
            reply.usage.input_tokens,
            reply.usage.output_tokens,
        )
        return reply

    def _post_with_retries(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> dict[str, Any]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(url, headers=headers, json=body, timeout=self._timeout)
            except httpx.TransportError as error:  # includes timeouts and connection errors
                if attempt == self._max_attempts:
                    raise LLMError(f"{self.provider} request failed: {error!r}") from error
                wait = 2.0**attempt
            else:
                if response.status_code == 200:
                    data: dict[str, Any] = response.json()
                    return data
                if response.status_code not in RETRY_STATUS or attempt == self._max_attempts:
                    raise LLMError(
                        f"{self.provider} returned HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )
                wait = _retry_after(response) or 2.0**attempt
            logger.warning("llm request failed, retrying in %.0fs (attempt %d)", wait, attempt)
            self._sleep(min(wait, MAX_RETRY_WAIT_SECONDS))
        raise AssertionError("unreachable")

    def _build_request(
        self, system: str, messages: list[Message], json_mode: bool
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def _parse_response(self, data: dict[str, Any]) -> Reply:
        raise NotImplementedError


class GeminiModel(HttpChatModel):
    provider = "gemini"

    def _build_request(
        self, system: str, messages: list[Message], json_mode: bool
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [
                # Gemini calls the assistant role "model".
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [{"text": m.content}],
                }
                for m in messages
            ],
        }
        if json_mode:
            body["generationConfig"] = {"responseMimeType": "application/json"}
        headers = {"x-goog-api-key": self._api_key}
        return GEMINI_URL.format(model=self.model), headers, body

    def _parse_response(self, data: dict[str, Any]) -> Reply:
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError) as error:
            raise LLMError(f"gemini returned no answer: {str(data)[:500]}") from error
        text = "".join(part.get("text", "") for part in parts)
        meta = data.get("usageMetadata", {})
        # Thinking models also bill the "thoughts" tokens as output.
        output = meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
        return Reply(text, TokenUsage(meta.get("promptTokenCount", 0), output))


class DeepSeekModel(HttpChatModel):
    provider = "deepseek"

    def __init__(self, *args: Any, base_url: str = DEEPSEEK_DEFAULT_BASE_URL, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._base_url = base_url.rstrip("/")

    def _build_request(
        self, system: str, messages: list[Message], json_mode: bool
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}]
            + [{"role": m.role, "content": m.content} for m in messages],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        return f"{self._base_url}/chat/completions", headers, body

    def _parse_response(self, data: dict[str, Any]) -> Reply:
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as error:
            raise LLMError(f"deepseek returned no answer: {str(data)[:500]}") from error
        usage = data.get("usage", {})
        return Reply(
            text, TokenUsage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        )


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "")
    try:
        return float(value)
    except ValueError:
        return None


# Settings from environment variables

API_KEY_VARIABLES = {"gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    api_key: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    # Prices in US dollars per million tokens. None means unknown: cost is not estimated.
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    deepseek_base_url: str = DEEPSEEK_DEFAULT_BASE_URL

    def estimate_cost(self, usage: TokenUsage) -> float | None:
        if self.price_input_per_mtok is None or self.price_output_per_mtok is None:
            return None
        cost = (
            usage.input_tokens * self.price_input_per_mtok
            + usage.output_tokens * self.price_output_per_mtok
        ) / 1_000_000
        return round(cost, 6)  # a millionth of a dollar is enough, and hides float noise


def load_settings(
    env: Mapping[str, str], provider: str | None = None, model: str | None = None
) -> Settings:
    """Reads the settings. `provider` and `model` override the environment (CLI flags)."""
    provider = (provider or env.get("REPRO_PROVIDER", "")).strip().lower()
    if provider not in API_KEY_VARIABLES:
        raise ConfigError(
            f"REPRO_PROVIDER must be one of {sorted(API_KEY_VARIABLES)}, got '{provider}'"
        )
    model = (model or env.get("REPRO_MODEL", "")).strip()
    if not model:
        raise ConfigError("REPRO_MODEL is not set (or pass --model)")
    key_variable = API_KEY_VARIABLES[provider]
    api_key = env.get(key_variable, "").strip()
    if not api_key:
        raise ConfigError(f"{key_variable} is not set. Put it in the .env file")

    return Settings(
        provider=provider,
        model=model,
        api_key=api_key,
        timeout_seconds=_float(env, "REPRO_LLM_TIMEOUT") or DEFAULT_TIMEOUT_SECONDS,
        price_input_per_mtok=_float(env, "REPRO_PRICE_INPUT_PER_MTOK"),
        price_output_per_mtok=_float(env, "REPRO_PRICE_OUTPUT_PER_MTOK"),
        deepseek_base_url=env.get("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE_URL,
    )


def create_model(settings: Settings) -> ChatModel:
    if settings.provider == "gemini":
        return GeminiModel(settings.model, settings.api_key, settings.timeout_seconds)
    return DeepSeekModel(
        settings.model,
        settings.api_key,
        settings.timeout_seconds,
        base_url=settings.deepseek_base_url,
    )


def _float(env: Mapping[str, str], name: str) -> float | None:
    value = env.get(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number, got '{value}'") from error
