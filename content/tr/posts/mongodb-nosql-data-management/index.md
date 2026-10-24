---
title: "MongoDB Şema Tasarımı: Python ile NoSQL Veri Yönetimi"
description: "MongoDB şema tasarımı: gömme mi referans mı, şema doğrulama, ESR kuralıyla indeks, aggregation ve transaction. PyMongo async API ile çalışan Python örnekleri."
date: 2025-01-05T14:00:00+03:00
slug: "mongodb-nosql-veri-yonetimi"
postType: guide
tags: ["databases", "python", "asyncio", "performance"]
categories: ["Backend"]
coverAlt: "MongoDB'de gömülü belgeler ve referanslarla modellenmiş bir koleksiyon yapısı"
draft: false
---

İlişkisel veritabanlarından MongoDB'ye geçen çoğu kişi gibi ben de ilk başta tabloları koleksiyonlara birebir taşıdım. `users`, `orders`, `products`, hepsi ayrı, hepsi `_id` ile birbirine bağlı. Sonuç, JOIN'i olmayan bir PostgreSQL gibi davranan yavaş bir sistemdi. MongoDB şema tasarımında ilk soru, verinin nasıl okunacağıdır. Bu yazıda bu soruyu nasıl cevapladığımı, şema doğrulamayı, indeksleri, aggregation'ı ve transaction'ları PyMongo'nun async API'siyle yazılmış Python örnekleriyle anlatıyorum.

Verileriniz ilişkiselse ve birkaç esnek alana ihtiyacınız varsa, PostgreSQL'in `JSONB` kolonu çoğu zaman yeter. Ben MongoDB'yi belgeler doğal olarak hiyerarşik olduğunda ve okuma desenleri belli olduğunda seçiyorum.

## Python sürücüsü: PyMongo async API

