#!/usr/bin/env python3
"""Check whether a web image may be used in a post, and record its credit.

    python scripts/images/license_check.py URL [--media-dir DIR --file img/x.webp]

Supported sources, in order of preference:

1. Wikimedia Commons file pages or upload URLs: the Commons API ``extmetadata``
   (LicenseShortName, Artist, Credit, AttributionRequired, Restrictions).
2. Files in a GitHub repository (``raw.githubusercontent.com/<owner>/<repo>/...``):
   the repository licence from the public GitHub API. A repository's code
   licence does not cover its logo; logos stay trademarks.
3. Anything else: UNKNOWN, so do not use it unless the source page grants use in
   writing (an official press kit or brand page). Record that page as
   ``license_url`` and quote the permission in ``note`` by hand.

Verdicts:

``OK``            free licence; use it with the printed credit line
``OK-TRADEMARK``  free file with trademark restrictions; identify the product only
``CHECK``         copyleft repository licence; ask before using, prefer a screenshot
``NO``            non-commercial, no-derivatives, all rights reserved or unknown

The JSON result is printed. Exit code 0 for OK and OK-TRADEMARK (the credit is
written when ``--media-dir`` and ``--file`` are given), 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import add_logging_flags, setup_logging, user_agent
from credits import append_credit

log = logging.getLogger("license_check")

USER_AGENT = {"User-Agent": user_agent("blog-images")}
FREE = re.compile(r"^(cc0|public domain|pd|cc[ -]by(-sa)?[ -]?[0-9.]*|mit|apache|bsd|ofl)", re.I)
COPYLEFT = re.compile(r"^(gpl|lgpl|agpl|mpl|epl)", re.I)
BAD = re.compile(r"(\bnc\b|\bnd\b|non-?commercial|no ?deriv|all rights reserved|fair use)", re.I)


def get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=USER_AGENT)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def strip_html(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def check_commons(url: str) -> dict[str, Any]:
    """Licence data of a Wikimedia Commons file."""
    m = re.search(r"(File:[^?#]+)", urllib.parse.unquote(url))
    if m:
        title = m.group(1)
    else:  # upload.wikimedia.org/.../a/ab/Name.svg or .../thumb/.../Name.svg/1280px-Name.svg.png
        parts = urllib.parse.urlparse(url).path.split("/")
        title = "File:" + urllib.parse.unquote(parts[-2] if "/thumb/" in url else parts[-1])
    api = (
        "https://commons.wikimedia.org/w/api.php?action=query&format=json&prop=imageinfo"
        "&iiprop=url|extmetadata|mime|size&titles=" + urllib.parse.quote(title)
    )
    page = next(iter(get_json(api)["query"]["pages"].values()))
    info = page["imageinfo"][0]
    md = {k: strip_html(v.get("value", "")) for k, v in info["extmetadata"].items()}
    lic = md.get("LicenseShortName", "")
    verdict = "NO"
    if FREE.search(lic) and not BAD.search(lic):
        verdict = "OK-TRADEMARK" if "trademark" in md.get("Restrictions", "").lower() else "OK"
    return {
        "verdict": verdict,
        "source": info["descriptionurl"],
        "download": info["url"],
        "author": md.get("Artist") or md.get("Credit") or "unknown",
        "license": lic or "unknown",
        "license_url": md.get("LicenseUrl") or info["descriptionurl"],
        "note": ("Restrictions: " + md["Restrictions"]) if md.get("Restrictions") else None,
        "attribution_required": md.get("AttributionRequired", "true") != "false",
    }


def check_github_raw(url: str) -> dict[str, Any]:
    """Licence data of a file inside a public GitHub repository."""
    path = urllib.parse.urlparse(url).path.split("/")
    owner, repo = path[1], path[2]
    lic = get_json(f"https://api.github.com/repos/{owner}/{repo}").get("license") or {}
    spdx = lic.get("spdx_id") or "NOASSERTION"
    if FREE.search(spdx) and spdx != "NOASSERTION":
        verdict = "OK"
    elif COPYLEFT.search(spdx):
        verdict = "CHECK"
    else:
        verdict = "NO"
    return {
        "verdict": verdict,
        "source": url,
        "download": url,
        "author": f"{owner}/{repo} contributors",
        "license": spdx,
        "license_url": f"https://github.com/{owner}/{repo}/blob/HEAD/LICENSE",
        "note": "Repository licence; logos inside a repository remain trademarks.",
        "attribution_required": True,
    }


def check(url: str) -> dict[str, Any]:
    host = urllib.parse.urlparse(url).netloc
    if host.endswith(("wikimedia.org", "wikipedia.org")):
        return check_commons(url)
    if host == "raw.githubusercontent.com":
        return check_github_raw(url)
    return {
        "verdict": "NO",
        "source": url,
        "license": "unknown",
        "note": "No machine-checkable licence. Use only with written permission "
        "(press kit or brand page) and record it by hand in credits.yaml.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check an image's licence and record its credit.")
    ap.add_argument("url", help="Commons file page/upload URL or raw.githubusercontent.com URL")
    ap.add_argument("--media-dir", type=pathlib.Path, help="content/media/posts/<bundle>")
    ap.add_argument("--file", help="bundle-relative path the image will be saved as")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)
    result = check(a.url)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["verdict"].startswith("OK"):
        return 1
    log.info("caption credit: %s, %s (%s)", result["author"], result["license"], result["source"])
    if a.media_dir and a.file:
        append_credit(
            a.media_dir,
            {
                "file": a.file,
                "kind": "third-party",
                "source": result["source"],
                "author": result["author"],
                "license": result["license"],
                "license_url": result["license_url"],
                "note": result.get("note"),
            },
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
