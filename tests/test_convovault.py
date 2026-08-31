"""End-to-end tests for ConvoVault: parsers, writers, and the CLI.

Run with ``pytest tests/test_convovault.py`` from the project root.
Standard library + pytest's ``tmp_path`` fixture only.

The parser/writer entry points are looked up through small name resolvers
(``_resolve``/``_parse``) so the suite stays pinned to *behaviour* rather than
to one particular spelling of a function name.  Each resolver raises a loud
AssertionError listing the names it tried, so a mismatch shows up as an
obvious failure message instead of an ImportError.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES = TESTS_DIR / "fixtures"
CHATGPT_FIXTURE = FIXTURES / "chatgpt_conversations.json"
CLAUDE_FIXTURE = FIXTURES / "claude_conversations.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from convovault.cli import main  # noqa: E402
from convovault.models import Conversation, Message  # noqa: E402
from convovault.output.markdown import write_markdown  # noqa: E402
from convovault.output.site import write_site  # noqa: E402
from convovault.parsers import chatgpt, claude  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_PARSE_NAMES = (
    "parse_export",
    "parse_file",
    "parse_conversations",
    "parse",
    "load_export",
    "load_file",
    "load",
)


def _resolve(module, names):
    """Return the first callable attribute of *module* named in *names*."""
    for name in names:
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate
    raise AssertionError(
        "%s exposes none of the expected entry points %r (has: %r)"
        % (module.__name__, list(names), sorted(n for n in dir(module) if not n.startswith("_")))
    )


def _parse(module, path):
    """Parse *path* with *module*, accepting a path- or JSON-taking signature."""
    fn = _resolve(module, _PARSE_NAMES)
    try:
        result = fn(path)
    except (TypeError, AttributeError):
        result = fn(json.loads(Path(path).read_text(encoding="utf-8")))
    conversations = list(result)
    for conv in conversations:
        assert isinstance(conv, Conversation), "parser returned %r, not Conversation" % (conv,)
    return conversations


def _by_id(conversations):
    return {c.id: c for c in conversations}


def _texts(conv):
    return [m.text for m in conv.messages]


def _roles(conv):
    return [m.role for m in conv.messages]


def _write_site(conversations, out_dir):
    """Call write_site with a directory, falling back to a file target."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        write_site(conversations, out_dir)
    except (TypeError, IsADirectoryError):
        write_site(conversations, out_dir / "index.html")
    index = out_dir / "index.html"
    assert index.is_file(), "write_site did not produce index.html in %s" % out_dir.name
    return index


def _run_cli(argv):
    """Run cli.main, normalising SystemExit / None into an int exit code."""
    try:
        code = main(argv)
    except SystemExit as exc:  # argparse-style exit
        code = exc.code
    return 0 if code is None else int(code)


def _md_files(root):
    return sorted(p for p in Path(root).rglob("*.md") if p.is_file())


# --------------------------------------------------------------------------
# ChatGPT parser
# --------------------------------------------------------------------------


def test_chatgpt_parser_shape_and_metadata():
    """Both conversations parse with the right ids, provider and timestamps."""
    convs = _parse(chatgpt, CHATGPT_FIXTURE)
    assert len(convs) == 2

    by_id = _by_id(convs)
    assert set(by_id) == {"cgpt-conv-0001", "cgpt-conv-0002"}

    first = by_id["cgpt-conv-0001"]
    assert first.title == "Sourdough starter help"
    assert first.provider == "chatgpt"
    assert first.created_at == 1755000000.0
    assert first.updated_at == 1755000300.5

    second = by_id["cgpt-conv-0002"]
    assert second.title == "Python list comprehension"
    assert second.provider == "chatgpt"
    assert second.created_at == 1756100000.0
    assert second.updated_at == 1756100050.0


def test_chatgpt_walks_current_branch_only():
    """The active leaf path is kept; the regenerated sibling is dropped."""
    first = _by_id(_parse(chatgpt, CHATGPT_FIXTURE))["cgpt-conv-0001"]

    assert len(first.messages) == 4
    assert _roles(first) == ["user", "assistant", "user", "assistant"]

    texts = _texts(first)
    assert texts[0].startswith("My sourdough starter smells like acetone")
    assert "acetone smell means it's hungry" in texts[1]
    assert texts[2] == "Great, what ratio is 1:1:1 exactly?"
    assert "Equal parts by weight" in texts[3]

    # The abandoned regenerated branch must not survive anywhere.
    assert "should NOT appear" not in "\n".join(texts)

    # Message timestamps follow the branch, in order.
    stamps = [m.timestamp for m in first.messages]
    assert stamps == [1755000000.0, 1755000020.0, 1755000200.0, 1755000300.5]


def test_chatgpt_drops_hidden_empty_system_message():
    """The visually-hidden, empty system node is not archived."""
    first = _by_id(_parse(chatgpt, CHATGPT_FIXTURE))["cgpt-conv-0001"]

    assert "system" not in _roles(first)
    assert all(m.text.strip() for m in first.messages), "empty message body was kept"


