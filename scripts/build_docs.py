"""Build lokra.dev/docs: Markdown in site/docs becomes static pages in site/public/docs.

The CLI reference is generated from the real argument parser, so it cannot drift from the code.
It also fingerprints the stylesheet, script and demo that the hand-written site pages load, so browsers
fetch a changed file at once instead of keeping a cached copy for hours.
`--check` rebuilds into a temporary folder and fails if the committed pages are stale, a fingerprint is out
of date or a link is broken.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "docs"
PUBLIC = ROOT / "site" / "public"
OUT = PUBLIC / "docs"
SITE_PAGES = ("index", "privacy", "joined", "404", "llm-context")
STAMPED = re.compile(r'(href|src)="(/(?:styles\.css|site\.js|demo\.svg))(?:\?v=[0-9a-f]+)?"')
SITE = "https://lokra.dev"
REPO = "https://github.com/lokrahq/lokra"
sys.path.insert(0, str(ROOT / "src"))

NAV = [
    ("Getting started", ["index", "quickstart", "install"]),
    ("Self-hosting", ["connect-database", "policies", "masking", "row-locks", "approvals"]),
    ("Running agents", ["connect-clients", "production", "stop-agents", "slack", "audit", "ci"]),
    ("Lokra Cloud", ["cloud", "cloud-gates", "cloud-alerts", "cloud-team", "cloud-activity", "cloud-billing"]),
    ("Reference", ["cli", "policy-reference", "mcp-tools", "environment", "security-model", "troubleshooting"]),
]
ORDER = [slug for _, slugs in NAV for slug in slugs]
SECTION = {slug: name for name, slugs in NAV for slug in slugs}
CALLOUT = re.compile(r"<blockquote>\s*<p><strong>(Note|Warning|Tip):</strong>")
# A fence can name its block after the language, as in ```ini lokra.service; otherwise the language decides.
FENCE = re.compile(r"^([ \t]*)```(\w+)(?:[ \t]+([^`\n]+?))?[ \t]*$", re.M)
LABELS = {"bash": "Terminal", "yaml": "policies.yaml", "json": "JSON", "sql": "SQL", "ini": "INI", "text": "Output"}
MENU = ('<svg viewBox="0 0 20 20" aria-hidden="true"><path class="i-open" d="M3 6h14M3 10h14M3 14h14"/>'
        '<path class="i-close" d="M5 5l10 10M15 5L5 15"/></svg>')

BRAND = ('<a class="brand" href="/"><svg viewBox="21.5 26 57 48" aria-hidden="true"><path d="M33 54.5V46.5a17 17 0 0 1 34 0V54.5" '
         'fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round"/><path d="M25 70.5H75" fill="none" '
         'stroke="currentColor" stroke-width="7" stroke-linecap="round"/></svg><span>lokra</span></a>')
GITHUB = ('<svg viewBox="0 0 16 16" aria-hidden="true" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 '
          '7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53'
          '.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08'
          '-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92'
          '.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8'
          '.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>')


def versioned(url: str, source: Path) -> str:
    """The URL with a fingerprint of the file, so browsers fetch a changed stylesheet or script at once."""
    return f"{url}?v={hashlib.sha256(source.read_bytes()).hexdigest()[:10]}"


def stamp_site(write: bool) -> list[str]:
    """Bring the fingerprints in the site pages up to date; returns the pages that were out of date."""
    stale = []
    for name in SITE_PAGES:
        path = PUBLIC / f"{name}.html"
        text = path.read_text()
        fresh = STAMPED.sub(lambda m: f'{m.group(1)}="{versioned(m.group(2), PUBLIC / m.group(2)[1:])}"', text)
        if fresh != text:
            stale.append(name)
            if write:
                path.write_text(fresh)
    return stale


def front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("missing front matter")
    head, body = text[4:].split("\n---\n", 1)
    meta = {}
    for line in head.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    for key in ("title", "description"):
        if not meta.get(key):
            raise ValueError(f"front matter needs a {key}")
    return meta, body


def cli_page() -> str:
    """The CLI reference, written from the parser `lokra` itself uses."""
    from lokra.cli import build_parser

    parser = build_parser()
    out = ["---", "title: CLI reference", "description: Every lokra command and flag, generated from the CLI itself.", "---",
           "Every command and flag, generated from `lokra` itself, so this page always matches the version it was built from. "
           "Run `lokra <command> --help` for the same text in your terminal.", "", "## Global options", "",
           "These go before the command, for example `lokra --config ./policies.yaml check`.", "",
           "| Option | What it does |", "|---|---|"]
    for action in parser._actions:
        if action.option_strings and action.dest not in ("help",):
            out.append(f"| `{', '.join(action.option_strings)}` | {cell(action.help or '')} |")
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {choice.dest: choice.help for choice in sub._choices_actions}
    for name, command in sub.choices.items():
        summary = helps.get(name) or ""
        out += ["", f"## lokra {name}", "", f"{summary[:1].upper()}{summary[1:]}.", "", "```text Usage",
                command.format_usage().replace("usage: ", "").strip(), "```"]
        rows = [a for a in command._actions if a.dest != "help"]
        if rows:
            out += ["", "| Argument | What it does |", "|---|---|"]
            for a in rows:
                label = ", ".join(a.option_strings) if a.option_strings else a.dest
                extra = f" Choices: {', '.join(f'`{c}`' for c in a.choices)}." if a.choices and a.option_strings else ""
                out.append(f"| `{label}` | {cell(a.help or '')}{extra} |")
    return "\n".join(out) + "\n"


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def code_labels(body: str) -> tuple[str, list[tuple[str, str]]]:
    blocks = []

    def strip(m):
        blocks.append((m.group(2), (m.group(3) or LABELS.get(m.group(2), m.group(2).upper())).strip()))
        return f"{m.group(1)}```{m.group(2)}"

    return FENCE.sub(strip, body), blocks


def render(slug: str, text: str) -> tuple[dict, str, list]:
    meta, body = front_matter(text)
    body, blocks = code_labels(body)
    md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "attr_list", "sane_lists", "codehilite"],
                           extension_configs={
                               "toc": {"permalink": "#", "permalink_class": "anchor", "toc_depth": "2-3"},
                               "codehilite": {"css_class": "hl", "guess_lang": False, "noclasses": False, "classprefix": "t-"}})
    content = md.convert(body)
    opening = '<div class="hl"><pre>'
    if content.count(opening) != len(blocks):
        raise ValueError(f"{slug}: {len(blocks)} code fences but {content.count(opening)} code blocks")
    labelled = iter(blocks)
    content = re.sub(re.escape(opening), lambda m: '<div class="hl" data-lang="{}"><div class="code-head"><span>{}</span></div><pre>'.format(
        *(html.escape(part) for part in next(labelled))), content)
    content = CALLOUT.sub(lambda m: f'<blockquote class="callout {m.group(1).lower()}">\n<p><strong>{m.group(1)}:</strong>', content)
    return meta, content, md.toc_tokens


def toc_html(tokens: list) -> str:
    if len(tokens) < 2:
        return ""
    items = "".join(f'<li><a href="#{t["id"]}">{t["name"]}</a></li>' for t in tokens)
    return f'<p>On this page</p><ul>{items}</ul>'


def nav_html(current: str, titles: dict) -> str:
    parts = []
    for name, slugs in NAV:
        links = []
        for s in slugs:
            if s not in titles:
                continue
            here = ' class="on" aria-current="page"' if s == current else ""
            links.append(f'<li><a href="/docs/{"" if s == "index" else s}"{here}>{html.escape(titles[s])}</a></li>')
        if links:
            parts.append(f'<p>{html.escape(name)}</p><ul>{"".join(links)}</ul>')
    return "".join(parts)


def page(slug: str, meta: dict, content: str, tokens: list, titles: dict) -> str:
    present = [s for s in ORDER if s in titles]
    i = present.index(slug)
    url = lambda s: "/docs/" + ("" if s == "index" else s)
    prev = f'<a class="prev" href="{url(present[i - 1])}"><span>Previous</span>{html.escape(titles[present[i - 1]])}</a>' if i else "<span></span>"
    nxt = (f'<a class="next" href="{url(present[i + 1])}"><span>Next</span>{html.escape(titles[present[i + 1]])}</a>'
           if i + 1 < len(present) else "")
    title, desc = html.escape(meta["title"]), html.escape(meta["description"])
    edit = f"{REPO}/blob/main/scripts/build_docs.py" if slug == "cli" else f"{REPO}/edit/main/site/docs/{slug}.md"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon-32.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="preload" href="/fonts/geist-400.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{versioned("/styles.css", PUBLIC / "styles.css")}">
<link rel="stylesheet" href="{versioned("/docs/docs.css", SRC / "assets" / "docs.css")}">
<meta name="theme-color" content="#ffffff">
<title>{title} · Lokra docs</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{SITE}{url(slug)}">
<meta property="og:title" content="{title} · Lokra docs">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{SITE}/social-preview.png">
</head>
<body class="docs">
<header class="docs-header">
  <div class="wrap docs-top">
    <div class="docs-brand">{BRAND}<a class="docs-label" href="/docs/">Docs</a></div>
    <div class="docs-search" role="search">
      <input type="search" placeholder="Search docs" aria-label="Search the docs" autocomplete="off" data-search>
      <kbd>/</kbd>
      <div class="docs-results" data-results hidden></div>
    </div>
    <nav class="links">
      <a class="hide-sm" href="https://cloud.lokra.dev">Lokra Cloud</a>
      <a class="gh" href="{REPO}">{GITHUB}<span>GitHub</span></a>
    </nav>
    <button class="docs-menu" type="button" aria-label="Menu" aria-expanded="false" aria-controls="docs-side" data-menu>{MENU}</button>
  </div>
</header>
<div class="wrap docs-shell">
  <aside class="docs-side" id="docs-side">
    <nav class="docs-nav" aria-label="Docs">{nav_html(slug, titles)}</nav>
  </aside>
  <main class="docs-main">
    <p class="docs-eyebrow">{html.escape(SECTION[slug])}</p>
    <h1>{title}</h1>
    <p class="docs-lede">{desc}</p>
    <div class="docs-body">
{content}
    </div>
    <nav class="docs-pager" aria-label="Pages">{prev}{nxt}</nav>
    <p class="docs-edit"><a href="{edit}">Edit this page on GitHub</a></p>
  </main>
  <aside class="docs-toc">{toc_html(tokens)}</aside>
</div>
<footer>
  <div class="wrap">
    <span>© 2026 Lokra</span>
    <nav>
      <a href="/docs/">Docs</a>
      <a href="{REPO}">GitHub</a>
      <a href="{REPO}/blob/main/SECURITY.md">Security</a>
      <a href="/privacy">Privacy</a>
      <a href="/llm-context">LLM context</a>
      <a href="mailto:hello@lokra.dev">Contact</a>
    </nav>
  </div>
</footer>
<script src="{versioned("/docs/docs.js", SRC / "assets" / "docs.js")}" defer></script>
</body>
</html>
"""


