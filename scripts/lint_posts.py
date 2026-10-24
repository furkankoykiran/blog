#!/usr/bin/env python3
"""Lint blog posts against the blog's rules for front matter, prose, images and credits.

    python scripts/lint_posts.py [--root .] [--schema schemas/frontmatter.schema.json]
                                 [--changed-only BASE_REF] [--format text|github|json]
                                 [--max-warnings N] [--summary] [BUNDLE ...]

Input: every ``content/tr/posts/*/index.md`` and ``content/en/posts/*/index.md``, their
images in the language bundle and in ``content/media/posts/<bundle>/``, and each
bundle's ``credits.yaml``. No Hugo build is needed.

Rules (the IDs are stable):

* ``FM001``-``FM019`` front matter: required and forbidden keys, lengths, formats,
  vocabularies, TR/EN pairing, uniqueness, honest ``lastmod``, JSON Schema.
* ``BD001``-``BD018`` body: headings, intro length, references to an earlier
  version of the post (BD004), code fences, images and alt text, internal links,
  placeholders, blocked names (``data/blocklist.toml``), reader address, stock
  phrases, client-side Mermaid, too many body images (BD016), dates the page
  cannot vouch for.
* ``MD001``-``MD008`` media: ``credits.yaml`` completeness, unused images, size
  budgets, formats, SVG safety, no Markdown in ``content/media``, no GPS data,
  unrendered diagram sources.
* ``LM001``-``LM004`` (only with ``--changed-only``): ``lastmod`` without a real
  change, a large change without ``lastmod``, changed ``date`` or slug of a
  published post.

Scope: site-wide checks always run on the whole site. ``--changed-only`` and
positional bundle names only filter which findings are reported.

Exit codes: 0 no errors (warnings allowed unless ``--max-warnings`` is exceeded),
1 errors, 2 internal failure. Needs Python 3.11+. ``pyyaml`` and ``jsonschema``
are used when installed (CI installs both); without them a small built-in parser
and the hand-written rules still run.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import difflib
import json
import logging
import pathlib
import re
import subprocess
import sys
from typing import Any

from common import (
    REPO_ROOT,
    BlockedTerm,
    add_logging_flags,
    blocked_terms,
    load_blocklist,
    portfolio_url,
    setup_logging,
    site_origin,
)

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML
    yaml = None  # type: ignore[assignment]

log = logging.getLogger("lint_posts")

# --------------------------------------------------------------------------- rule data

# BD004 errors: unambiguous references to an earlier version of the post or the blog.
BANNED_EARLIER_VERSION: dict[str, list[str]] = {
    "tr": [
        r"\beski\s+(yazı|yazımda|yazıda|yazıdaki|yazının)\b",
        r"\b(yazının|bu\s+yazının|yazımın)\s+(ilk|eski|önceki)\s+(hal|hâl|sürüm|versiyon)\w*",
        r"\bbu\s+yazıyı\s+(güncellerken|güncellediğim|yeniden\s+yazarken|düzenlerken|ilk\s+yazdığımda|yazdığım\s+sırada)",
        r"\b(yazıyı|yazıyı\s+ilk)\s+(yazdığımda|güncellerken|güncellediğimde)\b",
        r"\b(daha\s+önce|önceden|eskiden)\s+(burada|bu\s+yazıda)\s+\w+(mıştım|miştim|muştum|müştüm|dım|dim|dum|düm|tım|tim)\b",
        r"\b(burada|bu\s+yazıda)\s+(düzeltiyorum|düzelttim|güncelliyorum|güncelledim)\b",
        r"\beski\s+yazı(lar)?ım(da|daki)\b",
        r"^[ \t]*#+[ \t]*(güncelleme|update)\b",
        r"\(\s*(güncelleme|güncellendi|düzeltme)\s*[:)]",
        r"\bbu\s+yazının\s+(türkçe|ingilizce)\s+(sürüm|versiyon|hal|hâl)\w*",
    ],
    "en": [
        r"\b(the|my|this)\s+(earlier|original|old|previous|first)\s+(version|draft|edition|revision)\s+of\s+(this|the|my)\s+(post|article)\b",
        r"\b(the|my)\s+(earlier|original|old|previous|first)\s+(post|article)\s+(said|used|claimed|recommended|suggested|had|told|called|described|blamed|sold|left|looked)\b",
        r"\bwhen\s+I\s+(first\s+)?(wrote|updated|revised|rewrote)\s+this\s+(post|article)\b",
        r"\b(while|when)\s+(updating|revising|rewriting)\s+this\s+(post|article)\b",
        r"\bin\s+(the|my)\s+(original|old|earlier|previous)\s+(post|article|version)\b",
        r"\b(compared\s+(with|to)|unlike)\s+(the|my)\s+(original|old|earlier|previous)\s+(post|article|version|example)\b",
        r"\bthis\s+(post|article)\s+(was|has\s+been)\s+(updated|revised|rewritten)\b",
        r"\b(update|edit|correction)\s*(\(|:)\s*(19|20)\d\d",
        r"^[ \t]*#+[ \t]*(update|updated|changelog|edit)\b",
        r"\bI('m|\s+am)\s+correcting\s+(it|that)\s+here\b",
        r"\bthe\s+(turkish|english)\s+version\s+of\s+this\s+(post|article)\b",
    ],
}

# BD004 warnings: these forms also describe a project's or PR's own history ("my first
# version of the PR used ..."), which is allowed. A human decides; a confirmed false
# positive is silenced with a trailing `<!-- lint-ignore BD004 -->` on that line.
SUSPECT_EARLIER_VERSION: dict[str, list[str]] = {
    "tr": [
        r"\b(ilk|önceki|eski)\s+(hal|hâl|sürüm|versiyon)(i|inde|indeki|ü|ünde|ündeki)\b",
        r"\bönceki\s+yazı\w*",
    ],
    "en": [
        r"\b(the|my)\s+(earlier|original|old|previous|first)\s+(version|draft)\b",
        r"\b(earlier|older|previous)\s+posts?\b",
    ],
}

# BD011: the marker words are written with character classes so that searching
# the repository for them finds real markers only.
PLACEHOLDERS = r"\b(TOD[O]|TB[D]|FIXM[E]|XX[X]|lorem ipsum)\b|\[link\]|\(#\)|<placeholder>"
STOCK_PHRASES = {
    "en": r"\b("
    + "|".join(
        [
            "delve",
            "tapestry",
            "in today's (fast-paced|digital)",
            "it's worth noting",
            "game[- ]changer",
            "unlock the (power|potential)",
            "seamless(ly)?",
            "robust solution",
            "in conclusion",
            "let's dive in",
            "without further ado",
            "elevate your",
        ]
    )
    + r")\b",
    "tr": "("
    + "|".join(
        [
            "günümüzün hızla değişen",
            "sonuç olarak,",
            "özetlemek gerekirse",
            "hadi başlayalım",
            "bir sonraki seviyeye",
            "vazgeçilmez bir",
            "kusursuz bir şekilde",
            "derinlemesine dalalım",
        ]
    )
    + ")",
}
TR_SEN = r"\b(\w+(abilir|ebilir)(sin|sın)|senin|sana|seni)\b"
TR_MONTHS = [
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
]
EN_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
AS_OF = {
    "tr": rf"\b({'|'.join(TR_MONTHS)})\s+((?:19|20)\d\d)\s*(?:'?(?:de|da|te|ta))?"
    r"\s+(itibarıyla|itibariyle)",
    "en": rf"\bas\s+of\s+({'|'.join(EN_MONTHS)})\s+((?:19|20)\d\d)",
}
MONTHS = {m: i + 1 for names in (TR_MONTHS, EN_MONTHS) for i, m in enumerate(names)}

ALT_BAD_START = re.compile(
    r"^(image|picture|photo|screenshot) of\b|^(görsel|resim|ekran görüntüsü)\b", re.I
)
FILENAME_LIKE = re.compile(
    r"^[\w-]+\.(png|jpe?g|webp|svg|gif)$|^(img|image|screenshot|diagram)[-_ ]?\d*$", re.I
)
IMG_MD = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"(?P<title>[^\"]*)\")?\)")
IMG_SC = re.compile(r"\{\{<\s*figure[^>]*src=\"(?P<src>[^\"]+)\"(?P<rest>[^>]*)>\}\}")
LINK_MD = re.compile(r"(?<!!)\[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^[ \t]*(```+|~~~+)[ \t]*([^\s{`]*)", re.M)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
IMAGE_EXT = {".webp", ".svg", ".avif", ".png"}  # PNG only where lossless is needed
RASTER_LIMIT, FEATURE_LIMIT, SVG_LIMIT = 400 * 1024, 300 * 1024, 100 * 1024
# Soft maximum of body images (cover not counted): few, clear images beat many.
MAX_BODY_IMAGES = 3
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\+03:00|Z|[+-]\d{2}:\d{2})$")
SLUG_RX = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALIAS_RX = re.compile(r"^/([a-z0-9._-]+/)+$")
REQUIRED = [
    "title",
    "description",
    "date",
    "slug",
    "postType",
    "tags",
    "categories",
    "coverAlt",
    "draft",
]
OPTIONAL = [
    "lastmod",
    "series",
    "aliases",
    "cover",
    "canonical",
    "syndicate",
    "noindex",
    "showTableOfContents",
]
BOOL_KEYS = {"draft", "syndicate", "noindex", "showTableOfContents"}
CREDIT_KINDS = {"screenshot", "diagram", "render", "logo", "third-party", "cover"}

# --------------------------------------------------------------------------- data types


@dataclasses.dataclass
class Finding:
    level: str  # "error" | "warning"
    rule: str
    path: str
    line: int
    msg: str


@dataclasses.dataclass
class Post:
    lang: str
    bundle: str
    path: pathlib.Path
    rel: str
    fm: dict[str, Any] | None
    fm_lines: int
    body: str
    dirs: list[pathlib.Path]  # where bundle files may live: language dir first, then media
    flagged: set[str] = dataclasses.field(default_factory=set)  # front matter keys already reported


@dataclasses.dataclass
class Context:
    root: pathlib.Path
    schema: dict[str, Any]
    site: str = ""
    blocklist: tuple[BlockedTerm, ...] = ()
    findings: list[Finding] = dataclasses.field(default_factory=list)
    slugs: dict[tuple[str, str], list[str]] = dataclasses.field(default_factory=dict)
    descriptions: dict[tuple[str, str], list[str]] = dataclasses.field(default_factory=dict)
    aliases: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    lastmods: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    links: list[tuple[str, int, str, str]] = dataclasses.field(default_factory=list)
    images: dict[tuple[str, str], set[str]] = dataclasses.field(default_factory=dict)

    def add(self, level: str, rule: str, path: str, msg: str, line: int = 1) -> None:
        self.findings.append(Finding(level, rule, path, line, msg))


# --------------------------------------------------------------------------- parsing


def _scalar(v: str) -> Any:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _mini_yaml(text: str) -> dict[str, Any]:
    """Parser for this blog's flat front matter, used only when PyYAML is missing."""
    out: dict[str, Any] = {}
    key = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.lstrip().startswith("- ") and key:
            if not isinstance(out.get(key), list):
                out[key] = []
            out[key].append(_scalar(raw.split("-", 1)[1].strip()))
            continue
        k, sep, v = raw.partition(":")
        if not sep:
            continue
        key, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[key] = (
                [_scalar(x.strip()) for x in re.findall(r'"[^"]*"|\'[^\']*\'|[^,]+', inner)]
                if inner
                else []
            )
        else:
            out[key] = None if v == "" else _scalar(v)
    return out