def test_chatgpt_code_content_becomes_fenced_block():
    """A content_type=="code" part is rendered as a fenced python block."""
    second = _by_id(_parse(chatgpt, CHATGPT_FIXTURE))["cgpt-conv-0002"]

    assert len(second.messages) == 2
    assert _roles(second) == ["user", "assistant"]

    code_text = second.messages[1].text
    assert "```python" in code_text
    assert "squares = [n * n for n in nums if n % 2 == 0]" in code_text
    assert code_text.rstrip().endswith("```")
    # Opening and closing fence, nothing more.
    assert code_text.count("```") == 2


# --------------------------------------------------------------------------
# Claude parser
# --------------------------------------------------------------------------


def test_claude_parser_shape_and_titles():
    """Both conversations parse; a blank name falls back to 'Untitled'."""
    convs = _parse(claude, CLAUDE_FIXTURE)
    assert len(convs) == 2

    by_id = _by_id(convs)
    assert set(by_id) == {"claude-conv-0001", "claude-conv-0002"}
    assert all(c.provider == "claude" for c in convs)

    assert by_id["claude-conv-0001"].title == "Trip packing checklist"
    assert by_id["claude-conv-0002"].title == "Untitled"


def test_claude_iso_timestamps_become_epoch_floats():
    """ISO-8601 'Z' timestamps convert to UTC epoch seconds, sub-second kept."""
    by_id = _by_id(_parse(claude, CLAUDE_FIXTURE))
    first = by_id["claude-conv-0001"]

    # 2026-08-10T09:15:00.123456Z
    assert isinstance(first.created_at, float)
    assert first.created_at == 1786353300.123456
    assert first.updated_at == 1786353630.0

    second = by_id["claude-conv-0002"]
    assert second.created_at == 1787248800.0
    assert second.updated_at == 1787248860.0

    assert first.messages[0].timestamp == 1786353300.123456


def test_claude_multi_block_message_is_joined():
    """Two text blocks in one assistant turn join into a single message."""
    first = _by_id(_parse(claude, CLAUDE_FIXTURE))["claude-conv-0001"]

    assert len(first.messages) == 3
    assert _roles(first) == ["user", "assistant", "user"]

    joined = first.messages[1].text
    head = "Here's a solid 3-day list"
    tail = "Want me to tailor it for the weather forecast?"
    assert head in joined
    assert tail in joined
    assert joined.index(head) < joined.index(tail), "content blocks joined out of order"
    assert "layers rather than bulk" in joined


# --------------------------------------------------------------------------
# Markdown writer
# --------------------------------------------------------------------------


def test_write_markdown_one_file_per_conversation(tmp_path):
    """Every conversation gets its own slugged .md file with title + bodies."""
    convs = _parse(chatgpt, CHATGPT_FIXTURE) + _parse(claude, CLAUDE_FIXTURE)
    out = tmp_path / "md"

    write_markdown(convs, out)

    files = _md_files(out)
    assert len(files) == len(convs) == 4
    assert len({f.name for f in files}) == 4, "filenames collided"

    slug_re = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")
    for path in files:
        assert slug_re.match(path.name), "not a safe slug: %r" % path.name

    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert "# Sourdough starter help" in blob
    assert "# Trip packing checklist" in blob
    assert "# Untitled" in blob
    for conv in convs:
        for message in conv.messages:
            snippet = message.text.strip().splitlines()[0][:40]
            assert snippet in blob, "message body missing from markdown: %r" % snippet


def test_write_markdown_dedupes_identical_titles(tmp_path):
    """Two conversations sharing a title still get distinct filenames."""
    convs = [
        Conversation(
            id="dup-1",
            title="Same Title!",
            provider="chatgpt",
            created_at=1.0,
            updated_at=2.0,
            messages=[Message(role="user", text="first body alpha", timestamp=1.0)],
        ),
        Conversation(
            id="dup-2",
            title="Same Title!",
            provider="claude",
            created_at=3.0,
            updated_at=4.0,
            messages=[Message(role="user", text="second body beta", timestamp=3.0)],
        ),
    ]
    out = tmp_path / "dup"

    write_markdown(convs, out)

    files = _md_files(out)
    assert len(files) == 2
    assert files[0].name != files[1].name

    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert "first body alpha" in blob
    assert "second body beta" in blob


# --------------------------------------------------------------------------
# HTML site writer
# --------------------------------------------------------------------------


def test_write_site_embeds_every_message(tmp_path):
    """index.html carries the searchable text of every conversation."""
    convs = _parse(chatgpt, CHATGPT_FIXTURE) + _parse(claude, CLAUDE_FIXTURE)
    index = _write_site(convs, tmp_path / "site")
    html = index.read_text(encoding="utf-8")

    assert "<html" in html.lower()
    for conv in convs:
        assert conv.title in html or conv.id in html
    assert "Sourdough starter help" in html
    assert "sporange" in html
    assert "squares = [n * n for n in nums if n % 2 == 0]" in html


