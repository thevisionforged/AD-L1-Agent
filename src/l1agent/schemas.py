"""Structured output contract for every agent reply (online and offline engines)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    source_id: str = Field(description="KB section ID exactly as returned by search_kb, e.g. KB-AD-001#s3")
    title: str


class AgentReply(BaseModel):
    response_type: Literal["answer", "clarify", "escalate", "refuse"]
    message: str
    steps: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    facts_from_directory: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    escalation_reason: str | None = None
    ticket_draft_id: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"


# JSON schema for OpenAI structured outputs (strict mode: every key required, nullable via type list).
REPLY_JSON_SCHEMA = {
    "name": "l1_agent_reply",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["response_type", "message", "steps", "citations", "facts_from_directory", "assumptions",
                     "clarifying_question", "escalation_reason", "ticket_draft_id", "confidence"],
        "properties": {
            "response_type": {"type": "string", "enum": ["answer", "clarify", "escalate", "refuse"]},
            "message": {"type": "string", "description": "Plain-language reply to the user, 2-5 sentences."},
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "Numbered self-help steps taken ONLY from retrieved KB sections."},
            "citations": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["source_id", "title"],
                "properties": {"source_id": {"type": "string"}, "title": {"type": "string"}}}},
            "facts_from_directory": {"type": "array", "items": {"type": "string"},
                                     "description": "Facts taken from get_user_context / run_ad_diagnostics results."},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "clarifying_question": {"type": ["string", "null"]},
            "escalation_reason": {"type": ["string", "null"]},
            "ticket_draft_id": {"type": ["string", "null"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
    },
}
