#!/usr/bin/env python3
"""Fetch an official product logo (SVG) from thesvg.org and record its credit.

    python scripts/images/logo.py --search redis
    python scripts/images/logo.py SLUG BUNDLE_MEDIA_DIR [--variant default|mono|light|dark|wordmark]

thesvg.org is a static site without authentication:

* ``GET https://thesvg.org/icons/{slug}/{variant}.svg`` returns the file;
* ``GET https://thesvg.org/api/registry.json`` lists every icon with its title,
  variants, licence and brand-guidelines URL (about 2 MB, cached for a week).

The registry licence (usually CC0-1.0) covers the SVG file. The mark itself stays
its owner's trademark: use it only to identify the product the post discusses,
unmodified, never as the hero image and never in a way that suggests endorsement.
Read the guidelines URL when the registry lists one.

Writes ``<BUNDLE_MEDIA_DIR>/img/logo-<slug>.svg`` and adds an entry to
``<BUNDLE_MEDIA_DIR>/credits.yaml``.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import tempfile
import time
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import add_logging_flags, setup_logging, user_agent
from credits import append_credit
from optimize import check_svg

log = logging.getLogger("logo")

BASE = "https://thesvg.org"
USER_AGENT = {"User-Agent": user_agent("blog-images")}
CACHE = pathlib.Path(tempfile.gettempdir()) / "thesvg-registry.json"
CACHE_SECONDS = 7 * 86400


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=USER_AGENT)
    with urllib.request.urlopen(req, timeout=60) as r:
        return bytes(r.read())


def registry() -> dict[str, dict[str, Any]]:
    """Return the thesvg.org registry keyed by slug, using a local cache."""
    if not CACHE.exists() or time.time() - CACHE.stat().st_mtime > CACHE_SECONDS:
        log.debug("downloading the registry")
        CACHE.write_bytes(fetch(f"{BASE}/api/registry.json"))
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    return {i["slug"]: i for i in data["icons"]}


def search(query: str) -> list[dict[str, Any]]:
    q = query.lower()
    return [
        i
        for s, i in registry().items()
        if q in s or q in i["title"].lower() or any(q in x.lower() for x in i.get("aliases", []))
    ]


def download(slug: str, media_dir: pathlib.Path, variant: str = "default") -> pathlib.Path:
    """Save the logo into the bundle and record the credit. Returns the SVG path."""
    item = registry().get(slug)
    if not item:
        raise KeyError(f"unknown slug {slug!r}; try --search")
    if variant not in item["variants"]:
        raise ValueError(f"variant {variant!r} not in {item['variants']}")
    url = f"{BASE}/icons/{slug}/{variant}.svg"
    out = media_dir / "img" / f"logo-{slug}.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(fetch(url))
    for problem in check_svg(out):
        log.warning("%s: %s", out, problem)
    append_credit(
        media_dir,
        {
            "file": f"img/{out.name}",
            "kind": "logo",
            "source": url,
            "author": item["title"] + " (trademark owner)",
            "license": item.get("license") or "unknown",
            "license_url": item.get("guidelines") or item.get("url"),
            "note": "Trademark of its owner; used only to identify the product.",
        },
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Download a product logo from thesvg.org into a post bundle."
    )
    ap.add_argument("slug", nargs="?", help="thesvg.org icon slug, e.g. redis")
    ap.add_argument("media_dir", nargs="?", type=pathlib.Path, help="content/media/posts/<bundle>")
    ap.add_argument("--variant", default="default", help="icon variant (default: default)")
    ap.add_argument("--search", metavar="TEXT", help="list matching icons and exit")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)
    if a.search:
        for i in search(a.search):
            log.info(
                "%-30s %-30s %-30s license=%s",
                i["slug"],
                i["title"],
                ",".join(i["variants"]),
                i.get("license"),
            )
        return 0
    if not (a.slug and a.media_dir):
        ap.error("SLUG and BUNDLE_MEDIA_DIR are required (or use --search)")
    log.info("%s", download(a.slug, a.media_dir, a.variant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
