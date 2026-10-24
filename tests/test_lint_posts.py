"""Tests for scripts/lint_posts.py on a tiny, throw-away site.

Run: python -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import lint_posts as L
from common import term_hash

ROOT = pathlib.Path(__file__).resolve().parents[1]

INTRO = {
    "tr": (
        "Bu yazıda Redis ile önbellekleme yaparken kullandığım düzeni anlatıyorum. "
        "Örnekler Python 3.12 ve redis-py 5.2 ile çalıştırıldı. Hangi anahtarın ne kadar "
        "yaşayacağını, önbelleğin ne zaman temizleneceğini ve oturum verisinin nerede "
        "durduğunu kendi projelerimden aldığım gerçek örneklerle adım adım gösteriyorum."
    ),
    "en": (
        "This post walks through the caching layout I use with Redis in Python services. "
        "The examples ran on Python 3.12 and redis-py 5.2. It covers how long each key lives, "
        "when the cache is cleared and where session data is kept, with examples taken from "
        "my own projects and their code."
    ),
}
FRONT = {
    "tr": {
        "title": "Redis ile önbellekleme ve oturum yönetimi",
        "slug": "redis-onbellekleme",
        "categories": '["Backend"]',
        "description": "Redis ile önbellekleme ve oturum yönetimi: anahtar süreleri, "
        "cache-aside deseni, temizleme ve Python örnekleriyle oturum verisinin saklanması.",
        "coverAlt": "Bir Python servisinin önünde duran Redis önbelleğini gösteren çizim",
    },
    "en": {
        "title": "Redis caching and session management in Python",
        "slug": "redis-caching",
        "categories": '["Backend"]',
        "description": "Redis caching and session management in Python: key lifetimes, the "
        "cache-aside pattern, invalidation and where session data should live.",
        "coverAlt": "Drawing of a Redis cache sitting in front of a Python service",
    },
}


def page(lang: str, extra_fm: str = "", body_extra: str = "") -> str:
    f = FRONT[lang]
    other = "/en/posts/redis-caching/" if lang == "en" else "/posts/redis-onbellekleme/"
    return f"""---
title: "{f["title"]}"
description: "{f["description"]}"
date: 2026-03-01T10:00:00+03:00
slug: "{f["slug"]}"
postType: guide
tags: ["redis", "python", "databases"]
categories: {f["categories"]}
coverAlt: "{f["coverAlt"]}"
draft: false
{extra_fm}---

{INTRO[lang]}

## Cache-aside

![{f["coverAlt"]}](img/flow.svg "Cache-aside")

