#!/usr/bin/env python3
"""Render own graphics from text: code cards, terminal sessions, Mermaid diagrams
as static SVG, or any local HTML template.

    python scripts/images/render.py code     SRC.py   OUT.webp [--title main.py] [--lines 10-30]
    python scripts/images/render.py terminal LOG.txt  OUT.webp [--title bash]
    python scripts/images/render.py mermaid  FLOW.mmd OUT.svg
    python scripts/images/render.py html     PAGE.html OUT.webp [--selector "#card"]
    python scripts/images/render.py card     DATA.yaml "OUT-{lang}.webp" [--template NAME]
                                             [--lang tr]

code
    A highlighted card with a title bar. Use it as a visual extra (for example a
    before/after pair), never instead of a copyable code block.
terminal
    A transcript: lines starting with ``$ `` are prompts, the rest is output.
    ANSI colour codes are stripped. Redact tokens, host names and paths first.
mermaid
    Renders with a pinned Mermaid build in headless Chrome and saves an SVG whose
    labels are real ``<text>`` elements (no ``foreignObject``, no scripts). The
    diagram sits on a light card with a thin border, so it reads the same in the
    blog's light and dark themes, in RSS readers and on dev.to. The first render
    needs network access for the Mermaid module. Keep the ``.mmd`` source next to
    the SVG; ``scripts/render_diagrams.py`` does that for posts.
html
    Screenshot your own HTML template (a comparison card, a benchmark table).
card
    Fill one of the templates in ``scripts/images/templates/`` (compare, steps,
    before-after, annotated-shot) with a YAML or JSON data file. The card is
    laid out at 1280 CSS px (so its type reads well at the ~600 px the blog
    shows images at), captured at 2x and saved as a 1600 px wide WebP. Fonts
    (Inter, IBM Plex Mono) come from ``assets/fonts``, not from the system. The
    data file names its ``template`` (or pass ``--template``), holds the shared
    keys and a ``langs`` map with the text for each language; ``{lang}`` in OUT
    is replaced, so one call writes the TR and the EN image. ``height`` fixes the
    card height; by default it grows with the content (at least 720). Text that
    overflows its box is reported and makes the command exit with 1.

Brand palette: navy ``#11151d`` (as the covers), teal ``#0f766e``, amber ``#b45309``.
Requires Pillow, Pygments (code mode), websocket-client and Chrome.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _chrome import Chrome
from common import add_logging_flags, setup_logging
from optimize import to_webp

log = logging.getLogger("render")

MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs"
MONO = "ui-monospace,'JetBrains Mono','Cascadia Code',Consolas,monospace"
SANS = "Inter,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif"
# Metric-compatible fonts exist on every platform (Arial, Helvetica, Liberation Sans),
# so text measured while rendering still fits its boxes wherever the SVG is shown.
DIAGRAM_FONT = "Arial, Helvetica, 'Liberation Sans', sans-serif"
CARD_FILL, CARD_STROKE, CARD_PAD, CARD_RADIUS = "#ffffff", "#d0d7de", 20, 12

MERMAID_THEME = {
    "background": CARD_FILL,
    "fontFamily": DIAGRAM_FONT,
    "fontSize": "16px",
    "primaryColor": "#e7f5f3",
    "primaryBorderColor": "#0f766e",
    "primaryTextColor": "#11151d",
    "secondaryColor": "#fdf3e1",
    "secondaryBorderColor": "#b45309",
    "secondaryTextColor": "#11151d",
    "tertiaryColor": "#f3f5f8",
    "tertiaryBorderColor": "#94a3b8",
    "tertiaryTextColor": "#11151d",
    "lineColor": "#475569",
    "textColor": "#11151d",
    "edgeLabelBackground": CARD_FILL,
    "clusterBkg": "#f6f8fa",
    "clusterBorder": "#94a3b8",
    "noteBkgColor": "#fdf3e1",
    "noteBorderColor": "#b45309",
    "actorBkg": "#e7f5f3",
    "actorBorder": "#0f766e",
    "signalColor": "#11151d",
    "labelBoxBkgColor": "#e7f5f3",
    "git0": "#0f766e",
    "git1": "#b45309",
    "git2": "#4f46e5",
    "git3": "#be185d",
    "gitBranchLabel0": "#ffffff",
    "gitBranchLabel1": "#ffffff",
    "gitBranchLabel2": "#ffffff",
    "gitBranchLabel3": "#ffffff",
}

FRAME = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;background:transparent}}
#card{{display:inline-block;margin:24px;border-radius:12px;overflow:hidden;
  box-shadow:0 10px 30px rgba(0,0,0,.35);background:#11151d;min-width:640px;max-width:1100px}}
.bar{{display:flex;align-items:center;gap:8px;padding:10px 14px;background:#1b2130;
  font:500 13px {sans};color:#9aa4b2}}
.dot{{width:12px;height:12px;border-radius:50%}}
.title{{margin-left:10px}}
pre{{margin:0;padding:18px 22px;font:15px/1.55 {mono};color:#e6edf3;white-space:pre;
  tab-size:4;font-variant-ligatures:none;font-feature-settings:'liga' 0,'calt' 0}}
.prompt{{color:#7ee787}} .muted{{color:#8b949e}}
{extra_css}
</style></head><body><div id="card"><div class="bar">
<span class="dot" style="background:#ff5f57"></span>
<span class="dot" style="background:#febc2e"></span>
<span class="dot" style="background:#28c840"></span><span class="title">{title}</span></div>
{body}</div></body></html>"""

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _shoot(
    page_html: str,
    out: pathlib.Path,
    selector: str = "#card",
    dark: bool = True,
    base: pathlib.Path | None = None,
) -> pathlib.Path:
    """Render ``page_html`` in Chrome and save the element ``selector`` as WebP."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = pathlib.Path(tmp)
        # html mode renders next to the source so relative <img>/<link> paths resolve
        page = (base or tmpdir) / ".render-page.html"
        page.write_text(page_html, encoding="utf-8", newline="\n")
        png = tmpdir / "out.png"
        try:
            with Chrome(1400, 900, 2, dark) as chrome:
                chrome.goto(page.resolve().as_uri(), 0.3)
                chrome.screenshot(png, clip=chrome.element_clip(selector, 0))
        finally:
            page.unlink(missing_ok=True)
        return to_webp(png, out, max_width=1600, quality=90)


def render_code(
    src: pathlib.Path, out: pathlib.Path, title: str | None = None, lines: str | None = None
) -> pathlib.Path:
    """Render a highlighted code card from a source file."""
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import get_lexer_for_filename, guess_lexer
    from pygments.util import ClassNotFound

    code = src.read_text(encoding="utf-8")
    if lines:
        first, last = (int(x) for x in lines.split("-"))
        code = "\n".join(code.splitlines()[first - 1 : last])
    try:
        lexer = get_lexer_for_filename(src.name)
    except ClassNotFound:
        lexer = guess_lexer(code)
    fmt = HtmlFormatter(style="github-dark", nowrap=True)
    page = FRAME.format(
        sans=SANS,
        mono=MONO,
        title=html.escape(title or src.name),
        body="<pre>" + highlight(code, lexer, fmt) + "</pre>",
        extra_css=fmt.get_style_defs("pre"),
    )
    return _shoot(page, out)


def render_terminal(src: pathlib.Path, out: pathlib.Path, title: str = "bash") -> pathlib.Path:
    """Render a terminal transcript card."""
    rows = []
    for line in ANSI.sub("", src.read_text(encoding="utf-8")).splitlines():
        if line.startswith("$ "):
            rows.append(f'<span class="prompt">$</span> {html.escape(line[2:])}')
        elif line.startswith("#"):
            rows.append(f'<span class="muted">{html.escape(line)}</span>')
        else:
            rows.append(html.escape(line))
    page = FRAME.format(
        sans=SANS,
        mono=MONO,
        title=html.escape(title),
        body="<pre>" + "\n".join(rows) + "</pre>",
        extra_css="",
    )
    return _shoot(page, out)


# --------------------------------------------------------------------------- Mermaid labels

# Flowchart and state diagram labels are read as Markdown (backticks,
# `__bold__`, `*em*`) and sanitised as HTML, so `<T>` vanishes. Mermaid's own
# entity codes are decoded only after parsing and Markdown, so writing these
# characters as entity codes keeps them literal; <br> line breaks stay tags.
# Sequence diagrams show their text as written and are left alone.
LABEL_ESCAPES = {"`": "#96;", "_": "#95;", "*": "#42;", "<": "#lt;", ">": "#gt;", '"': "#quot;"}
_BR = re.compile(r"(<br\s*/?>)", re.I)
_SHAPES = (
    ("([", "])"),
    ("[[", "]]"),
    ("[(", ")]"),
    ("((", "))"),
    ("{{", "}}"),
    ("[/", "/]"),
    ("[\\", "\\]"),
    ("[", "]"),
    ("(", ")"),
    ("{", "}"),
)
_SKIP_LINE = re.compile(
    r"^\s*(%%|classDef\b|class\b|style\b|linkStyle\b|click\b|direction\b|end\s*$)"
)


def _escape_text(text: str) -> str:
    """Escape label text, keeping <br> tags and existing entity codes."""
    parts = _BR.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = "".join(LABEL_ESCAPES.get(ch, ch) for ch in parts[i])
    return "".join(parts)


def _escape_quoted_or_plain(text: str, quote: bool) -> str:
    """Escape a label; ``quote`` wraps changed unquoted text in quotes."""
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return '"' + _escape_text(stripped[1:-1]) + '"'
    escaped = _escape_text(text)
    if escaped == text:
        return text
    return f'"{escaped.strip()}"' if quote else escaped


def _flowchart_line(line: str) -> str:
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        # Edge label: -->|text|
        if ch == "|":
            end = line.find("|", i + 1)
            if end > i:
                out.append("|" + _escape_quoted_or_plain(line[i + 1 : end], quote=True) + "|")
                i = end + 1
                continue
        # Node shape directly after an id: A[text], B((text)), C{text} ...
        if out and (out[-1][-1:].isalnum() or out[-1][-1:] == "_"):
            for opener, closer in _SHAPES:
                if line.startswith(opener, i):
                    start = i + len(opener)
                    end = _closing(line, start, closer)
                    if end >= 0:
                        label = _escape_quoted_or_plain(line[start:end], quote=True)
                        out.append(opener + label + closer)
                        i = end + len(closer)
                        break
            else:
                out.append(ch)
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _closing(line: str, start: int, closer: str) -> int:
    """Index of ``closer`` after ``start``, skipping a quoted label."""
    j = start
    while j < len(line) and line[j] == " ":
        j += 1
    if j < len(line) and line[j] == '"':
        q = line.find('"', j + 1)
        if q < 0:
            return -1
        return line.find(closer, q + 1)
    return line.find(closer, start)


def escape_labels(definition: str) -> str:
    """Return ``definition`` with label text protected from Markdown and HTML handling."""
    lines = definition.splitlines()
    if not lines:
        return definition
    kind = lines[0].split(None, 1)[0] if lines[0].strip() else ""
    out = [lines[0]]
    for line in lines[1:]:
        if _SKIP_LINE.match(line) or not line.strip():
            out.append(line)
        elif kind in ("flowchart", "graph"):
            m = re.match(r"^(\s*subgraph\s+)(\w+\s*)?(\[.*\])?(.*)$", line)
            if m and line.lstrip().startswith("subgraph"):
                head, ident, bracket, rest = m.group(1), m.group(2) or "", m.group(3), m.group(4)
                if bracket:
                    inner = _escape_quoted_or_plain(bracket[1:-1], quote=True)
                    out.append(f"{head}{ident}[{inner}]{rest}")
                else:
                    out.append(head + _escape_quoted_or_plain(ident + rest, quote=True))
            else:
                out.append(_flowchart_line(line))
        elif kind.startswith("stateDiagram"):
            m = re.match(r'^(\s*state\s+)"(.*)"(\s+as\s+.*)$', line)
            if m:
                out.append(f'{m.group(1)}"{_escape_text(m.group(2))}"{m.group(3)}')
            elif ":" in line:
                head, text = line.split(":", 1)
                out.append(head + ":" + _escape_text(text))
            else:
                out.append(line)
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if definition.endswith("\n") else "")


def mermaid_to_svg(definitions: list[str], ids: list[str] | None = None) -> list[str]:
    """Render Mermaid definitions to raw SVG strings in one browser session.

    ``ids`` become the SVG element ids (Mermaid scopes its CSS with them); they
    default to ``d0``, ``d1``, ...
    """
    ids = ids or [f"d{i}" for i in range(len(definitions))]
    definitions = [escape_labels(d) for d in definitions]
    config = {
        "startOnLoad": False,
        "theme": "base",
        "themeVariables": MERMAID_THEME,
        "securityLevel": "strict",
        "htmlLabels": False,
        "fontFamily": DIAGRAM_FONT,
        "flowchart": {"htmlLabels": False, "useMaxWidth": False, "padding": 12},
        "sequence": {"useMaxWidth": False, "mirrorActors": False},
        "state": {"useMaxWidth": False},
        "gitGraph": {"useMaxWidth": False},
        "class": {"useMaxWidth": False},
        "er": {"useMaxWidth": False},
    }
    page = f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<script type="module">
import mermaid from {json.dumps(MERMAID_URL)};
mermaid.initialize({json.dumps(config)});
const defs = {json.dumps(definitions)};
const ids = {json.dumps(ids)};
const out = [];
// HTML entity references, and Mermaid's own entity placeholders (`#95;` becomes
// "\ufb02\u00b0\u00b095\u00b6\u00df" while parsing).
const ENTITY = /&(#\\d+|#x[0-9a-f]+|[a-z]+);|\ufb02\u00b0(\u00b0)?(\\w+)\u00b6\u00df/gi;
const NAMED = {{lt: '<', gt: '>', amp: '&', quot: '"', apos: "'", nbsp: '\u00a0'}};
const decodeEntity = (m, html, numeric, code) => {{
  const e = html || (numeric ? '#' + code : code);
  if (e[0] === '#') {{
    const hex = e[1].toLowerCase() === 'x';
    return String.fromCodePoint(parseInt(e.slice(hex ? 2 : 1), hex ? 16 : 10));
  }}
  return NAMED[e.toLowerCase()] ?? m;
}};
// Flowchart and state labels reach the DOM still escaped (`&lt;T&gt;` or a
// placeholder as text). Decode them whenever SVG text is set, before Mermaid
// measures it, so boxes fit the text that is finally shown.
const textContent = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
Object.defineProperty(Node.prototype, 'textContent', {{
  configurable: true,
  get() {{ return textContent.get.call(this); }},
  set(v) {{
    if (typeof v === 'string' && this instanceof SVGElement) v = v.replace(ENTITY, decodeEntity);
    textContent.set.call(this, v);
  }},
}});
const box = document.createElement('div');
document.body.appendChild(box);
for (let i = 0; i < defs.length; i++) {{
  try {{
    box.innerHTML = (await mermaid.render(ids[i], defs[i])).svg;
    // Text added without the textContent setter: decode it as well.
    for (const el of box.querySelectorAll('text, tspan')) {{
      for (const n of el.childNodes) {{
        if (n.nodeType === 3) n.data = n.data.replace(ENTITY, decodeEntity);
      }}
    }}
    // Without HTML labels, edge labels have no backdrop and lines run through
    // the text. Measure each label and put a card-coloured rectangle behind it.
    for (const g of box.querySelectorAll('g.edgeLabel')) {{
      const b = g.getBBox();
      if (!b.width) continue;
      const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      r.setAttribute('x', b.x - 3); r.setAttribute('y', b.y - 1);
      r.setAttribute('width', b.width + 6); r.setAttribute('height', b.height + 2);
      r.setAttribute('fill', {json.dumps(CARD_FILL)});
      g.insertBefore(r, g.firstChild);
    }}
    out.push({{svg: new XMLSerializer().serializeToString(box.querySelector('svg'))}});
  }} catch (e) {{ out.push({{error: String(e && e.message || e)}}); }}
}}
window.__result = out;
</script></body></html>"""
    with tempfile.TemporaryDirectory() as tmp:
        html_file = pathlib.Path(tmp) / "mermaid.html"
        html_file.write_text(page, encoding="utf-8", newline="\n")
        with Chrome(1600, 1000, 1, False) as chrome:
            chrome.goto(html_file.as_uri(), 0.1)
            result = None
            for _ in range(300):
                result = chrome.eval("window.__result || null")
                if result:
                    break
                chrome.eval("new Promise(r => setTimeout(r, 200))", await_promise=True)
    if not result:
        raise RuntimeError("Mermaid did not load (no network access to the CDN?)")
    svgs = []
    for i, r in enumerate(result):
        if "error" in r:
            raise ValueError(f"diagram {i + 1}: {r['error']}")
        svgs.append(r["svg"])
    return svgs


