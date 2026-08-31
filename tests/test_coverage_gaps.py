"""Tests for every remaining untested line and branch.

Where the other suites test behaviour from the outside, this file drills into
the defensive branches: error injection for I/O failures, exotic objects fed
to the writers, and every fallback in the parsers' salvage logic. Together
with the other suites, this brings the project to full line coverage.
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES = TESTS_DIR / "fixtures"
CLAUDE_FIXTURE = FIXTURES / "claude_conversations.json"
CHATGPT_FIXTURE = FIXTURES / "chatgpt_conversations.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from convovault import cli  # noqa: E402
from convovault.cli import main  # noqa: E402
from convovault.models import Conversation, Message  # noqa: E402
from convovault.output import markdown as md  # noqa: E402
from convovault.output import site  # noqa: E402
from convovault.parsers import chatgpt, claude  # noqa: E402


def _conv(**overrides):
    base = dict(
        id="c-1",
        title="Title",
        provider="claude",
        created_at=1.0,
        updated_at=2.0,
        messages=[Message(role="user", text="hello", timestamp=1.0)],
    )
    base.update(overrides)
    return Conversation(**base)


class ExplodingAttrs:
    """Object whose attribute access always raises (not AttributeError)."""

    def __getattr__(self, name):
        raise RuntimeError("boom: " + name)


# --------------------------------------------------------------------------
# cli: input discovery
# --------------------------------------------------------------------------


def test_directory_with_export_at_top_level(tmp_path):
    (tmp_path / "conversations.json").write_text(
        CLAUDE_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    out = tmp_path / "vault"
    assert main([str(tmp_path), "-o", str(out), "-q"]) == 0
    assert (out / "index.html").is_file()


def test_directory_prefers_shallowest_export(tmp_path):
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    shallow = tmp_path / "a"
    (deep / "conversations.json").write_text("[]", encoding="utf-8")
    (shallow / "conversations.json").write_text(
        CLAUDE_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    found = cli._find_in_directory(tmp_path)
    assert found == shallow / "conversations.json"


def test_directory_without_export_warns(tmp_path, capsys):
    (tmp_path / "unrelated.txt").write_text("hi", encoding="utf-8")
    assert main([str(tmp_path), "-o", str(tmp_path / "v")]) == 1
    assert "no conversations.json found in this directory" in capsys.readouterr().err


def test_missing_input_path_warns(tmp_path, capsys):
    assert main([str(tmp_path / "nope.json"), "-o", str(tmp_path / "v")]) == 1
    assert "no such file or directory" in capsys.readouterr().err


def test_unreadable_zip_is_skipped(tmp_path, capsys, monkeypatch):
    bogus = tmp_path / "fake.zip"
    bogus.write_text("not a zip at all", encoding="utf-8")
    monkeypatch.setattr(cli.zipfile, "is_zipfile", lambda path: True)
    assert main([str(bogus), "-o", str(tmp_path / "v")]) == 1
    assert "could not read zip archive" in capsys.readouterr().err


def test_zip_member_extraction_failure_is_skipped(tmp_path, capsys, monkeypatch):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(CLAUDE_FIXTURE, "conversations.json")

    def broken_read(self, name, *args, **kwargs):
        raise zipfile.BadZipFile("CRC mismatch")

    monkeypatch.setattr(zipfile.ZipFile, "read", broken_read)
    assert main([str(archive), "-o", str(tmp_path / "v")]) == 1
    assert "could not extract" in capsys.readouterr().err


def test_zip_staging_write_failure_is_skipped(tmp_path, capsys, monkeypatch):
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(CLAUDE_FIXTURE, "conversations.json")

    def broken_write(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", broken_write)
    assert main([str(archive), "-o", str(tmp_path / "v")]) == 1
    assert "could not stage" in capsys.readouterr().err


def test_resolve_failure_falls_back_to_raw_path(tmp_path, monkeypatch):
    """Path.resolve raising OSError must not break de-duplication."""

    def broken_resolve(self, *args, **kwargs):
        raise OSError("resolve failed")

    monkeypatch.setattr(Path, "resolve", broken_resolve)
    out = tmp_path / "v"
    assert main([str(CLAUDE_FIXTURE), "-o", str(out), "-q"]) == 0
    assert (out / "index.html").is_file()


# --------------------------------------------------------------------------
# cli: provider detection
# --------------------------------------------------------------------------


def test_detect_provider_shapes():
    detect = cli._detect_provider
    assert detect([{"mapping": {}}]) == "chatgpt"
    assert detect([{"chat_messages": []}]) == "claude"
    assert detect({"conversations": [{"chat_messages": []}]}) == "claude"
    assert detect({"items": [{"mapping": {}}]}) == "chatgpt"
    assert detect({"chat_messages": []}) == "claude"  # bare single object
    assert detect({"mapping": {}}) == "chatgpt"
    assert detect(["junk", 42, {"mapping": {}}]) == "chatgpt"  # scans past junk
    assert detect([{"neither": 1}]) is None
    assert detect("a string") is None
    assert detect(42) is None
    assert detect({"unrelated": True}) is None
    assert detect([]) is None


def test_parser_crash_is_survived(tmp_path, capsys, monkeypatch):
    """A parser raising mid-run is reported, and other inputs still import."""

    def explode(path):
        raise RuntimeError("synthetic parser bug")

    monkeypatch.setattr(claude, "parse", explode)
    out = tmp_path / "v"
    assert main([str(CLAUDE_FIXTURE), str(CHATGPT_FIXTURE), "-o", str(out), "-q"]) == 0
    assert "claude parser failed" in capsys.readouterr().err
    assert len(list(out.glob("markdown/*.md"))) == 2  # the chatgpt ones


# --------------------------------------------------------------------------
# cli: merging and sorting
# --------------------------------------------------------------------------


def test_merge_keeps_first_seen_when_second_is_older():
    newer = _conv(id="same", updated_at=100.0, messages=[Message("user", "NEW")])
    older = _conv(id="same", updated_at=50.0, messages=[Message("user", "OLD")])
    merged = cli._merge([newer, older])
    assert len(merged) == 1
    assert merged[0].messages[0].text == "NEW"


def test_merge_handles_conversations_without_timestamps():
    a = _conv(id="a", created_at=None, updated_at=None)
    b = _conv(id="b", created_at="garbage", updated_at=None)
    c = _conv(id="c", created_at=5.0, updated_at=None)
    merged = cli._merge([a, b, c])
    assert [m.id for m in merged] == ["c", "a", "b"]  # dated first, then stable


# --------------------------------------------------------------------------
# cli: writer failures and argparse
# --------------------------------------------------------------------------


def test_markdown_writer_failure_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "write_markdown", lambda *a: (_ for _ in ()).throw(OSError("no space")))
    assert main([str(CLAUDE_FIXTURE), "-o", str(tmp_path / "v"), "-q"]) == 1
    assert "could not write Markdown" in capsys.readouterr().err


def test_site_writer_failure_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "write_site", lambda *a: (_ for _ in ()).throw(OSError("no space")))
    assert main([str(CLAUDE_FIXTURE), "-o", str(tmp_path / "v"), "-q"]) == 1
    assert "could not write search page" in capsys.readouterr().err


def test_no_arguments_is_a_usage_error():
    try:
        main([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("argparse should exit on missing INPUT")


def test_python_dash_m_entry_point(tmp_path):
    """`python -m convovault.cli` runs the same CLI."""
    result = subprocess.run(
        [sys.executable, "-m", "convovault.cli", str(CLAUDE_FIXTURE), "-o",
         str(tmp_path / "v"), "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "v" / "index.html").is_file()


# --------------------------------------------------------------------------
# chatgpt parser: salvage branches
# --------------------------------------------------------------------------


def _parse_chatgpt_entries(tmp_path, entries):
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return chatgpt.parse(path)


def test_chatgpt_wrapper_and_single_object_shapes(tmp_path):
    entry = json.loads(CHATGPT_FIXTURE.read_text(encoding="utf-8"))[1]
    # Wrapper dict.
    assert len(_parse_chatgpt_entries(tmp_path, {"conversations": [entry]})) == 1
    assert len(_parse_chatgpt_entries(tmp_path, {"chats": [entry]})) == 1
    # Bare single conversation object.
    assert len(_parse_chatgpt_entries(tmp_path, entry)) == 1
    # Unusable top-level shapes.
    assert _parse_chatgpt_entries(tmp_path, 42) == []
    assert _parse_chatgpt_entries(tmp_path, {"unrelated": 1}) == []


def test_chatgpt_unusable_entries_are_skipped(tmp_path):
    convs = _parse_chatgpt_entries(
        tmp_path,
        [
            "junk",
            {"mapping": "not a dict"},
            {"no_mapping": True},
            {"mapping": {"root": {"id": "root", "parent": None, "children": [], "message": None}},
             "current_node": "root"},  # no visible messages
            {"mapping": {}},  # empty mapping
        ],
    )
    assert convs == []


def test_chatgpt_id_and_title_fallbacks(tmp_path):
    node = {
        "id": "n1", "parent": None, "children": [],
        "message": {"author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["hi"]}},
    }
    base = {"mapping": {"n1": node}, "current_node": "n1"}

    convs = _parse_chatgpt_entries(
        tmp_path,
        [
            dict(base, id=42),  # integer id
            dict(base, title="From The Title"),  # title as id
            dict(base),  # no id, no title -> positional
        ],
    )
    assert [c.id for c in convs] == ["42", "From The Title", "chatgpt-2"]
    assert convs[2].title == "Untitled"


def test_chatgpt_timestamp_coercions(tmp_path):
    node = {
        "id": "n1", "parent": None, "children": [],
        "message": {"author": {"role": "user"}, "create_time": "1755000000.5",
                    "content": {"content_type": "text", "parts": ["hi"]}},
    }
    (conv,) = _parse_chatgpt_entries(
        tmp_path,
        [{"mapping": {"n1": node}, "current_node": "n1", "conversation_id": "t1",
          "create_time": "not a number", "update_time": True}],
    )
    assert conv.created_at is None  # unparseable string
    assert conv.updated_at is None  # bool rejected
    assert conv.messages[0].timestamp == 1755000000.5  # numeric string accepted


def test_chatgpt_content_shapes(tmp_path):
    def entry(cid, message_content):
        return {
            "conversation_id": cid,
            "current_node": "n1",
            "mapping": {
                "n1": {"id": "n1", "parent": None, "children": [],
                       "message": {"author": {"role": "user"}, "content": message_content}},
            },
        }

    convs = _parse_chatgpt_entries(
        tmp_path,
        [
            entry("plain-string", "content as a bare string"),
            entry("parts-string", {"content_type": "text", "parts": "parts as a string"}),
            entry("nested-dicts", {"content_type": "multimodal_text",
                                   "parts": [{"text": "nested payload"}, {"asset": "img"}]}),
            entry("text-field", {"content_type": "tether_quote", "text": "fallback text"}),
            entry("weird-parts", {"content_type": "text", "parts": 42}),  # -> empty, dropped
            entry("no-content", None),  # message not a dict? content None -> empty
        ],
    )
    by_id = {c.id: c for c in convs}
    assert by_id["plain-string"].messages[0].text == "content as a bare string"
    assert by_id["parts-string"].messages[0].text == "parts as a string"
    assert by_id["nested-dicts"].messages[0].text == "nested payload"
    assert by_id["text-field"].messages[0].text == "fallback text"
    assert "weird-parts" not in by_id
    assert "no-content" not in by_id


def test_chatgpt_role_normalisation(tmp_path):
    def entry(cid, author):
        return {
            "conversation_id": cid, "current_node": "n1",
            "mapping": {"n1": {"id": "n1", "parent": None, "children": [],
                               "message": {"author": author,
                                           "content": {"content_type": "text", "parts": ["x"]}}}},
        }

    convs = _parse_chatgpt_entries(
        tmp_path,
        [
            entry("r1", {"role": " Critic "}),  # unknown role kept, normalised
            entry("r2", {"role": ""}),  # empty -> assistant
            entry("r3", "not a dict"),  # -> assistant
            entry("r4", {"role": 42}),  # non-string -> assistant
        ],
    )
    roles = {c.id: c.messages[0].role for c in convs}
    assert roles == {"r1": "critic", "r2": "assistant", "r3": "assistant", "r4": "assistant"}


def test_chatgpt_code_fence_edge_cases(tmp_path):
    def entry(cid, content):
        return {
            "conversation_id": cid, "current_node": "n1",
            "mapping": {"n1": {"id": "n1", "parent": None, "children": [],
                               "message": {"author": {"role": "assistant"}, "content": content}}},
        }

    convs = _parse_chatgpt_entries(
        tmp_path,
        [
            entry("lang-bad", {"content_type": "code", "language": "py thon`",
                               "text": "print(1)\n"}),
            entry("has-fence", {"content_type": "code", "language": "md",
                                "text": "look: ```` four backticks"}),
            entry("empty-code", {"content_type": "code", "language": "python", "text": ""}),
        ],
    )
    by_id = {c.id: c for c in convs}
    # Whitespace/backtick language tags are dropped.
    assert by_id["lang-bad"].messages[0].text.startswith("```\n")
    # The fence widens past embedded backtick runs.
    assert by_id["has-fence"].messages[0].text.startswith("`````md")
    # Empty code bodies produce no message at all.
    assert "empty-code" not in by_id


def test_chatgpt_broken_tree_shapes(tmp_path):
    convs = _parse_chatgpt_entries(
        tmp_path,
        [
            {   # node in path is not a dict; dangling parent ends the walk
                "conversation_id": "broken-1", "current_node": "leaf",
                "mapping": {
                    "leaf": {"id": "leaf", "parent": "ghost", "children": [],
                             "message": {"author": {"role": "user"},
                                         "content": {"content_type": "text", "parts": ["end"]}}},
                    "junk": "not a node dict",
                },
            },
        ],
    )
    assert [m.text for m in convs[0].messages] == ["end"]


def test_chatgpt_fallback_leaf_helpers():
    """_fallback_leaf/_depth skip malformed nodes and survive parent cycles."""
    mapping = {
        "a": {"parent": None, "children": ["b"]},
        "b": {"parent": "a", "children": []},
        "junk": "not a dict",
        "cyc1": {"parent": "cyc2", "children": []},
        "cyc2": {"parent": "cyc1", "children": ["cyc1"]},
    }
    assert chatgpt._fallback_leaf(mapping) in {"b", "cyc1"}
    assert chatgpt._depth(mapping, "b") == 1
    assert chatgpt._depth(mapping, "cyc1") == 1  # cycle-safe
    assert chatgpt._fallback_leaf({}) is None
    assert chatgpt._active_path({}, None) == []
    # Non-string keys are ignored (unreachable via JSON, guarded anyway).
    assert chatgpt._fallback_leaf({1: {"children": []}}) is None


def test_chatgpt_hidden_and_non_dict_metadata(tmp_path):
    def entry(cid, metadata):
        return {
            "conversation_id": cid, "current_node": "n1",
            "mapping": {"n1": {"id": "n1", "parent": None, "children": [],
                               "message": {"author": {"role": "user"}, "metadata": metadata,
                                           "content": {"content_type": "text", "parts": ["x"]}}}},
        }

    convs = _parse_chatgpt_entries(
        tmp_path,
        [entry("meta-str", "not a dict"), entry("hidden", {"is_visually_hidden_from_conversation": True})],
    )
    by_id = {c.id: c for c in convs}
    assert "meta-str" in by_id  # non-dict metadata means not hidden
    assert "hidden" not in by_id


# --------------------------------------------------------------------------
# claude parser: salvage branches
# --------------------------------------------------------------------------


def _parse_claude(tmp_path, data):
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return claude.parse(path)


def test_claude_wrapper_and_bad_top_level(tmp_path):
    entry = json.loads(CLAUDE_FIXTURE.read_text(encoding="utf-8"))[0]
    assert len(_parse_claude(tmp_path, {"conversations": [entry]})) == 1
    assert len(_parse_claude(tmp_path, {"data": [entry]})) == 1
    assert _parse_claude(tmp_path, "just a string") == []
    assert _parse_claude(tmp_path, {"unrelated": 1}) == []


def test_claude_content_block_filtering(tmp_path):
    (conv,) = _parse_claude(
        tmp_path,
        [{
            "uuid": "blocks-1",
            "name": "Blocks",
            "chat_messages": [
                {"sender": "assistant", "text": "outer fallback", "content": [
                    "not a dict",
                    {"type": "tool_use", "name": "search"},
                    {"type": "text", "text": "   "},
                    {"type": "text", "text": 42},
                ]},
            ],
        }],
    )
    # All blocks unusable -> falls back to the top-level text field.
    assert [m.text for m in conv.messages] == ["outer fallback"]


def test_claude_timestamp_edge_cases(tmp_path):
    to_epoch = claude._to_epoch
    assert to_epoch(True) is None
    assert to_epoch([2026]) is None
    assert to_epoch("") is None
    assert to_epoch("   ") is None
    assert to_epoch("definitely not a date") is None
    assert to_epoch(1755000000) == 1755000000.0
    # Naive datetimes are treated as UTC.
    assert to_epoch("2026-01-01T00:00:00") == to_epoch("2026-01-01T00:00:00Z")
    # Explicit offsets are honoured.
    assert to_epoch("2026-01-01T02:00:00+02:00") == to_epoch("2026-01-01T00:00:00Z")
    # Lowercase z is accepted.
    assert to_epoch("2026-01-01T00:00:00z") == to_epoch("2026-01-01T00:00:00Z")


# --------------------------------------------------------------------------
# markdown writer: formatting helpers and failure paths
# --------------------------------------------------------------------------


def test_markdown_text_coercion_and_slug_truncation():
    assert md._as_text(None) == ""
    assert md._as_text(42) == "42"
    long_title = "alpha-beta-" * 12  # > 60 chars, hyphenated
    slug = md._slugify(long_title)
    assert len(slug) <= md.MAX_SLUG_LEN
    assert not slug.endswith("-")
    assert slug.startswith("alpha-beta")


def test_markdown_role_labels_and_unknown_provider(tmp_path):
    conv = _conv(
        provider="",
        messages=[
            Message(role="user", text="u"),
            Message(role="assistant", text="a"),
            Message(role="system", text="s"),
            Message(role="tool", text="t"),
            Message(role="critic", text="c"),
            Message(role="", text="e"),
        ],
    )
    (path,) = md.write_markdown([conv], tmp_path)
    blob = path.read_text(encoding="utf-8")
    for label in ("## You", "## Assistant", "## System", "## Tool", "## Critic", "## Unknown"):
        assert label in blob, "missing %s" % label
    # Known provider display names.
    assert md._provider_name("openai") == "OpenAI"
    assert md._provider_name("someai") == "Someai"


def test_markdown_identical_stems_get_suffixes(tmp_path):
    twins = [
        _conv(id="same-id", title="Same", messages=[Message("user", "one")]),
        _conv(id="same-id", title="Same", messages=[Message("user", "two")]),
    ]
    files = md.write_markdown(twins, tmp_path)
    names = sorted(f.name for f in files)
    assert len(names) == 2 and names[0] != names[1]
    assert any(name.endswith("-2.md") for name in names)


def test_markdown_skips_non_conversations_and_render_failures(tmp_path):
    good = _conv(id="ok")
    shapeless = object()  # neither .messages nor .id
    broken = _conv(id="broken", messages=7)  # list(7) raises inside _render
    exploding = ExplodingAttrs()

    files = md.write_markdown([shapeless, broken, good, exploding], tmp_path)
    assert [f for f in files] == [tmp_path / files[0].name]
    assert "hello" in files[0].read_text(encoding="utf-8")


def test_markdown_write_failure_is_skipped(tmp_path, monkeypatch):
    def broken_write(self, *args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", broken_write)
    assert md.write_markdown([_conv()], tmp_path) == []


# --------------------------------------------------------------------------
# site writer: coercions and exotic objects
# --------------------------------------------------------------------------


def test_site_coercions():
    assert site._coerce_text(3.5) == "3.5"
    assert site._coerce_text(True) == ""
    assert site._coerce_text(None) == ""
    assert site._coerce_timestamp("12.5") == 12.5
    assert site._coerce_timestamp("nope") is None
    assert site._coerce_timestamp(float("nan")) is None
    assert site._coerce_timestamp(float("inf")) is None


def test_site_skips_exotic_objects(tmp_path):
    class BadMessages:
        id, title, provider = "x", "t", "p"
        messages = "a string, not a list"

    good = _conv(id="good")
    site.write_site([object(), ExplodingAttrs(), BadMessages(), good], tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    payload = json.loads(
        html.split('id="convovault-data">')[1].split("</script>")[0]
    )
    ids = [c["id"] for c in payload]
    assert "good" in ids
    assert len(payload) == 2  # BadMessages survives with zero messages
    assert next(c for c in payload if c["id"] == "x")["messages"] == []


def test_site_message_level_skips_and_defaults(tmp_path):
    conv = _conv(
        id="mixed",
        created_at=None,
        updated_at=None,
        messages=[
            Message(role="user", text="   ", timestamp=5.0),  # blank -> dropped
            Message(role="", text="kept", timestamp=7.0),  # empty role -> assistant
            object(),  # not message-shaped -> dropped
            ExplodingAttrs(),  # raises -> whole conversation still survives? no:
        ],
    )
    # ExplodingAttrs raises inside the conversation's try, dropping the whole
    # conversation -- so test it separately from the mixed-message case.
    conv.messages.pop()

    site.write_site([conv], tmp_path)
    payload = json.loads(
        (tmp_path / "index.html")
        .read_text(encoding="utf-8")
        .split('id="convovault-data">')[1]
        .split("</script>")[0]
    )
    (record,) = payload
    assert [m["text"] for m in record["messages"]] == ["kept"]
    assert record["messages"][0]["role"] == "assistant"
    # Timestamps fall back to the surviving message stamps.
    assert record["created_at"] == 7.0
    assert record["updated_at"] == 7.0


class _LateExplodingMessage:
    """Passes the duck-type check, then raises on the timestamp access."""

    role = "user"
    text = "looks fine"

    @property
    def timestamp(self):
        raise RuntimeError("boom")


class _LateExplodingConversation:
    """Passes the duck-type check, then raises on created_at access."""

    id, title, provider = "late", "Late", "claude"
    messages = ()

    @property
    def created_at(self):
        raise RuntimeError("boom")


def test_site_inner_exception_guards(tmp_path):
    """Objects that explode only after the shape check are still skipped."""
    conv = _conv(id="carrier", messages=[_LateExplodingMessage(),
                                         Message(role="user", text="real", timestamp=1.0)])
    site.write_site([_LateExplodingConversation(), conv], tmp_path)
    payload = json.loads(
        (tmp_path / "index.html")
        .read_text(encoding="utf-8")
        .split('id="convovault-data">')[1]
        .split("</script>")[0]
    )
    (record,) = payload  # the late-exploding conversation is gone
    assert record["id"] == "carrier"
    # The exploding message is dropped; the real one survives.
    assert [m["text"] for m in record["messages"]] == ["real"]


def test_chatgpt_remaining_salvage_branches(tmp_path):
    # _as_timestamp: value that is neither str, number, bool, nor None.
    assert chatgpt._as_timestamp([1755]) is None
    # _join_parts: empty string part, non-str/non-dict part, dict without text.
    assert chatgpt._join_parts(["", 42, {"no_text": 1}, "kept"]) == "kept"
    # _active_thread_messages: current_node resolves to a non-dict member.
    convs = _parse_chatgpt_entries(
        tmp_path,
        [{"conversation_id": "junk-node", "current_node": "x",
          "mapping": {"x": "not a node dict"}}],
    )
    assert convs == []  # no messages -> conversation dropped, no crash


def test_site_conversation_without_any_timestamps(tmp_path):
    conv = _conv(id="undated", created_at=None, updated_at=None,
                 messages=[Message(role="user", text="hi", timestamp=None)])
    site.write_site([conv], tmp_path)
    payload = json.loads(
        (tmp_path / "index.html")
        .read_text(encoding="utf-8")
        .split('id="convovault-data">')[1]
        .split("</script>")[0]
    )
    assert payload[0]["created_at"] is None
    assert payload[0]["updated_at"] is None
