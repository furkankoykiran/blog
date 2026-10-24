---
title: "Python Loglama: structlog ve logging ile Doğru Kurulum"
description: "Python loglama için doğru kurulum: logging modülünün kuralları, structlog ile JSON log, request ID, hassas veri maskeleme, OpenTelemetry ve sık hatalar."
date: 2025-02-22T13:00:00+03:00
slug: "python-loglama-en-iyi-uygulamalar"
postType: guide
tags: ["python", "monitoring", "fastapi", "devops"]
categories: ["Python"]
coverAlt: "Python uygulamasından çıkan JSON logların toplayıcı üzerinden log sistemine aktığı yapı"
draft: false
---

Python loglama konusunda en sık gördüğüm iki uç var: her yere `print` serpiştirmek ya da `logging` modülünü tamamen bırakıp başka bir kütüphaneye geçmek. İkisi de gereksiz. Benim kullandığım kurulum `logging` modülünü omurga olarak tutuyor ve üstüne structlog ile yapılandırılmış (JSON) çıktı ekliyor. Böylece hem kendi kodunuzun hem de uvicorn, SQLAlchemy, httpx gibi kütüphanelerin logları aynı formatta çıkıyor.

structlog'u kendi trading botlarımdan birinde de kullanıyorum. Aşağıdaki kurulum yeni bir servis için iyi bir başlangıç noktası; örnekleri Python 3.13 ve structlog 26.1 ile çalıştırdım.

## Önce logging modülünün kuralları

structlog'a geçmeden önce, `logging` ile doğru çalışmanın birkaç kuralı var ve bunlar structlog kullansanız da geçerli.

Her modülde kendi logger'ınızı adıyla alın:

```python
import logging

logger = logging.getLogger(__name__)


def fetch_prices(symbol: str) -> None:
    logger.info("fetching prices for %s", symbol)
```

`%s` biçimi bilinçli bir tercih. f-string kullanırsanız mesaj, log seviyesi kapalı olsa bile her seferinde oluşturulur. Ayrıca Sentry gibi araçlar aynı şablondan gelen hataları gruplayamaz, çünkü her mesaj farklı bir string olur.

Yapılandırma (handler, format, seviye) sadece uygulamanın giriş noktasında, bir kez yapılır. Kütüphane yazıyorsanız hiç yapılandırma yapmayın; sadece paketin `__init__.py` dosyasına bir `NullHandler` ekleyin:

```python
# mylib/__init__.py
import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
```

Kütüphane içinde `logging.basicConfig()` çağırmak, kütüphaneyi kullanan uygulamanın log ayarlarını bozar.

Hata yakaladığınızda `logger.exception()` kullanın; mesajın yanına traceback'i de ekler:

```python
try:
    place_order(order)
except OrderRejected:
    logger.exception("order rejected: %s", order.id)
    raise
```

## Neden JSON log

Düz metin log insan için okunaklı ama makine için değil:

```text
2026-10-23 13:00:00 ERROR User 123 failed to purchase item 456 using coupon SAVE10
```

Bu satırdan kupon kodunu çıkarmak için regex yazmanız gerekir ve mesajın formatı değiştiği gün regex bozulur. Aynı olay JSON olarak:

```json
{"event": "purchase_failed", "user_id": 123, "item_id": 456, "coupon": "SAVE10", "error_code": "INSUFFICIENT_FUNDS", "level": "error", "timestamp": "2026-10-23T10:00:00Z"}
```

Artık Loki, Elasticsearch ya da CloudWatch Logs Insights'ta `event="purchase_failed" AND coupon="SAVE10"` diye sorgulayabilirsiniz. Olay adını sabit bir anahtar (`purchase_failed`) tutup değişken bilgileri ayrı alanlara koymak işin özü.

Bu loglar uygulamadan stdout'a JSON olarak çıkar, container runtime ve bir toplayıcı (Fluent Bit, Vector ya da Alloy) üzerinden Loki'ye ya da Elasticsearch'e gider; arayüzde Grafana veya Kibana ile okunur.

