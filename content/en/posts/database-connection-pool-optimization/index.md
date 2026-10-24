---
title: "Database Connection Pooling: SQLAlchemy and PgBouncer"
description: "Database connection pooling for PostgreSQL: SQLAlchemy 2 async pool settings, sizing limits per process, leaks, timeouts and PgBouncer transaction mode."
date: 2025-11-01T10:00:00+03:00
slug: "database-connection-pool-optimization"
postType: guide
tags: ["databases", "python", "performance", "asyncio"]
categories: ["Backend"]
coverAlt: "A connection pool sharing database connections between an application and PostgreSQL"
draft: false
---

Database connection pooling is one of the first things to check when a Python service falls over under load. If PostgreSQL logs `FATAL: sorry, too many clients already`, or SQLAlchemy raises `QueuePool limit of size 5 overflow 10 reached`, the problem is most likely how you manage connections, not your queries.

This post covers pool settings with SQLAlchemy 2's async engine, how to work out how many connections you can afford, the common mistakes, and PgBouncer for when the number of app processes grows.

## Why a connection is expensive

Opening a PostgreSQL connection is nothing like a function call:

1. A TCP connection is set up, and in most environments a TLS handshake on top.
2. The client authenticates. SCRAM takes several round trips.
3. PostgreSQL forks a new backend process for the connection, with its own memory.

On the same machine that takes a few milliseconds; across a network with TLS it takes longer. A simple indexed `SELECT` is often faster than that. If you open and close a connection per HTTP request, most of the time goes into handshakes. You also can't raise `max_connections` as far as you like, because every connection is a process with its own memory.

That's what the pool is for: open connections once, keep them, and lend them out between requests.

## An async pool with SQLAlchemy 2

`create_async_engine` uses a queue-based pool by default, with `pool_size=5`, `max_overflow=10` and `pool_timeout=30`. These are my starting values:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

