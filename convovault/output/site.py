"""Write a single self-contained HTML search page for an archive.

The page embeds every conversation as a JSON payload inside a
``<script type="application/json">`` tag and renders the whole UI client-side
with vanilla JavaScript.  There are no external resources of any kind, so the
result works when opened straight from ``file://``.

Public API::

    write_site(conversations, out_dir) -> pathlib.Path
"""
from __future__ import annotations

import json
import pathlib

__all__ = ["write_site"]


# --------------------------------------------------------------------------
# Payload construction
# --------------------------------------------------------------------------

_MISSING = object()


def _coerce_text(value) -> str:
    """Return ``value`` as a string, tolerating None and unexpected types."""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return ""


def _looks_like(obj, attributes) -> bool:
    """True when ``obj`` exposes every named attribute (duck-typing guard)."""
    return all(getattr(obj, name, _MISSING) is not _MISSING for name in attributes)


def _coerce_timestamp(value):
    """Return ``value`` as a float epoch, or ``None`` if it is not usable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _message_to_dict(message):
    """Convert one ``Message`` to a JSON-safe dict, or ``None`` to skip it."""
    if not _looks_like(message, ("role", "text")):
        return None
    try:
        role = _coerce_text(getattr(message, "role", "")).strip().lower()
        text = _coerce_text(getattr(message, "text", ""))
        timestamp = _coerce_timestamp(getattr(message, "timestamp", None))
    except Exception:
        return None
    if not text.strip():
        return None
    return {
        "role": role or "assistant",
        "text": text,
        "timestamp": timestamp,
    }


def _conversation_to_dict(conversation, index: int):
    """Convert one ``Conversation`` to a JSON-safe dict, or ``None`` to skip."""
    if not _looks_like(conversation, ("id", "title", "provider", "messages")):
        return None
    try:
        raw_messages = getattr(conversation, "messages", None) or []
        if isinstance(raw_messages, (str, bytes)):
            raw_messages = []
        messages = []
        for raw in raw_messages:
            converted = _message_to_dict(raw)
            if converted is not None:
                messages.append(converted)

        title = _coerce_text(getattr(conversation, "title", "")).strip()
        provider = _coerce_text(getattr(conversation, "provider", "")).strip().lower()
        conv_id = _coerce_text(getattr(conversation, "id", "")).strip()
        created_at = _coerce_timestamp(getattr(conversation, "created_at", None))
        updated_at = _coerce_timestamp(getattr(conversation, "updated_at", None))
    except Exception:
        # A single broken entry must never abort the whole run.
        return None

    # Fall back to message timestamps when the export gave us nothing useful.
    stamps = [m["timestamp"] for m in messages if m["timestamp"] is not None]
    if created_at is None and stamps:
        created_at = min(stamps)
    if updated_at is None:
        updated_at = max(stamps) if stamps else created_at

    return {
        "id": conv_id or "conversation-%d" % index,
        "title": title or "Untitled conversation",
        "provider": provider or "unknown",
        "created_at": created_at,
        "updated_at": updated_at,
        "messages": messages,
    }


def _build_payload(conversations) -> str:
    """Serialise conversations to a string safe to inline in an HTML script."""
    records = []
    for index, conversation in enumerate(conversations or []):
        record = _conversation_to_dict(conversation, index)
        if record is not None:
            records.append(record)

    # ensure_ascii keeps the payload pure ASCII (no U+2028/U+2029 hazards);
    # escaping "</" as "<\/" (a valid JSON escape) means no substring of the
    # payload can ever terminate the surrounding <script> element.
    text = json.dumps(records, ensure_ascii=True, separators=(",", ":"))
    return text.replace("</", "<\\/")


# --------------------------------------------------------------------------
# Static assets
# --------------------------------------------------------------------------

_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --panel-alt: #f0f2f5;
  --border: #dfe3e8;
  --text: #1c1f24;
  --muted: #646b76;
  --accent: #3b62d9;
  --accent-text: #ffffff;
  --bubble: #eef0f4;
  --mark: #ffe07a;
  --mark-text: #2a2100;
  --badge-chatgpt: #10a37f;
  --badge-claude: #c96442;
  --badge-other: #6b7280;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171c;
    --panel: #1b1f26;
    --panel-alt: #222731;
    --border: #333a45;
    --text: #e8eaee;
    --muted: #9aa2ae;
    --accent: #6d8cf0;
    --accent-text: #10131a;
    --bubble: #262c36;
    --mark: #7a5f10;
    --mark-text: #ffeeb8;
    --badge-chatgpt: #16b58c;
    --badge-claude: #e0794f;
    --badge-other: #808a99;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, "Noto Sans", sans-serif;
  display: flex;
  flex-direction: column;
}
header.top {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 12px 18px;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
}
h1.brand { font-size: 17px; margin: 0; letter-spacing: -0.01em; }
h1.brand span { color: var(--accent); }
#search {
  flex: 1 1 240px;
  min-width: 180px;
  padding: 8px 12px;
  font: inherit;
  color: var(--text);
  background: var(--panel-alt);
  border: 1px solid var(--border);
  border-radius: 8px;
}
#search:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
.filters { display: flex; gap: 6px; }
.filters button {
  font: inherit;
  padding: 7px 13px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--panel-alt);
  color: var(--muted);
  cursor: pointer;
}
.filters button:hover { color: var(--text); }
.filters button[aria-pressed="true"] {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
}
main { flex: 1; display: flex; min-height: 0; }
#sidebar {
  width: 330px;
  flex: 0 0 330px;
  overflow-y: auto;
  background: var(--panel);
  border-right: 1px solid var(--border);
}
#count {
  padding: 9px 16px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
}
.item {
  display: block;
  width: 100%;
  text-align: left;
  font: inherit;
  color: inherit;
  background: none;
  border: none;
  border-bottom: 1px solid var(--border);
  padding: 11px 16px;
  cursor: pointer;
}
.item:hover { background: var(--panel-alt); }
.item[aria-current="true"] {
  background: var(--panel-alt);
  box-shadow: inset 3px 0 0 var(--accent);
}
.item .title {
  font-weight: 600;
  margin-bottom: 4px;
  overflow-wrap: anywhere;
}
.item .meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}
.item .snippet {
  margin-top: 5px;
  font-size: 12.5px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.badge {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  background: var(--badge-other);
}
.badge.chatgpt { background: var(--badge-chatgpt); }
.badge.claude { background: var(--badge-claude); }
#reader { flex: 1; overflow-y: auto; padding: 20px 24px 48px; min-width: 0; }
#reader-head {
  max-width: 820px;
  margin: 0 auto 20px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
#reader-head h2 { margin: 0 0 6px; font-size: 20px; overflow-wrap: anywhere; }
#reader-head .meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--muted);
}
#thread { max-width: 820px; margin: 0 auto; display: flex; flex-direction: column; gap: 12px; }
.msg { display: flex; flex-direction: column; max-width: 78%; }
.msg .who {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
  margin-bottom: 3px;
}
.msg .bubble {
  padding: 10px 13px;
  border-radius: 13px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--bubble);
  border: 1px solid var(--border);
}
.msg.user { align-self: flex-end; align-items: flex-end; }
.msg.user .bubble {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-text);
}
.msg.assistant { align-self: flex-start; }
.msg.system, .msg.tool { align-self: center; max-width: 92%; }
.msg.system .bubble, .msg.tool .bubble {
  background: transparent;
  border-style: dashed;
  color: var(--muted);
  font-size: 13px;
}
mark { background: var(--mark); color: var(--mark-text); border-radius: 3px; padding: 0 1px; }
.empty { color: var(--muted); padding: 26px 16px; text-align: center; }
@media (max-width: 780px) {
  main { flex-direction: column; }
  #sidebar { width: auto; flex: 0 0 auto; max-height: 42vh; border-right: none;
             border-bottom: 1px solid var(--border); }
  .msg { max-width: 92%; }
}
"""


