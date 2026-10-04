"""Build the Confluence-style HTML docs site from the Markdown pages.

    pip install markdown
    python docs/build_site.py

Reads docs/README.md and docs/NN-*.md, writes docs/site/*.html (open docs/site/index.html).
The Markdown files stay the source of truth; the generated HTML is committed so readers need no tooling.
"""
import html
import re
from pathlib import Path

import markdown

DOCS = Path(__file__).parent
OUT = DOCS / "site"
REPO_BLOB = "https://github.com/Sanjit-kumar/Amex/blob/main/"
PROJECT = "Transaction Standardizer"

PANELS = {  # GitHub alert type -> (css class, title, glyph)
    "NOTE": ("note", "Note", "i"),
    "TIP": ("tip", "Tip", "✓"),
    "IMPORTANT": ("important", "Important", "!"),
    "WARNING": ("warning", "Warning", "!"),
    "CAUTION": ("caution", "Caution", "×"),
}


def slugify(value, sep="-"):
    s = re.sub(r"[^\w\s-]", "", re.sub(r"<[^>]+>", "", value).lower()).strip()
    return re.sub(r"[\s]+", sep, s)


def page_files():
    return [DOCS / "README.md"] + sorted(DOCS.glob("[0-9][0-9]-*.md"))


def out_name(src: Path) -> str:
    return "index.html" if src.name == "README.md" else src.stem + ".html"


def convert(src: Path):
    text = src.read_text(encoding="utf-8")
    text = re.sub(r"^\[&larr;.*\n", "", text, flags=re.M)  # prev/next line is replaced by the site chrome
    title = re.search(r"^# (.+)$", text, re.M).group(1).strip()
    text = re.sub(r"^# .+\n", "", text, count=1, flags=re.M)

    stash = []

    def keep(m):
        stash.append(m.group(1))
        return f"\n\nMERMAIDSLOT{len(stash) - 1}END\n\n"

    text = re.sub(r"```mermaid\n(.*?)```", keep, text, flags=re.S)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists", "toc"],
                           extension_configs={"toc": {"slugify": slugify}})
    body = md.convert(text)
    toc = [(m.group(1), html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))) for m in re.finditer(r'<h2 id="([^"]+)">(.*?)</h2>', body)]

    body = re.sub(r"<p>MERMAIDSLOT(\d+)END</p>",
                  lambda m: f'<div class="diagram"><pre class="mermaid">{html.escape(stash[int(m.group(1))])}</pre></div>', body)

    def panel(m):  # > [!NOTE] blocks become Confluence-style panels
        kind = m.group(1)
        cls, ptitle, glyph = PANELS[kind]
        inner = m.group(2).strip()
        return f'<div class="panel {cls}"><div class="picon">{glyph}</div><div class="pbody"><div class="ptitle">{ptitle}</div>{inner}</div></div>'

    body = re.sub(r"<blockquote>\s*<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*?)</blockquote>",
                  lambda m: panel(type("M", (), {"group": lambda self, i: (m.group(1) if i == 1 else "<p>" + m.group(2))})()),
                  body, flags=re.S)

    def link(m):  # .md pages -> .html, repo source files -> GitHub, status words -> lozenges
        url = m.group(1)
        if url.startswith(("http", "#")):
            return m.group(0)
        path, _, frag = url.partition("#")
        if path.endswith(".md"):
            path = "index.html" if path.endswith("README.md") else path[:-3] + ".html"
        elif path.startswith("../"):
            path = REPO_BLOB + path[3:]
        return f'href="{path}{"#" + frag if frag else ""}"'

    body = re.sub(r'href="([^"]+)"', link, body)
    body = body.replace("<table>", '<div class="tablewrap"><table>').replace("</table>", "</table></div>")
    body = re.sub(r"<td>(<strong>)?(yes|no|warn if missing)(</strong>)?</td>",
                  lambda m: f'<td><span class="lozenge {"req" if m.group(2) == "yes" else "opt"}">{m.group(2)}</span></td>', body)
    return title, body, toc


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - {project}</title>
<link rel="stylesheet" href="assets/site.css">
</head>
<body>
<header class="top"><div class="logo">&#9638; {project}</div><div class="space">Documentation space</div></header>
<div class="layout">
  <nav class="tree"><div class="tree-h">Pages</div>{tree}</nav>
  <main>
    <div class="crumbs">{crumbs}</div>
    <h1 class="title">{title}</h1>
    <div class="meta">Project documentation &middot; Last built from Markdown source</div>
    <article>{body}</article>
    <div class="pager">{prev}{next}</div>
  </main>
  <aside class="otp"><div class="tree-h">On this page</div>{toc}</aside>
</div>
<script src="assets/mermaid.min.js"></script>
<script>
  mermaid.initialize({{startOnLoad: false, theme: "neutral", securityLevel: "loose", flowchart: {{htmlLabels: true, curve: "basis"}}}});
  mermaid.run({{querySelector: "pre.mermaid"}});
</script>
</body>
</html>
"""


def main():
    OUT.mkdir(exist_ok=True)
    srcs = page_files()
    pages = [(s, *convert(s)) for s in srcs]
    for i, (src, title, body, toc) in enumerate(pages):
        name = out_name(src)
        tree = "".join(f'<a class="{"on" if p[0] == src else ""}" href="{out_name(p[0])}">{html.escape(p[1])}</a>' for p in pages)
        crumbs = f'<a href="index.html">{PROJECT}</a>' + ("" if i == 0 else f" / <span>{html.escape(title)}</span>")
        toc_html = "".join(f'<a href="#{a}">{html.escape(t)}</a>' for a, t in toc) or '<span class="mute">&mdash;</span>'
        prev = f'<a class="prev" href="{out_name(pages[i - 1][0])}"><small>&larr; Previous</small><br>{html.escape(pages[i - 1][1])}</a>' if i else "<span></span>"
        nxt = f'<a class="next" href="{out_name(pages[i + 1][0])}"><small>Next &rarr;</small><br>{html.escape(pages[i + 1][1])}</a>' if i < len(pages) - 1 else "<span></span>"
        (OUT / name).write_text(TEMPLATE.format(title=html.escape(title), project=PROJECT, tree=tree, crumbs=crumbs,
                                                body=body, toc=toc_html, prev=prev, next=nxt), encoding="utf-8")
        print("wrote", name)


if __name__ == "__main__":
    main()
