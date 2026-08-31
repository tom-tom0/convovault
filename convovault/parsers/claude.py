"""Parser for Claude data exports (``conversations.json``).

A Claude export is a JSON list of conversation objects::

    [
      {
        "uuid": "...",
        "name": "Trip packing checklist",
        "created_at": "2026-08-10T09:15:00.123456Z",
        "updated_at": "2026-08-10T09:20:30.000000Z",
        "chat_messages": [
          {
            "sender": "human",
            "created_at": "2026-08-10T09:15:00.123456Z",
            "text": "...",
            "content": [{"type": "text", "text": "..."}]
          },
          ...
        ]
      },
      ...
    ]

The parser is deliberately forgiving: any conversation or message that is
malformed (wrong type, missing keys, unparseable timestamp, empty body) is
skipped rather than allowed to abort the whole import.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import Conversation, Message

__all__ = ["parse"]

PROVIDER = "claude"
DEFAULT_TITLE = "Untitled"

#: Claude's ``sender`` values mapped onto the shared model's roles.
_ROLE_BY_SENDER = {
    "human": "user",
    "user": "user",
    "assistant": "assistant",
    "system": "system",
    "tool": "tool",
}

#: Role used when ``sender`` is missing or unrecognised.
_FALLBACK_ROLE = "assistant"

#: Separator used when a message carries several text content blocks.
_BLOCK_SEPARATOR = "\n\n"


def parse(path) -> list[Conversation]:
    """Parse a Claude ``conversations.json`` export into ``Conversation`` objects.

    Args:
        path: ``str`` or ``pathlib.Path`` pointing at the export file.

    Returns:
        A list of ``Conversation`` objects in export order. Conversations whose
        messages all turn out to be empty are still returned (with an empty
        ``messages`` list); entries that are not JSON objects are skipped.

    Raises:
        OSError: if the file cannot be read.
        ValueError: if the file does not contain valid JSON.
    """
    with open(Path(path), "r", encoding="utf-8") as handle:
        data = json.load(handle)

    conversations = []
    for raw in _iter_raw_conversations(data):
        conversation = _parse_conversation(raw)
        if conversation is not None:
            conversations.append(conversation)
    return conversations


def _iter_raw_conversations(data: Any) -> list:
    """Return the list of raw conversation objects found in *data*.

    Handles the documented top-level list as well as the occasional wrapper
    object (``{"conversations": [...]}``) seen in hand-edited exports.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("conversations", "chats", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # A single conversation object on its own.
        if "chat_messages" in data:
            return [data]
    return []


def _parse_conversation(raw: Any) -> Conversation | None:
    """Build a ``Conversation`` from one raw export entry, or ``None`` if unusable."""
    if not isinstance(raw, dict):
        return None

    conversation_id = _as_text(raw.get("uuid")) or _as_text(raw.get("id"))
    if not conversation_id:
        return None

    title = _as_text(raw.get("name")).strip() or DEFAULT_TITLE

    raw_messages = raw.get("chat_messages")
    messages = []
    if isinstance(raw_messages, list):
        for raw_message in raw_messages:
            message = _parse_message(raw_message)
            if message is not None:
                messages.append(message)

    return Conversation(
        id=conversation_id,
        title=title,
        provider=PROVIDER,
        created_at=_to_epoch(raw.get("created_at")),
        updated_at=_to_epoch(raw.get("updated_at")),
        messages=messages,
    )


def _parse_message(raw: Any) -> Message | None:
    """Build a ``Message`` from one raw chat message, or ``None`` if it is empty."""
    if not isinstance(raw, dict):
        return None

    text = _message_text(raw)
    if not text:
        return None

    sender = _as_text(raw.get("sender")).strip().lower()
    role = _ROLE_BY_SENDER.get(sender, _FALLBACK_ROLE)

    return Message(role=role, text=text, timestamp=_to_epoch(raw.get("created_at")))


def _message_text(raw: dict) -> str:
    """Extract a message body: joined ``content`` text blocks, else top-level ``text``.

    Blocks that are not dicts, not of type ``"text"``, or whose text is blank are
    ignored, so tool-use and attachment blocks do not leak into the archive.
    """
    blocks = raw.get("content")
    if isinstance(blocks, list):
        parts = []
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            part = _as_text(block.get("text")).strip()
            if part:
                parts.append(part)
        if parts:
            return _BLOCK_SEPARATOR.join(parts)

    return _as_text(raw.get("text")).strip()


def _as_text(value: Any) -> str:
    """Return *value* as a string, mapping non-string values to ``""``."""
    return value if isinstance(value, str) else ""


def _to_epoch(value: Any) -> float | None:
    """Convert an ISO-8601 timestamp to a UTC unix epoch float.

    Accepts a trailing ``"Z"`` (rewritten to ``"+00:00"`` for Python < 3.11),
    treats naive datetimes as UTC, and passes numeric values through. Returns
    ``None`` for missing or unparseable input.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
