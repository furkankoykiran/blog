---
title: "SQLAlchemy 2.0 ile MySQL: Async ORM, N+1 ve Alembic"
description: "SQLAlchemy 2.0 ile MySQL ve PostgreSQL: Mapped modeller, async oturum, N+1 problemine karşı eager loading, Alembic migration ve bağlantı havuzu ayarları."
date: 2024-12-22T10:00:00+03:00
slug: "mysql-sqlalchemy-veritabani-yonetimi"
postType: guide
tags: ["python", "databases", "asyncio", "performance"]
categories: ["Backend"]
coverAlt: "SQLAlchemy engine, bağlantı havuzu ve MySQL veritabanı arasındaki katmanlar"
draft: false
---

"ORM yavaştır, ham SQL yazalım" lafını çok duydum, ama hata ayıkladığım yavaş sayfaların neredeyse hepsinde kimse arkada çalışan SQL'e bakmamıştı. Python'da ilişkisel veritabanıyla çalışırken varsayılan tercihim SQLAlchemy 2.0. Bu yazıda MySQL ve PostgreSQL üzerinde kullandığım düzeni anlatıyorum: `Mapped` ile tip güvenli modeller, async oturum, N+1 problemini baştan engelleyen ayarlar, Alembic ile migration ve bağlantı havuzu. Örnekleri Python 3.12 ve SQLAlchemy 2.0 ile çalıştırdım.

## SQLAlchemy'nin katmanları

Kodunuz bir `Session` ile konuşur, gerisini engine halleder. Engine, SQL lehçesini bilen dialect'i ve bağlantıları tutan havuzu yönetir. Gerçek ağ trafiğini DBAPI sürücüsü yapar.

SQLAlchemy'nin iyi tarafı, ihtiyaç olduğunda SQL'e çok yakın yazabilmeniz. Gerekirse `text()` ile ham sorgu da çalıştırabilirsiniz, ikisi aynı oturumda yan yana durur.

## Sürücü ve bağlantı adresi

Async kullanmak için async bir DBAPI sürücüsü gerekir:

| Veritabanı | Sürücü | Bağlantı adresi |
|---|---|---|
| MySQL / MariaDB | `asyncmy` veya `aiomysql` | `mysql+asyncmy://app:secret@localhost:3306/shop?charset=utf8mb4` |
| PostgreSQL | `asyncpg` | `postgresql+asyncpg://app:secret@localhost:5432/shop` |
| SQLite (test, demo) | `aiosqlite` | `sqlite+aiosqlite:///./shop.db` |

MySQL'de `charset=utf8mb4` yazmayı unutmayın. MySQL'in `utf8`'i aslında 3 baytlık bir alt küme (`utf8mb3`), emojiler ve bazı karakterler sessizce bozulur.

## Modeller: `Mapped` ve `mapped_column`

2.0 ile modeller Python tip ipuçlarıyla tanımlanıyor. Mypy ve Pyright `user.email`'in `str`, `user.phone`'un `str | None` olduğunu bilir. Kolonun boş bırakılabilir olup olmadığı da tipten çıkarılır.

```python {title="shop.py"}
import asyncio
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, String, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    joinedload,
    mapped_column,
    relationship,
    selectinload,
)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    orders: Mapped[list["Order"]] = relationship(back_populates="user", lazy="raise")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="orders", lazy="raise")
```

İki ayrıntıya dikkat edin. `created_at` için sık görülen `default=datetime.utcnow` kalıbı eskidi: `utcnow()` Python 3.12'de kullanımdan kaldırıldı. Zamanı veritabanının vermesi de daha tutarlı, `server_default=func.now()` bunu yapıyor. Para için `float` yerine `Numeric` ve `Decimal` kullanıyorum.

İndeksleri modelde açıkça yazıyorum. Yabancı anahtar kolonu (`user_id`) MySQL InnoDB'de otomatik indekslenir ama PostgreSQL'de indekslenmez. `index=True` iki tarafta da aynı sonucu verir. `status` ve `created_at` ile sık filtreleme yapılıyorsa bileşik indeks de burada tanımlanıyor ve Alembic onu migration'a çeviriyor.

## Sorgular: `select()` ile 2.0 stili

`session.query(User).filter(...)` artık eski API. Yeni stil Core ile ORM'i birleştiren `select()`:

```python
async def active_users_with_orders(session: AsyncSession) -> list[User]:
    stmt = (
        select(User)
        .where(User.is_active)
        .options(selectinload(User.orders))
        .order_by(User.created_at.desc())
    )
    return list(await session.scalars(stmt))


async def recent_orders(session: AsyncSession) -> list[Order]:
    stmt = select(Order).options(joinedload(Order.user)).order_by(Order.id.desc()).limit(50)
    return list(await session.scalars(stmt))
```

