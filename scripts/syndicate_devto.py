#!/usr/bin/env python3
"""Cross-post English blog posts to dev.to with the blog as the canonical URL.

    python scripts/syndicate_devto.py [--dry-run] [--site URL] [--feed URL] [--state FILE]

Reads ``/en/syndication.json`` from the deployed site (so only posts that are
live are considered; Hugo builds it from ``layouts/home.syndication.json``) and
keeps the dev.to article ids in ``.syndication/state.json``, which the workflow
commits back. A post is published when:

* it is not a draft and its ``syndicate`` flag is not false;
* its date is on or after ``SYNDICATE_SINCE``, so older posts are not mass-posted;
* it is at least ``SYNDICATION_DELAY_DAYS`` old, so search engines index the
  original first.

Already-published posts are updated when their content changes. Diagrams are
static SVG images with absolute URLs, so they show on dev.to as on the blog.

Environment:

``DEVTO_API_KEY``            required unless ``--dry-run``
``SITE_URL``                 default: ``baseURL`` in hugo.toml (or ``--site``)
``SYNDICATION_DELAY_DAYS``   default 3
``SYNDICATE_SINCE``          default 2026-10-25 (ISO date)

Exit codes: 0 success or nothing to do, 1 at least one post failed, 2 the feed
could not be read or a setting is invalid.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from common import (
    REPO_ROOT,
    ScriptError,
    add_logging_flags,
    base_url,
    portfolio_url,
    setup_logging,
    user_agent,
    write_text,
)

log = logging.getLogger("syndicate_devto")

API = "https://dev.to/api"
DEFAULT_SINCE = "2026-10-25"
DEFAULT_DELAY_DAYS = "3"
STATE_FILE = REPO_ROOT / ".syndication" / "state.json"
WRITE_PAUSE_SECONDS = 4  # stays well under dev.to's write rate limit

# Blog tags -> dev.to tags (dev.to allows at most 4, lower-case letters and digits only).
TAG_MAP = {
    "ai-agents": "ai",
    "github-actions": "githubactions",
    "ci-cd": "cicd",
    "open-source": "opensource",
    "llm": "ai",
}
ALERT_LABELS = {
    "NOTE": "Note",
    "TIP": "Tip",
    "IMPORTANT": "Important",
    "WARNING": "Warning",
    "CAUTION": "Caution",
}
UTM = "?utm_source=devto&utm_medium=syndication&utm_campaign=crosspost"


class DevTo:
    """Minimal dev.to API client that retries on rate limits."""

    def __init__(self, key: str) -> None:
        self.key = key

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{API}{path}",
            data=data,
            method=method,
            headers={
                "api-key": self.key,
                "Content-Type": "application/json",
                "Accept": "application/vnd.forem.api-v1+json",
                "User-Agent": user_agent("blog-syndication"),
            },
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as res:
                    return json.loads(res.read() or b"null")
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 4:
                    time.sleep(int(exc.headers.get("Retry-After", "30")))
                    continue
                raise RuntimeError(
                    f"{method} {path}: HTTP {exc.code} {exc.read()[:300]!r}"
                ) from exc
        raise RuntimeError(f"{method} {path}: gave up after retries")

    def my_articles(self) -> dict[str, int]:
        """Canonical URL -> article id of every article on the account."""
        found: dict[str, int] = {}
        page = 1
        while True:
            batch = self.request("GET", f"/articles/me/all?per_page=100&page={page}") or []
            for article in batch:
                if article.get("canonical_url"):
                    found[article["canonical_url"].rstrip("/") + "/"] = article["id"]
            if len(batch) < 100:
                return found
            page += 1


def devto_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for tag in tags:
        clean = re.sub(r"[^a-z0-9]", "", TAG_MAP.get(tag, tag).lower())
        if clean and clean not in out:
            out.append(clean)
    return out[:4]


def to_devto_markdown(post: dict[str, Any], portfolio: str) -> str:
    """The post body as dev.to Markdown, ending with a link to the original."""
    md = post["markdown"].replace("\r\n", "\n")
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)  # lint markers and other comments
    # GitHub-style alerts -> blockquotes with a bold label.
    md = re.sub(
        r"^> \[!(\w+)\]\s*$",
        lambda m: f"> **{ALERT_LABELS.get(m.group(1).upper(), m.group(1).title())}:**",
        md,
        flags=re.M,
    )
    if "{{<" in md or "{{%" in md:
        raise ValueError(f"unconverted Hugo shortcode in {post['canonical_url']}")
    # Relative links must not survive: dev.to would resolve them against dev.to.
    prose = re.sub(r"(?ms)^[ \t]*(```+|~~~+).*?^[ \t]*\1[ \t]*$", "", md)
    prose = re.sub(r"`[^`\n]+`", "", prose)
    if re.search(r"\]\((?!https?://|#|mailto:)", prose):
        raise ValueError(f"relative link or image left in {post['canonical_url']}")
    host = re.sub(r"^https?://|/.*$", "", portfolio)
    footer = (
        f"\n\n---\n\n*Originally published on [my blog]({post['canonical_url']}{UTM}). "
        "I write about MCP servers, AI agents, developer tools and Python automation; "
        f"more at [{host}]({portfolio}{UTM}).*\n"
    )
    return md.strip() + footer


def payload(post: dict[str, Any], body_md: str) -> dict[str, Any]:
    article: dict[str, Any] = {
        "title": post["title"],
        "body_markdown": body_md,
        "canonical_url": post["canonical_url"],
        "description": post.get("description", "")[:150],
        "tags": devto_tags(post.get("tags", [])),
    }
    if post.get("cover_image"):
        article["main_image"] = post["cover_image"]
    if post.get("series"):
        article["series"] = post["series"][0]
    return article


def eligible(post: dict[str, Any], now: dt.datetime, since: dt.date, delay_days: float) -> bool:
    if post.get("draft") or not post.get("syndicate", True):
        return False
    published = dt.datetime.fromisoformat(post["date"])
    age = (now - published).total_seconds()
    return published.date() >= since and age >= delay_days * 86400


def load_feed(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent("blog-syndication")})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            feed: dict[str, Any] = json.load(res)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ScriptError(f"could not read {url}: {exc}") from exc
    return feed


def sync(args: argparse.Namespace, key: str) -> int:
    since = dt.date.fromisoformat(os.environ.get("SYNDICATE_SINCE") or DEFAULT_SINCE)
    delay = float(os.environ.get("SYNDICATION_DELAY_DAYS") or DEFAULT_DELAY_DAYS)
    feed = load_feed(args.feed or args.site.rstrip("/") + "/en/syndication.json")
    state_file: Path = args.state
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    now = dt.datetime.now(dt.UTC)
    client = DevTo(key)
    existing = client.my_articles() if key else {}  # recovers ids if the state file was lost
    portfolio = portfolio_url("en")

    errors: list[str] = []
    counts = {"create": 0, "update": 0, "skip": 0}
    for post in feed["posts"]:
        url = post["canonical_url"]
        try:
            body = to_devto_markdown(post, portfolio)
            article = payload(post, body)
            digest = hashlib.sha256(json.dumps(article, sort_keys=True).encode()).hexdigest()
            entry = state.get(url) or ({"id": existing[url]} if url in existing else None)
            unchanged = entry and entry.get("hash") == digest
            if unchanged or (not entry and not eligible(post, now, since, delay)):
                counts["skip"] += 1
                continue
            action = "update" if entry else "create"
            counts[action] += 1
            log.info("%s: %s", action, post["title"])
            if args.dry_run:
                continue
            if entry:
                # Leave `published` out on updates, or a live article goes back to draft.
                res = client.request("PUT", f"/articles/{entry['id']}", {"article": article})
            else:
                res = client.request(
                    "POST", "/articles", {"article": {**article, "published": True}}
                )
            state[url] = {
                "id": res["id"],
                "url": res.get("url"),
                "hash": digest,
                "synced_at": now.isoformat(timespec="seconds"),
            }
            time.sleep(WRITE_PAUSE_SECONDS)
        except (RuntimeError, ValueError, KeyError) as exc:
            # One bad post must not block the rest; the run still fails at the end.
            errors.append(f"{url}: {exc}")
            log.error("%s: %s", url, exc)

    log.info(
        "%d posts in the feed (since %s, delay %g days): %d to create, %d to update, "
        "%d skipped, %d errors%s",
        len(feed["posts"]),
        since,
        delay,
        counts["create"],
        counts["update"],
        counts["skip"],
        len(errors),
        " (dry run)" if args.dry_run else "",
    )
    if not args.dry_run:
        write_text(state_file, json.dumps(state, indent=2, ensure_ascii=False) + "\n")
    if errors:
        write_text(state_file.with_name("errors.txt"), "\n".join(errors) + "\n")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would be posted, change nothing"
    )
    parser.add_argument(
        "--site",
        default=os.environ.get("SITE_URL") or None,
        help="site to read /en/syndication.json from (default: $SITE_URL or baseURL)",
    )
    parser.add_argument("--feed", help="full feed URL (default: <site>/en/syndication.json)")
    parser.add_argument(
        "--state",
        type=Path,
        default=STATE_FILE,
        help="dev.to article ids (default: %(default)s)",
    )
    add_logging_flags(parser)
    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    key = os.environ.get("DEVTO_API_KEY", "")
    if not key and not args.dry_run:
        log.info("DEVTO_API_KEY is not set; nothing to do")
        return 0
    try:
        args.site = args.site or base_url()
        return sync(args, key)
    except (ScriptError, ValueError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
