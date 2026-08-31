"""Edge-case and robustness tests for ConvoVault.

Where ``test_convovault.py`` proves the happy path, this file attacks it:
hostile titles, broken trees, extreme timestamps, unicode, duplicate inputs,
CLI flags, and a mid-sized synthetic archive. Everything here must hold for
the "one bad entry never costs you the run" promise in the README to be true.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES = TESTS_DIR / "fixtures"
CLAUDE_FIXTURE = FIXTURES / "claude_conversations.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from convovault.cli import main  # noqa: E402
from convovault.models import Conversation, Message  # noqa: E402
from convovault.output.markdown import write_markdown  # noqa: E402
from convovault.output.site import write_site  # noqa: E402
from convovault.parsers import chatgpt, claude  # noqa: E402

SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")


def _conv(cid, title, text="body", provider="claude", created=1.0, updated=2.0):
    return Conversation(
        id=cid,
        title=title,
        provider=provider,
        created_at=created,
        updated_at=updated,
        messages=[Message(role="user", text=text, timestamp=created)],
    )


def _payload(index_html: Path):
    """Extract and decode the embedded JSON payload from index.html."""
    html = index_html.read_text(encoding="utf-8")
    match = re.search(
        r'<script type="application/json" id="convovault-data">(.*?)</script>',
        html,
        re.S,
    )
    assert match, "payload script tag missing"
    return json.loads(match.group(1))


# --------------------------------------------------------------------------
# Hostile / unusual conversation metadata
# --------------------------------------------------------------------------


def test_hostile_titles_become_safe_filenames(tmp_path):
    """Path traversal, separators, reserved names, and unicode stay contained."""
    titles = [
        "../../../etc/passwd",
        "..\\..\\windows\\system32",
        "CON",
        "a" * 500,
        "naïve café ☕ résumé",
        "日本語のタイトルだけ",
        "",
        "   ",
        "<script>alert(1)</script>",
        "slashes/in\\the:title|everywhere?*",
    ]
    convs = [_conv(f"id-{i}", t) for i, t in enumerate(titles)]

    written = write_markdown(convs, tmp_path / "md")

    assert len(written) == len(titles)
    for path in written:
        # Every file landed inside the output directory...
        assert (tmp_path / "md") in path.parents
        # ...with a conservative filename.
        assert SAFE_NAME.match(path.name), "unsafe filename: %r" % path.name
        assert len(path.name) < 100


def test_title_with_newline_stays_one_heading(tmp_path):
    """A newline inside a title cannot smuggle extra markdown lines."""
    conv = _conv("nl-1", "Line one\nLine two\n# fake heading")
    (path,) = write_markdown([conv], tmp_path)
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "# Line one Line two # fake heading"


def test_extreme_timestamps_never_crash(tmp_path):
    """NaN, infinities, huge, negative, bool, and string timestamps all survive."""
    weird = [
        float("nan"),
        float("inf"),
        float("-inf"),
        1e30,
        -1e12,
        True,
        "not-a-number",
        "1755000000",
        None,
        0,
    ]
    convs = [
        Conversation(
            id=f"ts-{i}",
            title=f"Timestamp case {i}",
            provider="chatgpt",
            created_at=value,
            updated_at=value,
            messages=[Message(role="user", text=f"text {i}", timestamp=value)],
        )
        for i, value in enumerate(weird)
    ]

    written = write_markdown(convs, tmp_path / "md")
    assert len(written) == len(weird)

    write_site(convs, tmp_path / "site")
    payload = _payload(tmp_path / "site" / "index.html")
    assert len(payload) == len(weird)
    # The payload must be strictly valid JSON: no NaN/Infinity literals.
    raw = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r'<script type="application/json" id="convovault-data">(.*?)</script>', raw, re.S
    )
    assert "NaN" not in match.group(1) and "Infinity" not in match.group(1)


def test_control_characters_survive_the_site(tmp_path):
    """Nulls, tabs, CRLF, and line/paragraph separators round-trip safely."""
    text = "before\x00after\ttab\r\nline sep done"
    write_site([_conv("ctl-1", "Control chars", text=text)], tmp_path)
    payload = _payload(tmp_path / "index.html")
    assert payload[0]["messages"][0]["text"] == text
    # U+2028/U+2029 must be escaped, never raw, inside the script element.
    raw = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert " " not in raw and " " not in raw


def test_unicode_round_trip_through_cli(tmp_path):
    """Emoji, CJK, and RTL text survive from export to markdown and HTML."""
    text = "Emoji 🎉🚀, 中文测试, עברית, العربية, ñandú"
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "uuid": "uni-1",
                    "name": "Unicode ✓ test",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:01:00Z",
                    "chat_messages": [
                        {
                            "sender": "human",
                            "created_at": "2026-01-01T00:00:00Z",
                            "text": text,
                            "content": [{"type": "text", "text": text}],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "vault"
    assert main([str(export), "-o", str(out), "-q"]) == 0

    (md_file,) = sorted(out.glob("markdown/*.md"))
    assert text in md_file.read_text(encoding="utf-8")
    payload = _payload(out / "index.html")
    assert payload[0]["messages"][0]["text"] == text
    assert payload[0]["title"] == "Unicode ✓ test"


# --------------------------------------------------------------------------
# Broken parser input
# --------------------------------------------------------------------------


def test_chatgpt_parent_cycle_terminates(tmp_path):
    """A parent-pointer cycle in the mapping must not hang or crash."""
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "Cycle",
                    "conversation_id": "cycle-1",
                    "current_node": "a",
                    "mapping": {
                        "a": {
                            "id": "a",
                            "parent": "b",
                            "children": [],
                            "message": {
                                "author": {"role": "user"},
                                "content": {"content_type": "text", "parts": ["from a"]},
                            },
                        },
                        "b": {
                            "id": "b",
                            "parent": "a",
                            "children": ["a"],
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"content_type": "text", "parts": ["from b"]},
                            },
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    convs = chatgpt.parse(export)
    assert len(convs) == 1
    assert [m.text for m in convs[0].messages] == ["from b", "from a"]


def test_chatgpt_missing_current_node_uses_deepest_leaf(tmp_path):
    """Without current_node, the longest root-to-leaf path is archived."""
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "No pointer",
                    "conversation_id": "nocur-1",
                    "mapping": {
                        "root": {"id": "root", "parent": None, "children": ["u1"], "message": None},
                        "u1": {
                            "id": "u1",
                            "parent": "root",
                            "children": ["short", "a1"],
                            "message": {
                                "author": {"role": "user"},
                                "content": {"content_type": "text", "parts": ["question"]},
                            },
                        },
                        "short": {
                            "id": "short",
                            "parent": "u1",
                            "children": [],
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"content_type": "text", "parts": ["abandoned"]},
                            },
                        },
                        "a1": {
                            "id": "a1",
                            "parent": "u1",
                            "children": ["u2"],
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"content_type": "text", "parts": ["answer"]},
                            },
                        },
                        "u2": {
                            "id": "u2",
                            "parent": "a1",
                            "children": [],
                            "message": {
                                "author": {"role": "user"},
                                "content": {"content_type": "text", "parts": ["follow-up"]},
                            },
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (conv,) = chatgpt.parse(export)
    texts = [m.text for m in conv.messages]
    assert texts == ["question", "answer", "follow-up"]
    assert "abandoned" not in texts


def test_claude_malformed_entries_are_skipped(tmp_path):
    """Junk entries between valid conversations are dropped, not fatal."""
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                42,
                "not a conversation",
                None,
                [],
                {},  # no uuid
                {"uuid": "bad-msgs", "name": "x", "chat_messages": "not a list"},
                {
                    "uuid": "good-1",
                    "name": "Survivor",
                    "chat_messages": [
                        {"sender": "human", "text": "still here", "content": []},
                        12345,
                        {"sender": "assistant", "text": ""},  # empty -> dropped
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )
    convs = claude.parse(export)
    by_id = {c.id: c for c in convs}
    assert "good-1" in by_id
    assert [m.text for m in by_id["good-1"].messages] == ["still here"]
    assert "bad-msgs" in by_id  # kept, just empty
    assert by_id["bad-msgs"].messages == []


# --------------------------------------------------------------------------
# CLI behaviour
# --------------------------------------------------------------------------


def test_cli_dedupes_across_inputs_keeping_newest(tmp_path):
    """The same conversation in two exports keeps only the newer copy."""

    def export(path, text, updated):
        path.write_text(
            json.dumps(
                [
                    {
                        "uuid": "same-conv",
                        "name": "Evolving chat",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": updated,
                        "chat_messages": [
                            {"sender": "human", "text": text, "content": []}
                        ],
                    }
                ]
            ),
            encoding="utf-8",
        )

    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    export(old, "OLDER text", "2026-01-01T00:00:00Z")
    export(new, "NEWER text", "2026-06-01T00:00:00Z")
    out = tmp_path / "vault"

    assert main([str(old), str(new), "-o", str(out), "-q"]) == 0

    files = sorted(out.glob("markdown/*.md"))
    assert len(files) == 1
    blob = files[0].read_text(encoding="utf-8")
    assert "NEWER text" in blob
    assert "OLDER text" not in blob


def test_cli_same_input_listed_twice_reads_it_once(tmp_path):
    out = tmp_path / "vault"
    assert main([str(CLAUDE_FIXTURE), str(CLAUDE_FIXTURE), "-o", str(out), "-q"]) == 0
    assert len(sorted(out.glob("markdown/*.md"))) == 2


def test_cli_no_site_and_no_markdown_flags(tmp_path):
    out1 = tmp_path / "v1"
    assert main([str(CLAUDE_FIXTURE), "-o", str(out1), "-q", "--no-site"]) == 0
    assert not (out1 / "index.html").exists()
    assert len(sorted(out1.glob("markdown/*.md"))) == 2

    out2 = tmp_path / "v2"
    assert main([str(CLAUDE_FIXTURE), "-o", str(out2), "-q", "--no-markdown"]) == 0
    assert (out2 / "index.html").is_file()
    assert not (out2 / "markdown").exists()

    out3 = tmp_path / "v3"
    assert main(
        [str(CLAUDE_FIXTURE), "-o", str(out3), "-q", "--no-markdown", "--no-site"]
    ) == 0
    assert not out3.exists()


def test_cli_quiet_prints_nothing_on_success(tmp_path, capsys):
    assert main([str(CLAUDE_FIXTURE), "-o", str(tmp_path / "v"), "-q"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_zip_without_export_fails_cleanly(tmp_path, capsys):
    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "nothing to see")

    assert main([str(archive), "-o", str(tmp_path / "v")]) == 1
    err = capsys.readouterr().err
    assert "warning" in err
    assert "no conversations were parsed" in err


def test_cli_empty_export_fails_cleanly(tmp_path, capsys):
    export = tmp_path / "conversations.json"
    export.write_text("[]", encoding="utf-8")
    assert main([str(export), "-o", str(tmp_path / "v")]) == 1
    assert "unrecognized export format" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Scale
# --------------------------------------------------------------------------


def test_five_hundred_conversations_end_to_end(tmp_path):
    """A mid-sized archive builds completely and stays internally consistent."""
    count = 500
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "uuid": f"bulk-{i:04d}",
                    "name": f"Conversation number {i}",
                    "created_at": f"2026-01-{(i % 28) + 1:02d}T12:00:00Z",
                    "updated_at": f"2026-02-{(i % 28) + 1:02d}T12:00:00Z",
                    "chat_messages": [
                        {"sender": "human", "text": f"question {i}", "content": []},
                        {"sender": "assistant", "text": f"answer {i}", "content": []},
                    ],
                }
                for i in range(count)
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "vault"

    assert main([str(export), "-o", str(out), "-q"]) == 0

    files = sorted(out.glob("markdown/*.md"))
    assert len(files) == count
    assert len({f.name for f in files}) == count

    payload = _payload(out / "index.html")
    assert len(payload) == count
    assert sum(len(c["messages"]) for c in payload) == count * 2