`session.scalars()` satırları değil ORM nesnelerini döndürür. Tek bir kayıt için `session.get(User, 42)` ya da `(await session.scalars(stmt)).first()` kullanıyorum.

## N+1 problemi ve async'te `lazy="raise"`

100 siparişi listeleyip her birinin yanında `order.user.email` gösterdiğinizi düşünün. Lazy loading açıksa önce siparişler için 1 sorgu, sonra her sipariş için kullanıcıyı getiren 100 sorgu daha gider. Toplam 101 sorgu. Geliştirme ortamında 5 kayıtla fark edilmez, üretimde sayfa saniyeler sürer.

Async tarafta durum biraz farklı. `AsyncSession` ile örtük lazy loading çalışmaz, yüklenmemiş bir ilişkiye eriştiğinizde `MissingGreenlet` hatası alırsınız. Ben bunu bir avantaja çeviriyorum ve ilişkileri `lazy="raise"` ile tanımlıyorum. Böylece yükleme stratejisini belirtmeyi unuttuğum her yerde, anlaşılması zor bir greenlet hatası yerine açık bir hata alıyorum:

```text
InvalidRequestError: 'User.orders' is not available due to lazy='raise'
```

Çözüm sorguda neyi yükleyeceğinizi söylemek. Hangi stratejinin ne zaman uygun olduğu:

- `selectinload`: Ana sorgudan sonra `SELECT ... WHERE user_id IN (...)` şeklinde ikinci bir sorgu atar. Koleksiyonlar (bir kullanıcının siparişleri) için varsayılan tercihim. Toplam iki sorgu, satır tekrarı yok.
- `joinedload`: Tek sorguda `LEFT OUTER JOIN` yapar. Çoğa-bir ilişkiler (siparişin kullanıcısı) için iyi. Varsayılan olarak `LEFT OUTER JOIN` üretir; ilişki zorunluysa `joinedload(Order.user, innerjoin=True)` daha hızlı bir `INNER JOIN` verir.

Aşağıdaki kart hatayı ve çözümü yan yana gösteriyor.

