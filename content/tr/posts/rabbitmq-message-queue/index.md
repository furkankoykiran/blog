---
title: "RabbitMQ Mesaj Kuyruğu: aio-pika ile Güvenilir Mimari"
description: "RabbitMQ mesaj kuyruğunda veri kaybetmemek için prefetch, publisher confirm, quorum queue ve dead letter exchange; Python aio-pika ile çalışan örneklerle."
date: 2025-08-18T10:30:00+03:00
slug: "rabbitmq-mesaj-kuyrugu-sistemi"
postType: guide
tags: ["messaging", "python", "asyncio", "monitoring"]
categories: ["Backend"]
coverAlt: "RabbitMQ exchange, kuyruk ve dead letter kuyruğu arasındaki mesaj akışı"
draft: false
---

RabbitMQ mesaj kuyruğu ilk bakışta basit: bir servis mesajı atar, diğeri alır. Ama production'da asıl sorular başka. Broker yeniden başlarsa mesaj kaybolur mu? Bir consumer çökerse mesajı kim alır? Hatalı bir mesaj kuyruğu sonsuza kadar tıkar mı?

Bu yazıda RabbitMQ'yu Python tarafında `aio-pika` ile kullanırken uyguladığım ayarları anlatıyorum. Örnekler RabbitMQ 4.x ve aio-pika 9.x ile yazıldı.

## Mimari: exchange, kuyruk ve DLX

Producer mesajı doğrudan kuyruğa değil exchange'e gönderir. Exchange, routing key'e ve binding'lere bakıp mesajı bir veya daha fazla kuyruğa kopyalar. İşlenemeyen mesajlar da bir dead letter exchange (DLX) üzerinden ayrı bir kuyruğa düşer.

Exchange tipini ihtiyaca göre seçin:

- `direct`: routing key birebir eşleşmeli. Basit iş dağıtımı için yeterli.
- `fanout`: routing key önemsiz, mesaj bağlı tüm kuyruklara gider. "Kullanıcı kaydoldu" olayını hem e-posta hem analitik servisinin alması gibi.
- `topic`: `order.created`, `order.*.eu` gibi desenlerle eşleşir; `*` tek kelime, `#` sıfır veya daha fazla kelime yerine geçer. Ben çoğu projede bununla başlıyorum.
- `headers`: routing key yerine header değerlerine bakar. Nadiren gerekiyor.

## Veri kaybetmemek için dört ayar

Mesajın uçtan uca güvende olması için hepsinin birlikte açık olması gerekiyor:

1. Kuyruk `durable=True` olmalı ki broker yeniden başladığında tanım silinmesin.
2. Mesaj `delivery_mode=PERSISTENT` ile gönderilmeli ki diske yazılsın. Quorum queue'larda mesajlar zaten kalıcı tutuluyor, ama classic queue'larda bu ayar şart.
3. Publisher confirm açık olmalı. Bu olmadan `publish` çağrısı başarılı döner ama broker'ın mesajı gerçekten alıp almadığını bilemezsiniz. aio-pika'da channel'lar confirm modu açık geliyor ve `await exchange.publish(...)` broker onayını bekliyor.
4. Consumer tarafında otomatik ACK kullanılmamalı. `no_ack=True` ile mesaj consumer'a ulaştığı an silinir; işlerken process çökerse mesaj gider. ACK'i iş bittikten sonra gönderin.

## Producer: aio-pika ile yayınlama

Her mesaj için yeni bağlantı açmayın. Bağlantıyı uygulama açılışında kurup açık tutun, `connect_robust` kopmalarda kendisi yeniden bağlanıyor.

```python
import asyncio
import json

import aio_pika

AMQP_URL = "amqp://app:secret@localhost/"


async def main() -> None:
    connection = await aio_pika.connect_robust(AMQP_URL)
    async with connection:
        channel = await connection.channel()  # publisher confirm varsayılan olarak açık
        exchange = await channel.declare_exchange(
            "orders", aio_pika.ExchangeType.TOPIC, durable=True
        )

        order = {"id": 42, "total": 199.90}
        await exchange.publish(
            aio_pika.Message(
                body=json.dumps(order).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(order["id"]),
            ),
            routing_key="order.created",
        )
        print("broker mesajı onayladı")


asyncio.run(main())
```

