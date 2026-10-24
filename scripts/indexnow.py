#!/usr/bin/env python3
"""Submit new, changed and removed URLs to IndexNow after a deploy.

The steps run in separate CI jobs and share state through a directory
(``--state-dir``, passed between jobs as an artifact):

``snapshot``  before building: record the live sitemap (URL -> lastmod)
``diff``      after building: compare with the sitemaps in ``public/``
``submit``    after deploying: send the changed URLs to api.indexnow.org

The host defaults to ``SITE_HOST`` or the ``baseURL`` in hugo.toml. The key
comes from ``INDEXNOW_KEY``; a matching ``static/<key>.txt`` must be deployed.
Network failures are logged and never fail the deploy (exit 0).

Exit codes: 0 success or skipped, 1 unexpected error, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from common import REPO_ROOT, ScriptError, add_logging_flags, setup_logging, site_host

log = logging.getLogger("indexnow")

ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10_000  # IndexNow limit per request
URL_RE = re.compile(r"<url>(.*?)</url>", re.S)
LOC_RE = re.compile(r"<loc>(.*?)</loc>")
LASTMOD_RE = re.compile(r"<lastmod>(.*?)</lastmod>")
USER_AGENT = "indexnow-submit/1.0 (+https://www.indexnow.org/)"


def parse_urls(xml: str, out: dict[str, str]) -> None:
    for block in URL_RE.findall(xml):
        loc = LOC_RE.search(block)
        if loc:
            lastmod = LASTMOD_RE.search(block)
            out[loc.group(1).strip()] = lastmod.group(1).strip() if lastmod else ""


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        # Expected on the very first deploy, when the site does not exist yet.
        log.warning("could not fetch %s: %s", url, exc)
        return ""


def cmd_snapshot(args: argparse.Namespace) -> int:
    seen: set[str] = set()
    urls: dict[str, str] = {}
    stack = [f"https://{args.host}/sitemap.xml"]
    while stack:
        url = stack.pop()
        if url in seen:
            continue
        seen.add(url)
        xml = fetch(url)
        if "<sitemapindex" in xml:
            stack += [u for u in LOC_RE.findall(xml) if args.host in u]
        parse_urls(xml, urls)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    (args.state_dir / "old.json").write_text(json.dumps(urls, indent=0), encoding="utf-8")
    log.info("live sitemap: %d URLs", len(urls))
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    old_file = args.state_dir / "old.json"
    old = json.loads(old_file.read_text(encoding="utf-8")) if old_file.is_file() else {}
    if not args.public_dir.is_dir():
        raise ScriptError(f"{args.public_dir} does not exist; build the site first")
    new: dict[str, str] = {}
    for f in sorted(args.public_dir.rglob("sitemap*.xml")):
        parse_urls(f.read_text(encoding="utf-8"), new)
    changed = sorted(u for u, v in new.items() if old.get(u) != v)
    removed = sorted(u for u in old if u not in new)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    (args.state_dir / "changed.txt").write_text("\n".join(changed + removed), encoding="utf-8")
    log.info("%d new or changed, %d removed", len(changed), len(removed))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    if not args.key:
        log.info("INDEXNOW_KEY is not set; skipping")
        return 0
    path = args.state_dir / "changed.txt"
    urls = path.read_text(encoding="utf-8").split() if path.is_file() else []
    urls = [u for u in urls if u.startswith(f"https://{args.host}/")][:MAX_URLS]
    if not urls:
        log.info("nothing to submit")
        return 0
    body = json.dumps(
        {
            "host": args.host,
            "key": args.key,
            "keyLocation": f"https://{args.host}/{args.key}.txt",
            "urlList": urls,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            log.info("IndexNow answered %s for %d URLs", res.status, len(urls))
    except urllib.error.HTTPError as exc:
        log.warning("IndexNow rejected the submission: HTTP %s %s", exc.code, exc.reason)
    except (urllib.error.URLError, TimeoutError) as exc:
        log.warning("IndexNow unreachable: %s", exc)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tell IndexNow which URLs changed in a deploy.")
    parser.add_argument("--host", help="site host (default: $SITE_HOST or baseURL in hugo.toml)")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("INDEXNOW_STATE_DIR", ".indexnow")),
        help="directory shared between the steps (default: %(default)s)",
    )
    add_logging_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot", help="record the live sitemap").set_defaults(func=cmd_snapshot)
    p_diff = sub.add_parser("diff", help="compare the built sitemaps with the snapshot")
    p_diff.add_argument(
        "--public-dir",
        type=Path,
        default=REPO_ROOT / "public",
        help="Hugo output directory (default: %(default)s)",
    )
    p_diff.set_defaults(func=cmd_diff)
    p_submit = sub.add_parser("submit", help="send the changed URLs to IndexNow")
    p_submit.add_argument(
        "--key",
        default=os.environ.get("INDEXNOW_KEY", ""),
        help="IndexNow key (default: $INDEXNOW_KEY)",
    )
    p_submit.set_defaults(func=cmd_submit)

    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    try:
        args.host = args.host or site_host()
        return args.func(args)
    except ScriptError as exc:
        log.error("%s", exc)
        return 2
    except Exception:
        log.exception("unexpected error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
