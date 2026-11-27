"""OpenAI access layer: one server-side client, bounded retries, usage accounting.

* The SDK's own retries are disabled; we retry 429 / timeout / connection / 5xx
  errors ourselves with exponential backoff and full jitter, capped at
  MAX_RETRIES attempts, and then raise LLMUnavailable so the orchestrator can fall
  back to the offline engine instead of looping.
* 4xx errors other than 429 (bad request, auth) are not retried.
* The API key is never logged or included in exceptions surfaced to users.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import openai
from openai import OpenAI

from .config import Settings

RETRYABLE = (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError)


class LLMUnavailable(RuntimeError):
    """The model could not be reached after bounded retries (or returned unusable output)."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retries: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, resp: Any) -> None:
        self.calls += 1
        u = getattr(resp, "usage", None)
        if u:
            self.prompt_tokens += getattr(u, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(u, "completion_tokens", 0) or 0

    def as_dict(self) -> dict[str, Any]:
        return {"model_calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "retries": self.retries}


class LLM:
    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.sleep = sleep
        self.client = OpenAI(api_key=settings.openai_api_key or "missing", base_url=settings.openai_base_url,
                             timeout=settings.request_timeout_s, max_retries=0)

    def _with_retry(self, fn: Callable[[], Any], usage: Usage) -> Any:
        attempts = max(1, self.settings.max_retries)
        for attempt in range(attempts):
            try:
                return fn()
            except RETRYABLE as e:
                usage.errors.append(type(e).__name__)
                if attempt == attempts - 1:
                    raise LLMUnavailable(f"OpenAI unavailable after {attempts} attempts ({type(e).__name__})") from None
                usage.retries += 1
                delay = min(8.0, 0.5 * (2 ** attempt))
                self.sleep(random.uniform(0, delay) + 0.1)   # full jitter
            except openai.APIStatusError as e:
                usage.errors.append(f"HTTP {e.status_code}")
                raise LLMUnavailable(f"OpenAI request rejected (HTTP {e.status_code})") from None
        raise LLMUnavailable("unreachable")

    def chat(self, messages: list[dict], usage: Usage, *, tools: list[dict] | None = None,
             tool_choice: str | None = None, response_format: dict | None = None,
             max_tokens: int | None = None) -> Any:
        kwargs: dict[str, Any] = {"model": self.settings.model, "messages": messages,
                                  "max_completion_tokens": max_tokens or self.settings.max_output_tokens}
        if not self.settings.model.startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["temperature"] = 0.2   # reasoning models only accept the default temperature
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = False
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": response_format}
        resp = self._with_retry(lambda: self.client.chat.completions.create(**kwargs), usage)
        usage.add(resp)
        return resp

    def embed(self, texts: list[str]) -> list[list[float]]:
        usage = Usage()
        resp = self._with_retry(lambda: self.client.embeddings.create(
            model=self.settings.embedding_model, input=[t[:4000] for t in texts]), usage)
        return [d.embedding for d in resp.data]

    def moderate(self, text: str, usage: Usage) -> tuple[bool, list[str]]:
        """Returns (flagged, categories). Raises LLMUnavailable on failure (caller fails open to local checks)."""
        try:
            resp = self.client.with_options(timeout=10, max_retries=0).moderations.create(
                model=self.settings.moderation_model, input=text)
        except (openai.APIError, openai.APIConnectionError) as e:
            usage.errors.append(f"moderation:{type(e).__name__}")
            raise LLMUnavailable("moderation unavailable") from None
        r = resp.results[0]
        cats = [k for k, v in r.categories.model_dump().items() if v]
        return bool(r.flagged), cats
