#!/usr/bin/env python3
"""Manage and test the hashed blocklist in ``data/blocklist.toml``.

Examples::

    python scripts/blocklist.py hash "some name"            # print a TOML entry to add
    python scripts/blocklist.py hash --word "abc"            # whole-word entry for a short term
    python scripts/blocklist.py check README.md layouts      # fail if a file contains a term

``check`` accepts files and directories (searched recursively); files that are
not UTF-8 text are skipped. Exit codes: 0 clean, 1 a blocked term was found.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

from common import add_logging_flags, blocked_terms, setup_logging, term_hash

log = logging.getLogger("blocklist")

SKIP_DIRS = {".git", "node_modules", "public", "resources", "themes", "__pycache__", ".ruff_cache"}


def cmd_hash(args: argparse.Namespace) -> int:
    for term in args.terms:
        clean = term.strip().lower()
        word = "\n  word = true" if args.word else ""
        print(f'[[terms]]\n  length = {len(clean)}\n  sha256 = "{term_hash(clean)}"{word}\n')
    return 0


def iter_files(paths: list[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and not SKIP_DIRS.intersection(child.relative_to(path).parts):
                    yield child
        else:
            yield path


def cmd_check(args: argparse.Namespace) -> int:
    hits = checked = 0
    for path in iter_files(args.paths):
        if not path.is_file():
            log.error("%s: not a file", path)
            hits += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            log.debug("%s: skipped (not UTF-8 text)", path)
            continue
        checked += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if blocked_terms(line):
                log.error("%s:%d contains a blocked term", path, lineno)
                hits += 1
    if hits:
        return 1
    log.info("%d file(s) clean", checked)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_logging_flags(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_hash = sub.add_parser("hash", help="print blocklist entries for the given terms")
    p_hash.add_argument("terms", nargs="+", metavar="TERM")
    p_hash.add_argument("--word", action="store_true", help="match whole words only")
    p_hash.set_defaults(func=cmd_hash)

    p_check = sub.add_parser("check", help="exit 1 if any file contains a blocked term")
    p_check.add_argument("paths", nargs="+", type=Path, metavar="PATH")
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    setup_logging(args.verbose, args.quiet)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