Text with a [related post]({other}) and [its cache section]({other}#cache-aside) plus [the docs](https://redis.io/docs/).

```python
print("ok")
```
{body_extra}
"""


class LintSiteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "schemas").mkdir()
        shutil.copy(ROOT / "schemas" / "frontmatter.schema.json", self.tmp / "schemas")
        media = self.tmp / "content" / "media" / "posts" / "redis-caching"
        (media / "img").mkdir(parents=True)
        (media / "img" / "flow.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>', encoding="utf-8"
        )
        (media / "credits.yaml").write_text(
            '- file: "img/flow.svg"\n  kind: "diagram"\n  license: "own"\n', encoding="utf-8"
        )
        self.write("tr")
        self.write("en")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, lang: str, extra_fm: str = "", body_extra: str = "") -> None:
        d = self.tmp / "content" / lang / "posts" / "redis-caching"
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.md").write_text(page(lang, extra_fm, body_extra), encoding="utf-8")

    def errors(self) -> list[L.Finding]:
        findings, _ = L.run(self.tmp, self.tmp / "schemas" / "frontmatter.schema.json")
        return [f for f in findings if f.level == "error"]

    def rules(self) -> set[str]:
        return {f.rule for f in self.errors()}

    def test_valid_pair_has_no_errors(self) -> None:
        self.assertEqual(self.errors(), [])

    def test_workflow_key_is_forbidden(self) -> None:
        self.write("en", extra_fm='notes: "check the benchmark numbers"\n')
        self.assertIn("FM004", self.rules())

    def test_lastmod_before_date_is_rejected(self) -> None:
        self.write("tr", extra_fm="lastmod: 2026-02-01T10:00:00+03:00\n")
        self.assertIn("FM008", self.rules())

    def test_shared_lastmod_is_a_bulk_stamp(self) -> None:
        for i in range(5):
            bundle = f"redis-caching-{i}"
            for lang in ("tr", "en"):
                d = self.tmp / "content" / lang / "posts" / bundle
                d.mkdir(parents=True)
                text = page(lang, extra_fm="lastmod: 2026-04-01T10:00:00+03:00\n")
                (d / "index.md").write_text(text, encoding="utf-8")
        self.assertTrue(any("bulk" in f.msg for f in self.errors() if f.rule == "FM008"))

    def test_en_slug_must_match_bundle(self) -> None:
        FRONT["en"]["slug"], old = "redis-caching-guide", FRONT["en"]["slug"]
        try:
            self.write("en")
            self.assertIn("FM009", self.rules())
        finally:
            FRONT["en"]["slug"] = old

    def test_earlier_version_reference_is_an_error(self) -> None:
        self.write("en", body_extra="\nThe original post said the TTL was ten minutes.\n")
        self.assertIn("BD004", self.rules())

    def test_code_does_not_trigger_prose_rules(self) -> None:
        self.write(
            "en", body_extra="\n```text\nThe original post said the TTL was ten minutes.\n```\n"
        )
        self.assertNotIn("BD004", self.rules())

    def blocklist(self, term: str, word: bool = False) -> None:
        data = self.tmp / "data"
        data.mkdir(exist_ok=True)
        flag = "\n  word = true" if word else ""
        entry = f'[[terms]]\n  length = {len(term)}\n  sha256 = "{term_hash(term)}"{flag}\n'
        (data / "blocklist.toml").write_text(entry, encoding="utf-8")

    def test_blocked_names_are_matched_by_hash(self) -> None:
        self.blocklist("zzyzxcorp")
        self.write("tr", body_extra="\nZzyzx Corp burada geçmemeli.\n")
        self.assertIn("BD013", self.rules())

    def test_blocked_word_does_not_match_inside_longer_words(self) -> None:
        self.blocklist("zzyx", word=True)
        self.write("en", body_extra="\nThe zzyxian layer stays.\n")
        self.assertNotIn("BD013", self.rules())
        self.write("en", body_extra="\nZzyx must not appear.\n")
        self.assertIn("BD013", self.rules())

    def test_image_without_credit(self) -> None:
        (self.tmp / "content" / "media" / "posts" / "redis-caching" / "credits.yaml").write_text(
            "[]\n", encoding="utf-8"
        )
        self.assertIn("MD001", self.rules())

    def test_language_override_image_is_found(self) -> None:
        d = self.tmp / "content" / "tr" / "posts" / "redis-caching" / "img"
        d.mkdir(parents=True)
        (d / "flow.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>', encoding="utf-8"
        )
        self.assertEqual(self.errors(), [])

    def test_missing_translation(self) -> None:
        (self.tmp / "content" / "tr" / "posts" / "redis-caching" / "index.md").unlink()
        self.assertIn("FM016", self.rules())

    def test_unknown_internal_link(self) -> None:
        self.write("en", body_extra="\nSee [this](/en/posts/does-not-exist/).\n")
        self.assertIn("BD007", self.rules())

    def test_as_of_newer_than_date(self) -> None:
        self.write("en", body_extra="\nIt had 18,000 stars as of October 2026.\n")
        self.assertIn("BD017", self.rules())

    def warning_rules(self) -> set[str]:
        findings, _ = L.run(self.tmp, self.tmp / "schemas" / "frontmatter.schema.json")
        return {f.rule for f in findings if f.level == "warning"}

    def test_few_images_are_fine(self) -> None:
        self.assertNotIn("BD016", self.warning_rules())

    def test_more_than_three_body_images_warn(self) -> None:
        extra = "".join(
            f"\nText before image {i}.\n\n![Diagram number {i} of the cache](img/x{i}.svg)\n"
            for i in range(3)
        )
        self.write("en", body_extra=extra)
        self.assertIn("BD016", self.warning_rules())

    def cli(self, *args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return L.main(["--root", str(self.tmp), "--format", "json", *args])

    def test_cli_exit_codes(self) -> None:
        self.assertEqual(self.cli(), 0)
        self.write("en", extra_fm='translationKey: "x"\n')
        self.assertEqual(self.cli(), 1)
        self.assertEqual(self.cli("redis-caching-other"), 0)  # findings outside the scope


if __name__ == "__main__":
    unittest.main()