Async MongoDB kodu için yıllarca Motor kullanıldı. MongoDB, 14 Mayıs 2025'te Motor'u PyMongo'nun kendi async API'si lehine kullanımdan kaldırdı. Motor artık yeni özellik almıyor ve destek takvimi [Motor'un README'sinde](https://github.com/mongodb/motor) yazıyor:

![Motor README'sindeki uyarı: Motor, PyMongo Async API lehine kullanımdan kaldırıldı ve yalnızca sınırlı bir süre hata düzeltmesi alacak](img/motor-deprecation-notice.webp "Kaynak: mongodb/motor README, GitHub (alıntı)")

Yeni projelerde `AsyncMongoClient` kullanın. Mevcut Motor kodunda çoğu zaman import'u ve sınıf adını değiştirmek yetiyor, ayrıntılar [PyMongo Async geçiş rehberinde](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/).

```python
from pymongo import AsyncMongoClient

client = AsyncMongoClient("mongodb://localhost:27017")
db = client["shop"]

# Uygulama kapanırken:
# await client.close()
```

İstemci kendi bağlantı havuzunu yönetir, uygulama başına bir tane oluşturun. API'de dikkat edilecek bir ayrıntı var: `find()` doğrudan bir cursor döndürür, `aggregate()` ise `await` edilmesi gereken bir coroutine. Pydantic tabanlı bir ODM isterseniz Beanie'nin 2.x sürümleri de bu async API üzerinde çalışıyor.

## Gömmek mi, referans vermek mi?

MongoDB'de en çok düşündüğüm karar bu. Kendime şu soruları soruyorum:

- Bu iki veri neredeyse her zaman birlikte mi okunuyor?
- Alt verinin sayısı sınırlı mı, yoksa sınırsız büyüyebilir mi?
- Alt veri tek başına sorgulanıyor ya da başka yerlerden güncelleniyor mu?

Bir siparişin teslimat adresi ve kalemleri birlikte okunur, sayıları sınırlıdır, sipariş oluştuktan sonra değişmezler. Bunları siparişin içine gömüyorum. Tek bir okuma ile her şey gelir:

```json
{
  "_id": "ord_1001",
  "customer_id": "usr_42",
  "status": "paid",
  "total_cents": 249900,
  "created_at": {"$date": "2024-12-20T09:15:00Z"},
  "shipping_address": {"city": "İstanbul", "district": "Kadıköy"},
  "items": [
    {"sku": "KB-01", "name": "Mekanik klavye", "qty": 1, "price_cents": 249900}
  ]
}
```

Ürünün adını ve fiyatını kaleme kopyaladığıma dikkat edin. Bu bilinçli bir tekrar, çünkü sipariş verildiği andaki fiyat sabit kalmalı. Ürün kataloğunda fiyat değişince eski siparişler etkilenmiyor.

Bir blog yazısının yorumları ise farklı. Sayıları sınırsız büyüyebilir ve tek bir MongoDB belgesinin üst sınırı 16 MB. Yorumları ayrı bir koleksiyonda `post_id` ile tutuyorum. Yazının sayfasında son birkaç yorumu hızlı göstermek için yazı belgesine yalnızca son 3 yorumu da gömebilirsiniz. Buna subset pattern deniyor.

Kullanıcı ve kategori gibi birçok yerden referans verilen, bağımsız güncellenen veriler de kendi koleksiyonlarında kalıyor. Kategori adı değiştiğinde bir milyon ürünü güncellemek istemezsiniz.

Paraları `float` olarak saklamıyorum. Ya kuruş cinsinden tam sayı (`total_cents`) ya da `Decimal128` kullanıyorum.

## Şema kuralını veritabanına koyun

MongoDB şemayı zorlamaz, ama uygulamanızın her zaman bir şema beklentisi vardır. `email` alanı olmayan bir kullanıcı belgesi bir gün bir `KeyError` olarak geri döner. Kuralı veritabanına koymak için `$jsonSchema` kullanıyorum:

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
    except CollectionInvalid:  # koleksiyon zaten var: kuralı güncelle
        await db.command("collMod", "users", validator={"$jsonSchema": USER_SCHEMA})
```

`validationLevel` iki değer alır. `strict` kuralı tüm eklemelere ve güncellemelere uygular. `moderate` ise eklemelere ve yalnızca zaten kurala uyan belgelerin güncellemelerine uygular, kurala uymayan eski belgeleri güncellemenize izin verir. Hiçbiri var olan veriyi geriye dönük kontrol etmez. Kuralı sonradan eklediyseniz eski belgeleri `{"$nor": [{"$jsonSchema": USER_SCHEMA}]}` filtresiyle bulup temizlemek gerekiyor.

Uygulama tarafında aynı modeli Pydantic ile de doğruluyorum. Böylece hata veritabanına gitmeden, okunabilir bir mesajla yakalanıyor:

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

## İndeksler ve ESR kuralı

Yavaş bir sorguyu incelerken ilk baktığım şey planın `COLLSCAN` mı `IXSCAN` mı olduğu ve kaç belgenin tarandığı:

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

`totalDocsExamined`, `nReturned`'den çok büyükse indeks ya yok ya da yanlış sırada. Bileşik indekslerde sırayı ESR kuralıyla belirliyorum:

1. Equality: tam eşleşme yapılan alanlar (`status`) önce.
2. Sort: sıralama alanları (`created_at`) ortada.
3. Range: aralık sorguları (`total_cents >= ...`) en sonda.

```python
from pymongo import ASCENDING, DESCENDING

await db.orders.create_index(
    [("status", ASCENDING), ("created_at", DESCENDING), ("total_cents", ASCENDING)]
)
```

Bu sorgu için `{total_cents: 1, status: 1}` gibi bir indeks verimsiz. Aralık alanı başta olduğu için MongoDB önce bütün fiyat aralığını tarar, sonra `status`'u filtreler ve sonucu bellekte sıralar. `explain` çıktısında bunu `totalDocsExamined` ile `nReturned` arasındaki uçurumdan ve plandaki `SORT` aşamasından görürsünüz.

Her indeks yazmaları yavaşlatır ve RAM kaplar. Kullanılmayanları `$indexStats` ile bulup siliyorum.

## Aggregation pipeline

Raporlamanın çoğunu aggregation ile yapıyorum. Belgeler aşamalardan sırayla geçer. Aşağıdaki pipeline en çok harcayan 20 müşteriyi bulur:

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

Sıranın bir sebebi var. `$match` en başta olursa indeks kullanabilir ve sonraki aşamalara giden belge sayısı azalır. `$lookup`'ı `$limit`'ten sonraya koyduğum için sadece 20 müşteri için çalışıyor. Aynı `$lookup` en başta olsaydı her sipariş için ayrı ayrı çalışırdı.

## Transaction'ı yalnızca gerektiğinde kullanın

Tek bir belge üzerindeki işlemler MongoDB'de her zaman atomiktir. Veri modeliniz doğru gömülmüşse işlemlerin çoğu zaten tek belgeyi günceller. Çok belgeli transaction 4.0'dan beri var ama replica set (ya da sharded cluster) gerektiriyor ve kilit maliyeti getiriyor. Ben onu iki belgenin gerçekten birlikte değişmesi gerektiğinde kullanıyorum, örneğin iki cüzdan arasında bakiye aktarımı:

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

`with_transaction`, geçici hatalarda (örneğin bir seçim sırasında primary değişirse) transaction'ı kendisi yeniden dener. Elle `start_transaction` yazıp bu döngüyü kurmaktan daha güvenli. Bakiye kontrolünü filtrenin içine koyduğum için bakiye yetersizse güncelleme hiç olmuyor ve hata transaction'ı geri alıyor.

## Replica set ve sharding

Üretimde tek bir `mongod` çalıştırmıyorum, en az üç üyeli bir replica set kuruyorum. Replica set yüksek erişilebilirlik sağlar ve transaction için de şarttır.

Sharding veriyi shard key'e göre birden fazla replica set'e dağıtır. Uygulama doğrudan shard'lara değil `mongos` yönlendiricilerine bağlanır, `mongos` da hangi verinin nerede olduğunu config server'lardan öğrenir.

Operasyon yükü belirgin şekilde artar ve en kritik karar shard key'dir. Tarih gibi sürekli artan bir alan seçerseniz bütün yeni yazmalar aynı shard'a gider. Kardinalitesi yüksek ve sorgularınızda zaten geçen bir alan, örneğin `customer_id`, çoğu zaman daha iyi bir başlangıç. Çoğu uygulamanın verisi tek bir replica set'e rahatça sığar; sharding'i ölçülmüş bir ihtiyaç ortaya çıkana kadar ertelemenizi öneririm.

## Sırada ne var

İlk adım olarak en yavaş sorgunuzu `explain` ile çalıştırın ve `totalDocsExamined` ile `nReturned` değerlerini karşılaştırın. İlişkisel tarafı merak ediyorsanız aynı soruları [SQLAlchemy 2.0 ile MySQL](/posts/mysql-sqlalchemy-veritabani-yonetimi/) yazısında ele aldım. MongoDB'yi bir HTTP API'nin arkasına koymak için [FastAPI ile REST API geliştirme](/posts/python-fastapi-restful-api-gelistirme/) yazısındaki `lifespan` ve bağımlılık kalıpları birebir işe yarıyor. Sık okunan raporları önbelleğe almak için [Redis ile önbellekleme](/posts/redis-onbellekleme-oturum-yonetimi/) yazısına bakabilirsiniz. Ayrıntılar için [MongoDB veri modelleme rehberi](https://www.mongodb.com/docs/manual/data-modeling/) ve [PyMongo async dokümantasyonu](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/) en doğru kaynaklar.
