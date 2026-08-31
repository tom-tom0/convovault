# ConvoVault

> **What is this?** ConvoVault is a small command-line tool for your own chat history.
> You download your data export from ChatGPT and/or Claude (a zip file each service
> gives you on request), point ConvoVault at it, and it turns everything into a tidy
> folder on your computer: one easy-to-read text file per conversation, plus a single
> `index.html` page you double-click to search all of your conversations at once.
> If you use the Claude Code terminal, it can also pull in your coding sessions
> directly from your disk — no export needed. It works completely offline and never
> sends your data anywhere.

**One private, offline archive for all your ChatGPT, Claude, and Claude Code conversations.**

ConvoVault takes the data exports you download from ChatGPT and Claude and turns them
into a single searchable vault on your own machine: clean Markdown files you can read,
grep, and back up, plus a self-contained `index.html` search page you open straight from
your file browser.

No account. No server. No dependencies. Nothing ever leaves your computer.

```
convovault chatgpt-export.zip claude-export.zip --claude-code -o my-vault
```

---

## Why?

Provider exports are technically complete and practically unusable:

- **ChatGPT** hands you a `conversations.json` where every chat is a *mapping tree* of
  nodes — with hidden system messages, tool calls, and dead branches from every time you
  hit "regenerate". Reading it means walking parent/child pointers.
- **Claude** hands you a different shape entirely: a flat `chat_messages` list where the
  text you want may live in a `text` field, or split across several content blocks, or
  both.
- Timestamps are unix floats in one and ISO-8601 strings in the other.

The tools that exist tend to be **single-provider** and dump a pile of loose files, so
your history stays split across two formats in two folders with no way to search across
them. ConvoVault normalizes both exports into one model, writes one consistent vault, and
gives you one search box over the whole thing.

Your conversations are some of the most personal text you've ever written. They deserve
better than a zip file in your Downloads folder.

## Features

- **Three sources, one archive** — ChatGPT exports, Claude exports, and local Claude
  Code sessions merged into a single chronological vault, each conversation tagged
  with where it came from.
- **Claude Code sessions with zero waiting** — your terminal sessions are already on
  your disk, so `--claude-code` archives them instantly: no export request, no email,
  no zip. Tool calls, thinking, and other machinery are filtered out, leaving the
  readable back-and-forth.
- **Readable Markdown** — one file per conversation, with a metadata header
  (title, provider, dates) and speaker-labelled turns. Plain text, forever readable.
- **Self-contained search page** — `index.html` with instant full-text search and
  provider filtering. One file, no build step, no CDN, no internet. Double-click it.
- **Handles the messy parts** — follows the ChatGPT mapping tree along the *current*
  branch so regenerated dead ends don't pollute your archive, drops hidden empty system
  messages, joins multi-block Claude messages, and preserves code blocks.
- **Defensive by default** — a malformed conversation is skipped with a warning, not a
  crash. One bad entry never costs you the other nine hundred.
- **Zero dependencies** — Python 3.10+ standard library only. Nothing to audit but the
  code itself.
- **Idempotent** — re-run it after your next export and the vault is simply rebuilt.

## Getting your exports

**ChatGPT**
1. Open ChatGPT → **Settings** → **Data controls** → **Export data**.
2. Confirm the request. A download link arrives by email, usually within minutes.
3. Save the `.zip` — you do not need to unpack it.

**Claude**
1. Open claude.ai → **Settings** → **Privacy** → **Export data**.
2. Confirm the request. A download link arrives by email.
3. Save the `.zip` — again, no need to unpack it.

Both archives contain a `conversations.json`; ConvoVault reads it out of the zip for you.

**Claude Code** needs no export at all — see the next section.

## Claude Code sessions

