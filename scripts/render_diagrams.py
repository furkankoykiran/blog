#!/usr/bin/env python3
"""Pre-render Mermaid diagrams in posts to static SVG images.

    python scripts/render_diagrams.py            # convert shortcodes, then render stale SVGs
    python scripts/render_diagrams.py convert    # {{< mermaid >}} blocks -> .mmd + image refs
    python scripts/render_diagrams.py render [--force] [BUNDLE ...]
    python scripts/render_diagrams.py check      # exit 1 if a shortcode or unrendered .mmd is left

Why: a client-side ``{{< mermaid >}}`` block needs JavaScript, so the diagram is
missing from RSS, from the Markdown output and from the dev.to copy. A static SVG
shows up everywhere.

Diagram labels differ between Turkish and English, so each language keeps its
own files inside its own bundle::

    content/tr/posts/<bundle>/img/<name>.mmd    source, not published (see hugo.toml mounts)
    content/tr/posts/<bundle>/img/<name>.svg    rendered card
    content/en/posts/<bundle>/img/<name>.mmd
    content/en/posts/<bundle>/img/<name>.svg

Both language files reference the same path (``img/<name>.svg``), and one
``kind: diagram`` entry in ``content/media/posts/<bundle>/credits.yaml`` covers
both versions; ``convert`` and ``render`` add it when it is missing.

``convert`` pairs the n-th diagram of the TR file with the n-th of the EN file.
The file name comes from the English section heading above the diagram. Alt
text summarises the diagram's nodes (or participants, states, branches) in the
page's language, and the caption is the section heading. Review both when you
edit the post. To change a diagram, edit its ``.mmd`` and run ``render``.

Rendering needs Chrome and, once, network access for the pinned Mermaid module
(see ``scripts/images/render.py``). Exit codes: 0 success, 1 ``check`` found a
problem or rendering failed, 2 the bundle cannot be converted automatically.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from common import (
    CONTENT_DIR,
    LANGS,
    REPO_ROOT,
    ScriptError,
    add_logging_flags,
    author_name,
    setup_logging,
    write_text,
)

sys.path.insert(0, str(Path(__file__).resolve().parent / "images"))
from credits import append_credit, load

log = logging.getLogger("render_diagrams")

BLOCK = re.compile(r"\{\{<\s*mermaid\s*>\}\}[ \t]*\r?\n(.*?)\{\{<\s*/mermaid\s*>\}\}[ \t]*", re.S)
HEADING = re.compile(r"^(#{2,3})\s+(.+?)\s*#*\s*$", re.M)
TITLE = re.compile(r'^title:\s*"?(.*?)"?\s*$', re.M)
STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "how",
    "why",
    "what",
    "when",
    "is",
    "are",
    "it",
    "its",
    "your",
    "my",
}
MAX_ALT = 160


# --------------------------------------------------------------------------- text helpers


def slugify(text: str, max_words: int = 5) -> str:
    text = re.sub(r"['’]", "", text.replace("ı", "i"))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"^\W*\d+\W+", "", text)  # "5. Fanning out" -> "Fanning out"
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]
    return "-".join(words[:max_words])


ENTITIES = {"quot": '"', "lt": "<", "gt": ">", "amp": "&", "nbsp": " "}


def clean_label(raw: str) -> str:
    """Plain text of a Mermaid label: line breaks, tags, entities and emphasis removed."""
    label = re.sub(r"<br\s*/?>", " ", raw, flags=re.I)
    label = re.sub(r"</?[a-z][^>]*>", "", label, flags=re.I)
    label = re.sub(
        r"[#&](\w+);",
        lambda m: (
            chr(int(m.group(1)))
            if m.group(1).isdigit()
            else ENTITIES.get(m.group(1).lower(), m.group(0))
        ),
        label,
    )
    # Markdown emphasis and code spans: keep the text, drop the markers.
    label = re.sub(r"(\*\*|__)(.+?)\1", r"\2", label)
    label = re.sub(r"`([^`]*)`", r"\1", label)
    label = label.strip().strip("\"'").strip()
    return re.sub(r"\s+", " ", label)


def join_list(items: list[str], lang: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    last = " ve " if lang == "tr" else " and "
    return ", ".join(items[:-1]) + last + items[-1]


def fit(items: list[str], lang: str, template: str) -> str:
    """Fill ``template`` with as many items as fit in the alt-text budget."""
    for n in range(len(items), 0, -1):
        shown = items[:n]
        rest = len(items) - n
        if rest:
            shown = [*shown, f"{rest} öğe daha" if lang == "tr" else f"{rest} more"]
        text = template.format(join_list(shown, lang))
        if len(text) <= MAX_ALT:
            return text
    return template.format(items[0])[:MAX_ALT]


# --------------------------------------------------------------------------- diagram summaries

NODE = re.compile(
    r"""(?:^|[\s;>|&-])([A-Za-z_][\w]*)\s*
    (\[\[|\[\(|\(\[|\(\(|\{\{|\[/|\[\\|\[|\(|\{|>)
    (.+?)
    (\]\]|\)\]|\]\)|\)\)|\}\}|/\]|\\\]|\]|\)|\})""",
    re.X,
)
SUBGRAPH = re.compile(r"^\s*subgraph\s+(?:\w+\s*\[(.+?)\]|(.+))$", re.M)


def flowchart_nodes(src: str) -> list[str]:
    seen: dict[str, str] = {}
    for line in src.splitlines()[1:]:
        line = re.sub(r"\|[^|]*\|", " ", line)  # edge labels
        if re.match(r"\s*(subgraph|end\b|direction|classDef|class |style |linkStyle|click)", line):
            continue
        for m in NODE.finditer(line):
            seen.setdefault(m.group(1), clean_label(m.group(3)))
    return [v for v in dict.fromkeys(seen.values()) if v]


def sequence_participants(src: str) -> list[str]:
    names: dict[str, str] = {}
    for m in re.finditer(r"^\s*(?:participant|actor)\s+(\S+)(?:\s+as\s+(.+))?$", src, re.M):
        names.setdefault(m.group(1), clean_label(m.group(2) or m.group(1)))
    for m in re.finditer(r"^\s*([^\s:>-]+)\s*-[-)x>]*>>?[+-]?\s*([^\s:]+)\s*:", src, re.M):
        for p in m.groups():
            names.setdefault(p, clean_label(p))
    return list(dict.fromkeys(names.values()))


def state_names(src: str) -> list[str]:
    states: list[str] = []
    for m in re.finditer(r"^\s*(\S+)\s*-->\s*([^\s:]+)", src, re.M):
        for s in m.groups():
            if s != "[*]" and s not in states:
                states.append(s)
    return states


def git_branches(src: str) -> list[str]:
    branches = ["main"]
    for m in re.finditer(r"^\s*branch\s+(\S+)", src, re.M):
        if m.group(1) not in branches:
            branches.append(m.group(1))
    return branches


def alt_text(src: str, lang: str) -> str:
    """A plain-language summary of the diagram, 10-160 characters."""
    kind = src.split(None, 1)[0]
    tr = lang == "tr"
    if kind.startswith("sequenceDiagram"):
        items = sequence_participants(src)
        tpl = (
            "{} arasındaki çağrı sırasını gösteren sıra diyagramı"
            if tr
            else "Sequence diagram of the calls between {}"
        )
    elif kind.startswith("stateDiagram"):
        items = state_names(src)
        tpl = (
            "{} durumlarını ve aralarındaki geçişleri gösteren diyagram"
            if tr
            else "State diagram with the states {} and the transitions between them"
        )
    elif kind.startswith("gitGraph"):
        items = git_branches(src)
        tpl = (
            "{} branch'lerini ve commit'lerini gösteren Git geçmişi"
            if tr
            else "Git history showing the commits on {}"
        )
    else:
        items = flowchart_nodes(src) or [
            clean_label(s) for s in (m.group(1) or m.group(2) for m in SUBGRAPH.finditer(src))
        ]
        tpl = "{} arasındaki akışı gösteren şema" if tr else "Diagram of the flow between {}"
    items = [i for i in items if i] or (["şema"] if tr else ["diagram"])
    return fit(items, lang, tpl)


# --------------------------------------------------------------------------- convert


@dataclass
class Found:
    start: int
    end: int
    source: str
    heading: str | None


def find_blocks(text: str) -> list[Found]:
    out = []
    for m in BLOCK.finditer(text):
        heads = HEADING.findall(text[: m.start()])
        out.append(
            Found(
                m.start(),
                m.end(),
                m.group(1).strip() + "\n",
                heads[-1][1].strip() if heads else None,
            )
        )
    return out


def md_escape(text: str) -> str:
    return text.replace("[", "(").replace("]", ")").replace('"', "'")


def convert_bundle(bundle: str, dry_run: bool = False) -> int:
    """Replace the shortcodes of one bundle with image references. Returns the count."""
    files = {lang: CONTENT_DIR / lang / "posts" / bundle / "index.md" for lang in LANGS}
    texts = {lang: f.read_text(encoding="utf-8") for lang, f in files.items() if f.exists()}
    blocks = {lang: find_blocks(t) for lang, t in texts.items()}
    counts = {lang: len(b) for lang, b in blocks.items()}
    if not any(counts.values()):
        return 0
    if len(set(counts.values())) != 1 or len(counts) != len(LANGS):
        raise ScriptError(
            f"{bundle}: diagram counts differ between languages {counts}; convert by hand"
        )
    names: list[str] = []
    for _i, found in enumerate(blocks["en"]):
        base = slugify(found.heading or "") or slugify(bundle, 3) + "-overview"
        name, n = base, 2
        existing = {
            p.stem
            for lang in LANGS
            for p in (CONTENT_DIR / lang / "posts" / bundle / "img").glob("*.svg")
        }
        while name in names or name in existing:
            name, n = f"{base}-{n}", n + 1
        names.append(name)
    for lang in LANGS:
        text = texts[lang]
        title = TITLE.search(text)
        page_title = title.group(1) if title else bundle
        img_dir = CONTENT_DIR / lang / "posts" / bundle / "img"
        pairs = sorted(
            zip(blocks[lang], names, strict=True), key=lambda x: x[0].start, reverse=True
        )
        for found, name in pairs:
            alt = md_escape(alt_text(found.source, lang))
            caption = md_escape(found.heading or page_title)
            ref = f'![{alt}](img/{name}.svg "{caption}")'
            text = text[: found.start] + ref + text[found.end :]
            if not dry_run:
                write_text(img_dir / f"{name}.mmd", found.source)
        if not dry_run:
            write_text(files[lang], text)
    if not dry_run:
        for name in names:
            ensure_credit(bundle, name)
    log.info("%s: %d diagram(s) -> %s", bundle, len(names), ", ".join(names))
    return len(names)


# --------------------------------------------------------------------------- render


def ensure_credit(bundle: str, name: str) -> bool:
    """Add the ``kind: diagram`` entry for ``img/<name>.svg`` when it is missing."""
    media = CONTENT_DIR / "media" / "posts" / bundle
    file = f"img/{name}.svg"
    if any(e.get("file") == file for e in load(media / "credits.yaml")):
        return False
    append_credit(
        media,
        {
            "file": file,
            "kind": "diagram",
            "author": author_name(),
            "license": "own",
            "note": f"Rendered from img/{name}.mmd (Mermaid); one version per language.",
        },
    )
    log.info("credited %s in content/media/posts/%s/credits.yaml", file, bundle)
    return True


def stale_sources(bundles: list[str], force: bool) -> list[Path]:
    out = []
    for lang in LANGS:
        for mmd in sorted((CONTENT_DIR / lang / "posts").glob("*/img/*.mmd")):
            if bundles and mmd.parent.parent.name not in bundles:
                continue
            svg = mmd.with_suffix(".svg")
            if force or not svg.exists() or svg.stat().st_mtime < mmd.stat().st_mtime:
                out.append(mmd)
    return out


def render(sources: list[Path], batch: int = 25) -> None:
    """Render ``.mmd`` files to SVG cards next to them and credit each diagram."""
    from render import card_svg, mermaid_to_svg

    for i in range(0, len(sources), batch):
        chunk = sources[i : i + batch]
        ids = [f"mmd-{p.parents[3].name}-{p.stem}" for p in chunk]  # mmd-<lang>-<name>
        svgs = mermaid_to_svg([p.read_text(encoding="utf-8") for p in chunk], ids)
        for path, svg in zip(chunk, svgs, strict=True):
            write_text(path.with_suffix(".svg"), card_svg(svg))
            log.info("rendered %s", path.with_suffix(".svg").relative_to(REPO_ROOT).as_posix())
            ensure_credit(path.parent.parent.name, path.stem)


def check() -> int:
    problems = []
    for lang in LANGS:
        for f in sorted((CONTENT_DIR / lang / "posts").glob("*/index.md")):
            if BLOCK.search(f.read_text(encoding="utf-8")):
                problems.append(
                    f"{f.relative_to(REPO_ROOT).as_posix()}: client-side mermaid shortcode"
                )
        for mmd in sorted((CONTENT_DIR / lang / "posts").glob("*/img/*.mmd")):
            if not mmd.with_suffix(".svg").exists():
                problems.append(f"{mmd.relative_to(REPO_ROOT).as_posix()}: not rendered")
    for p in problems:
        log.error("%s", p)
    return 1 if problems else 0


def bundles_with_sources() -> list[str]:
    return sorted(
        {p.parent.name for lang in LANGS for p in (CONTENT_DIR / lang / "posts").glob("*/index.md")}
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "command",
        nargs="?",
        default="all",
        choices=["all", "convert", "render", "check"],
        help="what to do (default: all = convert, then render)",
    )
    ap.add_argument("bundles", nargs="*", help="limit to these bundles")
    ap.add_argument("--force", action="store_true", help="re-render every SVG")
    ap.add_argument("--dry-run", action="store_true", help="convert: print what would change")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)
    if a.command == "check":
        return check()
    try:
        if a.command in ("all", "convert"):
            total = sum(convert_bundle(b, a.dry_run) for b in (a.bundles or bundles_with_sources()))
            log.info("%d diagram(s) converted", total)
        if a.command in ("all", "render") and not a.dry_run:
            sources = stale_sources(a.bundles, a.force)
            if sources:
                render(sources)
            log.info("%d SVG(s) rendered", len(sources))
    except ScriptError as exc:
        log.error("%s", exc)
        return 2
    except (RuntimeError, ValueError, OSError) as exc:
        log.error("rendering failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