Mesaj gövdesini küçük tutun. 10 MB'lık JSON ya da base64 resim koymak yerine dosyayı S3'e atıp mesaja sadece yolunu yazın. RabbitMQ birkaç KB'lık mesajlarla rahat çalışıyor.

## Consumer: prefetch, ACK ve DLX

Consumer tarafındaki en önemli ayar `prefetch_count`. Bu, consumer'ın elinde aynı anda ACK'lenmemiş kaç mesaj olabileceğini belirler. Ayarlamazsanız RabbitMQ kuyruktaki her şeyi ilk bağlanan consumer'a itebilir; o consumer belleği doldururken diğerleri boş bekler.

Ağır ve uzun süren işler için 1 ile başlayın, böylece iş consumer'lar arasında adil dağılır. Mesajlar küçük ve hızlıysa 20 ile 100 arası değerler ağ gidiş-dönüşlerini azaltıp throughput'u artırır. Doğru değeri ölçerek bulursunuz.

```python
import asyncio
import json

import aio_pika

AMQP_URL = "amqp://app:secret@localhost/"


async def handle(order: dict) -> None:
    if "id" not in order:
        raise ValueError("id alanı yok")
    await asyncio.sleep(0.1)  # gerçek iş burada


async def main() -> None:
    connection = await aio_pika.connect_robust(AMQP_URL)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=10)

        dlx = await channel.declare_exchange(
            "orders.dlx", aio_pika.ExchangeType.FANOUT, durable=True
        )
        dead = await channel.declare_queue("orders.dead", durable=True)
        await dead.bind(dlx)

        exchange = await channel.declare_exchange(
            "orders", aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(
            "orders",
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-dead-letter-exchange": "orders.dlx",
                "x-delivery-limit": 5,
            },
        )
        await queue.bind(exchange, routing_key="order.*")

        async with queue.iterator() as messages:
            async for message in messages:
                # Blok hatasız biterse ACK, exception olursa reject(requeue=False)
                async with message.process(requeue=False):
                    await handle(json.loads(message.body))


asyncio.run(main())
```

`message.process(requeue=False)` bloğu iki işi birden yapıyor: iş bitince ACK gönderiyor, exception çıkarsa mesajı yeniden kuyruğa koymadan reddediyor. Reddedilen mesaj `x-dead-letter-exchange` sayesinde silinmek yerine `orders.dead` kuyruğuna gidiyor.

Her seferinde consumer'ı çökerten bir mesajı sürekli kuyruğa geri koyarsanız, aynı mesaj sonsuz döngüye girer ve kuyruğu tıkar. DLX ile o mesaj kenara ayrılır, siz de sonra inceleyip hatayı düzeltir ve tekrar yayınlarsınız. Geçici hatalarda (bağlantı zaman aşımı gibi) yeniden denemek istiyorsanız `requeue=True` ile reddedip quorum queue'nun `x-delivery-limit` sayacına güvenebilirsiniz; limit aşılınca mesaj yine DLX'e düşer. RabbitMQ 4.0'dan beri quorum queue'larda bu limit varsayılan olarak 20.

İki yolu yan yana koyunca akış şöyle:

![Geçici hata reject(requeue=True) ile x-delivery-limit'e kadar tekrar denenir; kalıcı hata reject(requeue=False) ile hemen orders.dead'e gider](img/retry-vs-dead-letter.webp "Geçici hata tekrar denenir, kalıcı hata hemen kenara ayrılır")

## Classic queue mi quorum queue mu?

