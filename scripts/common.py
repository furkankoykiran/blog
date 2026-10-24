"""Shared helpers for the blog's maintenance scripts.

Paths, the site URL and the author are derived from the repository itself
(``config/_default/*.toml``), so the scripts work from any checkout on any
platform without editing.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config" / "_default"
CONTENT_DIR = REPO_ROOT / "content"
DATA_DIR = REPO_ROOT / "data"
BLOCKLIST_FILE = DATA_DIR / "blocklist.toml"
LANGS = ("tr", "en")


class ScriptError(RuntimeError):
    """An expected failure that should end the script with a message, not a traceback."""


# -------------------------------------------------------------- logging ---
def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    """Log to stderr; findings quote Turkish text, so never fail on a legacy console encoding."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


def add_logging_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="store_true", help="show debug output")
    group.add_argument("-q", "--quiet", action="store_true", help="only show warnings and errors")


# --------------------------------------------------------------- config ---
@cache
def _load_toml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def site_config() -> dict[str, Any]:
    """Return ``hugo.toml`` as a dict (empty if it is missing)."""
    return _load_toml("hugo.toml")


def site_params() -> dict[str, Any]:
    """Return ``params.toml`` as a dict (empty if it is missing)."""
    return _load_toml("params.toml")


def language_params(lang: str) -> dict[str, Any]:
    """Return the ``[params]`` table of ``languages.<lang>.toml``."""
    return dict(_load_toml(f"languages.{lang}.toml").get("params", {}))


def base_url() -> str:
    """The production base URL with a trailing slash, e.g. ``https://example.com/``."""
    url = os.environ.get("HUGO_BASEURL") or site_config().get("baseURL", "")
    if not url:
        raise ScriptError("baseURL is not set in config/_default/hugo.toml")
    return url if url.endswith("/") else url + "/"


def site_origin() -> str:
    """The base URL without the trailing slash, e.g. ``https://example.com``."""
    return base_url().rstrip("/")


def site_host() -> str:
    """Host name of the production site, overridable with ``SITE_HOST``."""
    return os.environ.get("SITE_HOST") or urlsplit(base_url()).netloc


def author_name() -> str:
    """The author's display name from ``languages.tr.toml`` (``params.author.name``)."""
    name = language_params(LANGS[0]).get("author", {}).get("name", "")
    if not name:
        raise ScriptError("set params.author.name in config/_default/languages.tr.toml")
    return str(name)


def portfolio_url(lang: str) -> str:
    """The author's portfolio for ``lang`` (``fk.portfolio`` / ``fk.portfolioEN``)."""
    fk = site_params().get("fk", {})
    return str(fk.get("portfolioEN" if lang == "en" else "portfolio", ""))


def user_agent(tool: str) -> str:
    """A descriptive User-Agent for outgoing requests."""
    return f"{tool}/1.0 (+{base_url()})"


def find_hugo(explicit: str | None = None) -> str:
    """Hugo executable: ``explicit``, then ``$HUGO``, then ``hugo`` on ``PATH``."""
    for candidate in (explicit, os.environ.get("HUGO")):
        if candidate:
            return candidate
    found = shutil.which("hugo")
    if not found:
        raise ScriptError("Hugo not found: install it, pass --hugo or set HUGO")
    return found


def write_text(path: Path, text: str) -> Path:
    """Write UTF-8 text with LF line endings, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# ------------------------------------------------------------ blocklist ---
@dataclass(frozen=True)
class BlockedTerm:
    """One ``[[terms]]`` entry of ``data/blocklist.toml``."""

    length: int
    sha256: str
    word: bool = False


def term_hash(term: str) -> str:
    """SHA-256 of a term as stored in ``data/blocklist.toml``."""
    return hashlib.sha256(term.strip().lower().encode("utf-8")).hexdigest()


def load_blocklist(path: Path = BLOCKLIST_FILE) -> tuple[BlockedTerm, ...]:
    """Read the hashed blocklist; a missing file means an empty list."""
    if not path.is_file():
        return ()
    with path.open("rb") as fh:
        terms = tomllib.load(fh).get("terms", [])
    return tuple(
        BlockedTerm(int(t["length"]), str(t["sha256"]).lower(), bool(t.get("word", False)))
        for t in terms
    )


@cache
def _default_blocklist() -> tuple[BlockedTerm, ...]:
    return load_blocklist()


_WORD = re.compile(r"[^\W\d_]+")


def blocked_terms(text: str, terms: tuple[BlockedTerm, ...] | None = None) -> list[tuple[int, str]]:
    """Offsets and spellings of blocked terms in ``text`` (case-insensitive).

    Terms are compared by hash, so they never need to appear in the repository
    in plain text. A term matches any substring of its length, or two adjacent
    words written apart ("Foo Bar" for "foobar"); a ``word`` term only matches
    whole words, which keeps short terms from matching inside longer words.
    """
    terms = _default_blocklist() if terms is None else terms
    if not terms or not text:
        return []
    low = text.lower()
    hits: dict[int, str] = {}
    substring = {(t.length, t.sha256) for t in terms if not t.word}
    for length in sorted({n for n, _ in substring}):
        for i in range(len(low) - length + 1):
            digest = hashlib.sha256(low[i : i + length].encode("utf-8")).hexdigest()
            if (length, digest) in substring:
                hits.setdefault(i, text[i : i + length])
    words = {t.sha256 for t in terms}
    if words:
        found = list(_WORD.finditer(low))
        for i, m in enumerate(found):
            if term_hash(m.group(0)) in words:
                hits.setdefault(m.start(), text[m.start() : m.end()])
            elif i + 1 < len(found):
                nxt = found[i + 1]
                gap = low[m.end() : nxt.start()]
                if gap.isspace() and term_hash(m.group(0) + nxt.group(0)) in words:
                    hits.setdefault(m.start(), text[m.start() : nxt.end()])
    return sorted(hits.items())


def is_blocked(*texts: object) -> bool:
    """True if any text contains a blocked term."""
    return any(blocked_terms(str(text)) for text in texts if text)
