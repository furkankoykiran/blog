---
title: "Veritabanı Bağlantı Havuzu: SQLAlchemy ve PgBouncer Ayarı"
description: "PostgreSQL için veritabanı bağlantı havuzu: SQLAlchemy 2 async ayarları, bağlantı limiti hesabı, sızıntılar, timeout'lar ve PgBouncer transaction modu."
date: 2025-11-01T10:00:00+03:00
slug: "veritabani-baglanti-havuzu-optimizasyon"
postType: guide
tags: ["databases", "python", "performance", "asyncio"]
categories: ["Backend"]
coverAlt: "Uygulama ile PostgreSQL arasında bağlantıları paylaştıran bir bağlantı havuzu"
draft: false
---

Veritabanı bağlantı havuzu, yük altında çöken bir Python servisinde ilk bakılması gereken yerlerden biri. PostgreSQL'in log'unda `FATAL: sorry, too many clients already` ya da SQLAlchemy'de `QueuePool limit of size 5 overflow 10 reached` görüyorsanız sorun büyük ihtimalle sorgularınızda değil, bağlantıları nasıl yönettiğinizde.

Bu yazıda SQLAlchemy 2'nin async engine'i ile havuz ayarlarını, kaç bağlantı açabileceğinizin hesabını, sık yapılan hataları ve uygulama sayısı arttığında PgBouncer'ı anlatıyorum.

## Bir bağlantı neden pahalı?

PostgreSQL'e yeni bağlantı açmak bir fonksiyon çağrısı gibi ucuz değil:

1. TCP bağlantısı kurulur, çoğu ortamda üstüne TLS el sıkışması gelir.
2. Kimlik doğrulama yapılır (SCRAM birkaç tur gidiş-dönüş demek).
3. PostgreSQL bu bağlantı için yeni bir backend process fork eder. Her process'in kendi belleği var.

Aynı makinede bu birkaç milisaniye sürer, ağ ve TLS eklendikçe daha da uzar. İndeksli basit bir `SELECT` çoğu zaman bundan daha kısa. Her HTTP isteğinde bağlantı açıp kapatırsanız sürenin büyük kısmını el sıkışmaya harcarsınız. Ayrıca `max_connections` değerini istediğiniz kadar artıramazsınız, çünkü her bağlantı bir process ve bellek demek.

Havuz bu yüzden var: bağlantıları bir kez açar, açık tutar ve istekler arasında ödünç verir.

## SQLAlchemy 2 ile async havuz

SQLAlchemy'nin `create_async_engine` fonksiyonu varsayılan olarak bir kuyruk tabanlı havuz kullanır. Varsayılan değerler `pool_size=5`, `max_overflow=10` ve `pool_timeout=30`. Benim başlangıç ayarlarım şöyle:

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

