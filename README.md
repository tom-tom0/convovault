# ConvoVault

**One private, offline archive for all your ChatGPT and Claude conversations.**

ConvoVault takes the data exports you download from ChatGPT and Claude and turns them
into a single searchable vault on your own machine: clean Markdown files you can read,
grep, and back up, plus a self-contained `index.html` search page you open straight from
your file browser.

No account. No server. No dependencies. Nothing ever leaves your computer.

```
convovault chatgpt-export.zip claude-export.zip -o my-vault
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

- **Two providers, one archive** — ChatGPT and Claude exports merged into a single
  chronological vault, each conversation tagged with where it came from.
- **Readable Markdown** — one file per conversation, with a YAML-ish front matter header
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
styles, so opening it sends nothing anywhere. There is no telemetry, no analytics, no
"anonymous usage data", no update check.

The vault is ordinary files on disk. If you want it encrypted or backed up, use the tools
you already trust for that. If you want it gone, delete the folder.

## Roadmap

- Google Gemini export support
- Tagging and starring, stored alongside the vault
- Stats: messages over time, busiest days, provider split, longest threads
- Incremental updates that merge a new export into an existing vault
- Attachment and image extraction

Ideas and opinions on any of these are welcome.

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