If you use [Claude Code](https://claude.com/claude-code) (Anthropic's terminal agent),
every session you have ever run is already stored on your machine, as one `.jsonl`
transcript per session under `~/.claude/projects/`. ConvoVault reads them in place:

```bash
# archive every local Claude Code session
convovault --claude-code -o my-vault

# combine with your exports for one vault across everything
convovault chatgpt-export.zip claude-export.zip --claude-code -o my-vault

# transcripts stored somewhere unusual? point at the directory
convovault --claude-code /path/to/projects -o my-vault

# or pass a single session transcript directly
convovault ~/.claude/projects/-home-me-myrepo/3f2a…d1.jsonl -o my-vault
```

What you get out of a session is the conversation you actually had: your messages and
Claude's replies. The machinery in between — tool calls and their output, file reads,
internal "thinking" blocks, injected system context — is filtered out. Sessions have
no stored name, so each one is titled after the first thing you typed in it.

Worth knowing:

- **Re-running is safe and incremental.** Sessions keep a stable id, so rebuilding
  the vault after more work simply refreshes them — updated sessions replace their
  older copies instead of duplicating.
- **The transcript format is Claude Code's internal storage**, not a documented
  export format, so a future Claude Code release could change it. ConvoVault fails
  soft: if the shape changes, affected records are skipped with the rest of the run
  intact — please open an issue with a scrubbed sample if that happens to you.
- Sessions read as more staccato than chats — several Claude turns can follow each
  other as it narrates its work between (filtered-out) tool calls. That is faithful
  to the session, not a bug.

## Install

Requires **Python 3.10 or newer**. No other dependencies.

```bash
# recommended: isolated install with pipx
pipx install .

# or a regular pip install
pip install .
```

To hack on it locally:

```bash
pip install -e .
```

## Usage

Point ConvoVault at one export or both, and tell it where to put the vault:

```bash
# both providers into one vault
convovault chatgpt-export.zip claude-export.zip -o my-vault

# just one provider
convovault chatgpt-export.zip -o my-vault

# already unpacked? pass the JSON directly
convovault ~/exports/chatgpt/conversations.json -o my-vault

# add your local Claude Code sessions to any of the above
convovault chatgpt-export.zip --claude-code -o my-vault

# defaults to ./vault if you omit -o
convovault chatgpt-export.zip
```

Then open the vault:

```bash
open my-vault/index.html      # macOS
xdg-open my-vault/index.html  # Linux
start my-vault\index.html     # Windows
```

## What you get

```
my-vault/
├── index.html                                  # open this — search everything
└── markdown/
    ├── 2026-08-10-trip-packing-checklist-claudeco.md
    ├── 2026-08-12-sourdough-starter-help-cgptconv.md
    └── ...
```

Each Markdown file looks like this:

```markdown
# Trip packing checklist

- **Provider:** Claude
- **Created:** 2026-08-10 09:15
- **Updated:** 2026-08-10 09:20
- **Messages:** 3

---

## You — 09:15

Help me make a packing list for a 3-day hiking trip.

---

## Claude — 09:15

Here's a solid 3-day list: tent, sleeping bag rated for the season, ...
```

`index.html` is a single standalone file with every conversation embedded in it, so the
search page works with no web server, no network, and no other files but itself.

## Privacy

**Everything stays on your machine.** ConvoVault makes no network requests of any kind —
it reads local files and writes local files, and that is the whole of it. The generated
`index.html` embeds your conversations directly and loads no external scripts, fonts, or
styles, so opening it sends nothing anywhere. It also ships a strict Content-Security-Policy
that forbids the page from making any network request at all, marks itself `noindex` so
search engines would ignore it even if it were ever accidentally uploaded somewhere, and
sends no referrer. There is no telemetry, no analytics, no "anonymous usage data", no
update check.

The vault is ordinary files on disk. If you want it encrypted or backed up, use the tools
you already trust for that. If you want it gone, delete the folder.

`--claude-code` only ever *reads* your session transcripts — nothing under
`~/.claude/` is modified, moved, or deleted.

## Contributing

Contributions are genuinely welcome — bug reports, fixture files for export formats that
break the parsers (**scrubbed of personal content, please**), documentation fixes, and
code alike.

A few house rules that keep this project what it is:

1. **Standard library only.** A zero-dependency archival tool is the point, not an
   accident.
2. **Fail soft.** A weird conversation gets skipped and reported; it never takes down
   the run.
3. **Tests for parser changes.** Add a small synthetic fixture under `tests/fixtures/`
   showing the shape you fixed.

Open an issue to discuss anything substantial before writing a lot of code.

## License

MIT — see [LICENSE](LICENSE).

Maintained by [@tom-tom0](https://github.com/tom-tom0).