engine = create_async_engine(
    "postgresql+asyncpg://app:secret@db:5432/shop",
    pool_size=10,        # sürekli açık tutulan bağlantı
    max_overflow=5,      # yoğunlukta geçici olarak açılabilecek ek bağlantı
    pool_timeout=5,      # havuz doluysa en fazla 5 sn bekle, sonra hata ver
    pool_pre_ping=True,  # bağlantıyı vermeden önce canlı mı diye kontrol et
    pool_recycle=1800,   # 30 dakikadan eski bağlantıları yenile
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_user(user_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            text("SELECT id, email FROM users WHERE id = :id"), {"id": user_id}
        )
        return result.one_or_none()
```

Ayarların ne işe yaradığı:

- `pool_pre_ping`, bağlantıyı havuzdan vermeden önce hafif bir kontrol yapar. Veritabanı yeniden başladıysa ya da bir firewall boşta kalan bağlantıyı sessizce kestiyse, ölü bağlantı yerine yenisini alırsınız. Maliyeti küçük, faydası büyük; ben hep açıyorum.
- `pool_recycle`, belli bir süreden eski bağlantıları kapatıp yeniler. Arada boşta bağlantıları kesen bir load balancer ya da proxy varsa onun timeout'undan kısa tutun.
- `pool_timeout`'u varsayılan 30 saniyede bırakırsanız havuz dolduğunda istekler 30 saniye asılı kalır. Kısa bir değer, hatayı hızlı göstermeyi ve isteği reddetmeyi sağlar.

`engine` uygulama başına bir kez oluşturulmalı. Her istekte yeni engine yaratmak, her istekte yeni bir havuz yaratmak demek ve havuzun bütün anlamını yok eder.

## Kaç bağlantı açabilirsiniz?

Asıl hesap budur ve sıklıkla atlanır:

```text
toplam bağlantı = process sayısı × (pool_size + max_overflow)
```

"Process sayısı" pod sayısı değil; her pod içinde birden fazla uvicorn ya da gunicorn worker'ı çalışıyorsa her worker'ın kendi havuzu var. 4 pod, pod başına 4 worker ve `pool_size=10, max_overflow=5` ile en kötü durumda 4 × 4 × 15 = 240 bağlantı açılır. PostgreSQL'in varsayılan `max_connections` değeri 100 ve bunun birkaçı süper kullanıcıya ayrılmış.

Bu toplamı `max_connections` değerinin altında tutun, migration, cron job ve sizin `psql` oturumunuz için de pay bırakın. Autoscaling kullanıyorsanız hesabı maksimum replika sayısıyla yapın. Toplam limit sizi zorluyorsa bir sonraki çözüm PgBouncer.

## Sızıntılar: iade edilmeyen bağlantılar

`QueuePool limit ... reached` hatasını alıyorsanız ilk şüphelendiğim şey yük değil, havuza geri dönmeyen bağlantılar. Session açıp bir hata yolunda kapatmayı unutursanız bağlantı "kullanımda" olarak kalır.

Kural basit: session'ı her zaman bir context manager ile açın. Context manager'ların genel mantığını [ayrı bir yazıda](/posts/python-context-managers-kaynak-yonetimi/) anlattım. SQLAlchemy'de transaction'ı da yöneten kısa bir yol var:

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
    # blok hatasız biterse commit, exception olursa rollback; bağlantı her durumda havuza döner
```

FastAPI'de bunu bir dependency olarak yazıyorum, böylece her endpoint aynı yaşam döngüsünü kullanıyor:

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

"Python garbage collector sonunda temizler" diye düşünmeyin. GC ne zaman çalışacağını bilmezsiniz ve o ana kadar bağlantı havuzda kilitli kalır.

## Transaction içinde dış dünyaya çıkmayın

Havuzu hızla tüketen bir başka desen: transaction açmak, içinde harici bir API'ye HTTP isteği atmak, sonra commit etmek. Harici servis 30 saniye cevap vermezse bağlantı 30 saniye boyunca `idle in transaction` durumunda bekler, aldığı kilitler de o süre boyunca tutulur.

Transaction'ları kısa tutun: önce dış çağrıyı yapın, sonra veritabanı işini tek seferde yapın. PostgreSQL tarafında bir emniyet kemeri de ekleyin:

```sql
ALTER ROLE app SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE app SET statement_timeout = '5s';
```

Bu ayarları rol üzerinde yapmak, her bağlantıda tekrar göndermekten daha güvenilir; aşağıdaki PgBouncer kurulumunda da çalışır. Raporlama gibi uzun sorgular gerekiyorsa onlar için ayrı bir rol kullanın.

## Ölçeklenince: PgBouncer

Process sayısı arttıkça her birine küçük bir havuz bile vermek toplamı `max_connections`'ın üstüne çıkarır. Bu noktada uygulama ile veritabanı arasına PgBouncer gibi bir bağlantı havuzlayıcı koyuyorum.

![Doğrudan havuzlarda toplam max_connections'ı aşar; transaction modundaki PgBouncer yüzlerce istemci bağlantısını 20-50 gerçek bağlantıya indirir](img/pgbouncer-scale-out.webp "PgBouncer, çok sayıda worker ile birkaç düzine veritabanı bağlantısı arasında durur")

PgBouncer'ın iki ana modu var:

- Session modu: istemci bağlı kaldığı sürece bir sunucu bağlantısını tutar. Toplam sayıyı pek azaltmaz.
- Transaction modu: sunucu bağlantısı sadece bir transaction süresince istemciye verilir, transaction bitince başka bir istemciye geçer. Asıl kazanç burada: yüzlerce istemci bağlantısı birkaç düzine gerçek bağlantıyla karşılanabilir.

Transaction modunun bedeli, bağlantıya bağlı durumu kaybetmeniz: `SET` ile yapılan oturum ayarları, advisory lock'lar, `LISTEN/NOTIFY` ve geçici tablolar transaction'lar arasında taşınmaz. Uzun süre asyncpg ile prepared statement da sorun çıkarıyordu. PgBouncer 1.21'den beri `max_prepared_statements` ayarıyla protokol seviyesindeki prepared statement'ları transaction modunda da destekliyor; bunu açın. Daha eski bir PgBouncer'a bağlıysanız hem SQLAlchemy'nin hem asyncpg'nin statement cache'ini kapatmanız gerekir:

```python
engine = create_async_engine(
    # sadece eski PgBouncer için: SQLAlchemy'nin ve asyncpg'nin statement cache'leri kapalı
    "postgresql+asyncpg://app:secret@pgbouncer:6432/shop?prepared_statement_cache_size=0",
    connect_args={"statement_cache_size": 0},
    pool_size=5,
    max_overflow=0,
    pool_pre_ping=True,
)
```

PgBouncer arkasında uygulama tarafındaki havuzu küçük tutuyorum. Bazı ekipler SQLAlchemy'de `NullPool` kullanıp havuzlamayı tamamen PgBouncer'a bırakıyor; bu da geçerli bir seçenek ama her istek PgBouncer'a yeni bir TCP bağlantısı açtığı için küçük bir uygulama havuzu genelde daha iyi sonuç veriyor.

## Önbellek ve havuz farklı şeyler

İkisi sık karıştırılır. Havuz, veritabanına giden yolu açık tutar; önbellek ise veritabanına hiç gitmemenizi sağlar. Sık okunan ve az değişen veriyi Redis'te tutmak havuz üzerindeki baskıyı azaltır ama cache miss anında yine sağlam bir havuza ihtiyacınız var. Önbellek desenlerini [Redis yazımda](/posts/redis-onbellekleme-oturum-yonetimi/) anlattım.

## İzleme

Havuzun ne kadar dolu olduğunu görmeden ayar yapmak tahmin etmektir. SQLAlchemy havuzu kendi durumunu raporluyor:

```python
pool = engine.sync_engine.pool
print(pool.status())  # boyut, kullanımdaki bağlantı, overflow ve bekleyenler
print(pool.checkedout(), pool.overflow())
```

Bu değerleri periyodik olarak bir metrik sistemine (örneğin Prometheus) yazın. Veritabanı tarafında `pg_stat_activity` tablosu kimin bağlı olduğunu ve hangi durumda beklediğini gösterir:

```sql
SELECT usename, application_name, state, count(*)
FROM pg_stat_activity
GROUP BY 1, 2, 3
ORDER BY 4 DESC;
```

`idle in transaction` satırları çoksa yukarıdaki dış çağrı sorununa bakın.

## Canlıya çıkmadan önce

- `process sayısı × (pool_size + max_overflow)` toplamı, maksimum replika sayısıyla bile `max_connections`'ın altında mı?
- `pool_pre_ping` açık ve `pool_recycle` aradaki proxy'nin timeout'undan kısa mı?
- `pool_timeout` kısa mı, yani havuz dolduğunda istekler hızla hata veriyor mu?
- Rol üzerinde `statement_timeout` ve `idle_in_transaction_session_timeout` var mı?
- Session'lar her yerde context manager ile mi açılıyor?

## Sonraki adım

Kendi servisinizde önce yukarıdaki hesabı yapın; çoğu "veritabanı yavaş" şikâyeti burada çözülüyor. SQLAlchemy'ye yeniyseniz [SQLAlchemy ile veritabanı yönetimi yazım](/posts/mysql-sqlalchemy-veritabani-yonetimi/) temel kavramları anlatıyor. Ayrıntılar için [SQLAlchemy havuz dokümantasyonu](https://docs.sqlalchemy.org/en/20/core/pooling.html) ve [PgBouncer ayar referansı](https://www.pgbouncer.org/config.html) en doğru kaynaklar.
