# blog.furkankoykiran.com.tr

[![Deploy](https://github.com/furkankoykiran/blog/actions/workflows/deploy.yml/badge.svg)](https://github.com/furkankoykiran/blog/actions/workflows/deploy.yml)
[![Hugo](https://img.shields.io/badge/Hugo-0.167.0_extended-ff4088?logo=hugo&logoColor=white)](https://gohugo.io/)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)

Source of **[blog.furkankoykiran.com.tr](https://blog.furkankoykiran.com.tr/)**, the engineering blog of Furkan Köykıran: MCP servers and AI agents, open-source contributions, Python trading bots, Python backend and DevOps. Turkish at the root, English under [`/en/`](https://blog.furkankoykiran.com.tr/en/). The portfolio is a separate site at [furkankoykiran.com.tr](https://furkankoykiran.com.tr/).

## Stack

- [Hugo](https://gohugo.io/) extended 0.167.0 with the [Blowfish](https://blowfish.page/) theme (Git submodule, never edited). Home, lists, posts, header, footer, SEO and structured data are the site's own templates in `layouts/`.
- Plain CSS on top of the theme's precompiled Tailwind build, and a few kilobytes of vanilla JavaScript for progressive enhancements.
- Responsive AVIF/WebP images and per-post Open Graph cards generated at build time.
- Python 3.11+ scripts for the post linter, image tooling, IndexNow and dev.to cross-posting.
- GitHub Actions and GitHub Pages for checks, builds and hosting.

## Local development

```sh
git clone --recurse-submodules https://github.com/furkankoykiran/blog.git
cd blog
hugo server                  # http://localhost:1313/
hugo server -D               # include drafts
hugo --gc --minify           # production build into public/
```

Use Hugo **extended 0.167.0** (the version CI uses). If you cloned without `--recurse-submodules`, run `git submodule update --init --recursive` first.

The scripts need Python 3.11+. The linter runs on the standard library alone; for every check and for the image tooling install:

```sh
python -m pip install pyyaml jsonschema pillow pygments websocket-client
```

## Writing a post

Each post has one bundle name, its English slug, in three content trees:

```
content/
├── tr/posts/<bundle>/index.md     Turkish  → /posts/<tr-slug>/
├── en/posts/<bundle>/index.md     English  → /en/posts/<bundle>/
└── media/posts/<bundle>/          shared by both languages
    ├── feature.webp               cover (scripts/make_covers.py)
    ├── credits.yaml               source and licence of every image
    └── img/                       screenshots, logos and other shared images
```

TR and EN pages pair by their identical path, which also produces the hreflang links. An image whose text differs per language (a diagram, a card) lives in the language tree instead (`content/<lang>/posts/<bundle>/img/x.webp`), and both pages reference it as `img/x.webp`.

Old URLs are kept with `aliases` in front matter. Hugo prefixes EN aliases with `/en/`, so an EN page's alias for a URL outside `/en/` is written as `/../posts/<old-slug>/`.

1. **Create the bundle.** `python scripts/new_post.py my-english-slug --tr-slug turkce-baslik --type guide` writes both drafts from `archetypes/posts.md` with the same date, creates `content/media/posts/my-english-slug/` with `img/` and `credits.yaml`, and generates the cover.
2. **Write.** Front matter follows `schemas/frontmatter.schema.json`: title, description, date, slug, `postType`, 3-6 tags from the vocabulary, one category, `coverAlt` and `draft`; `lastmod` only after a meaningful update. Images are `![alt](img/name.webp "caption")`; callouts use GitHub alert syntax (`> [!TIP]`); code fences always name a language.
3. **Add images.** Image cards (step flows, comparisons, before/after) are HTML templates filled from a YAML file:

   ```sh
   python scripts/images/render.py card scripts/images/templates/data/<bundle>/<name>.yaml \
     "content/{lang}/posts/<bundle>/img/<name>.webp"
   ```

   Diagrams are written in Mermaid in the post and turned into static SVG with `python scripts/render_diagrams.py`. Screenshots, terminal cards, logos and licence checks are covered in [`scripts/images/README.md`](scripts/images/README.md).
4. **Credit every image** in `content/media/posts/<bundle>/credits.yaml` (`python scripts/images/credits.py add …`). Third-party images, logos and screenshots of other projects are listed under the post automatically.
5. **Lint** until the linter reports 0 errors and 0 warnings:

   ```sh
   python scripts/lint_posts.py              # all posts
   python scripts/lint_posts.py <bundle>     # one bundle
   python -m unittest discover -s tests      # the linter's own tests
   ```

   The linter checks front matter (schema, TR/EN pairing, honest `lastmod`), prose (intro, headings, internal links, alt text, reader address) and media (`credits.yaml`, size budgets, formats). `--changed-only BASE_REF` adds the git-aware rules for changed `lastmod`, `date` and slugs.
6. **Publish** by setting `draft: false` in both languages.

## Scripts

All scripts take `--help`, read their defaults from the repository (`config/_default/*.toml`) and log to stderr. Shared helpers live in `scripts/common.py`.

| Script | Purpose |
|---|---|
| `scripts/new_post.py` | Creates a TR/EN post bundle with its media folder, credits file and cover. |
| `scripts/lint_posts.py` | Lints every post: front matter, prose, images and credits. |
| `scripts/make_covers.py` | Draws `feature.webp` covers from the bundle name and category with headless Chrome. |
| `scripts/render_diagrams.py` | Converts Mermaid blocks to `.mmd` sources and renders them to static SVG, with credits. |
| `scripts/images/*.py` | Image cards, screenshots, terminal cards, logos, optimisation and credits; see [`scripts/images/README.md`](scripts/images/README.md). |
| `scripts/check-old-urls.py` | Checks that every URL of the previous blog (`data/legacy_urls.json`) still resolves in a build. |
| `scripts/check_links.py` | Checks that every internal link and asset in a build resolves. |
| `scripts/blocklist.py` | Manages `data/blocklist.toml`, a hashed list of names that must never be published; `check` scans files for them. |
| `scripts/indexnow.py` | `snapshot`, `diff` and `submit` steps that report changed URLs to IndexNow after a deploy. |
| `scripts/syndicate_devto.py` | Cross-posts English posts to dev.to with the blog as canonical URL (`--dry-run` to preview). |

Lint the scripts with `ruff check scripts tests && ruff format --check scripts tests`; the configuration is in `pyproject.toml`.

## Deployment

| Workflow | When | What |
|---|---|---|
| `.github/workflows/deploy.yml` | push to `main`, on demand | Lints the posts, builds with Hugo, checks old URLs and internal links, deploys to GitHub Pages, then notifies IndexNow and the WebSub hub. |
| `.github/workflows/lint.yml` | pull requests | Ruff, linter tests, the post linter with the git-aware rules, diagram and blocklist checks, a build that fails on any warning, the old-URL and link checks. |
| `.github/workflows/lighthouse.yml` | pull requests touching the site | Lighthouse CI against the budgets in `.lighthouserc.json`. |
| `.github/workflows/syndicate.yml` | after a deploy, every six hours, on demand | Cross-posts new English posts to dev.to and commits the article ids to `.syndication/state.json`. |

Dependabot keeps the theme submodule and the actions up to date.

One-time setup on a new repository: under *Settings → Pages*, set **Source** to **GitHub Actions** and enter `blog.furkankoykiran.com.tr` as the custom domain. `static/CNAME` documents the domain, but a Pages site deployed by Actions takes it from the settings.

Analytics, verification and comment IDs are optional and are not committed. Set them as repository variables (*Settings → Secrets and variables → Actions → Variables*); the deploy workflow passes them to Hugo as `HUGO_PARAMS_*`, and unset ones render nothing.

| Variable | Used for |
|---|---|
| `GA4_ID` | Google Analytics 4 measurement ID |
| `CLARITY_ID` | Microsoft Clarity project ID |
| `CF_BEACON_BLOG` | Cloudflare Web Analytics token |
| `GOOGLE_SITE_VERIFICATION` | Google Search Console verification |
| `BING_SITE_VERIFICATION` | Bing Webmaster Tools verification |
| `YANDEX_VERIFICATION` | Yandex Webmaster verification |
| `GISCUS_REPO_ID` | giscus repository ID; comments appear once this and the category ID are set |
| `GISCUS_CATEGORY_ID` | giscus discussion category ID |
| `INDEXNOW_KEY` | IndexNow key; `static/<key>.txt` must contain the same key |
| `SYNDICATE_SINCE` | Only posts dated on or after this day go to dev.to (default `2026-10-25`) |
| `SYNDICATION_DELAY_DAYS` | Days a post stays on the blog before it goes to dev.to (default `3`) |

| Secret | Used for |
|---|---|
| `DEVTO_API_KEY` | dev.to API key for cross-posting; without it the workflow does nothing |

The consent banner and analytics scripts are only rendered when at least one analytics ID is set.

## Machine-readable outputs

| Path | What |
|---|---|
| `/feed.xml`, `/en/feed.xml` | RSS 2.0 with full content, latest 30 posts |
| `/sitemap.xml`, `/en/sitemap.xml` | Sitemaps with hreflang and image entries; thin tag pages left out |
| `/llms.txt`, `/llms-full.txt` (and under `/en/`) | Site summary and full text for language models |
| `<post URL>index.md` | Markdown version of every post |
| `/syndication.json`, `/en/syndication.json` | Post data for the cross-posting job |
| `/index.json` | Search index |

## License

The code is released under the [MIT License](LICENSE). The posts, images, covers, photos, logo and other branding are © Furkan Köykıran, all rights reserved; see [LICENSE-content](LICENSE-content). Third-party images are credited per post in `credits.yaml`, and LICENSE-content lists the third-party components (Blowfish, Font Awesome icons, Fuse.js, the Inter and IBM Plex Mono fonts, Mermaid) and their licences.