![Önce: yükleme stratejisi olmadan user.orders'a erişmek lazy='raise' ile InvalidRequestError veriyor. Sonra: selectinload siparişleri ikinci sorguyla yüklüyor](img/lazy-raise-fix.webp "Kaç kullanıcı dönerse dönsün toplam iki sorgu")

Koleksiyonları `joinedload` ile yüklemek, örneğin `joinedload(Order.items)`, eski örneklerde hâlâ sık görülür. 2.0'da bunu yaparsanız sonuca `.unique()` çağırmanız gerekir, yoksa hata alırsınız. Ayrıca her sipariş, kalemi kadar tekrar eden satırla döner. Koleksiyonlarda `selectinload` hem daha basit hem daha az veri taşıyor.

Geliştirmede `create_async_engine(..., echo=True)` ile çalışan SQL'i görmek, N+1'i yakalamanın en ucuz yolu. Yukarıdaki iki fonksiyon toplam üç sorgu üretiyor (kolon listelerini kısalttım):

```sql
SELECT users.* FROM users WHERE users.is_active = 1 ORDER BY users.created_at DESC
SELECT orders.* FROM orders WHERE orders.user_id IN (?)
SELECT orders.*, users_1.* FROM orders LEFT OUTER JOIN users AS users_1 ON users_1.id = orders.user_id ORDER BY orders.id DESC LIMIT ?
```

## Yazma ve transaction

`async with session.begin()` bloğu başarılı biterse commit, hata olursa rollback yapıyor. Elle `commit()` çağırmayı unutma riskini ortadan kaldırıyor:

```python
engine = create_async_engine("sqlite+aiosqlite:///./shop.db", echo=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session, session.begin():
        ada = User(email="ada@example.com", full_name="Ada Lovelace")
        ada.orders = [Order(total=Decimal("120.50")), Order(total=Decimal("75.00"))]
        session.add(ada)

    async with SessionLocal() as session:
        for user in await active_users_with_orders(session):
            print(user.full_name, [str(o.total) for o in user.orders])
        for order in await recent_orders(session):
            print(order.id, order.user.email)

    await engine.dispose()


asyncio.run(main())
```

Bu dosyayı SQLite ile de çalıştırdım. İlk komut `selectinload` vermeden `user.orders`'a erişen küçük bir betik ve `lazy="raise"` sayesinde hemen patlıyor. İkincisi `echo=True` ile aynı engine loglarını açıp sadece SQL satırlarını süzüyor: kullanıcılar için bir sorgu, siparişleri toplayan tek bir `IN (?)` sorgusu ve `LEFT OUTER JOIN`'li liste.

`expire_on_commit=False` async'te neredeyse zorunlu. Varsayılan ayarda commit sonrası nesnenin tüm alanları geçersiz sayılır ve bir sonraki erişimde tekrar yüklenmeye çalışılır. Bu da yine `MissingGreenlet` demek. Tabloları `create_all` ile oluşturmak sadece demo için, gerçek projede bu işi Alembic yapıyor.

## Alembic ile migration

Modele kolon eklediniz, kodu yayına aldınız ve uygulama çöktü, çünkü kolon veritabanında yok. Üretim veritabanına bağlanıp elle `ALTER TABLE` yazmak yerine Alembic kullanıyorum. Async projeler için hazır bir şablonu var:

```bash
uv add alembic
uv run alembic init -t async migrations
```

`migrations/env.py` içinde `target_metadata = Base.metadata` satırını ayarlayıp `alembic.ini` içindeki `sqlalchemy.url`'i doldurduktan sonra akış şöyle:

```bash
uv run alembic revision --autogenerate -m "add phone to users"
# migrations/versions/ altındaki dosyayı mutlaka okuyun
uv run alembic upgrade head
uv run alembic downgrade -1   # gerekirse geri al
```

Autogenerate'i ilk taslak olarak görün. Kolon adı değişikliğini "eski kolonu sil, yenisini ekle" olarak algılar ve veri kaybettirebilir. Bunu elle `op.alter_column(..., new_column_name=...)` ile yazmanız gerekir. Ayrıca büyük bir MySQL tablosuna kolon eklemek tabloyu kilitleyebilir, o yüzden migration'ı trafik düşükken çalıştırıyorum.

## Bağlantı havuzu ayarları

Her istek için yeni bir TCP bağlantısı açıp kimlik doğrulamak pahalı. SQLAlchemy engine bir havuz tutar ve bağlantıları yeniden kullanır. İstekler havuzdan bağlantı ödünç alıp geri verir, havuz doluysa yeni gelen bekler.

```python
engine = create_async_engine(
    "mysql+asyncmy://app:secret@db:3306/shop?charset=utf8mb4",
    pool_size=10,        # sürekli açık tutulan bağlantı
    max_overflow=5,      # yoğunlukta geçici ek bağlantı
    pool_timeout=30,     # havuz doluysa kaç saniye beklenecek
    pool_recycle=1800,   # 30 dakikadan eski bağlantıyı yenile
    pool_pre_ping=True,  # kullanmadan önce bağlantı canlı mı kontrol et
)
```

`pool_recycle` MySQL'de özellikle önemli. Sunucu `wait_timeout` süresince (varsayılan 8 saat) boşta kalan bağlantıyı kapatır, arada bir güvenlik duvarı varsa çok daha erken keser. Uygulama bunu bilmeden ölü bağlantıyı kullanınca "MySQL server has gone away" hatası gelir. `pool_pre_ping` bunu yakalar, `pool_recycle` ise çoğu zaman hiç oluşmasını engeller.

Boyutu belirlerken şu hesabı yapıyorum: `(pool_size + max_overflow) × süreç sayısı`, veritabanının `max_connections` değerinin altında kalmalı. 4 worker'lı 3 replika ve yukarıdaki ayarlarla bu 180 bağlantı ediyor. "QueuePool limit reached" hatası görürseniz önce havuzu büyütmeyin. Çoğu zaman sebep kapatılmayan bir oturum ya da async kod içinde bloklayan bir çağrıdır. Ayrıntıları [veritabanı bağlantı havuzu optimizasyonu](/posts/veritabani-baglanti-havuzu-optimizasyon/) yazısında anlattım.

## Sırada ne var

İlk adım olarak geliştirme ortamında `echo=True` açın, en çok kullanılan sayfanızı bir kez yükleyin ve kaç sorgu gittiğini sayın. Bu modelleri bir API'ye bağlamak için [FastAPI ile REST API geliştirme](/posts/python-fastapi-restful-api-gelistirme/) yazısındaki `SessionDep` kalıbını kullanabilirsiniz. Sık okunan sorguların sonucunu önbelleğe almak için [Redis ile önbellekleme](/posts/redis-onbellekleme-oturum-yonetimi/) yazısındaki cache-aside desenine bakın. Referans olarak [SQLAlchemy 2.0 asyncio dokümantasyonu](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) ve [Alembic dokümantasyonu](https://alembic.sqlalchemy.org/) en doğru kaynaklar.