engine = create_async_engine(
    "postgresql+asyncpg://app:secret@db:5432/shop",
    pool_size=10,        # connections kept open
    max_overflow=5,      # extra connections allowed during bursts
    pool_timeout=5,      # wait at most 5 s for a free connection, then raise
    pool_pre_ping=True,  # check the connection is alive before handing it out
    pool_recycle=1800,   # replace connections older than 30 minutes
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_user(user_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT id, email FROM users WHERE id = :id"), {"id": user_id}
        )
        return result.one_or_none()
```

What each setting buys you:

- `pool_pre_ping` runs a cheap liveness check before lending a connection. If the database restarted, or a firewall silently dropped an idle connection, you get a fresh one instead of an error. The cost is small; I always turn it on.
- `pool_recycle` closes and replaces connections past a certain age. If a load balancer or proxy in between kills idle connections, keep it below that timeout.
- `pool_timeout` left at the default 30 seconds means requests hang for 30 seconds when the pool is exhausted. A short value fails fast so you can shed load.

Create the `engine` once per application. A new engine per request is a new pool per request, which defeats the point.

## How many connections can you open?

This is the calculation people skip:

```text
total connections = number of processes × (pool_size + max_overflow)
```

"Processes" doesn't mean pods. If each pod runs several uvicorn or gunicorn workers, each worker has its own pool. With 4 pods, 4 workers per pod and `pool_size=10, max_overflow=5`, the worst case is 4 × 4 × 15 = 240 connections. PostgreSQL's default `max_connections` is 100, and a few of those are reserved for superusers.

Keep the total under `max_connections` and leave room for migrations, cron jobs and your own `psql` session. With autoscaling, do the math with the maximum replica count. When the total gets tight, the next step is PgBouncer.

## Leaks: connections that never come back

When I see `QueuePool limit ... reached`, my first suspect isn't load but connections that never return to the pool. Open a session, forget to close it on an error path, and the connection stays checked out.

The rule is simple: always open sessions with a context manager. SQLAlchemy also has a shortcut that manages the transaction for you:

```python
async def transfer(from_id: int, to_id: int, amount: int) -> None:
    async with SessionLocal.begin() as session:
        await session.execute(
            text("UPDATE accounts SET balance = balance - :a WHERE id = :id"),
            {"a": amount, "id": from_id},
        )
        await session.execute(
            text("UPDATE accounts SET balance = balance + :a WHERE id = :id"),
            {"a": amount, "id": to_id},
        )
    # commit if the block succeeds, rollback on an exception; the connection always goes back
```

In FastAPI I write it as a dependency so every endpoint shares the same lifecycle:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

Don't count on the garbage collector to clean up. You don't know when it runs, and until then the connection is stuck.

## Stay inside the database during a transaction

Another way to drain the pool: open a transaction, make an HTTP call to an external API inside it, then commit. If that API takes 30 seconds, the connection sits `idle in transaction` for 30 seconds and holds its locks the whole time.

Keep transactions short. Make the external call first, then do the database work in one go. Add a safety net on the PostgreSQL side as well:

```sql
ALTER ROLE app SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE app SET statement_timeout = '5s';
```

Setting these on the role is more reliable than sending them on every connection, and it keeps working behind PgBouncer. Give long-running reporting queries their own role.

## When you scale out: PgBouncer

As process counts grow, even a small pool per process pushes the total past `max_connections`. At that point I put a connection pooler like PgBouncer between the app and the database.

![Direct pools push the total past max_connections; through PgBouncer in transaction mode, hundreds of client connections become 20 to 50 real ones](img/pgbouncer-scale-out.webp "PgBouncer sits between many app workers and a few dozen database connections")

PgBouncer has two main modes:

- Session mode: a server connection stays with the client for as long as the client is connected. It barely reduces the total.
- Transaction mode: a server connection is assigned only for the length of a transaction, then goes to another client. This is where the win is: hundreds of client connections served by a few dozen real ones.

The price of transaction mode is losing per-connection state. Session settings made with `SET`, advisory locks, `LISTEN/NOTIFY` and temporary tables don't carry over between transactions. For a long time asyncpg's prepared statements were a problem too. Since 1.21, PgBouncer supports protocol-level prepared statements in transaction mode through `max_prepared_statements`; turn it on. If you're stuck on an older PgBouncer, disable both SQLAlchemy's and asyncpg's statement caches:

```python
engine = create_async_engine(
    # older PgBouncer only: SQLAlchemy and asyncpg statement caches disabled
    "postgresql+asyncpg://app:secret@pgbouncer:6432/shop?prepared_statement_cache_size=0",
    connect_args={"statement_cache_size": 0},
    pool_size=5,
    max_overflow=0,
    pool_pre_ping=True,
)
```

Behind PgBouncer I keep the application pool small. Some teams use `NullPool` in SQLAlchemy and leave all pooling to PgBouncer. That works, but every request then opens a new TCP connection to PgBouncer, so a small app-side pool usually performs better.

## Caching and pooling are different things

People mix these up. A pool keeps the road to the database open; a cache lets you skip the trip. Keeping hot, rarely changing data in Redis takes pressure off the pool, but on a cache miss you still need a solid pool. I cover caching patterns in my [Redis post](/en/posts/redis-caching-session-management/).

## Monitoring

Tuning a pool you can't see is guessing. SQLAlchemy reports the pool's state:

```python
pool = engine.sync_engine.pool
print(pool.status())  # size, checked out, overflow and waiting
print(pool.checkedout(), pool.overflow())
```

Export these numbers to your metrics system, such as Prometheus, on a timer. On the database side, `pg_stat_activity` shows who is connected and what state they're in:

```sql
SELECT usename, application_name, state, count(*)
FROM pg_stat_activity
GROUP BY 1, 2, 3
ORDER BY 4 DESC;
```

Lots of `idle in transaction` rows point back to the external-call problem above.

## Before you go live

- Is `processes × (pool_size + max_overflow)` below `max_connections`, even at the maximum replica count?
- Is `pool_pre_ping` on, and is `pool_recycle` shorter than any proxy's idle timeout?
- Is `pool_timeout` short enough that requests fail fast when the pool is full?
- Does the role have `statement_timeout` and `idle_in_transaction_session_timeout`?
- Is every session opened with a context manager?

## What's next

Run the connection math for your own service first; it explains a lot of "the database is slow" complaints. For the general idea behind `with` and `async with`, see my [context managers post](/en/posts/python-context-managers/), and for SQLAlchemy basics my [SQLAlchemy post](/en/posts/mysql-sqlalchemy-database-management/). The [SQLAlchemy pooling docs](https://docs.sqlalchemy.org/en/20/core/pooling.html) and the [PgBouncer config reference](https://www.pgbouncer.org/config.html) have the details.