Eski yazılarda yüksek erişilebilirlik için "mirrored classic queue" önerilir. Bu özellik RabbitMQ 4.0 ile tamamen kaldırıldı. Çoğaltılmış, kalıcı bir kuyruk istiyorsanız cevap quorum queue: Raft konsensüsüyle mesajları birden fazla node'a yazıyor, node kaybında veri tutarlılığını koruyor ve poison message'lar için yukarıdaki teslim limitini veriyor.

Classic queue, kaybedilmesi sorun olmayan geçici veriler ve tek node'lu kurulumlar için hâlâ mantıklı. Ayrıca `prefetch_count` için `global=True` kullanmayın; quorum queue'lar global QoS'u desteklemiyor ve consumer başına limit zaten istediğiniz şey.

## İzleme

Management arayüzü anlık durumu güzel gösteriyor ama geçmişi tutmuyor. RabbitMQ'nun yerleşik `rabbitmq_prometheus` eklentisini açıp metrikleri Prometheus'a, oradan Grafana'ya almak gerekiyor; kurulumu [Prometheus ve Grafana yazımda](/posts/prometheus-grafana-ile-izleme/) anlattım. Kuyruk bazlı metrikler `/metrics/per-object` endpoint'inden geliyor. Benim alarm kurduğum metrikler:

- `rabbitmq_queue_messages_ready`: bekleyen mesaj sayısı. Sürekli artıyorsa consumer'lar yetişemiyor.
- `rabbitmq_queue_messages_unacked`: alınmış ama ACK'lenmemiş mesajlar. Yüksekse consumer'lar takılmış ya da ACK göndermeyi unutuyor. Bu mesajlar broker belleğinde tutulur.
- `rabbitmq_queue_consumers`: beklenen consumer sayısının altına düşerse bir servis çökmüş olabilir.
- DLX kuyruğundaki mesaj sayısı: sıfırdan büyükse birinin bakması gerekiyor.

## Canlıya çıkmadan önce

- Varsayılan `guest` kullanıcısı silindi mi? (Zaten sadece localhost'tan bağlanabiliyor ama yine de.)
- Farklı projeler aynı broker'daysa ayrı vhost ve kullanıcılarla izole edildi mi?
- Kuyruklara `x-max-length` veya `x-message-ttl` ile bir üst sınır kondu mu? Durmuş bir consumer diski doldurmasın.
- Uygulama tek uzun ömürlü bağlantı ve iş başına channel kullanıyor mu?
- Cluster kuruyorsanız ağ bölünmesi stratejisi (`cluster_partition_handling`, genelde `pause_minority`) bilinçli seçildi mi?

## RabbitMQ mı Kafka mı?

İkisi farklı problemlere çözüm. RabbitMQ akıllı bir broker: mesajı routing kurallarına göre dağıtır, ACK gelince siler. İş kuyruğu, sipariş işleme, RPC gibi "bu işi biri yapsın" senaryolarında rahat. Kafka ise mesajları silmeyen, sıralı bir log: consumer kendi offset'ini tutar, geçmişi tekrar okuyabilir ve çok yüksek hacmi kaldırır. Olay akışı ve replay ihtiyacı varsa [Kafka yazıma](/posts/apache-kafka-event-streaming/) bakın. RabbitMQ'nun kendi stream kuyrukları da replay gerektiren daha küçük senaryolar için bir ara çözüm.

## Sonraki adım

Basit arka plan görevleri için doğrudan RabbitMQ yazmak yerine bir görev kuyruğu kütüphanesi daha az kod demek; [Celery ve ARQ karşılaştırmamda](/posts/celery-arq-asenkron-gorev-kuyrugu/) bunu anlattım. Doğrudan aio-pika kullanacaksanız yukarıdaki consumer'ı lokal bir RabbitMQ'ya bağlayıp bilerek hatalı bir mesaj gönderin ve `orders.dead` kuyruğuna düştüğünü görün. Detaylar için [aio-pika dokümantasyonu](https://docs.aio-pika.com/) ve RabbitMQ'nun [güvenilirlik rehberi](https://www.rabbitmq.com/docs/reliability) iyi başlangıç noktaları.
