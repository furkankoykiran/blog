"""Fixtures for rule BD004 (references to an earlier version of a post).

Every pattern in scripts/lint_posts.py needs a sentence that must fail and one that
must pass. The passing set holds legitimate project or PR history, which the
standard allows.

Run: python -m unittest discover -s tests
"""

from __future__ import annotations

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import lint_posts as L

MUST_FAIL = {
    "tr": [
        "Eski yazıda burada Argon2 da önermiştim.",
        "Eski yazıdaki örnek doğrulamayı bir middleware içinde yapıyordu.",
        "Bu yazının ilk halinde `public/CNAME` dosyasına kök alan adını yazmayı öneriyordum.",
        "Toplam 19 araç. Yazının ilk halinde ve README'de 15 araç yazıyordu, burada düzeltiyorum.",
        "Bu yazıyı güncellerken 10 WETH için çalıştırdığımda sıralama şöyleydi.",
        "Sürüm, bu yazıyı güncellediğim Ekim 2026'daki son sürüme ait.",
        "Eski yazılarımda python-binance kütüphanesini kullanmıştım.",
        "Bu yazının ilk hâli 2024'te yazılmıştı.",
        "## Güncelleme: blog artık Hugo'da",
        "Daha önce burada önermiştim; artık bu yöntemi kullanmıyorum.",
        "Redis 8 artık AGPLv3 seçeneği de sunuyor (güncelleme: Ekim 2026).",
        "Bu yazının İngilizce sürümünde bu bölüm yoktu.",
    ],
    "en": [
        "The old version of this post also suggested Argon2 here.",
        "The first version of this post told you to put the root domain in public/CNAME.",
        "When I ran it while updating this post, a 10 WETH round trip came back with 8.44 WETH.",
        "My original post sold this as capital-free arbitrage.",
        "The original post said API responses go through Zod too.",
        "That changed the week this post was updated.",
        "When I first wrote this post in 2025, MinIO was the default.",
        "What changed compared with the example in my original post:",
        "## Update: the blog now runs on Hugo",
        "I recounted the code, and it's 19; I'm correcting it here.",
        "Unlike the original post, this version uses uv.",
        "Update (2026): the endpoint now needs a token.",
        "The Turkish version of this post used to say Python SDK.",
    ],
}

MUST_PASS = {
    "tr": [
        "İlk sürümü 5 Şubat 2026 gecesi, yaklaşık iki saatlik bir oturumda yazdım.",
        "Eski yazılarda sık görülen discovery.zen.minimum_master_nodes ayarı Elasticsearch 7 "
        "ile kalktı.",
        "API'de create uç noktası yoktu, ben ekledim.",
        "Eski sürümlerde bu bayrak yoktu.",
        "PR'ın ilk hâlinde düz bir defer vardı; review sonrası değişti.",
    ],
    "en": [
        "My first version of the PR used a plain defer f.Close().",
        "The first version had tools for leaving reactions on articles.",
        "In v7 web3.py dropped geth_poa_middleware.",
        "The tool had no create endpoint, so I added one.",
        "Older releases of Poetry used a different lock format.",
        "The Turkish version of the docs is out of date.",
    ],
}


def matching(lang: str, text: str) -> list[str]:
    return [rx for rx in L.BANNED_EARLIER_VERSION[lang] if re.search(rx, text, re.I | re.M)]


class BannedPhraseTest(unittest.TestCase):
    def test_earlier_version_references_fail(self) -> None:
        for lang, sentences in MUST_FAIL.items():
            for s in sentences:
                with self.subTest(lang=lang, sentence=s):
                    self.assertTrue(matching(lang, s), f"should be an error: {s}")

    def test_project_history_passes(self) -> None:
        for lang, sentences in MUST_PASS.items():
            for s in sentences:
                with self.subTest(lang=lang, sentence=s):
                    self.assertEqual(matching(lang, s), [], f"false positive: {s}")

    def test_every_pattern_has_a_failing_fixture(self) -> None:
        for lang, patterns in L.BANNED_EARLIER_VERSION.items():
            for rx in patterns:
                with self.subTest(lang=lang, pattern=rx):
                    self.assertTrue(
                        any(re.search(rx, s, re.I | re.M) for s in MUST_FAIL[lang]),
                        f"pattern without a must-fail fixture: {rx}",
                    )


if __name__ == "__main__":
    unittest.main()
