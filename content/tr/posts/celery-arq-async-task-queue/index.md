---
title: "Celery mi Arq mı? Python'da Asenkron Görev Kuyruğu"
description: "Celery ve Arq ile asenkron görev kuyruğu: güvenli Celery ayarları, async Arq worker, idempotency, visibility timeout, retry ve izleme. Arq'ın bakım modu notu."
date: 2025-02-08T10:00:00+03:00
slug: "celery-arq-asenkron-gorev-kuyrugu"
postType: guide
tags: ["python", "asyncio", "redis", "messaging"]
categories: ["Backend"]
coverAlt: "Web uygulamasının görevleri broker üzerinden worker'lara dağıttığı asenkron görev kuyruğu"
draft: false
---

Kayıt butonuna basıldığında hoş geldin e-postası gönderilmesi üç saniye sürüyorsa, kullanıcı o üç saniyeyi bekler. Mail sunucusu o an cevap vermezse kayıt da hata verir. Bunun çözümü yıllardır aynı: asenkron görev kuyruğu. HTTP isteği hızlıca biter, ağır iş (e-posta, rapor, görsel işleme) arkadaki worker'lara bırakılır.

Python'da bu işin klasik aracı Celery. asyncio tabanlı projelerde ise Arq uzun süre hafif bir alternatif olarak öne çıktı. Bu yazıda ikisini karşılaştırıyorum, ama asıl ağırlık hangi aracı seçerseniz seçin karşınıza çıkacak dağıtık sistem sorunlarında: tekrar çalışan görevler, kaybolan görevler ve transaction sınırları.

## Görev kuyruğu nasıl çalışır

İki araçta da parçalar aynı; aşağıdaki şema bunları gösteriyor.

