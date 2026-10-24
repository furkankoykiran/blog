---
title: "SDK and CLI PRs: Jules, NotebookLM, Mission Control"
description: "Four PRs to Jules SDK, notebooklm-py, cli-agent and Mission Control: two merged, two closed. The code, the design choices, and why each one ended as it did."
date: 2026-03-11T18:00:00+03:00
slug: "python-sdk-and-cli-contributions"
postType: contribution
tags: ["python", "typescript", "open-source", "tooling", "ai-agents", "asyncio"]
categories: ["Open Source"]
series: ["Open Source Contributions"]
coverAlt: "Illustration of SDK calls and CLI commands in a terminal window"
draft: false
---

In the first two weeks of March 2026 I opened four PRs against the SDK and CLI layers of AI tools, two in Python projects and two in TypeScript. Two were merged. The other two were closed: one was a feature the maintainer didn't want, and the idea in the other made it in through the maintainer's own PR. This post walks through the code, the design choices, and why each PR ended the way it did.

## Four PRs and how they ended

Statuses and dates come from the PR records on GitHub.

| Project | PR | Language | Topic | Status |
| :--- | :--- | :--- | :--- | :--- |
| google-labs-code/jules-sdk | [#184](https://github.com/google-labs-code/jules-sdk/pull/184) | TypeScript | `session.delete()` | Merged, March 16, 2026 |
| teng-lin/notebooklm-py | [#174](https://github.com/teng-lin/notebooklm-py/pull/174) | Python | Notebook metadata export | Merged, March 11, 2026 |
| amranu/cli-agent | [#7](https://github.com/amranu/cli-agent/pull/7) | Python | `/health` command | Closed |
| builderz-labs/mission-control | [#271](https://github.com/builderz-labs/mission-control/pull/271) | TypeScript | Gateway-optional mode | Closed, folded into #291 |

## Jules SDK: deleting sessions

Jules is Google Labs' asynchronous coding agent, and jules-sdk is its TypeScript client. The API had an endpoint for deleting a session, but the SDK didn't expose it; issue [#105](https://github.com/google-labs-code/jules-sdk/issues/105) tracked the gap. My PR added a `delete()` method to `SessionClient`:

```ts
async delete(): Promise<void> {
  try {
    await this.request(`sessions/${this.id}`, { method: 'DELETE' });
  } catch (error: any) {
    // 404: the session is already gone, still clear the local cache
    if (error.status !== 404) {
      throw error;
    }
  }
  await this.sessionStorage.delete(this.id);
}
```

Two decisions are baked in. A 404 means the session no longer exists, so the local cache is cleared and the call succeeds. Any other error is rethrown without touching the cache, so the API and the cache never drift apart. The request helper only accepted `'GET' | 'POST'`, so I added `'DELETE'` to that union. Three tests cover success, 404 and 403. Usage is one line: `await session.delete();`. davideast merged the PR on March 16:

![Header of jules-sdk PR #184: merged by davideast, four commits, +134 −1](img/jules-sdk-pr-184-header.webp "Jules SDK PR #184. Source: GitHub")

## notebooklm-py: exporting notebook metadata

notebooklm-py is an unofficial Python API and CLI for Google NotebookLM. Google later renamed the product to Gemini Notebook; the library kept its name. In [#171](https://github.com/teng-lin/notebooklm-py/issues/171), a user wanted a notebook's details and its source list in one call. My PR added two things:

- `client.notebooks.get_metadata(notebook_id)` fetches the notebook and its sources concurrently with `asyncio.gather` and returns a `NotebookMetadata`. Sources are reduced to `SourceSummary(kind, title, url)`, and `to_dict()` always emits the same keys, with `null` for missing values.
- A `notebooklm metadata` CLI command with a `--json` flag.

There's also a small safety net: if the notebook reports a non-zero source count but the source listing comes back empty, the code logs a warning, so possible data loss doesn't go unnoticed.

Run `notebooklm login` once, then:

```python
import asyncio
import json

from notebooklm import NotebookLMClient

async def main() -> None:
    async with NotebookLMClient.from_storage() as client:
        notebooks = await client.notebooks.list()
        meta = await client.notebooks.get_metadata(notebooks[0].id)
        print(f"{meta.title}: {len(meta.sources)} sources")
        print(json.dumps(meta.to_dict(), indent=2, default=str))

asyncio.run(main())
```

From the terminal, `notebooklm metadata --json` gives you the same data. The PR was merged on March 11. The library has grown a lot since then and the method now delegates to a separate service layer, but the public API is unchanged.

## cli-agent: a /health command (closed)

cli-agent is a terminal agent that talks to several LLM providers. I proposed a `/health` command to show at a glance which API keys work. It checked six providers (Anthropic, OpenAI, DeepSeek, Google, OpenRouter, Ollama) concurrently with `asyncio.gather` and summarized the results with ✓, ✗ and ○. With tests and README changes, the diff went past 850 lines.

The maintainer's answer was short: not necessary, fork it if it's useful to you, the license is permissive.

Part of that was on me. I opened issue [#6](https://github.com/amranu/cli-agent/issues/6) sixteen seconds before the PR, which is announcing a feature rather than asking about it. For a new feature, open the issue and wait for the maintainer to agree, especially when the diff is that large.

The idea is still handy. Here's a minimal version with three providers:

```python
import asyncio
import os

import httpx

def targets() -> dict[str, tuple[str, dict[str, str]] | None]:
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    return {
        "openai": (
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {openai_key}"},
        ) if openai_key else None,
        "anthropic": (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"},
        ) if anthropic_key else None,
        "ollama": ("http://localhost:11434/api/tags", {}),
    }

async def check(client: httpx.AsyncClient, name: str, target) -> str:
    if target is None:
        return f"○ {name}: not configured"
    url, headers = target
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return f"✓ {name}: {response.elapsed.total_seconds() * 1000:.0f} ms"
    except httpx.HTTPError as exc:
        return f"✗ {name}: {exc}"

async def main() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await asyncio.gather(
            *(check(client, name, target) for name, target in targets().items())
        )
    print("\n".join(results))

asyncio.run(main())
```

With `asyncio.gather` you wait as long as the slowest provider, not the sum of all of them. `httpx.HTTPError` covers both connection failures and 4xx/5xx responses.

## Mission Control: running without the gateway

Mission Control is a self-hosted control panel for AI agents, built with Next.js. In [#270](https://github.com/builderz-labs/mission-control/issues/270), a user reported that the gateway always showed as offline on a VPS. The browser connects to the gateway over WebSocket on non-standard ports (18789 and 18790), and most VPS firewalls block them.

My PR did two things. First, a standalone mode enabled with `NEXT_PUBLIC_GATEWAY_OPTIONAL=true`: instead of a red "Disconnected" warning, the HUD shows a blue "Gateway Optional (Standalone)" badge. The task board, projects, agent registry, cron jobs and webhooks keep working without a gateway; live session updates and agent-to-agent messaging don't.

Second, documentation for the proper fix: proxy the WebSocket traffic through port 443. The general nginx pattern looks like this (adjust the path and port to your setup):

```nginx
location /gateway/ {
    proxy_pass http://127.0.0.1:18789/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 1h;
}
```

Without the `Upgrade` and `Connection` headers, nginx won't forward the WebSocket handshake. `proxy_read_timeout` keeps idle connections from being dropped after the default 60 seconds.

The same day, the maintainer 0xNyk collected the open deployment issues into one PR. [#291](https://github.com/builderz-labs/mission-control/pull/291) bundled two Docker fixes with the gateway-optional behavior and its docs, was merged on March 11, and closed my issue [#289](https://github.com/builderz-labs/mission-control/issues/289) along the way. #271 was closed with a note pointing to #291:

![Header of mission-control PR #271: closed, four commits from the fix/270-gateway-optional-mode branch, +173](img/mission-control-pr-271-header.webp "Mission Control PR #271. Source: GitHub")

The `NEXT_PUBLIC_GATEWAY_OPTIONAL` line is in the project's `.env.example`.

## What four PRs taught me

- Both merged PRs solved a well-scoped issue that someone else had opened.
- In Jules SDK the new method reused the existing `request` helper and test layout, so there was little to discuss in review.
- In Mission Control my PR was closed, the idea shipped through the maintainer's PR, and the user's problem got fixed.

## What's next

My other contributions from those weeks are in [MCP open source contributions](/en/posts/mcp-ai-open-source-contributions/) and [security and API fixes](/en/posts/security-and-api-fixes/). For more on `asyncio.gather` and async clients, see [Python asyncio for concurrent processing](/en/posts/python-asyncio-concurrent-processing/). If you're looking for a first SDK contribution, start with issues of the form "the API supports this but the SDK doesn't". The scope is clear and the change is easy to test. For a Go example, see [my Keploy contribution](/en/posts/my-open-source-contribution-keploy/).
