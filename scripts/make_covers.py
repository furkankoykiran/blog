#!/usr/bin/env python3
"""Generate the branded cover image (feature.webp) of post bundles.

    python scripts/make_covers.py [BUNDLE ...] [--force] [--dry-run]

Each cover is 1600x900: an abstract artwork on the brand navy (soft light
fields in the category's colours, crossed by the two diagonal light lines of
the FK logo) with the post's category and title set on the left. The artwork is
seeded by the bundle name, so a rerun gives the same picture.

Input and output:

* title and category are read from ``content/{tr,en}/posts/<bundle>/index.md``;
* one cover per language is written to ``content/{tr,en}/posts/<bundle>/feature.webp``
  (an older shared cover in ``content/media/posts/<bundle>/`` is removed), and a
  ``kind: cover`` entry is added to the bundle's ``credits.yaml`` when missing.

Without bundle names every bundle that has no cover yet gets one.
Bundles with a custom ``cover:`` in their front matter are skipped. ``--force``
regenerates existing covers (not custom ones).

The artwork is drawn with HTML/CSS and captured with headless Chrome (found
automatically, or set ``CHROME``), then saved as WebP with Pillow.

Exit codes: 0 success, 1 a bundle has no Turkish post or rendering failed.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import logging
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from common import CONTENT_DIR, REPO_ROOT, add_logging_flags, author_name, setup_logging, write_text

sys.path.insert(0, str(Path(__file__).resolve().parent / "images"))
from _chrome import screenshot_file
from credits import append_credit, load

log = logging.getLogger("make_covers")

TR_POSTS = CONTENT_DIR / "tr" / "posts"
EN_POSTS = CONTENT_DIR / "en" / "posts"
MEDIA_POSTS = CONTENT_DIR / "media" / "posts"
W, H = 1600, 900

FONTS = REPO_ROOT / "assets" / "fonts"

# Category (as written in the TR file) -> three colours for the light fields.
PALETTES = {
    "MCP ve Yapay Zekâ": ["#7c5cff", "#2dd4bf", "#38bdf8"],
    "Açık Kaynak": ["#38bdf8", "#34d399", "#a3e635"],
    "Python": ["#facc15", "#3b82f6", "#22d3ee"],
    "Backend": ["#f472b6", "#6366f1", "#22d3ee"],
    "DevOps": ["#22d3ee", "#fb923c", "#6366f1"],
    "Trading": ["#fbbf24", "#ef4444", "#f97316"],
    "Web3": ["#a855f7", "#3b82f6", "#ec4899"],
    "Projeler": ["#2dd4bf", "#facc15", "#60a5fa"],
    "Deneyimler": ["#fb923c", "#ec4899", "#facc15"],
}
DEFAULT = ["#60a5fa", "#2dd4bf", "#a78bfa"]

TEMPLATE = """<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>
@font-face{{font-family:D;src:url("{fonts}/InterDisplay-Bold.ttf")}}
@font-face{{font-family:S;src:url("{fonts}/Inter-SemiBold.ttf")}}
@font-face{{font-family:M;src:url("{fonts}/IBMPlexMono-Medium.ttf")}}
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:#11151d}}
.f{{position:absolute;border-radius:50%;filter:blur({blur}px);mix-blend-mode:screen;opacity:.72}}
.g{{position:absolute;inset:0;background-size:36px 36px;
  background-image:radial-gradient(rgba(255,255,255,.07) 1px,transparent 1.4px)}}
.l{{position:absolute;left:50%;top:50%;width:{diag}px;height:2px;transform-origin:center;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.55) 35%,
    rgba(255,255,255,.55) 65%,transparent)}}
.v{{position:absolute;inset:0;
  background:radial-gradient(ellipse at {vx}% {vy}%,transparent 35%,rgba(17,21,29,.85) 100%)}}
.n{{position:absolute;inset:0;opacity:.18;mix-blend-mode:overlay}}
.s{{position:absolute;inset:0;background:linear-gradient(90deg,rgba(17,21,29,.92) 0%,
  rgba(17,21,29,.78) 48%,rgba(17,21,29,.15) 82%,transparent)}}
