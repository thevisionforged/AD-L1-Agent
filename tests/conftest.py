"""Shared fixtures. Built by Samarth Pandey (hack-grp-22).

Tests never call the real OpenAI API. Online behaviour is exercised against a local
fake OpenAI server (FakeOpenAI) that replays scripted responses, so the suite is
fast, free and deterministic.
"""
from __future__ import annotations

import dataclasses
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from l1agent.agent import AgentService  # noqa: E402
from l1agent.config import Settings  # noqa: E402


@pytest.fixture
def offline_service(tmp_path) -> AgentService:
    s = dataclasses.replace(Settings(), agent_mode="offline", openai_api_key="", runtime_dir=tmp_path)
    return AgentService(s)


class FakeOpenAI:
    """Minimal OpenAI-compatible server. `script` is a list of chat responses consumed in order.

    Each item is either a dict {"status": 429} or a chat message dict:
      {"tool_calls": [(name, args_dict), ...]}  or  {"content": "<json string>"}
    """

    def __init__(self):
        self.script: list[dict] = []
        self.requests: list[dict] = []
        self.moderation_flagged = False
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                req = json.loads(self.rfile.read(n) or b"{}")
                outer.requests.append({"path": self.path, "body": req})
                if self.path.endswith("/moderations"):
                    return self._send(200, {"id": "m", "model": "omni", "results": [{
                        "flagged": outer.moderation_flagged, "categories": {"violence": outer.moderation_flagged},
                        "category_scores": {"violence": 0.9 if outer.moderation_flagged else 0.0}}]})
                if self.path.endswith("/embeddings"):
                    return self._send(500, {"error": {"message": "not scripted"}})
                if not outer.script:
                    return self._send(500, {"error": {"message": "script exhausted"}})
                item = outer.script.pop(0)
                if "status" in item:
                    return self._send(item["status"], {"error": {"message": "scripted error", "type": "rate_limit"}})
                msg = {"role": "assistant", "content": item.get("content")}
                if "tool_calls" in item:
                    msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                                          "function": {"name": name, "arguments": json.dumps(args)}}
                                         for i, (name, args) in enumerate(item["tool_calls"])]
                self._send(200, {"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                                 "choices": [{"index": 0, "message": msg,
                                              "finish_reason": "tool_calls" if "tool_calls" in item else "stop"}],
                                 "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def fake_openai():
    f = FakeOpenAI()
    yield f
    f.close()


@pytest.fixture
def online_service(tmp_path, fake_openai) -> AgentService:
    s = dataclasses.replace(Settings(), agent_mode="auto", openai_api_key="test-placeholder",
                            openai_base_url=fake_openai.url, runtime_dir=tmp_path, use_embeddings=False,
                            max_retries=3, request_timeout_s=5)
    svc = AgentService(s)
    assert svc.llm is not None
    svc.llm.sleep = lambda _s: None  # no real backoff waits in tests
    return svc


def final(response_type="answer", message="ok", steps=None, citations=None, ticket_draft_id=None, **kw) -> dict:
    body = {"response_type": response_type, "message": message, "steps": steps or [],
            "citations": citations or [], "facts_from_directory": [], "assumptions": [],
            "clarifying_question": None, "escalation_reason": None, "ticket_draft_id": ticket_draft_id,
            "confidence": "high"}
    body.update(kw)
    return {"content": json.dumps(body)}
