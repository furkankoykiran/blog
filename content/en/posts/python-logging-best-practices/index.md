---
title: "Python Logging Best Practices with structlog"
description: "Python logging best practices: stdlib logging rules, JSON logs with structlog, request IDs, redacting secrets, OpenTelemetry trace IDs, common mistakes."
date: 2025-02-22T13:00:00+03:00
slug: "python-logging-best-practices"
postType: guide
tags: ["python", "monitoring", "fastapi", "devops"]
categories: ["Python"]
coverAlt: "JSON logs flowing from a Python application through a log collector into a log store"
draft: false
---

The two extremes I see most in Python logging are sprinkling `print` everywhere and abandoning the `logging` module for some other library. Neither is necessary. The setup I use follows Python logging best practices: it keeps `logging` as the backbone and adds structured (JSON) output on top with structlog. That way your own code and libraries like uvicorn, SQLAlchemy and httpx all log in the same format.

I use structlog in one of my trading bots as well. The setup below is a good starting point for a new service; I ran the examples with Python 3.13 and structlog 26.1.

## First, the rules of the logging module

Before structlog, a few rules for using `logging` well. They still apply when you add structlog.

Get a named logger per module:

```python
import logging

logger = logging.getLogger(__name__)


def fetch_prices(symbol: str) -> None:
    logger.info("fetching prices for %s", symbol)
```

The `%s` style is deliberate. With an f-string, the message is built every time, even when that log level is off. Tools like Sentry also can't group errors coming from the same template, because each message is a different string.

Configure handlers, format and level once, at your application's entry point. If you write a library, don't configure logging at all; just add a `NullHandler` in the package's `__init__.py`:

```python
# mylib/__init__.py
import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
```

Calling `logging.basicConfig()` inside a library messes with the logging setup of every app that uses it.

When you catch an error, use `logger.exception()`, which attaches the traceback:

```python
try:
    place_order(order)
except OrderRejected:
    logger.exception("order rejected: %s", order.id)
    raise
```

## Why JSON logs

Plain-text logs are readable for people, not for machines:

```text
2026-10-23 13:00:00 ERROR User 123 failed to purchase item 456 using coupon SAVE10
```

Pulling the coupon code out of that line needs a regex, and the regex breaks the day someone rewords the message. The same event as JSON:

```json
{"event": "purchase_failed", "user_id": 123, "item_id": 456, "coupon": "SAVE10", "error_code": "INSUFFICIENT_FUNDS", "level": "error", "timestamp": "2026-10-23T10:00:00Z"}
```

Now you can query `event="purchase_failed" AND coupon="SAVE10"` in Loki, Elasticsearch or CloudWatch Logs Insights. The core idea is a stable event name (`purchase_failed`) with the variable parts in separate fields.

Those logs go from the app to stdout as JSON, then through the container runtime and a collector (Fluent Bit, Vector or Alloy) into Loki or Elasticsearch, and you read them in Grafana or Kibana.

## Wiring structlog into logging

You can use structlog on its own, but then third-party libraries log in a different format. With `ProcessorFormatter`, structlog acts as a formatter for the standard `logging` module and runs records from both sources through the same processor chain:

```python
# logging_config.py
import logging
import sys

import structlog


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    # Logs written through structlog.get_logger()
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    if json_logs:
        renderers = [
            # show_locals=False: keep local variables (passwords included) out of logs
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ]
    else:
        renderers = [structlog.dev.ConsoleRenderer()]

    # Formats both structlog and logging.getLogger() records
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[*shared_processors, structlog.stdlib.ExtraAdder()],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            *renderers,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn installs its own handlers; send its logs through root instead
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
```

Using it:

```python
import logging
import os

import structlog

from logging_config import configure_logging

configure_logging(json_logs=os.getenv("LOG_JSON", "1") == "1")

log = structlog.get_logger(__name__)
log.info("order_created", order_id=123, amount=49.9)

logging.getLogger("third_party").warning("disk %s%% full", 91)
```

Both lines come out in the same format:

```json
{"order_id": 123, "amount": 49.9, "event": "order_created", "level": "info", "logger": "__main__", "timestamp": "2026-10-23T10:00:00.123456Z"}
{"event": "disk 91% full", "level": "warning", "logger": "third_party", "timestamp": "2026-10-23T10:00:00.124001Z"}
```

Exceptions end up as a structured `exception` field in the JSON. Don't skip `show_locals=False`: by default the transformer also writes every frame's local variables, so a password or token sitting in memory at that moment can land in your logs.