def card_svg(svg: str) -> str:
    """Put a Mermaid SVG on a padded light card and give it an intrinsic size.

    The card makes the diagram readable on both light and dark pages. Width and
    height attributes equal the viewBox, so an ``<img>`` reserves the right space.
    """
    if "<foreignObject" in svg:
        raise ValueError("SVG contains foreignObject (HTML labels); text would not render in <img>")
    m = re.search(r'viewBox="([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)[ ,]+([-\d.]+)"', svg)
    if not m:
        raise ValueError("SVG has no viewBox")
    x, y, w, h = (float(v) for v in m.groups())
    x, y, w, h = x - CARD_PAD, y - CARD_PAD, w + 2 * CARD_PAD, h + 2 * CARD_PAD
    open_tag = re.search(r"<svg\b[^>]*>", svg)
    assert open_tag
    tag = open_tag.group(0)
    tag = re.sub(r'\s(width|height|style)="[^"]*"', "", tag)
    tag = re.sub(r'viewBox="[^"]*"', f'viewBox="{x:g} {y:g} {w:g} {h:g}"', tag)
    tag = tag[:-1] + f' width="{round(w)}" height="{round(h)}">'
    card = (
        f'<rect x="{x + 0.5:g}" y="{y + 0.5:g}" width="{w - 1:g}" height="{h - 1:g}" '
        f'rx="{CARD_RADIUS}" fill="{CARD_FILL}" stroke="{CARD_STROKE}"/>'
    )
    body = svg[open_tag.end() :]
    return tag + card + body