.t{{position:absolute;left:96px;top:0;bottom:0;width:1060px;display:flex;flex-direction:column;
  justify-content:center;gap:34px;color:#f5f6f8}}
.k{{font:500 30px/1 M;letter-spacing:.14em;text-transform:uppercase;
  color:color-mix(in srgb,{accent} 65%,#fff)}}
.h{{font:700 {size}px/1.12 D;letter-spacing:-.01em;margin:0;text-wrap:balance}}
.b{{position:absolute;left:96px;bottom:72px;font:600 28px/1 S;color:#aeb6c6}}
</style></head><body>
<div class="g"></div>
{fields}
<div class="l"
  style="transform:translate(-50%,-50%) translate({lx}px,{ly}px) rotate({a1}deg)"></div>
<div class="l"
  style="transform:translate(-50%,-50%) translate({lx}px,{ly}px) rotate({a2}deg)"></div>
<div class="v"></div>
<svg class="n" width="{w}" height="{h}"><filter id="t">
<feTurbulence type="fractalNoise" baseFrequency=".9" numOctaves="2" seed="{seed}"/></filter>
<rect width="100%" height="100%" filter="url(#t)"/></svg>
<div class="s"></div>
<div class="t"><div class="k" lang="{kicker_lang}">{kicker}</div><h1 class="h">{title}</h1></div>
<div class="b">{site}</div>
</body></html>"""


def front_matter(path: Path) -> str:
    if not path.exists():
        return ""
    m = re.match(r"---\r?\n(.*?)\r?\n---", path.read_text(encoding="utf-8"), re.S)
    return m.group(1) if m else ""


def category(fm: str) -> str:
    m = re.search(r'^categories:\s*\[\s*"([^"]+)"', fm, re.M)
    return m.group(1) if m else ""


def title(fm: str) -> str:
    m = re.search(r'^title:\s*"((?:[^"\\]|\\.)*)"', fm, re.M) or re.search(
        r"^title:\s*(.+)$", fm, re.M
    )
    return m.group(1).replace('\\"', '"').strip() if m else ""


def title_size(text: str) -> int:
    """Font size that keeps the title within about three lines."""
    n = len(text)
    return 96 if n <= 36 else 84 if n <= 56 else 72 if n <= 80 else 62


def has_custom_cover(bundle: str) -> bool:
    return any(
        re.search(r'^cover:\s*"?[^"\s]+', front_matter(d / bundle / "index.md"), re.M)
        for d in (TR_POSTS, EN_POSTS)
    )


def html_for(name: str, colours: list[str], heading: str, kicker: str, lang: str) -> str:
    """The cover for ``name`` as an HTML page (the artwork is deterministic per name)."""
    rnd = random.Random(int(hashlib.sha256(name.encode()).hexdigest(), 16))
    fields = []
    for i in range(rnd.randint(4, 6)):
        size = rnd.uniform(0.35, 0.7) * W
        x = rnd.uniform(-0.15, 0.85) * W
        y = rnd.uniform(-0.2, 0.75) * H
        fields.append(
            f'<div class="f" style="left:{x:.0f}px;top:{y:.0f}px;width:{size:.0f}px;'
            f"height:{size * rnd.uniform(0.45, 0.8):.0f}px;background:{colours[i % len(colours)]};"
            f'transform:rotate({rnd.uniform(-35, 35):.0f}deg)"></div>'
        )
    spread = rnd.uniform(28, 46)
    return TEMPLATE.format(
        w=W,
        h=H,
        blur=rnd.randint(90, 130),
        diag=int((W**2 + H**2) ** 0.5) + 200,
        fields="\n".join(fields),
        lx=rnd.randint(-260, 260),
        ly=rnd.randint(-120, 120),
        a1=f"{spread:.1f}",
        a2=f"{-spread:.1f}",
        vx=rnd.randint(40, 60),
        vy=rnd.randint(40, 60),
        seed=rnd.randint(1, 999),
        lang=lang,
        fonts=FONTS.as_uri(),
        accent=colours[0],
        size=title_size(heading),
        kicker=html.escape(kicker),
        # English labels such as "Trading" keep a dotless capital I in Turkish pages.
        kicker_lang="en" if kicker.isascii() else lang,
        # Non-breaking hyphens keep words like "Self-Hosted" on one line.
        title=html.escape(heading).replace("-", "‑"),
        site="blog.furkankoykiran.com.tr",
    )


def render(name: str, colours: list[str], heading: str, kicker: str, lang: str, out: Path) -> None:
    """Draw the cover for ``name`` in ``lang`` and save it as WebP at ``out``."""
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "cover.html"
        png = Path(tmp) / "cover.png"
        write_text(page, html_for(name, colours, heading, kicker, lang))
        screenshot_file(page, png, W, H)
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(png) as im:
            im.convert("RGB").save(out, "WEBP", quality=86, method=6)


def ensure_credit(media_dir: Path) -> None:
    """Add the ``kind: cover`` entry to the bundle's credits.yaml when it is missing."""
    if not any(e.get("file") == "feature.webp" for e in load(media_dir / "credits.yaml")):
        append_credit(
            media_dir,
            {
                "file": "feature.webp",
                "kind": "cover",
                "author": author_name(),
                "license": "own",
                "note": "Generated by scripts/make_covers.py from the post title and category.",
            },
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "bundles", nargs="*", help="bundle names (default: every bundle without a cover)"
    )
    ap.add_argument("--force", action="store_true", help="regenerate existing generated covers")
    ap.add_argument("--dry-run", action="store_true", help="only list what would be generated")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)

    names = a.bundles or sorted(p.parent.name for p in TR_POSTS.glob("*/index.md"))
    made = 0
    for name in names:
        tr = TR_POSTS / name / "index.md"
        if not tr.exists():
            log.error("%s: no Turkish post at %s", name, tr.relative_to(REPO_ROOT).as_posix())
            return 1
        if has_custom_cover(name):
            log.info("%s: custom cover in front matter, skipped", name)
            continue
        colours = PALETTES.get(category(front_matter(tr)), DEFAULT)
        for lang, posts in (("tr", TR_POSTS), ("en", EN_POSTS)):
            fm = front_matter(posts / name / "index.md")
            out = posts / name / "feature.webp"
            if not fm or (out.exists() and not a.force):
                continue
            rel = out.relative_to(REPO_ROOT).as_posix()
            log.info("%s: %s", "would write" if a.dry_run else "writing", rel)
            if not a.dry_run:
                try:
                    render(name, colours, title(fm), category(fm), lang, out)
                except (OSError, subprocess.SubprocessError) as exc:
                    log.error("%s: rendering failed: %s", name, exc)
                    return 1
            made += 1
        if not a.dry_run:
            (MEDIA_POSTS / name / "feature.webp").unlink(missing_ok=True)
            ensure_credit(MEDIA_POSTS / name)
    log.info("%d cover(s) %s", made, "planned" if a.dry_run else "written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