def search_entries(slug: str, meta: dict, content: str) -> list[dict]:
    url = "/docs/" + ("" if slug == "index" else slug)
    entries = [{"t": meta["title"], "s": SECTION[slug], "u": url, "x": meta["description"]}]
    for match in re.finditer(r'<h([23]) id="([^"]+)">(.*?)</h\1>(.*?)(?=<h[23] id=|\Z)', content, re.S):
        heading = re.sub(r"<[^>]+>", "", match.group(3)).strip().rstrip("#").strip()
        body = re.sub(r'<div class="code-head">.*?</div>', " ", match.group(4))
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
        entries.append({"t": heading, "s": meta["title"], "u": f"{url}#{match.group(2)}", "x": text[:220]})
    return entries


def build(out: Path) -> list[str]:
    sources = {p.stem: p.read_text() for p in SRC.glob("*.md")}
    sources["cli"] = cli_page()
    unknown = sorted(set(sources) - set(ORDER))
    if unknown:
        raise SystemExit(f"pages not in the navigation: {', '.join(unknown)}")
    rendered, titles, index = {}, {}, []
    for slug in ORDER:
        if slug in sources:
            meta, content, tokens = render(slug, sources[slug])
            rendered[slug] = (meta, content, tokens)
            titles[slug] = meta["title"]
    out.mkdir(parents=True, exist_ok=True)
    for slug, (meta, content, tokens) in rendered.items():
        (out / ("index.html" if slug == "index" else f"{slug}.html")).write_text(page(slug, meta, content, tokens, titles))
        index += search_entries(slug, meta, content)
    (out / "search.json").write_text(json.dumps(index, separators=(",", ":"), ensure_ascii=False))
    urls = "".join(f"<url><loc>{SITE}/docs/{'' if s == 'index' else s}</loc></url>" for s in rendered)
    (out / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n'
                                     f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n')
    for asset in ("docs.css", "docs.js"):
        shutil.copyfile(SRC / "assets" / asset, out / asset)
    return [s for s in ORDER if s not in rendered]


def broken_links(out: Path) -> list[str]:
    pages = {p.stem: p.read_text() for p in out.glob("*.html")}
    anchors = {slug: set(re.findall(r'id="([^"]+)"', body)) for slug, body in pages.items()}
    problems = []
    for slug, body in pages.items():
        for href in re.findall(r'href="(/docs/[^"]*)"', body):
            path, _, anchor = href.partition("#")
            target = path.partition("?")[0].removeprefix("/docs/").strip("/") or "index"
            if target in ("docs.css", "docs.js"):
                continue
            if target not in pages:
                problems.append(f"{slug}: link to missing page {href}")
            elif anchor and anchor not in anchors[target]:
                problems.append(f"{slug}: link to missing anchor {href}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed pages are stale or a link is broken")
    args = ap.parse_args()
    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            missing = build(Path(tmp))
            problems = broken_links(Path(tmp)) + [f"page missing: {s}" for s in missing]
            for p in sorted(Path(tmp).iterdir()):
                committed = OUT / p.name
                if not committed.exists() or committed.read_bytes() != p.read_bytes():
                    problems.append(f"stale: site/public/docs/{p.name} (run python scripts/build_docs.py)")
        problems += [f"stale fingerprint: site/public/{name}.html (run python scripts/build_docs.py)"
                     for name in stamp_site(write=False)]
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    missing = build(OUT)
    stamped = stamp_site(write=True)
    if stamped:
        print(f"updated fingerprints in {', '.join(f'{name}.html' for name in stamped)}")
    for problem in broken_links(OUT):
        print(f"warning: {problem}", file=sys.stderr)
    print(f"built {len(ORDER) - len(missing)} pages into {OUT.relative_to(ROOT)}" +
          (f"; not written yet: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
