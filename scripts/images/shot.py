#!/usr/bin/env python3
"""Screenshot a real page (repo, PR, issue, docs, a local tool UI) for a post.

    python scripts/images/shot.py URL OUT.webp [--selector CSS] [--pad 16]
        [--width 1280] [--height 800] [--scale 2] [--dark] [--full-page]
        [--hide CSS ...] [--preset github|docs] [--wait 1.5]
        [--media-dir DIR --license quotation|own --author "..."]

Examples::

    # PR header (title, state, branches, diffstat). GitHub's React UI uses hashed
    # class names, so match on the stable prefix.
    shot.py https://github.com/keploy/keploy/pull/3927 img/pr-3927.webp \\
        --preset github --selector "[class*='prc-PageLayout-Header']" --pad 8
    # README of a repository
    shot.py https://github.com/furkankoykiran/freqtrade-mcp img/readme.webp \\
        --preset github --selector "article.markdown-body" --height 1400
    # a tool running on this machine
    shot.py http://localhost:3001/dashboard img/dashboard.webp --width 1440 --height 900

The capture always uses a fresh, empty browser profile, so no account UI,
notifications or private repositories can appear in it. With ``--selector`` the
element is scrolled into view (the viewport grows to fit tall elements) and its
images are loaded before the capture. The result is saved as WebP without
metadata. Check every capture for tokens, e-mail addresses and host names
before committing it.

Exit codes: 0 saved, 1 the capture failed or came out blank.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _chrome import Chrome
from common import add_logging_flags, setup_logging
from credits import append_credit
from optimize import is_blank, to_webp

log = logging.getLogger("shot")

PRESETS: dict[str, list[str]] = {
    # GitHub's global chrome: sign-up banners, cookie notice, header, footer.
    "github": [
        "header.AppHeader",
        "header.HeaderMktg",
        ".js-header-wrapper",
        "footer",
        "#cookie-consent-banner",
        ".flash-full",
        ".signup-prompt",
        "[data-testid='sign-up-banner']",
        ".js-notification-shelf",
        "#repository-container-header .UnderlineNav",
    ],
    "docs": ["header", "footer", "[class*='cookie']", "[id*='cookie']", "[class*='banner']"],
}


def capture(
    url: str,
    out: pathlib.Path,
    selector: str | None = None,
    pad: int = 16,
    width: int = 1280,
    height: int = 800,
    scale: float = 2.0,
    dark: bool = False,
    full_page: bool = False,
    hide: list[str] | None = None,
    wait: float = 1.5,
    max_width: int = 1600,
    lossless: bool = False,
) -> pathlib.Path:
    """Capture ``url`` (optionally cropped to ``selector``) and save it as WebP at ``out``."""
    with tempfile.TemporaryDirectory() as tmp:
        png = pathlib.Path(tmp) / "shot.png"
        with Chrome(width, height, scale, dark) as chrome:
            chrome.goto(url, wait)
            if hide:
                chrome.add_css(",".join(hide) + "{display:none!important}")
            clip = chrome.element_clip(selector, pad) if selector else None
            chrome.screenshot(png, clip=clip, full_page=full_page)
        if is_blank(png):
            raise RuntimeError(f"the capture of {url} is a single flat colour")
        return to_webp(png, out, max_width=max_width, quality=88, lossless=lossless)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("out", type=pathlib.Path, help="output .webp path")
    ap.add_argument("--selector", help="crop to the first element matching this CSS selector")
    ap.add_argument("--pad", type=int, default=16, help="padding around --selector in CSS pixels")
    ap.add_argument("--width", type=int, default=1280, help="viewport width (default 1280)")
    ap.add_argument("--height", type=int, default=800, help="viewport height (default 800)")
    ap.add_argument("--scale", type=float, default=2.0, help="device pixel ratio (default 2)")
    ap.add_argument("--dark", action="store_true", help="emulate prefers-color-scheme: dark")
    ap.add_argument("--full-page", action="store_true")
    ap.add_argument("--hide", nargs="*", default=[], help="CSS selectors to hide before capturing")
    ap.add_argument("--preset", choices=sorted(PRESETS), help="selectors to hide for a known site")
    ap.add_argument("--wait", type=float, default=1.5, help="seconds to wait after load")
    ap.add_argument(
        "--max-width", type=int, default=1600, help="downscale the saved image (default 1600)"
    )
    ap.add_argument("--lossless", action="store_true", help="lossless WebP for small text")
    ap.add_argument(
        "--media-dir",
        type=pathlib.Path,
        help="bundle media dir: record the capture in its credits.yaml",
    )
    ap.add_argument("--license", default="quotation", help="credits licence (quotation or own)")
    ap.add_argument("--author", help="credits author, e.g. 'GitHub / keploy contributors'")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)

    try:
        out = capture(
            a.url,
            a.out,
            a.selector,
            a.pad,
            a.width,
            a.height,
            a.scale,
            a.dark,
            a.full_page,
            list(a.hide) + PRESETS.get(a.preset or "", []),
            a.wait,
            a.max_width,
            a.lossless,
        )
    except (LookupError, RuntimeError, FileNotFoundError, OSError) as exc:
        log.error("%s", exc)
        return 1
    if a.media_dir:
        rel = out.resolve().relative_to(a.media_dir.resolve()).as_posix()
        append_credit(
            a.media_dir,
            {
                "file": rel,
                "kind": "screenshot",
                "source": a.url,
                "author": a.author,
                "license": a.license,
            },
        )
    log.info("%s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
