"""Parser for local Claude Code session transcripts (``*.jsonl``).

Claude Code (Anthropic's terminal agent) stores every session on disk as a
JSON-lines file under ``~/.claude/projects/<project>/<session-id>.jsonl``.
Each line is one record; the ones that matter here look like::

    {"type": "user", "timestamp": "2026-08-31T20:01:36.476Z",
     "message": {"role": "user", "content": "fix my failing tests please"}}

    {"type": "assistant", "timestamp": "2026-08-31T20:02:10.000Z",
     "message": {"role": "assistant",
                 "content": [{"type": "text", "text": "On it."}]}}

Everything else in the file — tool calls and their results, thinking blocks,
meta records, summaries — is internal bookkeeping and is filtered out, so the
archive holds just the visible back-and-forth of the session.

Sessions have no stored name, so the title is derived from the first thing
the user typed.

This format is Claude Code's internal storage, not a documented export, so
the parser degrades gracefully: an unrecognized or malformed line is skipped,
never fatal, and a file that yields no visible messages simply produces no
conversation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Conversation, Message
from .claude import _to_epoch as _iso_to_epoch

__all__ = ["parse"]

PROVIDER = "claude-code"
DEFAULT_TITLE = "Untitled session"

#: Longest title derived from the first user message.
MAX_TITLE_LEN = 60

#: Separator used when a turn carries several text blocks.
_BLOCK_SEPARATOR = "\n\n"

#: Harness-injected payloads recorded as "user" messages that the user never
#: actually typed (slash-command envelopes, reminders, task notifications).
_NOISE_PREFIXES = (
    "<command-",
    "<local-command-",
    "<system-reminder>",
    "<task-notification>",
)


def parse(path) -> list[Conversation]:
    """Parse one Claude Code session transcript into ``Conversation`` objects.

    Args:
        path: ``str`` or ``pathlib.Path`` pointing at a session ``.jsonl``
            file.

    Returns:
        A single-element list holding the session as a ``Conversation``, or an
        empty list when the file contains no visible messages at all.

    Raises:
        OSError: if the file cannot be read.
    """
    source = Path(path)
    messages: list[Message] = []
    first_user_text: str | None = None
    session_id: str | None = None

    with open(source, "r", encoding="utf-8") as handle:
        for line in handle:
            record = _load_record(line)
            if record is None:
                continue

            if session_id is None:
                candidate = record.get("sessionId")
                if isinstance(candidate, str) and candidate.strip():
                    session_id = candidate.strip()

            message = _parse_record(record)
            if message is None:
                continue
            if first_user_text is None and message.role == "user":
                first_user_text = message.text
            messages.append(message)

    if not messages:
        return []

    stamps = [m.timestamp for m in messages if m.timestamp is not None]
    return [
        Conversation(
            id=session_id or source.stem,
            title=_derive_title(first_user_text),
            provider=PROVIDER,
            created_at=min(stamps) if stamps else None,
            updated_at=max(stamps) if stamps else None,
            messages=messages,
        )
    ]


def _load_record(line: str) -> dict | None:
    """Decode one transcript line, returning ``None`` for anything unusable."""
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _parse_record(record: dict) -> Message | None:
    """Convert one record into a ``Message``, or ``None`` if it is not one.

    Only visible ``user`` and ``assistant`` turns survive: meta records,
    summaries, tool traffic, thinking blocks, and harness-injected noise are
    all filtered out here.
    """
    kind = record.get("type")
    if kind not in ("user", "assistant") or record.get("isMeta"):
        return None

    payload = record.get("message")
    if not isinstance(payload, dict):
        return None

    text = _extract_text(payload.get("content"))
    if not text.strip() or _is_noise(text):
        return None

    return Message(
        role="user" if kind == "user" else "assistant",
        text=text,
        timestamp=_iso_to_epoch(record.get("timestamp")),
    )


def _extract_text(content: Any) -> str:
    """Join the text blocks of a ``content`` value into one string.

    ``content`` is either a plain string or a list of typed blocks; only
    ``{"type": "text"}`` blocks contribute, which is exactly what drops tool
    calls, tool results, and thinking blocks.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return _BLOCK_SEPARATOR.join(parts)


def _is_noise(text: str) -> bool:
    """True for harness-injected payloads the user never typed."""
    return text.lstrip().startswith(_NOISE_PREFIXES)


def _derive_title(first_user_text: str | None) -> str:
    """Title the session after the first thing the user typed."""
    if not first_user_text:
        return DEFAULT_TITLE
    collapsed = " ".join(first_user_text.split())
    if len(collapsed) <= MAX_TITLE_LEN:
        return collapsed
    cut = collapsed[:MAX_TITLE_LEN]
    # Prefer a word boundary over a mid-word chop.
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "\N{HORIZONTAL ELLIPSIS}"
