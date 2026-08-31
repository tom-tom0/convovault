"""Tests for the Claude Code session parser and its CLI integration.

The fixture ``claude_code_session.jsonl`` is a synthetic transcript containing
every record shape the real format produces: summaries, meta records,
command envelopes, thinking blocks, tool calls and results, multi-block text,
and malformed lines.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES = TESTS_DIR / "fixtures"
SESSION_FIXTURE = FIXTURES / "claude_code_session.jsonl"
CLAUDE_FIXTURE = FIXTURES / "claude_conversations.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from convovault import cli  # noqa: E402
from convovault.cli import main  # noqa: E402
from convovault.parsers import claude_code  # noqa: E402


def _payload(index_html: Path):
    html = index_html.read_text(encoding="utf-8")
    return json.loads(html.split('id="convovault-data">')[1].split("</script>")[0])


def _session_root(tmp_path, name="projects"):
    """Build a fake ~/.claude/projects layout holding the fixture session."""
    root = tmp_path / name
    project = root / "-home-user-myrepo"
    project.mkdir(parents=True)
    target = project / "4305aaaa-bbbb-cccc-dddd-eeeeffff0000.jsonl"
    target.write_text(SESSION_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def test_session_parses_to_visible_turns_only():
    (conv,) = claude_code.parse(SESSION_FIXTURE)

    assert conv.provider == "claude-code"
    assert conv.id == "cc-sess-0001"  # from sessionId, not the filename
    assert [m.role for m in conv.messages] == [
        "user", "assistant", "assistant", "user", "assistant",
    ]

    blob = "\n".join(m.text for m in conv.messages)
    # Visible content survives...
    assert "timezone error" in blob
    assert "Pin the test to UTC" in blob
    # ...and every kind of internal record is gone.
    for hidden in (
        "internal reasoning",           # thinking block
        "tool_use",                     # tool call
        "TestTimestamps",               # tool result
        "<command-name>",               # slash-command envelope
        "injected context",             # isMeta record
        "Debugging a flaky test",       # summary record
    ):
        assert hidden not in blob, "leaked internal record: %r" % hidden


def test_session_title_comes_from_first_user_message():
    (conv,) = claude_code.parse(SESSION_FIXTURE)
    assert conv.title == "my pytest suite fails only on CI with a timezone error, can…"
    assert len(conv.title) <= claude_code.MAX_TITLE_LEN + 1  # +1 for the ellipsis


def test_session_timestamps_span_the_conversation():
    (conv,) = claude_code.parse(SESSION_FIXTURE)
    assert conv.created_at == conv.messages[0].timestamp
    assert conv.updated_at == conv.messages[-1].timestamp
    assert conv.created_at < conv.updated_at


def test_multi_block_text_is_joined():
    (conv,) = claude_code.parse(SESSION_FIXTURE)
    joined = conv.messages[2].text
    assert "Found it" in joined and "CI runs in UTC" in joined
    assert joined.index("Found it") < joined.index("CI runs in UTC")


def test_empty_or_junk_files_yield_no_conversation(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert claude_code.parse(empty) == []

    junk = tmp_path / "junk.jsonl"
    junk.write_text('nope\n[]\n42\n{"type": "summary"}\n\n', encoding="utf-8")
    assert claude_code.parse(junk) == []


def test_id_falls_back_to_filename_and_title_to_default(tmp_path):
    session = tmp_path / "abcd-1234.jsonl"
    session.write_text(
        json.dumps({"type": "assistant",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "only me"}]}})
        + "\n",
        encoding="utf-8",
    )
    (conv,) = claude_code.parse(session)
    assert conv.id == "abcd-1234"
    assert conv.title == claude_code.DEFAULT_TITLE  # no user message to name it
    assert conv.created_at is None and conv.updated_at is None  # no timestamps


def test_short_first_message_is_kept_whole(tmp_path):
    session = tmp_path / "s.jsonl"
    session.write_text(
        json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"role": "user", "content": "short   and\nsweet"}})
        + "\n",
        encoding="utf-8",
    )
    (conv,) = claude_code.parse(session)
    assert conv.title == "short and sweet"  # whitespace collapsed, no ellipsis


def test_title_truncation_without_word_boundary(tmp_path):
    session = tmp_path / "s.jsonl"
    session.write_text(
        json.dumps({"type": "user",
                    "message": {"role": "user", "content": "x" * 100}})
        + "\n",
        encoding="utf-8",
    )
    (conv,) = claude_code.parse(session)
    assert conv.title == "x" * claude_code.MAX_TITLE_LEN + "…"


def test_message_payload_edge_shapes(tmp_path):
    records = [
        {"type": "user", "message": "not a dict"},
        {"type": "user", "message": {"role": "user", "content": 42}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "   "}]}},
        {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": 99}]}},
        {"type": "user", "message": {"role": "user", "content": "  <system-reminder> noise"}},
        {"type": "user", "message": {"role": "user", "content": "the real message"}},
    ]
    session = tmp_path / "s.jsonl"
    session.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    (conv,) = claude_code.parse(session)
    assert [m.text for m in conv.messages] == ["the real message"]


# --------------------------------------------------------------------------
# CLI integration
# --------------------------------------------------------------------------


def test_cli_flag_with_explicit_root(tmp_path):
    root = _session_root(tmp_path)
    out = tmp_path / "vault"

    assert main(["--claude-code", str(root), "-o", str(out), "-q"]) == 0

    (md_file,) = sorted(out.glob("markdown/*.md"))
    blob = md_file.read_text(encoding="utf-8")
    assert "**Provider:** Claude Code" in blob
    assert "## Claude Code —" in blob  # assistant turns labelled by provider
    assert "Pin the test to UTC" in blob

    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-provider="claude-code"' in html  # filter pill exists
    (record,) = _payload(out / "index.html")
    assert record["provider"] == "claude-code"


def test_cli_flag_default_root_uses_home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    root = fake_home / ".claude" / "projects" / "-some-project"
    root.mkdir(parents=True)
    (root / "sess.jsonl").write_text(
        SESSION_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(fake_home))

    out = tmp_path / "vault"
    assert main(["--claude-code", "-o", str(out), "-q"]) == 0
    assert len(sorted(out.glob("markdown/*.md"))) == 1


def test_cli_flag_missing_root_warns(tmp_path, capsys):
    assert main(["--claude-code", str(tmp_path / "nowhere"), "-o", str(tmp_path / "v")]) == 1
    assert "projects directory not found" in capsys.readouterr().err


def test_cli_flag_empty_root_warns(tmp_path, capsys):
    empty = tmp_path / "projects"
    (empty / "some-project").mkdir(parents=True)
    assert main(["--claude-code", str(empty), "-o", str(tmp_path / "v")]) == 1
    assert "no Claude Code session transcripts" in capsys.readouterr().err


def test_cli_flag_bad_session_is_skipped(tmp_path, capsys, monkeypatch):
    root = _session_root(tmp_path)

    def explode(path):
        raise RuntimeError("synthetic parser bug")

    monkeypatch.setattr(claude_code, "parse", explode)
    assert main(["--claude-code", str(root), "-o", str(tmp_path / "v")]) == 1
    assert "claude-code parser failed" in capsys.readouterr().err


def test_cli_jsonl_passed_directly_as_input(tmp_path):
    out = tmp_path / "vault"
    assert main([str(SESSION_FIXTURE), "-o", str(out), "-q"]) == 0
    (record,) = _payload(out / "index.html")
    assert record["provider"] == "claude-code"
    assert record["id"] == "cc-sess-0001"


def test_cli_mixed_providers_in_one_vault(tmp_path):
    root = _session_root(tmp_path)
    out = tmp_path / "vault"

    assert main([str(CLAUDE_FIXTURE), "--claude-code", str(root),
                 "-o", str(out), "-q"]) == 0

    payload = _payload(out / "index.html")
    providers = sorted(c["provider"] for c in payload)
    assert providers == ["claude", "claude", "claude-code"]


def test_cli_session_via_flag_and_input_dedupes(tmp_path):
    """The same session through --claude-code and as an INPUT counts once."""
    root = _session_root(tmp_path)
    session_file = next(root.glob("*/*.jsonl"))
    out = tmp_path / "vault"

    assert main([str(session_file), "--claude-code", str(root),
                 "-o", str(out), "-q"]) == 0
    assert len(_payload(out / "index.html")) == 1


def test_cli_no_inputs_and_no_flag_is_usage_error():
    try:
        main([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected a usage error")
