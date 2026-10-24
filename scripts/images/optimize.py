#!/usr/bin/env python3
"""Normalise images for a post bundle: limited width, no metadata, WebP.

    python scripts/images/optimize.py IN [IN ...] [--out DIR] [--max-width 1600]
                                      [--quality 82] [--lossless]

* Raster inputs (PNG, JPEG, WebP, the first frame of a GIF) become ``<name>.webp``
  next to the input or in ``--out``. EXIF, XMP and ICC data are dropped, which
  also removes GPS positions and device names.
* SVG inputs are only checked: size budget, no ``<script>`` or event handlers,
  no external references, a ``viewBox``.
* One line per file is printed: path, size, dimensions and verdict.

Exit code 1 when a file breaks the budget (raster over 400 KB, SVG over 100 KB)
or an SVG is unsafe.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from common import add_logging_flags, setup_logging

log = logging.getLogger("optimize")

RASTER_BUDGET = 400 * 1024
SVG_BUDGET = 100 * 1024


def to_webp(
    src: pathlib.Path,
    dst: pathlib.Path,
    max_width: int = 1600,
    quality: int = 82,
    lossless: bool = False,
) -> pathlib.Path:
    """Convert ``src`` to a metadata-free WebP at ``dst``, downscaled to ``max_width``."""
    from PIL import Image

    with Image.open(src) as im:
        if getattr(im, "is_animated", False):
            im.seek(0)
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        out = im.convert("RGBA" if has_alpha else "RGB")
    if out.width > max_width:
        out = out.resize((max_width, round(out.height * max_width / out.width)), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # No exif= or icc_profile= arguments: Pillow then writes no metadata at all.
    out.save(dst, "WEBP", quality=quality, method=6, lossless=lossless)
    return dst


def is_blank(path: pathlib.Path) -> bool:
    """True when a raster image is a single flat colour (a failed capture)."""
    from PIL import Image

    with Image.open(path) as im:
        lo, hi = zip(*im.convert("RGB").getextrema(), strict=True)
    return max(hi) - min(lo) <= 2 and all(h - lo_ <= 2 for lo_, h in zip(lo, hi, strict=True))


def check_svg(path: pathlib.Path) -> list[str]:
    """Return the problems found in an SVG file (empty list when it is fine)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    problems = []
    if path.stat().st_size > SVG_BUDGET:
        problems.append(f"over {SVG_BUDGET // 1024} KB")
    if re.search(r"<script|\son\w+\s*=", text, re.I):
        problems.append("contains a script or an event handler")
    if re.search(r"(href|src)\s*=\s*[\"']https?://", text, re.I):
        problems.append("references external resources")
    if "viewBox" not in text:
        problems.append("no viewBox (will not scale)")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert images to metadata-free WebP and check SVGs.")
    ap.add_argument("inputs", nargs="+", type=pathlib.Path, help="image files")
    ap.add_argument(
        "--out", type=pathlib.Path, help="output directory (default: next to the input)"
    )
    ap.add_argument(
        "--max-width", type=int, default=1600, help="downscale wider images (default 1600)"
    )
    ap.add_argument("--quality", type=int, default=82, help="WebP quality (default 82)")
    ap.add_argument(
        "--lossless", action="store_true", help="lossless WebP, for screenshots with small text"
    )
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)

    from PIL import Image

    bad = 0
    for src in a.inputs:
        if src.suffix.lower() == ".svg":
            problems = check_svg(src)
            bad += bool(problems)
            log.info(
                "%s  %d KB  svg  %s", src, src.stat().st_size // 1024, "; ".join(problems) or "ok"
            )
            continue
        dst = (a.out or src.parent) / (src.stem + ".webp")
        if dst.resolve() == src.resolve():
            dst = dst.with_name(src.stem + ".opt.webp")
        to_webp(src, dst, a.max_width, a.quality, a.lossless)
        size = dst.stat().st_size
        with Image.open(dst) as im:
            w, h = im.size
        over = size > RASTER_BUDGET
        bad += over
        log.info(
            "%s  %d KB  %dx%d  %s",
            dst,
            size // 1024,
            w,
            h,
            f"over {RASTER_BUDGET // 1024} KB" if over else "ok",
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
