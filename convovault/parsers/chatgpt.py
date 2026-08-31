"""Parser for ChatGPT ``conversations.json`` exports.

A ChatGPT export stores each conversation as a *tree* of nodes in a
``mapping`` dict rather than a flat list.  Every regeneration or edit adds a
sibling branch, so the conversation a user actually saw is the single path
running from ``current_node`` back up the ``parent`` links to the root.
This module reconstructs that active path only and converts it into the
shared :class:`~convovault.models.Conversation` model.

Public API::

    parse(path) -> list[Conversation]

The parser is deliberately forgiving: any conversation, node or message that
is malformed is skipped rather than aborting the whole run.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import Conversation, Message

__all__ = ["parse"]

PROVIDER = "chatgpt"

#: Roles understood by the shared model.  Anything else is normalised below.
_KNOWN_ROLES = frozenset({"user", "assistant", "system", "tool"})


def parse(path) -> list[Conversation]:
    """Parse a ChatGPT ``conversations.json`` export.

    Args:
        path: ``str`` or ``pathlib.Path`` pointing at the export file.  The
            file is expected to hold a JSON list of conversation objects; a
            dict wrapper containing such a list is also tolerated.

    Returns:
        A list of :class:`Conversation` objects, one per conversation that
        could be read.  Conversations that are malformed or that contain no
        visible messages are skipped.

    Raises:
        OSError: if the file cannot be read.
        ValueError: if the file does not contain usable JSON.
    """
    raw = _load_json(path)
    entries = _as_conversation_list(raw)

    conversations: list[Conversation] = []
    for index, entry in enumerate(entries):
        conversation = _parse_conversation(entry, index)
        if conversation is not None:
            conversations.append(conversation)
    return conversations


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _load_json(path) -> Any:
    """Read and decode the export file, raising ``ValueError`` on bad JSON."""
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"not valid JSON: {exc}") from exc


def _as_conversation_list(raw: Any) -> list[Any]:
    """Coerce the decoded export into a list of conversation objects."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        # Some exports wrap the list, e.g. {"conversations": [...]}.
        for key in ("conversations", "chats", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        # A single conversation object on its own.
        if "mapping" in raw:
            return [raw]
    return []


# --------------------------------------------------------------------------
# Conversation level
# --------------------------------------------------------------------------


def _parse_conversation(entry: Any, index: int) -> Conversation | None:
    """Convert one raw export entry into a :class:`Conversation`.

    Returns ``None`` when the entry is unusable or yields no visible
    messages.
    """
    if not isinstance(entry, dict):
        return None

    mapping = entry.get("mapping")
    if not isinstance(mapping, dict):
        return None

    title = _clean_title(entry.get("title"))
    messages = _active_thread_messages(mapping, entry.get("current_node"))
    if not messages:
        return None

    return Conversation(
        id=_conversation_id(entry, title, index),
        title=title,
        provider=PROVIDER,
        created_at=_as_timestamp(entry.get("create_time")),
        updated_at=_as_timestamp(entry.get("update_time")),
        messages=messages,
    )


def _clean_title(value: Any) -> str:
    """Return a non-empty title, falling back to ``"Untitled"``."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "Untitled"


def _conversation_id(entry: dict, title: str, index: int) -> str:
    """Pick a stable id: ``conversation_id``, ``id``, the title, or the index."""
    for key in ("conversation_id", "id"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    if title and title != "Untitled":
        return title
    return f"{PROVIDER}-{index}"


def _as_timestamp(value: Any) -> float | None:
    """Coerce an export timestamp to ``float`` seconds, or ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Thread reconstruction
# --------------------------------------------------------------------------


def _active_thread_messages(mapping: dict, current_node: Any) -> list[Message]:
    """Build the message list for the active branch of ``mapping``."""
    node_ids = _active_path(mapping, current_node)

    messages: list[Message] = []
    for node_id in node_ids:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        message = _parse_message(node.get("message"))
        if message is not None:
            messages.append(message)
    return messages


def _active_path(mapping: dict, current_node: Any) -> list[str]:
    """Return node ids from root to ``current_node`` along ``parent`` links.

    Regenerated siblings are never visited because the walk only ever follows
    the single ``parent`` pointer upwards.  A cycle or a dangling parent id
    simply ends the walk.  When ``current_node`` is missing or unknown, the
    longest root-to-leaf path in the mapping is used as a best-effort
    fallback so the conversation is not lost entirely.
    """
    start = current_node if isinstance(current_node, str) else None
    if start is None or start not in mapping:
        start = _fallback_leaf(mapping)
    if start is None:
        return []

    path: list[str] = []
    seen: set[str] = set()
    node_id: Any = start
    while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        path.append(node_id)
        node = mapping.get(node_id)
        node_id = node.get("parent") if isinstance(node, dict) else None

    path.reverse()
    return path


def _fallback_leaf(mapping: dict) -> str | None:
    """Pick the leaf whose ancestor chain is longest (ties: first seen)."""
    best_id: str | None = None
    best_depth = -1
    for node_id, node in mapping.items():
        if not isinstance(node_id, str) or not isinstance(node, dict):
            continue
        children = node.get("children")
        if isinstance(children, list) and children:
            continue  # not a leaf
        depth = _depth(mapping, node_id)
        if depth > best_depth:
            best_id, best_depth = node_id, depth
    return best_id


def _depth(mapping: dict, node_id: str) -> int:
    """Number of ancestors above ``node_id`` (cycle safe)."""
    depth = 0
    seen: set[str] = {node_id}
    current: Any = mapping.get(node_id, {}).get("parent")
    while isinstance(current, str) and current in mapping and current not in seen:
        seen.add(current)
        depth += 1
        node = mapping.get(current)
        current = node.get("parent") if isinstance(node, dict) else None
    return depth


# --------------------------------------------------------------------------
# Message level
# --------------------------------------------------------------------------


def _parse_message(raw: Any) -> Message | None:
    """Convert a node's ``message`` object into a :class:`Message`.

    Returns ``None`` for root nodes (``message`` is ``null``), for messages
    hidden from the conversation, and for messages whose rendered text is
    empty.
    """
    if not isinstance(raw, dict):
        return None
    if _is_hidden(raw):
        return None

    text = _extract_text(raw.get("content"))
    if not text.strip():
        return None

    return Message(
        role=_extract_role(raw.get("author")),
        text=text,
        timestamp=_as_timestamp(raw.get("create_time")),
    )


def _is_hidden(raw: dict) -> bool:
    """True when the export marks the message as hidden from the transcript."""
    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("is_visually_hidden_from_conversation") is True


def _extract_role(author: Any) -> str:
    """Map ``author.role`` onto the shared model's role vocabulary."""
    role = author.get("role") if isinstance(author, dict) else None
    if isinstance(role, str):
        normalised = role.strip().lower()
        if normalised in _KNOWN_ROLES:
            return normalised
        if normalised:
            # Unknown roles (e.g. "critic") are kept verbatim rather than
            # dropped, so no content silently disappears from the archive.
            return normalised
    return "assistant"


def _extract_text(content: Any) -> str:
    """Render a message ``content`` object as plain Markdown text.

    ``text`` content joins its non-empty string ``parts`` with newlines;
    ``code`` content is wrapped in a fenced block tagged with its language;
    any other content type falls back to whatever strings its ``parts`` or
    ``text`` fields hold.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return ""

    content_type = content.get("content_type")

    if content_type == "code":
        return _fence(_as_text(content.get("text")), content.get("language"))

    # "text" and every other content type share the same salvage strategy;
    # only "code" needs special framing.
    parts_text = _join_parts(content.get("parts"))
    if parts_text:
        return parts_text
    return _as_text(content.get("text"))


def _join_parts(parts: Any) -> str:
    """Join the non-empty string entries of a ``parts`` list with newlines."""
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""

    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            if part:
                chunks.append(part)
        elif isinstance(part, dict):
            # Multimodal parts occasionally carry a nested string payload.
            nested = _as_text(part.get("text"))
            if nested:
                chunks.append(nested)
    return "\n".join(chunks)


def _as_text(value: Any) -> str:
    """Return ``value`` if it is a string, else the empty string."""
    return value if isinstance(value, str) else ""


def _fence(text: str, language: Any) -> str:
    """Wrap ``text`` in a fenced code block tagged with ``language``.

    The fence is widened past any run of backticks inside ``text`` so that
    embedded fences cannot break out of the block.
    """
    if not text:
        return ""

    lang = language.strip() if isinstance(language, str) else ""
    # Language tags must not contain whitespace or backticks.
    if " " in lang or "`" in lang or "\n" in lang:
        lang = ""

    longest_run = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest_run = max(longest_run, run)
    fence = "`" * max(3, longest_run + 1)

    body = text[:-1] if text.endswith("\n") else text
    return f"{fence}{lang}\n{body}\n{fence}"