def parse_markdown(path: pathlib.Path) -> tuple[dict[str, Any] | None, int, str]:
    """Return (front matter, number of lines before the body, body)."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None, 0, text
    raw = m.group(1)
    try:
        # BaseLoader keeps every scalar a string (dates stay as written).
        fm = yaml.load(raw, Loader=yaml.BaseLoader) if yaml else _mini_yaml(raw)
    except Exception:
        return None, 0, text[m.end() :]
    if not isinstance(fm, dict):
        return None, 0, text[m.end() :]
    fm = {
        k: (v == "true" if k in BOOL_KEYS and v in ("true", "false") else v) for k, v in fm.items()
    }
    return fm, raw.count("\n") + 3, text[m.end() :]


def load_yaml_list(path: pathlib.Path) -> list[dict[str, Any]]:
    """Read a credits.yaml file (list of mappings)."""
    if yaml:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("- "):
            cur = {}
            items.append(cur)
            raw = raw[2:]
        k, _, v = raw.strip().partition(":")
        if cur is not None and k:
            cur[k.strip()] = _scalar(v.strip()) or None
    return items


def discover(root: pathlib.Path) -> list[Post]:
    content = root / "content"
    posts = []
    for lang in ("tr", "en"):
        for idx in sorted((content / lang / "posts").glob("*/index.md")):
            bundle = idx.parent.name
            fm, n, body = parse_markdown(idx)
            posts.append(
                Post(
                    lang,
                    bundle,
                    idx,
                    idx.relative_to(root).as_posix(),
                    fm,
                    n,
                    body,
                    [idx.parent, content / "media" / "posts" / bundle],
                )
            )
    return posts


# --------------------------------------------------------------------------- helpers


def lineno(post: Post, offset: int) -> int:
    return post.fm_lines + post.body.count("\n", 0, offset) + 1


def strip_code(body: str) -> str:
    """Blank out fenced and inline code (keeping line numbers) so prose rules skip code."""
    body = re.sub(
        r"(?ms)^[ \t]*(```+|~~~+).*?^[ \t]*\1[ \t]*$",
        lambda m: re.sub(r"[^\n]", " ", m.group(0)),
        body,
    )
    return re.sub(r"`[^`\n]+`", lambda m: " " * len(m.group(0)), body)


def strip_comments(body: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), body, flags=re.S)


def parse_date(value: Any) -> dt.datetime | None:
    s = str(value or "")
    if not DATE_RX.match(s):
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_file(post: Post, rel: str) -> pathlib.Path | None:
    for d in post.dirs:
        p = d / rel
        if p.is_file():
            return p
    return None


def lang_rules(schema: dict[str, Any], lang: str) -> dict[str, Any]:
    return next(
        r["then"]["properties"]
        for r in schema["allOf"]
        if r["if"]["properties"]["_lang"]["const"] == lang
    )


# --------------------------------------------------------------------------- front matter


def check_front_matter(post: Post, ctx: Context) -> dt.datetime | None:
    fm = post.fm
    assert fm is not None
    schema = ctx.schema
    props = schema["properties"]

    def add(level: str, rule: str, msg: str, key: str | None = None) -> None:
        ctx.add(level, rule, post.rel, msg)
        if key and level == "error":
            post.flagged.add(key)

    for k in REQUIRED:
        if k not in fm or fm[k] in (None, "", []):
            add("error", "FM002", f"required field `{k}` missing or empty", k)
    forbidden = set(schema.get("x-forbidden-keys", []))
    for k in fm:
        if k in forbidden or k.startswith("_"):
            add(
                "error",
                "FM004",
                f"forbidden field `{k}`: workflow or internal data never lives in front matter",
                k,
            )
        elif k not in REQUIRED + OPTIONAL:
            add("error", "FM003", f"unknown field `{k}`", k)
    for k in ("series", "aliases", "cover"):
        if k in fm and fm[k] in ([], None, ""):
            add("warning", "FM003", f"empty `{k}`: omit the key instead")
    title = str(fm.get("title") or "")
    if title:
        if not 15 <= len(title) <= 70:
            add("error", "FM005", f"title is {len(title)} characters (allowed 15-70)", "title")
        elif len(title) > 60:
            add("warning", "FM005", f"title is {len(title)} characters (aim for 60 or fewer)")
        if title.endswith((".", "!")) or re.search(r"[\U0001F300-\U0001FAFF]", title):
            add("error", "FM005", "title ends with punctuation or contains an emoji", "title")
    desc = str(fm.get("description") or "")
    if desc:
        if not 100 <= len(desc) <= 170:
            add(
                "error",
                "FM006",
                f"description is {len(desc)} characters (allowed 100-170)",
                "description",
            )
        elif not 120 <= len(desc) <= 160:
            add("warning", "FM006", f"description is {len(desc)} characters (aim for 120-160)")
        if desc.strip().lower() == title.strip().lower():
            add("error", "FM006", "description repeats the title", "description")
        ctx.descriptions.setdefault((post.lang, desc.strip()), []).append(post.rel)
    date = parse_date(fm.get("date"))
    if fm.get("date") and not date:
        add(
            "error",
            "FM007",
            "date must be ISO 8601 with offset, e.g. 2026-10-23T10:00:00+03:00",
            "date",
        )
    if "lastmod" in fm:
        raw = str(fm["lastmod"])
        lastmod = parse_date(raw)
        if not lastmod:
            add("error", "FM008", "lastmod must be ISO 8601 with offset", "lastmod")
        elif date and lastmod <= date:
            add("error", "FM008", "lastmod must be later than date (omit it when equal)", "lastmod")
        elif lastmod > dt.datetime.now(dt.UTC) + dt.timedelta(hours=1):
            add("error", "FM008", "lastmod is in the future", "lastmod")
        ctx.lastmods.setdefault(raw, []).append(post.rel)
    slug = str(fm.get("slug") or "")
    if slug:
        if not SLUG_RX.match(slug) or not 3 <= len(slug) <= 75:
            add(
                "error",
                "FM009",
                f"slug `{slug}` must be lower-case ASCII words joined by single "
                "hyphens, 3-75 characters",
                "slug",
            )
        if post.lang == "en" and slug != post.bundle:
            add(
                "error",
                "FM009",
                f"EN slug `{slug}` must equal the bundle directory `{post.bundle}`",
                "slug",
            )
        ctx.slugs.setdefault((post.lang, slug), []).append(post.rel)
    if not SLUG_RX.match(post.bundle):
        add("error", "FM009", f"bundle directory `{post.bundle}` is not a valid slug")
    tags = fm.get("tags") or []
    vocab = props["tags"]["items"]["enum"]
    if not isinstance(tags, list) or not 3 <= len(tags) <= 6:
        add("error", "FM010", "tags: 3-6 entries required", "tags")
    else:
        for t in tags:
            if t not in vocab:
                add("error", "FM010", f"tag `{t}` is not in the controlled vocabulary", "tags")
        if len(set(tags)) != len(tags):
            add("error", "FM010", "duplicate tags", "tags")
    rules = lang_rules(schema, post.lang)
    cats = fm.get("categories") or []
    if (
        not isinstance(cats, list)
        or len(cats) != 1
        or cats[0] not in rules["categories"]["items"]["enum"]
    ):
        add(
            "error",
            "FM011",
            f"categories must be exactly one of the {post.lang.upper()} categories, got {cats}",
            "categories",
        )
    series = fm.get("series")
    if series and (
        not isinstance(series, list)
        or len(series) != 1
        or series[0] not in rules["series"]["items"]["enum"]
    ):
        add(
            "error",
            "FM012",
            f"series must be one {post.lang.upper()} series name, got {series}",
            "series",
        )
    aliases = fm.get("aliases") or []
    for a in aliases if isinstance(aliases, list) else [aliases]:
        if not ALIAS_RX.match(str(a)):
            add(
                "error",
                "FM013",
                f"alias `{a}` must be an absolute site path with a trailing slash",
                "aliases",
            )
        ctx.aliases.setdefault(str(a), []).append(post.rel)
    cover_alt = str(fm.get("coverAlt") or "")
    if cover_alt:
        if not 20 <= len(cover_alt) <= 160:
            add(
                "error",
                "FM014",
                f"coverAlt is {len(cover_alt)} characters (allowed 20-160)",
                "coverAlt",
            )
        if ALT_BAD_START.search(cover_alt) or cover_alt.strip().lower() == title.strip().lower():
            add(
                "error",
                "FM014",
                "coverAlt must describe the image, not repeat the title or start "
                "with 'Image of' or 'Görsel'",
                "coverAlt",
            )
    if fm.get("cover") and not find_file(post, str(fm["cover"])):
        add("error", "FM015", f"cover `{fm['cover']}` not found in the bundle", "cover")
    if "postType" in fm and fm["postType"] not in props["postType"]["enum"]:
        add("error", "FM017", f"postType must be one of {props['postType']['enum']}", "postType")
    if post.lang == "tr" and "syndicate" in fm:
        add("warning", "FM003", "`syndicate` only applies to EN posts")
    return date


def check_json_schema(post: Post, ctx: Context) -> None:
    """FM019: validate against the JSON Schema; skip fields a specific rule already reported."""
    try:
        import jsonschema
    except ImportError:
        return
    data = dict(post.fm or {})
    data["_lang"] = post.lang
    validator = jsonschema.Draft202012Validator(ctx.schema)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        field = str(err.path[0]) if err.path else ""
        if not field:
            m = re.search(r"'([^']+)'", err.message)
            field = m.group(1) if m else ""
        if field in post.flagged or field.startswith("_"):
            continue
        post.flagged.add(field)
        ctx.add("error", "FM019", post.rel, f"schema: `{field or '/'}`: {err.message[:160]}")


# --------------------------------------------------------------------------- body


def check_body(post: Post, ctx: Context, date: dt.datetime | None) -> None:
    body, lang = post.body, post.lang
    prose = strip_comments(strip_code(body))

    def add(level: str, rule: str, msg: str, offset: int | None = None) -> None:
        ctx.add(level, rule, post.rel, msg, lineno(post, offset) if offset is not None else 1)

    # BD001/BD002 headings
    last, seen = 1, set()
    for m in HEADING.finditer(prose):
        level, text = len(m.group(1)), m.group(2)
        if level == 1:
            add("error", "BD001", "H1 in the body (the title is the only H1)", m.start())
        elif level > last + 1:
            add("error", "BD002", f"heading level jumps from H{last} to H{level}", m.start())
        elif level >= 4:
            add("warning", "BD002", "H4 or deeper: restructure into H2/H3", m.start())
        if len(text) > 70:
            add("warning", "BD002", f"heading is {len(text)} characters", m.start())
        if level == 2:
            if text.lower() in seen:
                add("warning", "BD002", f"duplicate H2 `{text}`", m.start())
            seen.add(text.lower())
        last = level
    # BD003 intro
    first_h2 = re.search(r"^##\s", prose, re.M)
    intro = prose[: first_h2.start()] if first_h2 else prose
    words = len(re.findall(r"\w+", re.sub(r"\{\{<.*?>\}\}|!\[.*?\]\(.*?\)", "", intro)))
    if words < 40:
        add(
            "error",
            "BD003",
            f"intro before the first H2 has {words} words (min 40): say what the post solves",
        )
    elif words > 160:
        add("warning", "BD003", f"intro has {words} words: move detail below the first H2")
    # BD004 earlier-version references, one finding per line
    hit_lines: dict[int, str] = {}
    for rx in BANNED_EARLIER_VERSION[lang]:
        for m in re.finditer(rx, prose, re.I | re.M):
            hit_lines.setdefault(lineno(post, m.start()), m.group(0).strip())
    for ln, phrase in sorted(hit_lines.items()):
        ctx.add(
            "error",
            "BD004",
            post.rel,
            f'refers to an earlier version of the post or blog: "{phrase}"',
            ln,
        )
    body_lines = body.splitlines()
    for rx in SUSPECT_EARLIER_VERSION[lang]:
        for m in re.finditer(rx, prose, re.I):
            ln = lineno(post, m.start())
            idx = ln - post.fm_lines - 1
            if 0 <= idx < len(body_lines) and "lint-ignore BD004" in body_lines[idx]:
                continue
            if ln not in hit_lines:
                hit_lines[ln] = m.group(0)
                ctx.add(
                    "warning",
                    "BD004",
                    post.rel,
                    f"possible earlier-version reference: "
                    f"\"{m.group(0).strip()}\" (fine only if it is the project's or PR's "
                    "own history)",
                    ln,
                )
    # BD005 code fences
    open_fence = None
    for m in FENCE.finditer(body):
        if open_fence is None:
            open_fence = m.group(1)
            if not m.group(2):
                add(
                    "error",
                    "BD005",
                    "code block without a language (use `text` for plain output)",
                    m.start(),
                )
            elif m.group(2) in ("sh", "shell", "zsh", "terminal", "cmd"):
                add(
                    "warning",
                    "BD005",
                    f"use `bash` (commands) or `console` (sessions) instead of `{m.group(2)}`",
                    m.start(),
                )
        elif (
            m.group(1)[0] == open_fence[0] and len(m.group(1)) >= len(open_fence) and not m.group(2)
        ):
            open_fence = None
    # BD006 images
    images = []
    for m in list(IMG_MD.finditer(prose)) + list(IMG_SC.finditer(strip_code(body))):
        src = m.group("src")
        alt = m.groupdict().get("alt")
        if alt is None:
            am = re.search(r'alt="([^"]*)"', m.group("rest") or "")
            alt = am.group(1) if am else ""
        images.append(src)
        if re.match(r"^https?://", src):
            add(
                "error",
                "BD006",
                f"remote image `{src}`: save it into the bundle (if the licence allows)",
                m.start(),
            )
            continue
        if not alt or len(alt) < 10:
            add(
                "error",
                "BD006",
                f"alt text missing or shorter than 10 characters for `{src}`",
                m.start(),
            )
        elif len(alt) > 160 or FILENAME_LIKE.match(alt) or ALT_BAD_START.search(alt):
            add(
                "error",
                "BD006",
                f"alt text for `{src}` must describe the content (10-160 characters, "
                "no 'Image of')",
                m.start(),
            )
        found = find_file(post, src)
        if not found:
            add("error", "BD006", f"image `{src}` not found in the bundle", m.start())
            continue
        if found.suffix.lower() not in IMAGE_EXT:
            add(
                "error",
                "BD006",
                f"`{src}`: {found.suffix} is not allowed (WebP, SVG, AVIF; PNG only "
                "when lossless is needed)",
                m.start(),
            )
    ctx.images[(post.bundle, post.lang)] = set(images)
    if len(set(images)) > MAX_BODY_IMAGES:
        add(
            "warning",
            "BD016",
            f"{len(set(images))} body images; keep at most {MAX_BODY_IMAGES} "
            "(cover plus 0-2 is the norm), each making one point",
        )
    for m in re.finditer(r"\{\{<\s*mermaid", body):
        add(
            "warning",
            "BD015",
            "client-side Mermaid: run scripts/render_diagrams.py to turn it into a "
            "static SVG (visible without JavaScript, in RSS and on dev.to)",
            m.start(),
        )
    # BD007/BD008/BD010 links
    internal = external = 0
    for m in LINK_MD.finditer(prose):
        href = m.group("href")
        if ctx.site and href.startswith(ctx.site):
            add("warning", "BD007", "use a root-relative link for own posts", m.start())
            href = href[len(ctx.site) :] or "/"
        if href.startswith("/"):
            internal += 1
            if lang == "tr" and href.startswith("/en/"):
                add("error", "BD007", f"TR post links to an EN page `{href}`", m.start())
            if lang == "en" and href.startswith("/posts/"):
                add("error", "BD007", f"EN post links to a TR page `{href}`", m.start())
            pm = re.match(r"^(/en)?/posts/([^/#?]+)/?", href)
            if pm:
                ctx.links.append(
                    (post.rel, lineno(post, m.start()), "en" if pm.group(1) else "tr", pm.group(2))
                )
        elif href.startswith("http://"):
            external += 1
            add("warning", "BD008", f"insecure link `{href}`", m.start())
        elif href.startswith("https://"):
            external += 1
            on_portfolio = href.startswith(portfolio_url("tr"))
            if on_portfolio and lang == "tr" and href.startswith(portfolio_url("en")):
                add("error", "BD007", "TR post links to the EN portfolio", m.start())
            if on_portfolio and lang == "en" and "/projeler/" in href:
                add("error", "BD007", "EN post links to the TR portfolio", m.start())
    if internal < 2:
        add("warning", "BD010", f"{internal} internal links (aim for 2-4 genuinely related posts)")
    if external < 1:
        add("warning", "BD010", "no external source link (repository, PR, official docs)")
    for m in re.finditer(r"(?<![(<\"'=])\bhttps?://[^\s)>\]]+", prose):
        if "](" not in prose[max(0, m.start() - 2) : m.start()]:
            add("warning", "BD008", "bare URL: wrap it in a descriptive link", m.start())
    # BD009 length
    n_words = len(re.findall(r"\w+", prose))
    if n_words < 600:
        add("warning", "BD009", f"{n_words} words: thin for a standalone post")
    # BD011 placeholders, BD013 blocked names
    for m in re.finditer(PLACEHOLDERS, prose):
        add("error", "BD011", f"placeholder `{m.group(0)}`", m.start())
    for offset, word in blocked_terms(body, ctx.blocklist):
        add("error", "BD013", f"a name that must never appear in a post: `{word}`", offset)
    # BD012 reader address, BD014 stock phrases
    if lang == "tr":
        for m in re.finditer(TR_SEN, prose, re.I):
            add(
                "warning",
                "BD012",
                f"addresses the reader as `sen` (`{m.group(0)}`): use `siz`",
                m.start(),
            )
    for m in re.finditer(STOCK_PHRASES[lang], prose, re.I):
        add("warning", "BD014", f"stock phrase `{m.group(0)}`", m.start())
    # BD017/BD018 dates newer than the page's own dates
    lastmod = parse_date((post.fm or {}).get("lastmod"))
    newest = max(filter(None, [date, lastmod]), default=None)
    if not newest:
        return
    for m in re.finditer(AS_OF[lang], prose):
        month = MONTHS.get(m.group(1).capitalize() if lang == "en" else m.group(1), 1)
        if (int(m.group(2)), month) > (newest.year, newest.month):
            add(
                "error",
                "BD017",
                f"`{m.group(0)}` is newer than the post's date/lastmod: reword it "
                "without the date, or set a real lastmod",
                m.start(),
            )
    later = [
        m
        for m in re.finditer(r"(?<![\d.\-/])(20[2-9]\d)(?![\d.\-/])", prose)
        if int(m.group(1)) > newest.year
    ]
    if later:
        add(
            "warning",
            "BD018",
            f"{len(later)} mention(s) of years after {newest.year} (first: "
            f"{later[0].group(1)}): the visible date can't vouch for them; reword or set a "
            "real lastmod",
            later[0].start(),
        )


def check_post(post: Post, ctx: Context) -> None:
    if post.fm is None:
        ctx.add("error", "FM001", post.rel, "missing or unparseable YAML front matter")
        return
    date = check_front_matter(post, ctx)
    check_json_schema(post, ctx)
    check_body(post, ctx, date)


# --------------------------------------------------------------------------- site-wide


def check_pairs(posts: list[Post], ctx: Context) -> dict[str, dict[str, Post]]:
    by_bundle: dict[str, dict[str, Post]] = {}
    for p in posts:
        by_bundle.setdefault(p.bundle, {})[p.lang] = p
    pairs = ctx.schema["x-pairs"]
    for bundle, langs in sorted(by_bundle.items()):
        for missing in sorted({"tr", "en"} - set(langs)):
            some = next(iter(langs.values()))
            ctx.add(
                "error",
                "FM016",
                some.rel,
                f"no {missing.upper()} counterpart for bundle `{bundle}`",
            )
        if {"tr", "en"} <= set(langs):
            tr, en = langs["tr"].fm or {}, langs["en"].fm or {}
            rel = langs["en"].rel
            for k in ("date", "postType", "draft"):
                if tr.get(k) != en.get(k):
                    ctx.add("error", "FM016", rel, f"`{k}` differs between TR and EN")
            if sorted(tr.get("tags") or []) != sorted(en.get("tags") or []):
                ctx.add("error", "FM016", rel, "tags differ between TR and EN")
            tc, ec = (tr.get("categories") or [None])[0], (en.get("categories") or [None])[0]
            if tc and ec and pairs["categories"].get(tc) != ec:
                ctx.add("error", "FM016", rel, f"category pair mismatch: {tc} / {ec}")
            ts, es = (tr.get("series") or [None])[0], (en.get("series") or [None])[0]
            if (ts or es) and pairs["series"].get(ts) != es:
                ctx.add("error", "FM016", rel, f"series pair mismatch: {ts} / {es}")
    return by_bundle


def check_uniqueness(ctx: Context) -> None:
    for (lang, slug), files in ctx.slugs.items():
        if len(files) > 1:
            ctx.add("error", "FM009", files[0], f"{lang} slug `{slug}` is used {len(files)} times")
    for (_lang, _d), files in ctx.descriptions.items():
        if len(files) > 1:
            ctx.add("error", "FM018", files[0], f"description also used by {', '.join(files[1:])}")
    for alias, files in ctx.aliases.items():
        if len(files) > 1:
            ctx.add("error", "FM013", files[0], f"alias `{alias}` is used by {len(files)} posts")
    for lastmod, files in ctx.lastmods.items():
        if len(files) >= 5:
            ctx.add(
                "error",
                "FM008",
                files[0],
                f"{len(files)} posts share lastmod {lastmod}: bulk stamps are not real updates",
            )
    known = set(ctx.slugs)
    for rel, ln, lang, slug in ctx.links:
        if (lang, slug) not in known:
            ctx.add(
                "error", "BD007", rel, f"internal link to unknown {lang.upper()} post `{slug}`", ln
            )


def gps_tags(path: pathlib.Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        with Image.open(path) as im:
            return bool(im.getexif().get_ifd(0x8825))
    except Exception:
        return False


def check_media(by_bundle: dict[str, dict[str, Post]], ctx: Context) -> None:
    content = ctx.root / "content"
    rel = lambda p: p.relative_to(ctx.root).as_posix()  # noqa: E731
    files = [
        f
        for tree in ("media", "tr", "en")
        for f in sorted((content / tree).rglob("*"))
        if f.is_file()
    ]
    for f in files:
        suffix = f.suffix.lower()
        if f.is_relative_to(content / "media") and suffix == ".md":
            ctx.add("error", "MD006", rel(f), "Markdown inside content/media")
        if suffix in (".jpg", ".jpeg", ".gif"):
            ctx.add("error", "MD004", rel(f), "convert to WebP with scripts/images/optimize.py")
        size = f.stat().st_size
        if suffix == ".svg":
            if size > SVG_LIMIT:
                ctx.add("error", "MD003", rel(f), f"SVG is {size // 1024} KB (max 100 KB)")
            if re.search(
                r"<script|\son\w+\s*=", f.read_text(encoding="utf-8", errors="ignore"), re.I
            ):
                ctx.add("error", "MD005", rel(f), "SVG contains a script or an event handler")
        elif suffix in (".webp", ".png", ".avif"):
            limit = FEATURE_LIMIT if f.stem == "feature" else RASTER_LIMIT
            if size > limit:
                ctx.add("error", "MD003", rel(f), f"{size // 1024} KB (max {limit // 1024} KB)")
            if gps_tags(f):
                ctx.add(
                    "error",
                    "MD007",
                    rel(f),
                    "image carries GPS metadata: re-save it with optimize.py",
                )
        if suffix == ".mmd":
            svg = f.with_suffix(".svg")
            if not svg.exists():
                ctx.add(
                    "error",
                    "MD008",
                    rel(f),
                    "diagram source has no rendered SVG: run scripts/render_diagrams.py render",
                )
    for bundle, langs in by_bundle.items():
        media = content / "media" / "posts" / bundle
        used = {lang: ctx.images.get((bundle, lang), set()) for lang in langs}
        all_used = set().union(*used.values()) if used else set()
        covers = {
            str((p.fm or {}).get("cover")) for p in langs.values() if (p.fm or {}).get("cover")
        }
        # MD002 unused images: shared media against both languages, language dirs
        # against their own page.
        dirs = [(media, all_used)] + [
            (content / lang / "posts" / bundle, used[lang]) for lang in langs
        ]
        for d, refs in dirs:
            for f in sorted(d.rglob("*")) if d.exists() else []:
                r = f.relative_to(d).as_posix()
                if (
                    f.is_file()
                    and f.suffix.lower() in IMAGE_EXT | {".jpg", ".jpeg", ".gif"}
                    and not r.startswith("feature")
                    and r not in refs
                    and r not in covers
                ):
                    ctx.add(
                        "warning", "MD002", rel(f), "image not used by the page(s) that can see it"
                    )
        # MD001 credits
        credits_path = media / "credits.yaml"
        crel = (
            rel(credits_path)
            if credits_path.exists()
            else f"content/media/posts/{bundle}/credits.yaml"
        )
        entries = load_yaml_list(credits_path) if credits_path.exists() else []
        if not credits_path.exists():
            ctx.add(
                "error",
                "MD001",
                crel,
                "credits.yaml missing (every bundle lists its images, including feature.webp)",
            )
        by_file = {str(e.get("file")): e for e in entries}
        has_feature = any(
            (d / "feature.webp").exists()
            for d in (media, *(content / lg / "posts" / bundle for lg in langs))
        )
        needed = set(all_used) | ({"feature.webp"} if has_feature else set()) | covers
        for img in sorted(needed):
            if img not in by_file and not re.match(r"^https?://", img):
                where = next(
                    (p.rel for p in langs.values() if img in used.get(p.lang, set())), crel
                )
                ctx.add("error", "MD001", where, f"`{img}` has no entry in credits.yaml")
        for name, entry in by_file.items():
            if not any(
                (d / name).is_file()
                for d in (media, *(content / lg / "posts" / bundle for lg in langs))
            ):
                ctx.add("error", "MD001", crel, f"entry for a missing file `{name}`")
            for k in ("kind", "license"):
                if not entry.get(k):
                    ctx.add("error", "MD001", crel, f"`{name}`: `{k}` missing")
            kind = entry.get("kind")
            if kind and kind not in CREDIT_KINDS:
                ctx.add("error", "MD001", crel, f"`{name}`: unknown kind `{kind}`")
            if kind in ("third-party", "logo", "screenshot") and not entry.get("source"):
                ctx.add("error", "MD001", crel, f"`{name}`: `source` is required for kind {kind}")


# --------------------------------------------------------------------------- git-aware rules


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def changed_files(root: pathlib.Path, base: str) -> list[str]:
    return git(root, "diff", "--name-only", f"{base}...HEAD", "--", "content").splitlines()


def changed_bundles(root: pathlib.Path, base: str) -> set[str]:
    names = set()
    for line in changed_files(root, base):
        parts = line.split("/")
        if len(parts) > 3 and parts[0] == "content" and parts[2] == "posts":
            names.add(parts[3])
    return names


def body_words(body: str) -> list[str]:
    return re.findall(r"\w+", strip_code(body))


def code_blocks(body: str) -> list[str]:
    return re.findall(r"(?ms)^[ \t]*(```+|~~~+).*?^[ \t]*\1[ \t]*$", body)


def check_git(posts: list[Post], ctx: Context, base: str) -> None:
    """LM001-LM004: compare each changed post with its version on the base ref."""
    changed = set(changed_files(ctx.root, base))
    for post in posts:
        if post.rel not in changed or post.fm is None:
            continue
        try:
            old_text = git(ctx.root, "show", f"{base}:{post.rel}")
        except subprocess.CalledProcessError:
            continue  # new post
        m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", old_text, re.S)
        if not m:
            continue
        old_fm = (
            yaml.load(m.group(1), Loader=yaml.BaseLoader) if yaml else _mini_yaml(m.group(1))
        ) or {}
        old_body = old_text[m.end() :]
        published = str(old_fm.get("draft", "false")).lower() != "true"
        new_fm = post.fm
        old_w, new_w = body_words(old_body), body_words(post.body)
        diff = sum(
            max(i2 - i1, j2 - j1)
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                a=old_w, b=new_w, autojunk=False
            ).get_opcodes()
            if tag != "equal"
        )
        code_changed = code_blocks(old_body) != code_blocks(post.body)
        h2 = lambda b: [h for lvl, h in HEADING.findall(strip_code(b)) if len(lvl) == 2]  # noqa: E731
        lastmod_changed = str(old_fm.get("lastmod") or "") != str(new_fm.get("lastmod") or "")
        if lastmod_changed and new_fm.get("lastmod") and diff < 40 and not code_changed:
            ctx.add(
                "error",
                "LM001",
                post.rel,
                f"lastmod changed but only {diff} words of prose and no "
                "code changed: a lastmod needs a meaningful update",
            )
        if not lastmod_changed and (
            diff > 0.25 * max(len(old_w), 1) or h2(old_body) != h2(post.body)
        ):
            ctx.add(
                "warning",
                "LM002",
                post.rel,
                f"{diff} words changed (or H2s added/removed) without "
                "a lastmod: decide whether this is a meaningful update",
            )
        if published and str(old_fm.get("date")) != str(new_fm.get("date")):
            ctx.add("error", "LM003", post.rel, "date of a published post changed")
        if published and str(old_fm.get("slug")) != str(new_fm.get("slug")):
            ctx.add(
                "error", "LM004", post.rel, "slug of a published post changed (its URL is indexed)"
            )


# --------------------------------------------------------------------------- main


def run(
    root: pathlib.Path, schema_path: pathlib.Path, changed_only: str | None = None
) -> tuple[list[Finding], int]:
    """Lint every post under ``root``. Returns the findings and the number of files."""
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    ctx = Context(
        root=root,
        schema=schema,
        site=site_origin(),
        blocklist=load_blocklist(root / "data" / "blocklist.toml"),
    )
    posts = discover(root)
    for post in posts:
        check_post(post, ctx)
    by_bundle = check_pairs(posts, ctx)
    check_uniqueness(ctx)
    check_media(by_bundle, ctx)
    if changed_only:
        check_git(posts, ctx, changed_only)
    return ctx.findings, len(posts)


def emit(findings: list[Finding], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps([dataclasses.asdict(f) for f in findings], ensure_ascii=False, indent=1))
        return
    for f in findings:
        if fmt == "github":
            msg = f.msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::{f.level} file={f.path},line={f.line},title={f.rule}::{msg}")
        else:
            print(f"{f.path}:{f.line}: {f.level} {f.rule} {f.msg}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bundles", nargs="*", help="only report findings for these bundles")
    ap.add_argument(
        "--root",
        type=pathlib.Path,
        default=REPO_ROOT,
        help="repository root (default: this checkout)",
    )
    ap.add_argument(
        "--schema", type=pathlib.Path, help="default: <root>/schemas/frontmatter.schema.json"
    )
    ap.add_argument(
        "--changed-only",
        metavar="BASE_REF",
        help="report only bundles changed since BASE_REF and run the git rules (LM001-LM004)",
    )
    ap.add_argument("--format", choices=["text", "github", "json"], default="text")
    ap.add_argument(
        "--max-warnings", type=int, default=-1, help="fail above this many warnings (default: off)"
    )
    ap.add_argument("--summary", action="store_true", help="print counts per rule to stderr")
    add_logging_flags(ap)
    a = ap.parse_args(argv)
    setup_logging(a.verbose, a.quiet)
    root = a.root.resolve()
    findings, n_files = run(
        root, a.schema or root / "schemas" / "frontmatter.schema.json", a.changed_only
    )
    scope = set(a.bundles)
    if a.changed_only:
        scope |= changed_bundles(root, a.changed_only)
        if not scope:
            findings = []
    if scope:
        findings = [f for f in findings if any(f"/{b}/" in f.path for b in scope)]
    findings.sort(key=lambda f: (f.path, f.line, f.rule))
    emit(findings, a.format)
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    if a.summary:
        counts: dict[tuple[str, str], int] = {}
        for f in findings:
            counts[(f.rule, f.level)] = counts.get((f.rule, f.level), 0) + 1
        for (rule, level), n in sorted(counts.items()):
            log.info("  %s %-7s %d", rule, level, n)
    log.info("%d files, %d errors, %d warnings", n_files, errors, warnings)
    if errors or (a.max_warnings >= 0 and warnings > a.max_warnings):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logging.getLogger("lint_posts").exception("internal error: %r", exc)
        sys.exit(2)
