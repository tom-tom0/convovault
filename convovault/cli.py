"""Command line entry point for ConvoVault.

Reads one or more ChatGPT/Claude export inputs (a ``conversations.json`` file,
a directory containing one, or an export ``.zip``), merges and de-duplicates the
conversations, and writes a Markdown archive plus a self-contained HTML search
page.

Standard library only.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, Sequence

from .models import Conversation
from .output.markdown import write_markdown
from .output.site import write_site
from .parsers import chatgpt, claude

__all__ = ["main"]

#: Name of the JSON member we look for inside directories and zip archives.
EXPORT_FILENAME = "conversations.json"

#: provider name -> module exposing ``parse(path) -> list[Conversation]``
PARSERS = {"chatgpt": chatgpt, "claude": claude}


# --------------------------------------------------------------------------- #
# input discovery
# --------------------------------------------------------------------------- #

def _warn(message: str) -> None:
    """Print a warning to stderr (never suppressed by ``--quiet``)."""
    print(f"convovault: warning: {message}", file=sys.stderr)


def _find_in_directory(directory: Path) -> Path | None:
    """Return the best ``conversations.json`` inside *directory*, if any.

    Prefers a file directly in the directory, otherwise the shallowest match
    found by walking the tree.
    """
    direct = directory / EXPORT_FILENAME
    if direct.is_file():
        return direct

    candidates: list[tuple[int, Path]] = []
    for root, dirnames, filenames in os.walk(directory):
        dirnames.sort()
        if EXPORT_FILENAME in filenames:
            candidate = Path(root) / EXPORT_FILENAME
            depth = len(candidate.relative_to(directory).parts)
            candidates.append((depth, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], str(item[1])))
    return candidates[0][1]


def _zip_member(archive: zipfile.ZipFile) -> str | None:
    """Return the name of the shallowest ``conversations.json`` member."""
    matches = [
        name
        for name in archive.namelist()
        if not name.endswith("/") and Path(name).name == EXPORT_FILENAME
    ]
    if not matches:
        return None
    matches.sort(key=lambda name: (name.count("/"), name))
    return matches[0]


def _extract_zip(path: Path, stack: contextlib.ExitStack) -> Path | None:
    """Extract the export JSON from a zip archive into a temporary file.

    Returns the path of the temporary copy, or ``None`` when the archive is
    unreadable or contains no ``conversations.json``.
    """
    try:
        archive = stack.enter_context(zipfile.ZipFile(path))
    except (zipfile.BadZipFile, OSError) as exc:
        _warn(f"{path}: could not read zip archive ({exc}); skipping")
        return None

    member = _zip_member(archive)
    if member is None:
        _warn(f"{path}: no {EXPORT_FILENAME} found inside the archive; skipping")
        return None

    try:
        data = archive.read(member)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        _warn(f"{path}: could not extract {member} ({exc}); skipping")
        return None

    tmpdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="convovault-")))
    tmpfile = tmpdir / EXPORT_FILENAME
    try:
        tmpfile.write_bytes(data)
    except OSError as exc:
        _warn(f"{path}: could not stage {member} for parsing ({exc}); skipping")
        return None
    return tmpfile


def _resolve_input(raw: str, stack: contextlib.ExitStack) -> tuple[str, Path] | None:
    """Turn one command line INPUT into ``(label, json_path)``.

    *label* is what the user typed, used for messages; *json_path* points at a
    real ``conversations.json`` on disk (possibly a temporary extraction).
    """
    path = Path(raw).expanduser()

    if not path.exists():
        _warn(f"{raw}: no such file or directory; skipping")
        return None

    if path.is_dir():
        found = _find_in_directory(path)
        if found is None:
            _warn(f"{raw}: no {EXPORT_FILENAME} found in this directory; skipping")
            return None
        return raw, found

    if zipfile.is_zipfile(path):
        extracted = _extract_zip(path, stack)
        if extracted is None:
            return None
        return raw, extracted

    return raw, path


# --------------------------------------------------------------------------- #
# provider sniffing
# --------------------------------------------------------------------------- #

def _load_json(path: Path, label: str) -> object | None:
    """Parse JSON defensively, warning and returning ``None`` on failure."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        _warn(f"{label}: could not read JSON ({exc}); skipping")
        return None


def _detect_provider(data: object) -> str | None:
    """Guess the provider from the shape of a parsed export.

    A list of conversations carrying a ``mapping`` is a ChatGPT export; one
    carrying ``chat_messages`` is a Claude export. Some exports wrap the list
    in an object, so that shape is unwrapped first. Every entry is scanned so
    a leading unrecognized entry cannot hide an otherwise valid export.
    """
    if isinstance(data, dict):
        for key in ("conversations", "chats", "items", "data"):
            nested = data.get(key)
            if isinstance(nested, list):
                data = nested
                break
        else:
            data = [data]

    if not isinstance(data, list):
        return None

    for entry in data:
        if not isinstance(entry, dict):
            continue
        if "mapping" in entry:
            return "chatgpt"
        if "chat_messages" in entry:
            return "claude"
    return None


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #

