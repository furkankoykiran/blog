#!/usr/bin/env python3
"""Read and write a bundle's image manifest, ``content/media/posts/<bundle>/credits.yaml``.

The manifest is a YAML list with one entry per image in the bundle (both
languages), including ``feature.webp``::

    - file: "img/pr-3927.webp"
      kind: "screenshot"     # screenshot | diagram | render | logo | third-party | cover
      source: "https://github.com/keploy/keploy/pull/3927"
      author: "GitHub / keploy contributors"
      license: "quotation"   # own | CC0-1.0 | PD | CC-BY-4.0 | CC-BY-SA-4.0 | MIT | Apache-2.0
                             # or quotation
      license_url: "https://..."
      captured: "2026-10-23"
      note: "..."

Values are written as JSON strings, which are valid YAML, so the module needs no
YAML library. Run it as a script to add or list entries::

    python scripts/images/credits.py add  content/media/posts/<b> img/x.webp --kind screenshot \\
        --source https://... --license quotation --author "..."
    python scripts/images/credits.py list content/media/posts/<b>
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from common import add_logging_flags, setup_logging

log = logging.getLogger("credits")

FIELDS = (
    "file",
    "kind",
    "source",
    "author",
    "license",
    "license_url",
    "captured",
    "note",
)
KINDS = ("screenshot", "diagram", "render", "logo", "third-party", "cover")
HEADER = "# Image credits for this bundle. Format: scripts/images/README.md\n"


def _scalar(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def load(path: pathlib.Path) -> list[dict[str, Any]]:
    """Parse a credits file written by :func:`save` (or by hand in the same simple style)."""
    if not path.exists():
        return []
    try:
        import yaml  # optional: handles any hand-written YAML

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        return [dict(e) for e in data if isinstance(e, dict)]
    except ImportError:
        pass
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("- "):
            cur = {}
            items.append(cur)
            raw = raw[2:]
        key, _, value = raw.strip().partition(":")
        value = value.strip()
        if cur is None:
            continue
        if value in ("null", "~", ""):
            cur[key.strip()] = None
        elif value.startswith('"'):
            cur[key.strip()] = json.loads(value)
        else:
            cur[key.strip()] = value.strip("'")
    return items


def save(path: pathlib.Path, entries: list[dict[str, Any]]) -> pathlib.Path:
    """Write ``entries`` in a stable field order."""
    lines = [HEADER.rstrip("\n")]
    for e in entries:
        first = True
        for k in FIELDS:
            if e.get(k) is not None:
                lines.append(("- " if first else "  ") + f"{k}: {_scalar(e[k])}")
                first = False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def append_credit(media_dir: pathlib.Path, entry: dict[str, Any]) -> pathlib.Path:
    """Add or replace the entry for ``entry['file']`` in ``media_dir/credits.yaml``."""
    entry = {k: v for k, v in entry.items() if v is not None}
    if entry.get("kind") not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    entry.setdefault("captured", datetime.date.today().isoformat())
    path = media_dir / "credits.yaml"
    entries = [e for e in load(path) if e.get("file") != entry["file"]]
    entries.append(entry)
    log.debug("credit %s -> %s", entry["file"], path)
    return save(path, entries)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Add or list image credits of a post bundle.")
    add_logging_flags(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="add or replace one entry")
    add.add_argument("media_dir", type=pathlib.Path, help="content/media/posts/<bundle>")
    add.add_argument("file", help="bundle-relative image path, e.g. img/x.webp")
    add.add_argument("--kind", required=True, choices=KINDS)
    for name in ("source", "author", "license", "license-url", "note"):
        add.add_argument(f"--{name}")
    ls = sub.add_parser("list", help="print the entries")
    ls.add_argument("media_dir", type=pathlib.Path)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)
    if a.cmd == "add":
        path = append_credit(
            a.media_dir,
            {
                "file": a.file,
                "kind": a.kind,
                "source": a.source,
                "author": a.author,
                "license": a.license or "own",
                "license_url": a.license_url,
                "note": a.note,
            },
        )
        log.info("%s", path)
    else:
        for e in load(a.media_dir / "credits.yaml"):
            log.info(
                "%-40s %-12s %-14s %s",
                e.get("file"),
                e.get("kind"),
                e.get("license"),
                e.get("source") or "",
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