_JS = """
(function () {
  "use strict";

  var DEBOUNCE_MS = 150;
  var PROVIDER_NAMES = { chatgpt: "ChatGPT", claude: "Claude" };

  var data = [];
  try {
    var node = document.getElementById("convovault-data");
    data = JSON.parse(node.textContent) || [];
    if (!Array.isArray(data)) { data = []; }
  } catch (err) {
    data = [];
  }

  // Pre-compute lowercase haystacks once so typing stays cheap.
  data.forEach(function (conv, i) {
    conv._order = i;
    conv._sort = conv.updated_at || conv.created_at || 0;
    conv._title = String(conv.title || "").toLowerCase();
    conv.messages = Array.isArray(conv.messages) ? conv.messages : [];
    conv.messages.forEach(function (m) {
      m.text = String(m.text == null ? "" : m.text);
      m._low = m.text.toLowerCase();
    });
  });
  data.sort(function (a, b) { return (b._sort - a._sort) || (a._order - b._order); });

  var state = { query: "", provider: "all", selected: data.length ? data[0].id : null };

  var searchInput = document.getElementById("search");
  var sidebar = document.getElementById("list");
  var countEl = document.getElementById("count");
  var headEl = document.getElementById("reader-head");
  var threadEl = document.getElementById("thread");
  var filterButtons = Array.prototype.slice.call(
    document.querySelectorAll(".filters button")
  );

  // ---------------------------------------------------------------- helpers

  function providerName(p) {
    var key = String(p || "").toLowerCase();
    if (PROVIDER_NAMES[key]) { return PROVIDER_NAMES[key]; }
    return key ? key.charAt(0).toUpperCase() + key.slice(1) : "Unknown";
  }

  function badge(provider) {
    var key = String(provider || "").toLowerCase();
    var el = document.createElement("span");
    el.className = "badge" + (key === "chatgpt" || key === "claude" ? " " + key : "");
    el.textContent = providerName(key);
    return el;
  }

  function formatDate(ts, withTime) {
    if (!ts) { return "no date"; }
    var d = new Date(ts * 1000);
    if (isNaN(d.getTime())) { return "no date"; }
    var opts = { year: "numeric", month: "short", day: "numeric" };
    if (withTime) { opts.hour = "2-digit"; opts.minute = "2-digit"; }
    try {
      return d.toLocaleString(undefined, opts);
    } catch (err) {
      return d.toISOString().slice(0, 10);
    }
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  /* Append text to an element, wrapping query matches in <mark>.
     Everything goes in through textContent / createTextNode, so user content
     can never be interpreted as markup. */
  function appendHighlighted(el, text, query) {
    text = String(text == null ? "" : text);
    if (!query) { el.appendChild(document.createTextNode(text)); return; }
    var low = text.toLowerCase();
    var needle = query.toLowerCase();
    var from = 0;
    var at = low.indexOf(needle, from);
    while (at !== -1) {
      if (at > from) {
        el.appendChild(document.createTextNode(text.slice(from, at)));
      }
      var mark = document.createElement("mark");
      mark.textContent = text.slice(at, at + needle.length);
      el.appendChild(mark);
      from = at + needle.length;
      at = low.indexOf(needle, from);
    }
    if (from < text.length) {
      el.appendChild(document.createTextNode(text.slice(from)));
    }
  }

  function makeSnippet(conv, query) {
    var source = null;
    var at = -1;
    if (query) {
      for (var i = 0; i < conv.messages.length; i++) {
        var found = conv.messages[i]._low.indexOf(query);
        if (found !== -1) { source = conv.messages[i].text; at = found; break; }
      }
    }
    if (source === null) {
      source = conv.messages.length ? conv.messages[0].text : "";
      at = 0;
    }
    var flat = source.replace(/\\s+/g, " ").trim();
    if (!flat) { return ""; }
    // Re-locate the match after whitespace collapsing; fall back to the start.
    var pos = query ? flat.toLowerCase().indexOf(query) : 0;
    if (pos < 0) { pos = 0; }
    var start = Math.max(0, pos - 40);
    var snippet = flat.slice(start, start + 170);
    if (start > 0) { snippet = "\\u2026" + snippet; }
    if (start + 170 < flat.length) { snippet = snippet + "\\u2026"; }
    return snippet;
  }

  function matches(conv, query, provider) {
    if (provider !== "all" && String(conv.provider).toLowerCase() !== provider) {
      return false;
    }
    if (!query) { return true; }
    if (conv._title.indexOf(query) !== -1) { return true; }
    for (var i = 0; i < conv.messages.length; i++) {
      if (conv.messages[i]._low.indexOf(query) !== -1) { return true; }
    }
    return false;
  }

  function clear(el) { while (el.firstChild) { el.removeChild(el.firstChild); } }

  function emptyNote(text) {
    var p = document.createElement("p");
    p.className = "empty";
    p.textContent = text;
    return p;
  }

  // --------------------------------------------------------------- render

  function renderList() {
    var query = state.query.toLowerCase();
    var results = data.filter(function (c) {
      return matches(c, query, state.provider);
    });

    countEl.textContent = plural(results.length, "conversation") +
      (state.query || state.provider !== "all" ? " matched" : "");

    clear(sidebar);
    if (!results.length) {
      sidebar.appendChild(emptyNote(
        data.length ? "No conversations match." : "This archive is empty."
      ));
      renderReader(null, query);
      return;
    }

    var stillVisible = results.some(function (c) { return c.id === state.selected; });
    if (!stillVisible) { state.selected = results[0].id; }

    results.forEach(function (conv) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "item";
      if (conv.id === state.selected) { item.setAttribute("aria-current", "true"); }

      var title = document.createElement("div");
      title.className = "title";
      appendHighlighted(title, conv.title, query);
      item.appendChild(title);

      var meta = document.createElement("div");
      meta.className = "meta";
      meta.appendChild(badge(conv.provider));
      var when = document.createElement("span");
      when.textContent = formatDate(conv._sort, false);
      meta.appendChild(when);
      var howMany = document.createElement("span");
      howMany.textContent = plural(conv.messages.length, "message");
      meta.appendChild(howMany);
      item.appendChild(meta);

      var snippetText = makeSnippet(conv, query);
      if (snippetText) {
        var snippet = document.createElement("div");
        snippet.className = "snippet";
        appendHighlighted(snippet, snippetText, query);
        item.appendChild(snippet);
      }

      item.addEventListener("click", function () {
        state.selected = conv.id;
        renderList();
        document.getElementById("reader").scrollTop = 0;
      });
      sidebar.appendChild(item);
    });

    var current = results.filter(function (c) { return c.id === state.selected; })[0];
    renderReader(current || null, query);
  }

  function renderReader(conv, query) {
    clear(headEl);
    clear(threadEl);
    if (!conv) {
      threadEl.appendChild(emptyNote("Select a conversation to read it."));
      return;
    }

    var h2 = document.createElement("h2");
    h2.textContent = conv.title;
    headEl.appendChild(h2);

    var meta = document.createElement("div");
    meta.className = "meta";
    meta.appendChild(badge(conv.provider));
    var when = document.createElement("span");
    when.textContent = formatDate(conv._sort, true);
    meta.appendChild(when);
    var howMany = document.createElement("span");
    howMany.textContent = plural(conv.messages.length, "message");
    meta.appendChild(howMany);
    headEl.appendChild(meta);

    if (!conv.messages.length) {
      threadEl.appendChild(emptyNote("This conversation has no messages."));
      return;
    }

    conv.messages.forEach(function (m) {
      var role = String(m.role || "assistant").toLowerCase();
      if (["user", "assistant", "system", "tool"].indexOf(role) === -1) {
        role = "assistant";
      }
      var wrap = document.createElement("div");
      wrap.className = "msg " + role;

      var who = document.createElement("div");
      who.className = "who";
      who.textContent = m.timestamp
        ? role + " \\u00b7 " + formatDate(m.timestamp, true)
        : role;
      wrap.appendChild(who);

      var bubble = document.createElement("div");
      bubble.className = "bubble";
      appendHighlighted(bubble, m.text, query);
      wrap.appendChild(bubble);

      threadEl.appendChild(wrap);
    });
  }

  // --------------------------------------------------------------- events

  var timer = null;
  searchInput.addEventListener("input", function () {
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(function () {
      state.query = searchInput.value.trim();
      renderList();
    }, DEBOUNCE_MS);
  });

  filterButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      state.provider = button.getAttribute("data-provider") || "all";
      filterButtons.forEach(function (other) {
        other.setAttribute("aria-pressed", other === button ? "true" : "false");
      });
      renderList();
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "/" && document.activeElement !== searchInput) {
      event.preventDefault();
      searchInput.focus();
    } else if (event.key === "Escape" && document.activeElement === searchInput) {
      searchInput.value = "";
      state.query = "";
      renderList();
    }
  });

  renderList();
})();
"""


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ConvoVault</title>
<style>%(css)s</style>
</head>
<body>
<header class="top">
  <h1 class="brand">Convo<span>Vault</span></h1>
  <input id="search" type="search" placeholder="Search titles and messages&hellip;"
         autocomplete="off" spellcheck="false" aria-label="Search conversations">
  <div class="filters" role="group" aria-label="Filter by provider">
    <button type="button" data-provider="all" aria-pressed="true">All</button>
    <button type="button" data-provider="chatgpt" aria-pressed="false">ChatGPT</button>
    <button type="button" data-provider="claude" aria-pressed="false">Claude</button>
  </div>
</header>
<main>
  <nav id="sidebar" aria-label="Conversations">
    <div id="count"></div>
    <div id="list"></div>
  </nav>
  <section id="reader" aria-label="Conversation">
    <div id="reader-head"></div>
    <div id="thread"></div>
  </section>
</main>
<script type="application/json" id="convovault-data">%(payload)s</script>
<script>%(js)s</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def write_site(conversations, out_dir) -> pathlib.Path:
    """Write a self-contained ``index.html`` search page into ``out_dir``.

    Args:
        conversations: iterable of ``Conversation`` objects.  Entries that are
            malformed are skipped rather than raising.
        out_dir: directory to write into; created if it does not exist.

    Returns:
        ``pathlib.Path`` to the written ``index.html``.
    """
    directory = pathlib.Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)

    html = _HTML_TEMPLATE % {
        "css": _CSS,
        "js": _JS,
        "payload": _build_payload(conversations),
    }

    target = directory / "index.html"
    target.write_text(html, encoding="utf-8")
    return target