def _sort_key(conversation: Conversation) -> float:
    """Best-effort recency for sorting; unknown timestamps sort oldest."""
    for value in (conversation.updated_at, conversation.created_at):
        if isinstance(value, (int, float)):
            return float(value)
    return float("-inf")


def _merge(conversations: Iterable[Conversation]) -> list[Conversation]:
    """De-duplicate by ``(provider, id)`` keeping the newest, then sort."""
    best: dict[tuple[str, str], Conversation] = {}
    order: list[tuple[str, str]] = []
    for conversation in conversations:
        key = (conversation.provider, conversation.id)
        existing = best.get(key)
        if existing is None:
            best[key] = conversation
            order.append(key)
        elif _sort_key(conversation) >= _sort_key(existing):
            best[key] = conversation
    merged = [best[key] for key in order]
    merged.sort(key=_sort_key, reverse=True)
    return merged


def _collect(inputs: Sequence[str], stack: contextlib.ExitStack) -> list[Conversation]:
    """Parse every usable input, skipping anything malformed."""
    collected: list[Conversation] = []
    seen_paths: set[Path] = set()

    for raw in inputs:
        resolved = _resolve_input(raw, stack)
        if resolved is None:
            continue
        label, json_path = resolved

        try:
            real = json_path.resolve()
        except OSError:
            real = json_path
        if real in seen_paths:
            continue
        seen_paths.add(real)

        data = _load_json(json_path, label)
        if data is None:
            continue

        provider = _detect_provider(data)
        if provider is None:
            _warn(
                f"{label}: unrecognized export format "
                f"(expected a ChatGPT or Claude {EXPORT_FILENAME}); skipping"
            )
            continue

        parser = PARSERS[provider]
        try:
            parsed = parser.parse(json_path)
        except Exception as exc:  # defensive: one bad export must not abort the run
            _warn(f"{label}: {provider} parser failed ({exc}); skipping")
            continue

        collected.extend(item for item in parsed if isinstance(item, Conversation))

    return collected


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def _counts_by_provider(conversations: Sequence[Conversation]) -> list[tuple[str, int]]:
    """Return ``(provider, count)`` pairs, most conversations first."""
    counts: dict[str, int] = {}
    for conversation in conversations:
        counts[conversation.provider] = counts.get(conversation.provider, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _plural(count: int, noun: str) -> str:
    """``3 conversations`` / ``1 conversation``."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def _build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the ``convovault`` command."""
    parser = argparse.ArgumentParser(
        prog="convovault",
        description=(
            "Turn ChatGPT and Claude data exports into one searchable local "
            "archive of Markdown files plus a self-contained HTML search page."
        ),
        epilog=(
            "Each INPUT may be a conversations.json file, a directory "
            "containing one, or an export .zip archive."
        ),
    )
    parser.add_argument(
        "inputs",
        metavar="INPUT",
        nargs="+",
        help="export file, directory, or .zip to import",
    )
    parser.add_argument(
        "-o",
        "--out",
        default="./vault",
        metavar="DIR",
        help="output directory (default: ./vault)",
    )
    parser.add_argument(
        "--no-markdown",
        action="store_true",
        help="do not write the Markdown archive",
    )
    parser.add_argument(
        "--no-site",
        action="store_true",
        help="do not write the HTML search page",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only print warnings and errors",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI. Returns 0 on success, 1 when nothing could be imported."""
    args = _build_parser().parse_args(argv)

    def say(message: str = "") -> None:
        if not args.quiet:
            print(message)

    with contextlib.ExitStack() as stack:
        conversations = _merge(_collect(args.inputs, stack))

    if not conversations:
        print(
            "convovault: no conversations were parsed from the given input(s).",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.out).expanduser()
    message_count = sum(len(c.messages) for c in conversations)

    say(f"Imported {_plural(len(conversations), 'conversation')} "
        f"({_plural(message_count, 'message')}):")
    for provider, count in _counts_by_provider(conversations):
        say(f"  {provider:<8} {count}")
    say()

    wrote_site: Path | None = None

    if not args.no_markdown:
        markdown_dir = out_dir / "markdown"
        try:
            written = write_markdown(conversations, markdown_dir)
        except Exception as exc:
            print(f"convovault: error: could not write Markdown ({exc})", file=sys.stderr)
            return 1
        say(f"Markdown: {_plural(len(written), 'file')} in {markdown_dir}")

    if not args.no_site:
        try:
            wrote_site = write_site(conversations, out_dir)
        except Exception as exc:
            print(f"convovault: error: could not write search page ({exc})", file=sys.stderr)
            return 1
        say(f"Search page: {wrote_site}")

    if args.no_markdown and args.no_site:
        say("Nothing written (--no-markdown and --no-site were both given).")
    elif wrote_site is not None:
        say()
        say(f"Open {wrote_site} in your browser")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
