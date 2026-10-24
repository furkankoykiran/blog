#!/usr/bin/env python3
"""Check that every internal link and asset reference in a Hugo build resolves.

    hugo --gc --minify && python scripts/check_links.py [--public DIR]

Every ``href``, ``src``, ``srcset`` and ``imagesrcset`` in the built HTML that
points at this site (a root-relative path, a relative path or an absolute URL on
the site's host) must name a file in the output directory: ``/path/`` resolves
to ``/path/index.html``. Fragments must match an ``id`` on the target page.
External links are not fetched.

Exit codes: 0 every link resolves, 1 broken links were found, 2 usage error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from common import REPO_ROOT, ScriptError, add_logging_flags, base_url, setup_logging

log = logging.getLogger("check_links")

URL_ATTRS = {"href", "src", "poster", "data-src"}
SRCSET_ATTRS = {"srcset", "imagesrcset"}
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "#")


class PageParser(HTMLParser):
    """Collects the link targets and element ids of one HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if not value:
                continue
            if name == "id":
                self.ids.add(value)
            elif name in URL_ATTRS and not (tag == "link" and self._is_preconnect(attrs)):
                self.links.append(value.strip())
            elif name in SRCSET_ATTRS:
                self.links += [c.strip().split()[0] for c in value.split(",") if c.strip()]

    @staticmethod
    def _is_preconnect(attrs: list[tuple[str, str | None]]) -> bool:
        rel = dict(attrs).get("rel") or ""
        return any(r in rel for r in ("preconnect", "dns-prefetch"))


def page_url(public: Path, html: Path) -> str:
    """Site path of an output file, e.g. ``/posts/x/`` for ``posts/x/index.html``."""
    rel = html.relative_to(public).as_posix()
    if rel == "index.html":
        return "/"
    if rel.endswith("/index.html"):
        return "/" + rel.removesuffix("index.html")
    return "/" + rel


def target_file(public: Path, path: str) -> Path:
    path = unquote(path)
    target = public / path.lstrip("/")
    return target / "index.html" if path.endswith("/") or target.is_dir() else target


def check(public: Path, origin: str) -> dict[str, list[str]]:
    """Return broken targets mapped to the pages that link to them."""
    host = urlsplit(origin).netloc
    parsed: dict[Path, PageParser] = {}
    for html in sorted(public.rglob("*.html")):
        parser = PageParser()
        parser.feed(html.read_text(encoding="utf-8", errors="replace"))
        parsed[html] = parser
    broken: dict[str, list[str]] = defaultdict(list)
    for html, parser in parsed.items():
        here = page_url(public, html)
        for link in parser.links:
            if link.startswith(SKIP_SCHEMES):
                continue
            url = urlsplit(urljoin(origin + here, link))
            if url.scheme not in ("http", "https") or url.netloc != host:
                continue
            target = target_file(public, url.path or "/")
            if not target.is_file():
                broken[url.path].append(here)
                continue
            if url.fragment and target.suffix == ".html":
                ids = parsed.get(target)
                if ids is not None and unquote(url.fragment) not in ids.ids:
                    broken[f"{url.path}#{url.fragment}"].append(here)
    return broken


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--public",
        type=Path,
        default=REPO_ROOT / "public",
        help="Hugo output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--base-url", help="URL the site was built for (default: baseURL in hugo.toml)"
    )
    add_logging_flags(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    try:
        if not args.public.is_dir():
            raise ScriptError(f"{args.public} does not exist; build the site first")
        origin = (args.base_url or base_url()).rstrip("/")
    except ScriptError as exc:
        log.error("%s", exc)
        return 2
    broken = check(args.public, origin)
    for target, pages in sorted(broken.items()):
        log.error("%s (linked from %d page(s), e.g. %s)", target, len(pages), pages[0])
    pages = sum(1 for _ in args.public.rglob("*.html"))
    log.info("%d HTML files checked, %d broken link target(s)", pages, len(broken))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
