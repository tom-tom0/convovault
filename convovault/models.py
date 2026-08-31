"""Shared data model. Every parser returns these; every writer consumes them."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system" | "tool"
    text: str
    timestamp: float | None = None  # unix epoch seconds, UTC


@dataclass
class Conversation:
    id: str
    title: str
    provider: str  # "chatgpt" | "claude"
    created_at: float | None = None
    updated_at: float | None = None
    messages: list[Message] = field(default_factory=list)
