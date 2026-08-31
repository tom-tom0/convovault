"""Render :class:`~convovault.models.Conversation` objects as Markdown files.

One file per conversation, named ``<YYYY-MM-DD>-<slug>-<short id>.md``.  The
writer is deliberately forgiving: a single malformed conversation is skipped
with the rest of the run left intact.
"""
from __future__ import annotations

import pathlib
import re
import unicodedata
from datetime import datetime, timezone

__all__ = ["write_markdown"]

#: Maximum number of characters the title slug may contribute to a filename.
MAX_SLUG_LEN = 60

#: Providers whose display name is not simply title-cased.
_PROVIDER_NAMES = {
    "chatgpt": "ChatGPT",
    "openai": "OpenAI",
    "claude": "Claude",
    "anthropic": "Anthropic",
}

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_NON_ID = re.compile(r"[^A-Za-z0-9]+")


def _as_text(value) -> str:
    """Coerce *value* to a string, mapping ``None`` to the empty string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _slugify(title: str, max_len: int = MAX_SLUG_LEN) -> str:
    """Return a lowercase ascii ``a-z0-9-`` slug for *title*.

    Accented characters are folded to their ascii base form; everything else
    that is not a letter or digit collapses into a single hyphen.  Returns
    ``"untitled"`` when nothing usable survives.
    """
    text = _as_text(title)
    # Decompose accents ("é" -> "e" + combining acute) and drop non-ascii.
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_SLUG.sub("-", ascii_text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len]
        # Prefer cutting on a word boundary rather than mid-word.
        if "-" in slug:
            slug = slug.rsplit("-", 1)[0]
        slug = slug.strip("-")
    return slug or "untitled"


def _short_id(conversation_id) -> str:
    """Return a filesystem-safe 8-character fragment of *conversation_id*."""
    cleaned = _NON_ID.sub("", _as_text(conversation_id)).lower()
    return cleaned[:8] or "noid"


def _to_utc(timestamp) -> datetime | None:
    """Convert a unix epoch value to an aware UTC datetime, or ``None``."""
    if timestamp is None or isinstance(timestamp, bool):
        return None
    try:
        return datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _format_date(timestamp) -> str:
    """``YYYY-MM-DD`` for the filename, or ``"undated"``."""
    moment = _to_utc(timestamp)
    return moment.strftime("%Y-%m-%d") if moment else "undated"


def _format_datetime(timestamp) -> str:
    """``YYYY-MM-DD HH:MM`` in UTC for the metadata block, or ``"unknown"``."""
    moment = _to_utc(timestamp)
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "unknown"


def _format_time(timestamp) -> str | None:
    """``HH:MM`` in UTC for a message heading, or ``None``."""
    moment = _to_utc(timestamp)
    return moment.strftime("%H:%M") if moment else None


def _provider_name(provider) -> str:
    """Human-facing name for a provider slug (``chatgpt`` -> ``ChatGPT``)."""
    raw = _as_text(provider).strip()
    if not raw:
        return "Assistant"
    return _PROVIDER_NAMES.get(raw.lower(), raw.title())


def _role_label(role, provider) -> str:
    """Display label for a message role."""
    key = _as_text(role).strip().lower()
    if key == "user":
        return "You"
    if key == "assistant":
        return _provider_name(provider)
    if key == "system":
        return "System"
    if key == "tool":
        return "Tool"
    return key.title() if key else "Unknown"


def _unique_name(stem: str, used: set[str]) -> str:
    """Return ``<stem>.md``, adding a numeric suffix if the name is taken."""
    candidate = f"{stem}.md"
    counter = 2
    while candidate.lower() in used:
        candidate = f"{stem}-{counter}.md"
        counter += 1
    used.add(candidate.lower())
    return candidate


def _render(conversation) -> str:
    """Build the full Markdown document for one conversation."""
    provider = getattr(conversation, "provider", "")
    title = _as_text(getattr(conversation, "title", "")).strip() or "Untitled conversation"
    messages = list(getattr(conversation, "messages", None) or [])

    lines: list[str] = [f"# {title}", ""]
    lines.append(f"- **Provider:** {_provider_name(provider)}")
    lines.append(f"- **Created:** {_format_datetime(getattr(conversation, 'created_at', None))}")
    lines.append(f"- **Updated:** {_format_datetime(getattr(conversation, 'updated_at', None))}")
    lines.append(f"- **Messages:** {len(messages)}")
    lines.append("")

    for message in messages:
        label = _role_label(getattr(message, "role", ""), provider)
        clock = _format_time(getattr(message, "timestamp", None))
        heading = f"## {label} — {clock}" if clock else f"## {label}"
        # Trim surrounding blank lines only; inner text is kept verbatim so
        # code blocks and indentation survive the round trip.
        body = _as_text(getattr(message, "text", "")).strip("\r\n")
        lines.extend(["---", "", heading, "", body, ""])

    # Exactly one trailing newline.
    return "\n".join(lines).rstrip("\n") + "\n"


def write_markdown(conversations, out_dir) -> list[pathlib.Path]:
    """Write one Markdown file per conversation into *out_dir*.

    Args:
        conversations: iterable of :class:`~convovault.models.Conversation`.
        out_dir: destination directory as ``str`` or ``pathlib.Path``; it (and
            any missing parents) is created if absent.

    Returns:
        The paths written, in input order.  Conversations that fail to render
        or write are skipped rather than aborting the run.
    """
    directory = pathlib.Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    written: list[pathlib.Path] = []
    used_names: set[str] = set()

    for conversation in conversations or []:
        # Anything that is not conversation-shaped is not worth a file.
        if not hasattr(conversation, "messages") and not hasattr(conversation, "id"):
            continue
        try:
            stem = "-".join(
                (
                    _format_date(getattr(conversation, "created_at", None)),
                    _slugify(getattr(conversation, "title", "")),
                    _short_id(getattr(conversation, "id", "")),
                )
            )
            path = directory / _unique_name(stem, used_names)
            path.write_text(_render(conversation), encoding="utf-8", newline="\n")
        except (OSError, AttributeError, TypeError, ValueError):
            # Skip this conversation; the rest of the archive still gets built.
            continue
        written.append(path)

    return written