## structlog'u logging ile birlikte kurmak

structlog'u tek başına da kullanabilirsiniz, ama o zaman üçüncü parti kütüphanelerin logları başka formatta akar. `ProcessorFormatter` ile structlog, standart `logging`'in formatter'ı olarak çalışır ve iki kaynaktan gelen logları aynı işlemci zincirinden geçirir:

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

    # structlog.get_logger() ile yazılan loglar
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
            # show_locals=False: traceback'teki yerel değişkenler (şifreler dahil) loga düşmesin
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            structlog.processors.JSONRenderer(),
        ]
    else:
        renderers = [structlog.dev.ConsoleRenderer()]

    # Hem structlog hem de logging.getLogger() loglarını biçimlendiren formatter
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

    # uvicorn kendi handler'larını kurar; logları root'a yönlendir
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
```

Kullanımı:

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

İki satır da aynı formatta çıkar:

```json
{"order_id": 123, "amount": 49.9, "event": "order_created", "level": "info", "logger": "__main__", "timestamp": "2026-10-23T10:00:00.123456Z"}
{"event": "disk 91% full", "level": "warning", "logger": "third_party", "timestamp": "2026-10-23T10:00:00.124001Z"}
```

Exception'lar JSON içinde yapılandırılmış bir `exception` alanı olarak çıkar. `show_locals=False` ayarını atlamayın: varsayılan davranış traceback'teki her frame'in yerel değişkenlerini de yazar, yani o an bellekte duran bir şifre ya da token loga düşebilir.

Lokal geliştirmede `LOG_JSON=0` verip okunaklı konsol çıktısına geçiyorum; terminalde seviyeler renkli görünür. Aynı iki satır bu modda şöyle çıkıyor:

![LOG_JSON=0 ile çalışan app.py: order_created ve disk 91% full satırları zaman damgası, seviye ve logger adıyla hizalı konsol formatında](img/structlog-console-renderer.webp "structlog'un ConsoleRenderer çıktısı, yukarıdaki kodun gerçek çalıştırması")

Üretimde her zaman JSON.

## Bağlam: her satıra request ID

Bir isteğe ait bütün logları bir arada görmek için her satırda aynı `request_id` olmalı. Bunu her log çağrısına elle eklemek yerine `contextvars` ile bir kez bağlarsınız; `merge_contextvars` işlemcisi her satıra ekler. asyncio ile de doğru çalışır, çünkü her task kendi context kopyasını taşır. FastAPI'de bir middleware yeterli:

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

Gelen istekte `X-Request-ID` varsa onu kullanmak, gateway ya da Nginx'ten başlayan zinciri korur. Başka bir servise istek atarken aynı başlığı iletin. FastAPI servislerinin geri kalan yapısını [FastAPI yazısında](/posts/python-fastapi-restful-api-gelistirme/) anlattım.

Arka plan görevlerinde de aynı fikir geçerli: görevin başında `task_id` ve ilgili iş anahtarını (`user_id`, `order_id`) bağlayın. Görev kuyruklarında neden önemli olduğunu [Celery ve Arq yazısında](/posts/celery-arq-asenkron-gorev-kuyrugu/) anlattım.

## OpenTelemetry kullanıyorsanız trace ID ekleyin

Servisler arası izleme için OpenTelemetry kullanıyorsanız, trace ve span ID'lerini log satırına eklemek logla trace'i birbirine bağlar. Küçük bir işlemci yeter:

```python
from opentelemetry import trace