def test_write_site_escapes_script_terminator(tmp_path):
    """A '</script>' inside message text must not close the payload tag."""
    hostile = Conversation(
        id="hostile-1",
        title="Payload MARKERTITLE",
        provider="claude",
        created_at=10.0,
        updated_at=20.0,
        messages=[
            Message(
                role="user",
                text="MARKERHEAD</script>MARKERTAIL <b>bold</b> & — done",
                timestamp=10.0,
            )
        ],
    )
    index = _write_site([hostile], tmp_path / "hostile")
    html = index.read_text(encoding="utf-8")

    # The text made it into the page...
    assert "MARKERHEAD" in html
    assert "MARKERTAIL" in html
    # ...but never as a literal tag terminator that would break out of <script>.
    assert "MARKERHEAD</script>MARKERTAIL" not in html
    # Every script element is balanced: no stray injected closer.
    assert html.count("<script") == html.count("</script>")


# --------------------------------------------------------------------------
# CLI end-to-end
# --------------------------------------------------------------------------


def test_cli_end_to_end(tmp_path):
    """main() ingests both exports and writes markdown plus the search page."""
    out = tmp_path / "vault"

    code = _run_cli([str(CHATGPT_FIXTURE), str(CLAUDE_FIXTURE), "-o", str(out)])
    assert code == 0

    files = _md_files(out)
    assert len(files) == 4, "expected 4 markdown files, got %r" % [f.name for f in files]
    assert len({f.name for f in files}) == 4

    indexes = [p for p in out.rglob("index.html") if p.is_file()]
    assert len(indexes) == 1
    html = indexes[0].read_text(encoding="utf-8")

    for title in (
        "Sourdough starter help",
        "Python list comprehension",
        "Trip packing checklist",
        "Untitled",
    ):
        assert title in html, "conversation lost by dedup/sort: %r" % title

    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert "should NOT appear" not in blob
    assert "should NOT appear" not in html


def test_cli_is_idempotent_and_does_not_duplicate(tmp_path):
    """Re-running into the same directory keeps exactly 4 conversations."""
    out = tmp_path / "vault"

    assert _run_cli([str(CHATGPT_FIXTURE), str(CLAUDE_FIXTURE), "-o", str(out)]) == 0
    assert _run_cli([str(CHATGPT_FIXTURE), str(CLAUDE_FIXTURE), "-o", str(out)]) == 0

    assert len(_md_files(out)) == 4


def test_claude_single_conversation_object_parses(tmp_path):
    """A bare single-conversation object (no list wrapper) still parses."""
    raw = json.loads(CLAUDE_FIXTURE.read_text(encoding="utf-8"))[0]
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    convs = _parse(claude, path)
    assert len(convs) == 1
    assert convs[0].id == "claude-conv-0001"
    assert len(convs[0].messages) == 3


def test_write_site_neutralises_comment_opener(tmp_path):
    """'<!--' plus '<script' in a message cannot derail the HTML parser."""
    hostile = Conversation(
        id="hostile-2",
        title="Comment trick",
        provider="chatgpt",
        created_at=10.0,
        updated_at=20.0,
        messages=[
            Message(role="user", text="COMMENTHEAD <!--<script>COMMENTTAIL", timestamp=10.0)
        ],
    )
    index = _write_site([hostile], tmp_path / "hostile2")
    html = index.read_text(encoding="utf-8")

    assert "COMMENTHEAD" in html
    assert "COMMENTTAIL" in html
    # No literal '<' from message text survives into the embedded payload.
    assert "<!--<script>" not in html


def test_write_site_hardening_headers(tmp_path):
    """The page carries CSP, no-referrer, and noindex meta tags."""
    convs = _parse(claude, CLAUDE_FIXTURE)
    html = _write_site(convs, tmp_path / "hard").read_text(encoding="utf-8")

    assert "Content-Security-Policy" in html
    assert "default-src 'none'" in html
    assert 'name="referrer" content="no-referrer"' in html
    assert "noindex" in html


def test_cli_reads_zip_export(tmp_path):
    """A zip with conversations.json nested inside imports like the raw file."""
    import zipfile

    archive = tmp_path / "claude-export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.write(CLAUDE_FIXTURE, "export-2026/conversations.json")
    out = tmp_path / "vault"

    assert _run_cli([str(archive), "-o", str(out)]) == 0
    files = _md_files(out)
    assert len(files) == 2
    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert "Trip packing checklist" in blob


def test_cli_reads_directory_input(tmp_path):
    """A directory containing conversations.json somewhere inside works."""
    nested = tmp_path / "export" / "data"
    nested.mkdir(parents=True)
    (nested / "conversations.json").write_text(
        CHATGPT_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    out = tmp_path / "vault"

    assert _run_cli([str(tmp_path / "export"), "-o", str(out)]) == 0
    assert len(_md_files(out)) == 2


def test_cli_survives_malformed_input(tmp_path):
    """A junk export alongside a good one does not abort the whole run."""
    junk = tmp_path / "broken.json"
    junk.write_text("{ this is not json", encoding="utf-8")
    out = tmp_path / "vault"

    code = _run_cli([str(junk), str(CLAUDE_FIXTURE), "-o", str(out)])
    assert code == 0

    files = _md_files(out)
    assert len(files) == 2
    blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
    assert "Trip packing checklist" in blob