def render_mermaid(src: pathlib.Path, out: pathlib.Path) -> pathlib.Path:
    """Render one ``.mmd`` file to a static SVG card."""
    svg = card_svg(mermaid_to_svg([src.read_text(encoding="utf-8")])[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8", newline="\n")
    return out


TEMPLATES = pathlib.Path(__file__).resolve().parent / "templates"

# The card, its main area and any box marked .fit must not clip their content.
OVERFLOW_JS = """(() => [...document.querySelectorAll('#card, #card main, #card .fit')]
  .filter(e => e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1)
  .map(e => (e.className || e.id) + ': ' + e.scrollWidth + 'x' + e.scrollHeight +
            ' > ' + e.clientWidth + 'x' + e.clientHeight + ' ' +
            e.textContent.trim().slice(0, 50)))()"""


def _load_data(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        return yaml.safe_load(text)
    return json.loads(text)


def render_card(
    data_file: pathlib.Path,
    out: pathlib.Path,
    template: str | None = None,
    only_lang: str | None = None,
) -> tuple[list[pathlib.Path], list[str]]:
    """Render a template card per language. Returns the files and any overflow problems."""
    data = _load_data(data_file)
    name = template or data.pop("template", None)
    data.pop("template", None)
    if not name:
        raise ValueError("no template: set `template` in the data file or pass --template")
    tpl = TEMPLATES / f"{name}.html"
    if not tpl.exists():
        raise FileNotFoundError(
            f"unknown template {name!r}; have: "
            + ", ".join(sorted(t.stem for t in TEMPLATES.glob("*.html")))
        )
    langs = data.pop("langs", None) or {data.get("lang", "en"): {}}
    if len(langs) > 1 and "{lang}" not in str(out) and not only_lang:
        raise ValueError("several languages in the data file: put {lang} in OUT or pass --lang")
    files, problems = [], []
    for lang, texts in langs.items():
        if only_lang and lang != only_lang:
            continue
        d = {**data, **(texts or {}), "lang": lang}
        if d.get("image"):
            d["image"] = (data_file.parent / d["image"]).resolve().as_uri()
        target = pathlib.Path(str(out).replace("{lang}", lang))
        payload = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
        page_html = tpl.read_text(encoding="utf-8").replace(
            "</head>", f"<script>window.DATA = {payload};</script></head>", 1
        )
        page = TEMPLATES / f".render-{lang}-{target.stem}.html"
        page.write_text(page_html, encoding="utf-8", newline="\n")
        with tempfile.TemporaryDirectory() as tmp:
            png = pathlib.Path(tmp) / "card.png"
            try:
                with Chrome(
                    int(d.get("width", 1280)), int(d.get("height", 720)), 2, False
                ) as chrome:
                    chrome.goto(page.resolve().as_uri(), 0.3)
                    problems += [f"{target.name}: {p}" for p in chrome.eval(OVERFLOW_JS) or []]
                    chrome.screenshot(png, clip=chrome.element_clip("#card", 0))
            finally:
                page.unlink(missing_ok=True)
            files.append(to_webp(png, target, max_width=1600, quality=90))
    return files, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Render code cards, terminal sessions, Mermaid "
        "diagrams or HTML templates into post images."
    )
    ap.add_argument("mode", choices=["code", "terminal", "mermaid", "html", "card"])
    ap.add_argument("src", type=pathlib.Path, help="input file (a YAML/JSON data file for card)")
    ap.add_argument(
        "out", type=pathlib.Path, help="output file (.webp, or .svg for mermaid; {lang} for card)"
    )
    ap.add_argument("--title", help="title bar text (code, terminal)")
    ap.add_argument("--lines", help="line range for code mode, e.g. 10-32")
    ap.add_argument("--selector", default="body > *", help="element to capture in html mode")
    ap.add_argument("--template", help="card mode: template name (default: `template` in the data)")
    ap.add_argument("--lang", help="card mode: render only this language")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)
    if a.mode == "code":
        result = render_code(a.src, a.out, a.title, a.lines)
    elif a.mode == "terminal":
        result = render_terminal(a.src, a.out, a.title or "bash")
    elif a.mode == "mermaid":
        result = render_mermaid(a.src, a.out)
    elif a.mode == "card":
        files, problems = render_card(a.src, a.out, a.template, a.lang)
        for f in files:
            log.info("%s", f)
        for p in problems:
            log.error("overflow: %s", p)
        return 1 if problems else 0
    else:
        result = _shoot(
            a.src.read_text(encoding="utf-8"),
            a.out,
            a.selector,
            dark=False,
            base=a.src.resolve().parent,
        )
    log.info("%s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
