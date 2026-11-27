"""Scripted model for v2 tests: a fake OpenAI Chat Completions endpoint behind httpx.MockTransport.

The REAL v2 runtime runs against it: the OpenAI Agents SDK loop, the tool gate, the sub-agent, approvals,
output validation and fallbacks. Only the model's answers are scripted, so tests are exact and need no network.
Built by Samarth Pandey (hack-grp-22)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any

import httpx
import openai
from agents import OpenAIChatCompletionsModel, set_tracing_disabled

FAKE_KEY = "sk-test-FAKE0123456789abcdefFAKE"
TEST_MODEL = "approved-test-model"
set_tracing_disabled(True)


def call(name: str, **args: Any) -> dict:
    return {"tool_calls": [(name, json.dumps(args))]}


def raw_call(name: str, arguments: str) -> dict:
    return {"tool_calls": [(name, arguments)]}


def calls(*pairs: tuple[str, dict]) -> dict:
    return {"tool_calls": [(n, json.dumps(a)) for n, a in pairs]}


def answer(text: str) -> dict:
    return {"content": text}


def status(code: int) -> dict:
    return {"status": code}


class ScriptedModel:
    def __init__(self, steps: list):
        self.steps = list(steps)
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self._n = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(req.content))
        self.headers.append(dict(req.headers))
        step = self.steps.pop(0) if self.steps else answer("Done.")
        if isinstance(step, BaseException):
            raise step
        if "status" in step:
            return httpx.Response(step["status"], json={"error": {"message": "scripted failure", "type": "x"}})
        if "raw" in step:
            return httpx.Response(200, content=step["raw"], headers={"content-type": "application/json"})
        msg: dict[str, Any] = {"role": "assistant", "content": step.get("content")}
        if step.get("tool_calls"):
            msg["tool_calls"] = []
            for name, args in step["tool_calls"]:
                self._n += 1
                msg["tool_calls"].append({"id": f"call_{self._n}", "type": "function",
                                          "function": {"name": name, "arguments": args}})
        return httpx.Response(200, json={
            "id": f"chatcmpl-{len(self.requests)}", "object": "chat.completion", "created": 0, "model": TEST_MODEL,
            "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if step.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def sent_text(self) -> str:
        return json.dumps(self.requests)

    def tool_messages(self) -> list[str]:
        """Every distinct tool result the model received, in order."""
        seen, out = set(), []
        for r in self.requests:
            for m in r["messages"]:
                if m.get("role") == "tool" and m["tool_call_id"] not in seen:
                    seen.add(m["tool_call_id"])
                    out.append(m["content"] if isinstance(m["content"], str) else json.dumps(m["content"]))
        return out


def scripted_v2(steps: list):
    """(v2_service, scripted_model) with a fresh runtime directory."""
    os.environ["RUNTIME_DIR"] = tempfile.mkdtemp(prefix="v2test-")
    from l1agent.agent import AgentService
    from l1agent.config import get_settings
    from l1agent.v2.agents_app import V2Service
    model = ScriptedModel(steps)
    client = openai.AsyncOpenAI(api_key=FAKE_KEY, base_url="https://api.openai.com/v1", max_retries=0,
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(model.handler)))
    svc = AgentService(get_settings())
    return V2Service(svc, model=OpenAIChatCompletionsModel(model=TEST_MODEL, openai_client=client)), model


def chat(v2, message: str, user: str = "u1001", conversation_id: str | None = None) -> dict:
    return asyncio.run(v2.chat(user, message, conversation_id))