![Web uygulaması görevi broker'a bırakıyor, iki worker kuyruktan çekip sonucu result backend'e yazıyor, zamanlayıcı periyodik görevleri broker'a ekliyor](img/task-queue-architecture.webp "Celery'de de Arq'ta da aynı parçalar: broker, worker, result backend ve zamanlayıcı")

Web uygulaması görevi broker'a bırakır ve hemen cevap döner. Worker'lar kuyruktan görev çeker. Sonuca ihtiyacınız varsa bir result backend'e yazılır; e-posta gibi "at ve unut" işlerde çoğu zaman gerekmez.

## Celery: güçlü ama varsayılanları tehlikeli

Celery RabbitMQ, Redis ve SQS gibi broker'larla çalışır. Zincir (chain), grup ve chord gibi iş akışı yapıları, zamanlanmış görevler için beat ve izleme için Flower ile en olgun seçenek hâlâ o.

Bedeli, çok sayıda ayarın doğru yapılması gerekmesi. Varsayılan ayarlarda Celery görevi worker'a teslim eder etmez onaylar (ack). Worker görevin ortasında ölürse görev kaybolur. Benim her projede başlangıç noktam:

```python
# celery_app.py
from celery import Celery

app = Celery(
    "proj",
    broker="amqp://guest:guest@localhost:5672//",
    include=["tasks"],
)
app.conf.update(
    task_acks_late=True,  # görev bitince onayla, başlarken değil
    task_reject_on_worker_lost=True,  # worker ölürse görevi kuyruğa geri koy
    worker_prefetch_multiplier=1,  # uzun işlerde worker'lar arasında adil dağıtım
    task_soft_time_limit=240,
    task_time_limit=300,
    result_expires=3600,
)
```

`task_acks_late` açıldığında görevler "en az bir kez" çalışır. Yani aynı görev iki kez çalışabilir ve kodunuz buna dayanıklı olmalı (aşağıda idempotency bölümü). `worker_prefetch_multiplier=1` olmazsa yavaş bir worker'ın önünde on görev birikir, hızlı olan boş bekler. E-posta gibi kısa işlerde bu değeri 4 ya da 8 yapabilirsiniz.

Zaman limitlerinde iki seviye var. Soft limit dolunca görevin içinde `SoftTimeLimitExceeded` istisnası fırlar, temizlik yapma şansınız olur. Hard limit dolunca worker süreci öldürülüp yenisi başlatılır. İkisi de prefork havuzunda çalışır, Windows'ta çalışmaz.

### Broker seçimi

RabbitMQ, Celery için hâlâ en sağlam seçenek: onay mekanizması güçlü, yönlendirme esnek. Celery 5.5 ile quorum queue desteği de geldi; yeni bir RabbitMQ kurulumunda klasik kuyruk yerine quorum queue kullanmak mantıklı, ama ETA'lı görevlerin davranışı değiştiği için önce [Celery dokümanındaki](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/rabbitmq.html) notları okuyun. RabbitMQ'nun kendi tarafını [RabbitMQ yazısında](/posts/rabbitmq-mesaj-kuyrugu-sistemi/) anlattım.

Redis'i broker olarak kullanacaksanız bir ayarı mutlaka bilin: `visibility_timeout`. Redis broker'ında onaylanmamış bir görev bu süre (varsayılan bir saat) içinde bitmezse başka bir worker'a tekrar verilir. Üç saat süren bir rapor görevi, `acks_late` ile birlikte aynı anda iki, sonra üç worker'da çalışmaya başlar. Süreyi en uzun görevinizden uzun tutun:

```python
app.conf.broker_transport_options = {"visibility_timeout": 4 * 3600}
```

Redis'in kalıcılık (persistence) ayarı kapalıysa Redis yeniden başladığında kuyruktaki görevler de gider. Redis'i bu açıdan ayarlamayı [Redis yazısında](/posts/redis-onbellekleme-oturum-yonetimi/) anlattım.

### Celery ve async kod

Celery görevleri senkron fonksiyonlardır; yerleşik `async def` görev desteği yok. FastAPI gibi async bir uygulamadan `.delay()` çağırmak çalışır (broker'a mesaj yazmak kısa sürer), ama görev içinde asyncio kütüphanesi kullanacaksanız ya `asyncio.run()` ile sarmalarsınız ya da async bir kuyruk aracı seçersiniz.

## Arq: asyncio için hafif bir seçenek

[Arq](https://github.com/python-arq/arq), asyncio ve Redis üzerine kurulu. Worker'lar coroutine çalıştırır, bu yüzden tek bir süreç çok sayıda I/O ağırlıklı işi (HTTP isteği, veritabanı sorgusu) aynı anda yürütebilir. Celery'de aynısı için gevent ya da eventlet havuzuna geçmeniz gerekir.

Arq'ın README'si projeyi "maintenance only mode" olarak işaretliyor ve gerekçeyi [#510 numaralı issue'ya](https://github.com/python-arq/arq/issues/510) bağlıyor.

![Arq'ın GitHub README'si: başlığın altında In maintenance only mode notu ve #510 numaralı issue'ya bağlantı](img/arq-readme-maintenance-mode.webp "Arq README'sindeki bakım modu notu. Kaynak: GitHub")

Hata düzeltmeleri ve sürümler geliyor ama yeni özellik beklemeyin. Çalışan bir Arq kurulumunu sökmek için sebep değil; yeni bir projede ise bunu bilerek seçin. Async bir alternatif arıyorsanız Taskiq ve SAQ'ya bakmaya değer.

Arq ile bir worker:

```python
# tasks.py
import httpx
from arq import Retry
from arq.connections import RedisSettings


async def startup(ctx):
    ctx["http"] = httpx.AsyncClient(timeout=10)


async def shutdown(ctx):
    await ctx["http"].aclose()


async def send_welcome_email(ctx, user_id: int, email: str) -> None:
    resp = await ctx["http"].post(
        "https://mail.example.com/send",
        json={"to": email, "template": "welcome"},
        headers={"Idempotency-Key": f"welcome-{user_id}"},
    )
    if resp.status_code >= 500:
        # 10, 20, 30... saniye sonra yeniden dene
        raise Retry(defer=ctx["job_try"] * 10)
    resp.raise_for_status()


class WorkerSettings:
    functions = [send_welcome_email]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(host="localhost")
    job_timeout = 60
    max_tries = 5
```

Worker'ı `arq tasks.WorkerSettings` ile başlatırsınız. Görevi kuyruğa atan taraf:

```python
import asyncio

from arq import create_pool
from arq.connections import RedisSettings


async def main():
    redis = await create_pool(RedisSettings())
    job = await redis.enqueue_job(
        "send_welcome_email", 42, "ali@example.com", _job_id="welcome:42"
    )
    if job is None:
        print("Bu iş zaten kuyrukta ya da sonucu duruyor")


asyncio.run(main())
```

`_job_id` vermek, aynı işin iki kez kuyruğa girmesini engeller: aynı ID ile bir iş kuyruktaysa, çalışıyorsa ya da sonucu hâlâ saklanıyorsa `enqueue_job` `None` döner. HTTP client'ı `startup`'ta bir kez oluşturup `ctx` içinde paylaşmak da her görevde yeni bağlantı açmaktan çok daha verimli.

## Dağıtık sistem tuzakları

### Idempotency: görev iki kez çalışacak

Ağda bir sorun olur, onay broker'a ulaşmaz, broker görevi başka bir worker'a verir. Ya da worker görev bittikten hemen sonra, onaylamadan ölür. "En az bir kez" teslim garantisi olan her sistemde aynı görev iki kez çalışabilir. Kullanıcıya iki hoş geldin e-postası gitmesi can sıkıcıdır; bakiyeden iki kez para düşmesi ise ciddi bir hata.

Görev idempotent olmalı: bir kez de çalışsa beş kez de çalışsa sonuç aynı olmalı. Celery ile Redis üzerinde basit bir kilit:

```python
# tasks.py
import httpx
import redis

from celery_app import app

r = redis.Redis()


@app.task(
    autoretry_for=(httpx.TransportError, httpx.HTTPStatusError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
)
def send_welcome_email(user_id: int, email: str) -> None:
    key = f"email:welcome:{user_id}"
    if not r.set(key, "sending", nx=True, ex=3600):
        return  # başka bir çalıştırma bu işi aldı ya da bitirdi
    try:
        resp = httpx.post(
            "https://mail.example.com/send",
            json={"to": email, "template": "welcome"},
            headers={"Idempotency-Key": f"welcome-{user_id}"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception:
        r.delete(key)  # başarısız oldu, retry tekrar deneyebilsin
        raise
    r.set(key, "sent", ex=7 * 24 * 3600)
```

Bu yaklaşımın bir açığı var: e-posta gittikten sonra ama `sent` yazılmadan worker ölürse, bir saat sonra kilit düşer ve tekrar denenebilir. Bu yüzden karşı servis destekliyorsa `Idempotency-Key` gibi bir başlık göndermek en sağlamı; tekrarı karşı taraf yakalar. Para hareketlerinde ise veritabanında benzersiz bir kısıt (unique constraint) kullanın, Redis kilidine güvenmeyin.

### Transaction sınırı

Kullanıcıyı veritabanına yazdınız, sonra kuyruğa görev attınız. Transaction geri alınırsa kuyrukta var olmayan bir kullanıcı için görev kalır. Tersi de olur: görev, transaction commit edilmeden önce çalışır ve kullanıcıyı bulamaz.

Çözüm, görevi transaction başarıyla bittikten sonra göndermek. Django'da `transaction.on_commit(lambda: send_welcome_email.delay(user.id, user.email))` bunu yapar; Celery 5.4'ten beri Django görevlerinde kısa yolu `send_welcome_email.delay_on_commit(...)`. Django dışında, kaybolmaması gereken görevler için outbox deseni kullanın: görevi aynı transaction içinde bir `outbox` tablosuna yazın, ayrı bir süreç oradan okuyup kuyruğa göndersin.

### Retry'ları sınırlayın

Sonsuz retry, kalıcı bir hatayı sonsuz bir yüke çevirir. Celery'de `max_retries` ile `retry_backoff`, Arq'ta `max_tries` ile `Retry(defer=...)` kullanın. Denemeleri tükenen görevler loglanmalı ve bir yerde görünür olmalı. 4xx gibi kalıcı hataları yeniden denemek de anlamsız; yukarıdaki örnekte basitlik için `HTTPStatusError`'ı topluca yeniden deniyorum, gerçek kodda 5xx ile sınırlamak daha doğru.

## İzleme

Celery için Flower, görevleri ve worker durumlarını canlı gösterir. Ben ek olarak Prometheus'a metrik gönderip Grafana'da izlemeyi tercih ediyorum. Bakılması gereken üç metrik:

- Kuyruk derinliği: bekleyen görev sayısı sürekli artıyorsa worker yetmiyor.
- Bekleme süresi: görev kuyruğa girdikten ne kadar sonra çalışmaya başladı.
- Hata oranı: başarısız ve denemeleri tükenen görevler.

Alarm kurallarını ve PromQL tarafını [Prometheus ve Grafana yazısında](/posts/prometheus-grafana-ile-izleme/) anlattım.

## Celery'de döngüsel import

Celery'ye yeni başlayanların sık yaşadığı bir hata: `tasks.py` `models.py`'ı import eder, `models.py` de kayıttan sonra görev tetiklemek için `tasks.py`'ı. Sonuç `ImportError`. Çözümler:

- Görevleri `@shared_task` ile tanımlayıp Celery uygulamasında `app.autodiscover_tasks()` kullanın; görev modülü uygulama nesnesini import etmek zorunda kalmaz.
- Modelin içinden görev çağırmak yerine görevi servis katmanından ya da `on_commit` içinden tetikleyin.
- Son çare olarak import'u fonksiyonun içine alın.

## Hangi durumda hangisi

Celery'yi seçin, eğer:

- RabbitMQ'nun teslim garantilerine ihtiyacınız varsa,
- karmaşık iş akışlarınız (chain, chord, ayrı kuyruklara yönlendirme) varsa,
- proje senkron bir Django ya da Flask uygulamasıysa.

Arq ya da başka bir async kuyruğu seçin, eğer:

- uygulama FastAPI gibi asyncio tabanlıysa ve görevler çoğunlukla I/O bekliyorsa,
- Redis zaten altyapınızdaysa ve bir broker daha işletmek istemiyorsanız,
- bakım modundaki bir projeye bağlanmayı kabul ediyorsanız (Arq için) ya da Taskiq, SAQ gibi aktif geliştirilen bir alternatife bakıyorsanız.

asyncio'nun kendisini daha iyi anlamak isterseniz [asyncio yazıma](/posts/python-asyncio-paralel-islem/) göz atın. Hangi aracı seçerseniz seçin, ilk gün yapılacaklar aynı: zaman limiti koyun, retry sayısını sınırlayın, görevleri iki kez çalışmaya dayanıklı yazın ve kuyruk derinliğini izleyin.
