# Image toolkit

Scripts that put images into a post bundle and record where each one came from.
Every script has `--help`, logs to stderr and exits non-zero on failure. Run them
from the repository root.

```bash
python -m pip install pillow pygments pyyaml websocket-client
```

Chrome (or Chromium) is found automatically on Linux, macOS and Windows. Set
`CHROME=/path/to/chrome` to use a specific binary.

## Where images go

| Image | Path |
|---|---|
| Same in both languages (screenshots, logos) | `content/media/posts/<bundle>/img/<name>.webp` |
| Text differs per language (diagrams, cards, localized UI) | `content/tr/posts/<bundle>/img/<name>.webp` and `content/en/posts/<bundle>/img/<name>.webp` |
| Cover | `content/media/posts/<bundle>/feature.webp` (`scripts/make_covers.py`) |

Both language files reference the same relative path (`img/<name>.webp`). Use
descriptive, hyphenated file names (`pr-3927-header.webp`, not `image1.webp`).

## Scripts

| Script | What it does |
|---|---|
| `render.py` | Own graphics: `card` (a template filled from a YAML file, one image per language), `code` cards, `terminal` transcripts, `mermaid` diagrams as static SVG, and screenshots of local `html` files. |
| `shot.py` | Screenshot of a real page (repository, PR, docs, a local tool), cropped to a CSS selector, as WebP. Uses a fresh browser profile, so nothing personal appears. `--media-dir` records the credit. |
| `optimize.py` | Converts PNG, JPEG, GIF or WebP to metadata-free WebP within the size budget; checks SVGs. |
| `logo.py` | Official product logo (SVG) from thesvg.org, with its credit. Only to identify a product. |
| `license_check.py` | Checks the licence of a Wikimedia Commons or GitHub-hosted image and records the credit when it is usable. |
| `credits.py` | Adds (`add`) or lists (`list`) entries in a bundle's `credits.yaml`. |

Diagrams in posts are handled by `scripts/render_diagrams.py`, which keeps the
Mermaid source (`.mmd`) next to each SVG, re-renders changed ones and records
the `kind: diagram` credit.

## Image cards

A card is an HTML template from `templates/` (`steps`, `compare`,
`before-after`, `annotated-shot`) filled with a data file. The data files live
in `templates/data/<bundle>/<name>.yaml`: they name the template, hold the
shared values and a `langs` map with the text for each language.

```bash
python scripts/images/render.py card scripts/images/templates/data/<bundle>/<name>.yaml \
  "content/{lang}/posts/<bundle>/img/<name>.webp"
python scripts/images/credits.py add content/media/posts/<bundle> img/<name>.webp --kind diagram \
  --author "Furkan Köykıran" --license own \
  --note "Rendered with scripts/images/render.py card; one version per language."
```

Cards are laid out at 1280 CSS px, captured at 2x and saved 1600 px wide. Text
that overflows its box is reported and the command exits with 1.

## credits.yaml

One file per bundle, `content/media/posts/<bundle>/credits.yaml`, with an entry for
every image either language uses, including `feature.webp`. The linter
(`scripts/lint_posts.py`, rule MD001) rejects images without an entry and entries
without a file.

```yaml
- file: "img/pr-3927.webp"
  kind: "screenshot"     # screenshot | diagram | render | logo | third-party | cover
  source: "https://github.com/keploy/keploy/pull/3927"   # required for screenshot, logo, third-party
  author: "GitHub / keploy contributors"
  license: "quotation"   # own | CC0-1.0 | PD | CC-BY-4.0 | CC-BY-SA-4.0 | MIT | Apache-2.0 | quotation
  license_url: "https://..."
  captured: "2026-10-23"
  note: "..."
```

Third-party images, logos and screenshots of other people's pages (`license:
quotation`) are listed under the post as "Görsel kaynakları" / "Image credits"
(`layouts/partials/fk/credits.html`). Third-party images also need a caption with
the credit: `Kaynak: <author>, <licence>` / `Source: <author>, <licence>`.

## Rules of thumb

- Real beats decorative: own screenshots first, then own diagrams and cards,
  then official logos, then licence-checked third-party images.
- Only CC0, public domain, CC BY, CC BY-SA, permissive code licences or a written
  press-kit permission. Never NC/ND, "all rights reserved" or unknown.
- Alt text in the page's language, 10-160 characters, saying what the image shows.
- Budgets: raster ≤ 400 KB, cover ≤ 300 KB, SVG ≤ 100 KB. No JPEG or GIF in content.
- Redact tokens, e-mail addresses, host names and IPs before committing a capture.

## Examples

```bash
python scripts/images/shot.py https://github.com/keploy/keploy/pull/3927 \
  content/media/posts/my-open-source-contribution-keploy/img/pr-3927-header.webp \
  --preset github --selector "[class*='prc-PageLayout-Header']" --pad 8 \
  --media-dir content/media/posts/my-open-source-contribution-keploy --author "GitHub / keploy contributors"
python scripts/images/render.py terminal session.txt content/media/posts/<bundle>/img/run.webp --title bash
python scripts/images/logo.py --search redis
python scripts/images/logo.py redis content/media/posts/redis-caching-session-management
python scripts/images/license_check.py "https://commons.wikimedia.org/wiki/File:Python-logo-notext.svg" \
  --media-dir content/media/posts/<bundle> --file img/python-logo.svg
python scripts/render_diagrams.py render --force <bundle>
```
