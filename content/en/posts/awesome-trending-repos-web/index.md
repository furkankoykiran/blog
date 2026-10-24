---
title: "awesome-trending-repos: Tracking GitHub Trending Daily"
description: "How my open source tracker scrapes GitHub Trending every day, compares it with past snapshots and publishes a React dashboard to GitHub Pages with Actions."
date: 2026-03-27T18:00:00+03:00
slug: "awesome-trending-repos-web"
postType: project
tags: ["github-actions", "automation", "ci-cd", "web", "open-source"]
categories: ["Projects"]
aliases: ["/../posts/awesome-trending-repos-web-en/", "/../posts/awesome-trending-repos-en/"]
coverAlt: "Flow of a GitHub Actions cron job fetching trending data and publishing it to GitHub Pages"
draft: false
---

[awesome-trending-repos](https://github.com/furkankoykiran/awesome-trending-repos) is a small project of mine that tracks GitHub Trending on its own. Every night at 00:00 UTC a GitHub Actions job scrapes the trending list, compares it with the previous day, commits the result as JSON and redeploys a React dashboard to GitHub Pages. It has run without me touching it since March 2026. This post walks through both versions of the project, the data pipeline, and the mistakes I made along the way.

## Why I built it

I looked at the trending page most days but couldn't remember what was on it yesterday. Seeing which repos were new, which were climbing and which languages were hot meant keeping history somewhere. GitHub doesn't keep it for you, so I started keeping it myself.

## Version 1: a script that wrote the README

The March 17, 2026 release was plain Node.js. `scripts/update.js` scraped the trending page and wrote everything straight into `README.md`: language breakdowns, rank changes, ASCII charts, the fastest risers. The last 7 days of data lived in `.data/`.

It worked, but a README is a poor data UI. No filters, no search, and the charts were text. Ten days later I split the interface out.

## Version 2: JSON data, React front end

[PR #5](https://github.com/furkankoykiran/awesome-trending-repos/pull/5), merged on March 27, split the project in two. The script no longer touches the README; it only writes `public/data/trending.json` and `public/data/feed.json`, and the README became a static landing page. The front end is a single-page app that reads that JSON:

- React 19 and Vite 6
- Tailwind CSS v4 through the `@tailwindcss/vite` plugin
- Framer Motion for transitions, Recharts for the language distribution chart
- Language filter, instant search across name, owner and description, and a feed that lists each update

Here is the top of the live site. The Trending, Stats and Feed tabs, the language chips and the search box all read the same JSON:

![The live awesome-trending-repos site: Trending, Stats and Feed tabs, language filters, a search box and repo cards](img/trending-dashboard.webp "The React front end at furkankoykiran.com.tr/awesome-trending-repos")

With 25 repos, filtering doesn't need anything clever:

```javascript
const q = searchQuery.toLowerCase();

const filteredRepos = repos.filter((repo) => {
  const matchesLang = filter === 'All' || repo.language === filter;
  const matchesSearch =
    repo.name.toLowerCase().includes(q) ||
    repo.owner.toLowerCase().includes(q) ||
    (repo.description ?? '').toLowerCase().includes(q);
  return matchesLang && matchesSearch;
});
```

## The data pipeline

The real work happens in `scripts/update.js`:

1. It fetches `github.com/trending/<language>?since=daily` for 15 languages with axios and parses the HTML with cheerio. The all-languages page changed structure at some point, and going language by language turned out to be more reliable.
2. If that yields fewer than 25 repos, the GitHub Search API fills the gap.
3. Invalid entries and anything outside a list of popular languages are dropped.
4. If the list is still short, repos from the last 7 days of snapshots are added.
5. The result is compared with yesterday's snapshot: new entries, risers, fallers and a "rising star".
6. `.data/history.json` keeps at most 7 snapshots, and older snapshot files are deleted.

Two numbers in there are guesses. The Search API doesn't tell you how many stars a repo got today, so for repos that come from search I estimate daily stars as 1% of total stars. Repos pulled back in from history get half of their old daily count. Both are rough fillers, not measurements. Flagging them in the UI is on my list.

## The GitHub Actions workflow

The workflow runs once a day on a cron, on manual dispatch, and on pushes that touch `src/` or `scripts/`. It updates the data, builds, commits any changes and deploys to Pages. Here is a trimmed version of the one in the repo:

```yaml
name: Update and Deploy Trends

on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:
  push:
    branches: [main]
    paths: ['src/**', 'scripts/**', 'package.json', 'package-lock.json']

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  update-and-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version: '22'
          cache: npm
      - run: npm ci
      - run: npm run update
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - run: npm run build
      - name: Commit data changes
        run: |
          if [ -z "$(git status --porcelain .data/ public/data/)" ]; then exit 0; fi
          git config user.name 'github-actions[bot]'
          git config user.email 'github-actions[bot]@users.noreply.github.com'
          git add .data/ public/data/
          git commit -m "chore: update trending repositories [skip ci]"
          git push
      - uses: actions/upload-pages-artifact@v5
        with:
          path: ./dist

  deploy:
    needs: update-and-build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v5
```

The file in the repo still pins `checkout@v4`, `setup-node@v4`, `upload-pages-artifact@v3` and Node 20, which reached end of life in April 2026. If you're starting fresh, use the versions above.

A few details that pull their weight:

- `[skip ci]` keeps the bot's commit from kicking off another run. Pushes made with `GITHUB_TOKEN` don't start new workflow runs anyway, but the marker costs nothing.
- `concurrency` stops two deploys from racing each other.
- The build runs after the update. Vite copies `public/` into `dist/`, so the site always ships with that day's JSON.

My commit step above adds the whole `public/data/` folder. The workflow in the repo only commits `public/data/trending.json` and leaves `feed.json` out. So each run reads the stale feed from the repo and appends one entry, and the live feed always shows the March 27 entry plus today's. The fix is the one line shown above.

## Vite on GitHub Pages: the base path

The site is a project page served under `/awesome-trending-repos/`. If Vite doesn't know that, the built asset URLs point at the domain root and everything 404s in production:

```javascript
// vite.config.js
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/awesome-trending-repos/',
});
```

Vite already adds content hashes to file names in production builds, so you don't need extra `rollupOptions` for cache busting. The app loads its data with `fetch('./data/trending.json')`, a relative path that works with any `base`.

## The custom domain mistake

Putting the root domain in a project's `public/CNAME` is a common mistake, and this repo had exactly that file. `furkankoykiran.com.tr` belongs to my user site (`furkankoykiran.github.io`). Project pages inherit that domain automatically and are served at `furkankoykiran.com.tr/awesome-trending-repos/`. A project repo with the root domain in its own CNAME is trying to claim the same domain as the user site.

[PR #7](https://github.com/furkankoykiran/awesome-trending-repos/pull/7) removed `public/CNAME` and `dist/CNAME`. With `actions/deploy-pages`, a CNAME file inside the artifact is ignored anyway; the domain comes from Settings → Pages → Custom domain, and for a project page that field should stay empty.

## What's next

The project still runs every day. Next on the list: fix the feed bug, mark estimated star counts in the UI, and bump the workflow's action versions.

The live site is at [furkankoykiran.com.tr/awesome-trending-repos](https://furkankoykiran.com.tr/awesome-trending-repos/), and there's a summary on the [project page](https://furkankoykiran.com.tr/en/projects/awesome-trending-repos/). If your Actions minutes are running low, my [self-hosted runner post](/en/posts/github-actions-self-hosted-runner/) shows how I moved CI onto my own server. The story of how this blog ended up on GitHub Pages is in [my GitLab and GitHub migration post](/en/posts/gitlab-github-jekyll-migration/).
