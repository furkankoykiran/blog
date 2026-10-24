---
title: "MongoDB Schema Design with Python and Async PyMongo"
description: "MongoDB schema design in practice: embed or reference, schema validation, ESR index ordering, aggregation and transactions, with Python's async PyMongo API."
date: 2025-01-05T14:00:00+03:00
slug: "mongodb-nosql-data-management"
postType: guide
tags: ["databases", "python", "asyncio", "performance"]
categories: ["Backend"]
coverAlt: "A MongoDB collection modeled with embedded documents and references"
draft: false
---

Like most people coming from relational databases, I started MongoDB by copying tables into collections one to one: `users`, `orders`, `products`, all linked by `_id`. What I got was a slow PostgreSQL without joins. Good MongoDB schema design starts from how the data will be read. This post covers how I answer that question, plus schema validation, indexes, aggregation and transactions, all with Python examples on PyMongo's async API.

If your data is relational and you only need a few flexible fields, PostgreSQL's `JSONB` column usually does the job. I pick MongoDB when the documents are naturally hierarchical and the read patterns are known.

## The Python driver: PyMongo's async API

For years, async MongoDB code in Python meant Motor. On May 14, 2025, MongoDB deprecated Motor in favor of PyMongo's own async API. Motor gets no new features, and the support timeline is in [Motor's README](https://github.com/mongodb/motor):

![The warning in Motor's README: Motor is deprecated in favor of the PyMongo Async API and gets bug fixes only for a limited time](img/motor-deprecation-notice.webp "Source: mongodb/motor README on GitHub (quotation)")

Use `AsyncMongoClient` for new code. Migrating existing Motor code is mostly a matter of changing imports and the client class, as the [PyMongo Async migration guide](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/) shows.

```python
from pymongo import AsyncMongoClient

client = AsyncMongoClient("mongodb://localhost:27017")
db = client["shop"]

# On shutdown:
# await client.close()
```

The client manages its own connection pool, so create one per application. One API detail trips people up: `find()` returns a cursor directly, while `aggregate()` is a coroutine you have to `await`. If you want a Pydantic-based ODM, Beanie 2.x runs on this async API too.

## Embed or reference?

This is the decision I spend the most time on. I ask:

- Are these two pieces of data almost always read together?
- Is the child data bounded, or can it grow without limit?
- Is the child queried on its own or updated from elsewhere?

An order's shipping address and line items are read with the order, bounded in number and frozen once the order is placed. I embed them, and one read returns everything:

```json
{
  "_id": "ord_1001",
  "customer_id": "usr_42",
  "status": "paid",
  "total_cents": 7900,
  "created_at": {"$date": "2024-12-20T09:15:00Z"},
  "shipping_address": {"city": "Istanbul", "district": "Kadikoy"},
  "items": [
    {"sku": "KB-01", "name": "Mechanical keyboard", "qty": 1, "price_cents": 7900}
  ]
}
```

The product name and price are copied into the line item on purpose. The price at the time of purchase should stay fixed, so a catalog price change doesn't rewrite old orders.

Comments on a blog post are different. They can grow without limit, and a single MongoDB document caps at 16 MB. I keep comments in their own collection with a `post_id`, and if the post page needs the latest few fast, I also embed just the last three in the post. That's the subset pattern.

Data that many documents point to and that changes independently, like users and categories, stays in its own collection. Nobody wants to update a million products because a category got renamed.

I don't store money as `float`. It's either integer minor units (`total_cents`) or `Decimal128`.

## Put the schema rules in the database

MongoDB doesn't enforce a schema, but your application always assumes one. A user document without `email` comes back one day as a `KeyError`. I put the rules in the database with `$jsonSchema`:

```python
from pymongo.errors import CollectionInvalid

USER_SCHEMA = {
    "bsonType": "object",
    "required": ["name", "email"],
    "properties": {
        "name": {"bsonType": "string", "minLength": 2},
        "email": {"bsonType": "string", "pattern": "^[^@\\s]+@[^@\\s]+$"},
        "age": {"bsonType": "int", "minimum": 18},
    },
}


async def ensure_users_collection() -> None:
    try:
        await db.create_collection(
            "users",
            validator={"$jsonSchema": USER_SCHEMA},
            validationLevel="strict",
            validationAction="error",
        )
    except CollectionInvalid:  # collection exists: update the rules
        await db.command("collMod", "users", validator={"$jsonSchema": USER_SCHEMA})
```

`validationLevel` takes two values. `strict` applies the rules to every insert and update. `moderate` applies them to inserts and to updates of documents that already pass, so invalid legacy documents can still be updated. Neither checks existing data retroactively. If you add rules later, find the offenders with `{"$nor": [{"$jsonSchema": USER_SCHEMA}]}` and clean them up.

I validate the same shape in the app with Pydantic, so bad input fails early with a readable message:

```python
from pydantic import BaseModel, Field


class UserIn(BaseModel):
    name: str = Field(min_length=2)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    age: int | None = Field(default=None, ge=18)


async def create_user(data: dict) -> str:
    user = UserIn.model_validate(data)
    result = await db.users.insert_one(user.model_dump(exclude_none=True))
    return str(result.inserted_id)
```

## Indexes and the ESR rule

When a query is slow, the first thing I check is whether the plan is a `COLLSCAN` or an `IXSCAN`, and how many documents it examined:

```python
query = {"status": "paid", "total_cents": {"$gte": 100_000}}

plan = await db.command(
    "explain",
    {"find": "orders", "filter": query, "sort": {"created_at": -1}},
    verbosity="executionStats",
)
stats = plan["executionStats"]
print(stats["nReturned"], stats["totalDocsExamined"])
```

If `totalDocsExamined` is far larger than `nReturned`, the index is missing or in the wrong order. For compound indexes I use the ESR rule:

1. Equality: fields matched exactly (`status`) first.
2. Sort: sort fields (`created_at`) next.
3. Range: range filters (`total_cents >= ...`) last.

```python
from pymongo import ASCENDING, DESCENDING

await db.orders.create_index(
    [("status", ASCENDING), ("created_at", DESCENDING), ("total_cents", ASCENDING)]
)
```

An index like `{total_cents: 1, status: 1}` is a poor fit for this query. With the range field first, MongoDB walks the whole price range, filters on `status` afterwards and sorts the result in memory. In the `explain` output you see that as a big gap between `totalDocsExamined` and `nReturned`, plus a `SORT` stage in the plan.

Every index slows writes and takes RAM. I find unused ones with `$indexStats` and drop them.

## The aggregation pipeline

Most of my reporting runs as aggregations. Documents flow through the stages in order. The pipeline below finds the 20 customers who spent the most:

```python
from datetime import datetime, timezone

pipeline = [
    {"$match": {"status": "paid", "created_at": {"$gte": datetime(2024, 1, 1, tzinfo=timezone.utc)}}},
    {"$group": {"_id": "$customer_id", "total_cents": {"$sum": "$total_cents"}, "orders": {"$sum": 1}}},
    {"$match": {"total_cents": {"$gt": 100_000}}},
    {"$sort": {"total_cents": -1}},
    {"$limit": 20},
    {"$lookup": {"from": "users", "localField": "_id", "foreignField": "_id", "as": "customer"}},
    {"$project": {"_id": 0, "name": {"$first": "$customer.name"}, "total_cents": 1, "orders": 1}},
]

cursor = await db.orders.aggregate(pipeline)
top_customers = await cursor.to_list()
```

The order matters. A `$match` at the start can use an index and shrinks everything downstream. Putting `$lookup` after `$limit` means it runs for 20 customers; at the start it would run once per order.

## Use transactions only when you need them

Operations on a single document are always atomic in MongoDB, and with a well-embedded model most writes touch one document. Multi-document transactions have existed since 4.0, but they need a replica set (or sharded cluster) and add locking cost. I use them when two documents really must change together, like moving balance between two wallets:

```python
from pymongo.asynchronous.client_session import AsyncClientSession


class InsufficientFunds(Exception):
    pass


async def transfer(from_id: str, to_id: str, amount_cents: int) -> None:
    async def run(session: AsyncClientSession) -> None:
        debit = await db.wallets.update_one(
            {"_id": from_id, "balance_cents": {"$gte": amount_cents}},
            {"$inc": {"balance_cents": -amount_cents}},
            session=session,
        )
        if debit.modified_count != 1:
            raise InsufficientFunds(from_id)
        await db.wallets.update_one(
            {"_id": to_id}, {"$inc": {"balance_cents": amount_cents}}, session=session
        )

    async with client.start_session() as session:
        await session.with_transaction(run)
```

`with_transaction` retries on transient errors, such as a primary stepping down during an election, which is safer than hand-rolling a `start_transaction` loop. The balance check sits inside the filter, so an underfunded debit never happens and the exception aborts the transaction.

## Replica sets and sharding

I never run a single `mongod` in production. A replica set with at least three members gives you failover and is required for transactions anyway.

Sharding spreads data across several replica sets by shard key. The application never talks to shards directly. It connects to `mongos` routers, which learn from the config servers where each chunk of data lives.

Sharding adds real operational load, and the shard key is the decision that matters most. Pick a monotonically increasing field like a timestamp and every new write lands on the same shard. A high-cardinality field your queries already use, such as `customer_id`, is usually a better start. Most applications fit comfortably in a single replica set, so I'd hold off on sharding until you've measured a need for it.

## What's next

Start by running your slowest query through `explain` and comparing `totalDocsExamined` with `nReturned`. The same modeling questions on the relational side are in [SQLAlchemy 2.0 with MySQL](/en/posts/mysql-sqlalchemy-database-management/). To put MongoDB behind an HTTP API, the `lifespan` and dependency patterns from [building a REST API with FastAPI](/en/posts/python-fastapi-rest-api/) carry over directly. To cache expensive reports, see [Redis caching and session management](/en/posts/redis-caching-session-management/). For reference, see MongoDB's [data modeling guide](https://www.mongodb.com/docs/manual/data-modeling/) and the [PyMongo async docs](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/).