For local development I set `LOG_JSON=0` and get readable console output, with colored levels in a real terminal. The same two lines in that mode:

![app.py run with LOG_JSON=0: order_created and disk 91% full printed as aligned console lines with timestamp, level and logger name](img/structlog-console-renderer.webp "structlog's ConsoleRenderer output from a real run of the code above")

In production it's always JSON.

## Context: a request ID on every line

To see all logs for one request together, every line needs the same `request_id`. Instead of passing it to every log call, bind it once with `contextvars`; the `merge_contextvars` processor adds it to each line. It works with asyncio too, because each task carries its own copy of the context. In FastAPI, a middleware is enough:

```python
import uuid

import structlog
from fastapi import FastAPI, Request

app = FastAPI()
log = structlog.get_logger(__name__)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id, method=request.method, path=request.url.path
    )
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    log.info("request_finished", status=response.status_code)
    return response
```

Reusing an incoming `X-Request-ID` keeps the chain intact from your gateway or Nginx. Forward the same header when you call another service.

Background jobs work the same way: bind `task_id` and the relevant business key (`user_id`, `order_id`) at the start of each task. My [Celery vs Arq post](/en/posts/celery-arq-async-task-queue/) explains why that matters for task queues.

## If you use OpenTelemetry, add trace IDs

If you trace across services with OpenTelemetry, putting the trace and span IDs on each log line links logs to traces. A small processor does it:

```python
from opentelemetry import trace


def add_otel_ids(logger, method_name, event_dict):
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
```

Add it to `shared_processors`, and in Grafana you can jump from a log line to its trace in one click.

## Keep secrets out of logs

Once a password, token or card number hits the logs, everyone with access to the log system has it, and under GDPR that's a problem on its own. The best protection is not logging this data at all. Still, set up a safety net in case someone writes `log.info("signup", password=raw)` by mistake:

```python
SENSITIVE_KEYS = {"password", "token", "authorization", "api_key", "secret", "card_number"}


def _redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if isinstance(k, str) and k.lower() in SENSITIVE_KEYS else _redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def redact_sensitive(logger, method_name, event_dict):
    return _redact(event_dict)
```

Add this one to the end of `shared_processors` as well. It walks nested dicts, so it also covers calls like `log.info("webhook", payload=body)`. It won't catch a password embedded in free text, which is why the first rule is still "don't put secrets in messages".

## Performance

Logging isn't free. What I watch for:

- In containers, write to stdout, not files. Let the container runtime and the log collector handle files and rotation.
- If you must use a slow handler that ships logs over the network, move it to a separate thread with `QueueHandler` and `QueueListener` from the standard library so it doesn't block the main thread.
- Don't log inside hot loops. Keep a counter and log one line when the loop ends; that counter is probably a metric anyway.
- The `filter_by_level` processor skips the rest of the chain for disabled levels, so debug logs cost almost nothing in production.

Whether logging is actually a bottleneck is something to measure, not guess; my [Python profiling post](/en/posts/python-performance-profiling-optimization/) shows how.

## Two notes on the log backend

- On Elasticsearch, make sure numeric fields such as `latency_ms` are indexed as numbers, or `latency_ms > 500` won't work. The same field arriving as a number from one service and a string from another causes mapping conflicts, so keep field names consistent across services.
- Logs grow fast. Set up retention from day one (ILM in Elasticsearch, retention settings in Loki); deleting or archiving anything older than 30 days is enough for most teams.

Logs answer "what happened", metrics answer "how often does it happen". To set up the metrics side, see my [Prometheus and Grafana post](/en/posts/prometheus-grafana-monitoring/).

## Common mistakes

- Debugging with `print` and forgetting to remove it. Write `logger.debug` and turn it off with the level.
- f-strings in log calls (`logger.info(f"...")`).
- `basicConfig` inside a library.
- Adding a handler to your own logger while it also propagates to root: every line shows up twice.
- `except: logger.error(str(e))`, which throws away the traceback. Use `logger.exception`.

## What's next

To start in an existing project, call `configure_logging` at the entry point and add the request ID middleware to one endpoint. Once JSON logs are flowing, try filtering a single request's lines by `request_id` in your log system. The [structlog standard library guide](https://www.structlog.org/en/stable/standard-library.html) and Python's [Logging HOWTO](https://docs.python.org/3/howto/logging.html) are the best references from there.

For the metrics side, see my [Prometheus and Grafana post](/en/posts/prometheus-grafana-monitoring/). For carrying context into background jobs, the [Celery vs Arq post](/en/posts/celery-arq-async-task-queue/) is the next read.