def add_otel_ids(logger, method_name, event_dict):
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
```

Bunu `shared_processors` listesine ekleyin. Grafana'da bir log satırından ilgili trace'e tek tıkla geçebilirsiniz.

## Hassas veriyi log'a yazmayın

Şifre, token, kart numarası loga düştüğü anda log sistemine erişimi olan herkesin eline geçmiş olur; KVKK ve GDPR açısından da sorun. En iyi önlem bu verileri hiç loglamamak. Yine de biri yanlışlıkla `log.info("signup", password=raw)` yazarsa diye bir güvenlik ağı kurun:

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

Bu işlemciyi de `shared_processors`'ın sonuna ekleyin. İç içe sözlükleri de taradığı için `log.info("webhook", payload=body)` gibi çağrılarda da çalışır. Serbest metin içine gömülmüş bir şifreyi yakalamaz; o yüzden ilk kural yine "hassas veriyi mesaja koyma".

## Performans

Log yazmak bedava değil. Benim dikkat ettiklerim:

- Container içinde dosyaya değil stdout'a yazın. Dosyaya yazma ve rotasyon işini container runtime ile log toplayıcı yapsın.
- Ağ üzerinden log gönderen yavaş bir handler kullanmak zorundaysanız, ana thread'i bloklamaması için standart kütüphanedeki `QueueHandler` ve `QueueListener` ile ayrı bir thread'e alın.
- Sıcak döngü içinde log yazmayın. Gerekirse sayaç tutup döngü bitince tek satır yazın; sayaç zaten bir metrik adayıdır.
- `filter_by_level` işlemcisi, kapalı seviyedeki loglarda geri kalan zinciri hiç çalıştırmaz. Debug logları üretimde neredeyse maliyetsiz kalır.

Log maliyetinin gerçekten sorun olup olmadığını tahminle değil ölçerek anlayın; bunun yolunu [profiling yazısında](/posts/python-performans-profiling-optimizasyon/) anlattım.

## Log altyapısına dair iki not

- Elasticsearch kullanıyorsanız sayısal alanların (`latency_ms` gibi) sayı olarak indekslendiğinden emin olun. Yoksa `latency_ms > 500` sorgusu çalışmaz. Aynı alanın bir serviste sayı, diğerinde string gelmesi mapping çakışmasına yol açar; alan adlarını servisler arasında ortak tutun.
- Loglar çok hızlı büyür. Retention kuralı (Elasticsearch'te ILM, Loki'de retention ayarı) baştan kurun; 30 günden eskisini silmek ya da ucuz depolamaya taşımak çoğu ekip için yeterli.

Loglar "ne oldu" sorusunu, metrikler "ne kadar sık oluyor" sorusunu cevaplar. İkisini birlikte kurmak için [Prometheus ve Grafana yazıma](/posts/prometheus-grafana-ile-izleme/) bakabilirsiniz.

## Sık yapılan hatalar

- `print` ile debug, sonra silmeyi unutmak. `logger.debug` yazın, seviyeyle kapatın.
- f-string ile log mesajı (`logger.info(f"...")`).
- Kütüphane içinde `basicConfig`.
- Hem kendi logger'ına handler ekleyip hem root'a propagate etmek: her satır iki kez çıkar.
- `except: logger.error(str(e))` ile traceback'i kaybetmek. `logger.exception` kullanın.

## Sırada ne var

Mevcut bir projede başlamak için `configure_logging` fonksiyonunu giriş noktasına ekleyin ve tek bir endpoint'e request ID middleware'ini takın. Loglarınız JSON olarak akmaya başladığında, ilk iş log sisteminizde `request_id` ile tek bir isteğin bütün satırlarını filtrelemeyi deneyin. Ayrıntılar için [structlog dokümanı](https://www.structlog.org/en/stable/standard-library.html) ve Python'un [Logging HOWTO](https://docs.python.org/3/howto/logging.html) sayfası en iyi kaynaklar.

Loglarla birlikte metrikleri de kurmak isterseniz [Prometheus ve Grafana yazısı](/posts/prometheus-grafana-ile-izleme/), arka plan görevlerinde bağlam taşımak için de [Celery ve Arq yazısı](/posts/celery-arq-asenkron-gorev-kuyrugu/) sıradaki adım.
