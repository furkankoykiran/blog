#!/usr/bin/env python3
"""Check that every URL of the previous blog still behaves as intended in a Hugo build.

    hugo --gc --minify && python scripts/check-old-urls.py [--public DIR] [--urls FILE]

``data/legacy_urls.json`` lists the old URLs:

* ``must_resolve``: path -> expected target (or null). The path must be a real page,
  a file, or a Hugo alias whose redirect target is a real page; when a target is
  given, the alias must point exactly there.
* ``must_be_gone``: pages removed on purpose. They must not resolve (404).

Exit codes: 0 every URL behaves, 1 at least one failure, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from common import REPO_ROOT, ScriptError, add_logging_flags, base_url, setup_logging

log = logging.getLogger("check-old-urls")

REFRESH = re.compile(r'http-equiv="?refresh"?\s+content="0;\s*url=([^"]+)"', re.I)
Row = tuple[str, str, str]


def path_of(url: str, origin: str) -> str:
    """Site path of an absolute or relative URL."""
    path = url.removeprefix(origin)
    return path if path.startswith("/") else "/" + path


def resolve(public: Path, path: str, origin: str) -> tuple[str | None, str | None]:
    """Return (kind, alias target) where kind is 'page', 'alias', 'file' or None."""
    target = public / path.lstrip("/")
    if path.endswith("/"):
        target = target / "index.html"
    if not target.is_file():
        return None, None
    if target.suffix != ".html":
        return "file", None
    match = REFRESH.search(target.read_text(encoding="utf-8", errors="replace")[:3000])
    if match:
        return "alias", path_of(match.group(1), origin)
    return "page", None


def check(
    public: Path, must_resolve: dict[str, str | None], must_be_gone: list[str], origin: str
) -> list[Row]:
    """Check every URL; returns (status, url, note) rows with status OK, GONE or FAIL."""
    rows: list[Row] = []
    for url in sorted(must_be_gone):
        kind, _ = resolve(public, url, origin)
        if kind is None:
            rows.append(("GONE", url, "removed on purpose, 404 as intended"))
        else:
            rows.append(("FAIL", url, f"should 404 but resolves ({kind})"))
    for url, expected in sorted(must_resolve.items()):
        kind, target = resolve(public, url, origin)
        if kind is None:
            rows.append(("FAIL", url, "missing"))
        elif kind == "alias":
            target_kind, _ = resolve(public, target or "", origin)
            if target_kind != "page":
                rows.append(("FAIL", url, f"alias -> {target}, which is not a page"))
            elif expected and expected != target:
                rows.append(("FAIL", url, f"alias -> {target}, expected {expected}"))
            else:
                rows.append(("OK", url, f"alias -> {target}"))
        elif expected and expected != url:
            rows.append(("FAIL", url, f"is a {kind}, expected a redirect to {expected}"))
        else:
            rows.append(("OK", url, kind))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--public",
        type=Path,
        default=REPO_ROOT / "public",
        help="Hugo output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--urls",
        type=Path,
        default=REPO_ROOT / "data" / "legacy_urls.json",
        help="list of old URLs (default: %(default)s)",
    )
    add_logging_flags(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    try:
        if not args.public.is_dir():
            raise ScriptError(f"{args.public} does not exist; build the site first")
        data = json.loads(args.urls.read_text(encoding="utf-8"))
        rows = check(
            args.public, data["must_resolve"], data["must_be_gone"], base_url().rstrip("/")
        )
    except (ScriptError, OSError, KeyError, json.JSONDecodeError) as exc:
        log.error("%s", exc)
        return 2
    for status, url, note in rows:
        if status == "FAIL":
            log.error("%-5s %-75s %s", status, url, note)
        else:
            log.debug("%-5s %-75s %s", status, url, note)
    count = {s: sum(r[0] == s for r in rows) for s in ("OK", "GONE", "FAIL")}
    log.info(
        "%d old URLs checked: %d resolve, %d intentionally gone, %d failing",
        len(rows),
        count["OK"],
        count["GONE"],
        count["FAIL"],
    )
    return 1 if count["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
