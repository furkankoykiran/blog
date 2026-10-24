---
title: "Uptime Kuma ile Servis İzleme: Kurulum ve Pratik Ayarlar"
description: "Uptime Kuma 2 ile self-hosted izleme: Docker kurulumu, reverse proxy, keyword ve push monitor'ler, Telegram ve webhook bildirimleri, güvenlik, yedekleme."
date: 2025-02-01T10:00:00+03:00
slug: "uptime-kuma-servis-izleme"
postType: guide
tags: ["monitoring", "docker", "devops", "automation"]
categories: ["DevOps"]
coverAlt: "Uptime Kuma panelinde servislerin çalışma süresi ve yanıt süresi grafikleri"
draft: false
---

Uptime Kuma, kendi sunucunuzda çalıştırdığınız açık kaynak bir servis izleme aracı. Sitelerinize, API'lerinize ve arka plan işlerinize düzenli aralıklarla istek atar, bir şey bozulduğunda Telegram, Discord, e-posta gibi kanallardan haber verir ve isterseniz herkese açık bir durum sayfası yayınlar. UptimeRobot benzeri SaaS araçların ücretsiz planlarında kontrol aralığı ve monitor sayısı sınırlıdır. Uptime Kuma'da tek sınır sizin sunucunuz.

Bu yazıda Uptime Kuma 2 ile kurduğum yapıyı, işime yarayan monitor tiplerini, bildirim ayarlarını ve kurulumdan sonra mutlaka yaptığım güvenlik ve yedekleme adımlarını anlatıyorum.

## Uptime Kuma ne zaman yeter?

Uptime Kuma dışarıdan bakar: "Bu adres cevap veriyor mu, ne kadar sürede, içinde beklediğim metin var mı?" Uygulamanın içini görmez. CPU, bellek, kuyruk uzunluğu, istek başına gecikme dağılımı gibi metrikler için [Prometheus ve Grafana](/posts/prometheus-grafana-ile-izleme/) gerekiyor. Küçük bir proje veya birkaç kişisel servis için Uptime Kuma tek başına yeterli. Büyüdükçe ikisini birlikte kullanmak mantıklı: Prometheus içeriyi, Uptime Kuma kullanıcının gördüğünü ölçer.

Projenin README'sindeki ekran görüntüsü panelin nasıl göründüğünü özetliyor: solda monitor listesi, sağda seçili monitor'ün yanıt süresi, uptime oranları ve sertifika bitişine kalan gün.

![Uptime Kuma README'sindeki koyu temalı panel: monitor listesi, Up durumu, yanıt süresi, 24 saat ve 30 gün uptime, Cert Exp sütunu ve gecikme grafiği](img/uptime-kuma-readme.webp "Uptime Kuma'nın GitHub README'si. Kaynak: louislam/uptime-kuma, MIT")

## İzleme aracını izlenen sunucuya kurmayın

Kurulumdan önce yerine karar vermek gerekiyor. Uptime Kuma'yı izlediği servislerle aynı sunucuya kurarsanız, sunucu tamamen çöktüğünde izleme aracı da onunla birlikte gider ve size kimse haber vermez. Ben Uptime Kuma'yı ayrı, küçük bir VPS'te çalıştırıyorum. Bütçe yoksa en azından farklı bir sağlayıcıdaki ucuz bir makine veya evdeki bir mini PC, aynı sunucudan iyidir.

Bu yazıda kurduğum yapının tamamı şöyle: Uptime Kuma dışarıdan HTTP kontrolü yapıyor, bot kendisi push isteği gönderiyor, alarmlar Telegram'a ve webhook alıcısına gidiyor.

## Docker Compose ile kurulum

```yaml {title="compose.yaml"}
services:
  uptime-kuma:
    image: louislam/uptime-kuma:2
    restart: unless-stopped
    volumes:
      - ./data:/app/data
    ports:
      - "127.0.0.1:3001:3001"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

Portu `127.0.0.1` üzerine bağlıyorum, çünkü dışarıdan erişim reverse proxy üzerinden gelecek. `./data` klasörü bütün ayarları ve geçmişi tutar. Uptime Kuma'nın dokümantasyonu bu klasörün NFS gibi ağ dosya sistemlerinde tutulmasını desteklemiyor, yerel disk kullanın.

İlk açılışta kurulum ekranı veritabanını soruyor. Uptime Kuma 2 ile SQLite, gömülü MariaDB veya harici bir MariaDB/MySQL arasında seçim yapabiliyorsunuz. Birkaç düzine monitor için SQLite yeterli. Yüzlerce monitor ve uzun geçmiş tutacaksanız MariaDB daha rahat ediyor.

1.x sürümünden yükseltiyorsanız önce `data` klasörünün yedeğini alın. 2.x ilk açılışta veritabanını yeni şemaya taşıyor ve geçmiş veri çoksa bu işlem uzun sürebiliyor.

## Reverse proxy ve WebSocket

Arayüz, canlı güncellemeler için Socket.IO (WebSocket) kullanıyor. Proxy arkasında sayfa "Connecting..." yazısında takılıyorsa sebep neredeyse her zaman eksik WebSocket header'larıdır. nginx için:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name status.example.com;

    # ssl_certificate ve ssl_certificate_key satırları burada

    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Traefik kullanıyorsanız ek bir ayar gerekmiyor, WebSocket'i kendisi taşıyor. Kurulum için [Traefik v3 yazıma](/posts/traefik-reverse-proxy-ssl-yonetimi/) bakabilirsiniz. Proxy arkasında çalışırken Ayarlar > Reverse Proxy bölümünden `X-Forwarded-For` header'ına güvenmeyi açın, yoksa giriş loglarında herkes proxy'nin IP'siyle görünür.

## İşe yarayan monitor tipleri

Sadece "200 OK geliyor mu" diye bakmak yetmiyor. Bir uygulama veritabanına bağlanamadığında da 200 ile bir hata sayfası dönebilir. Benim en çok kullandığım tipler:

- HTTP(s) - Keyword: Sayfada belirli bir metnin olup olmadığına bakar. Ana sayfa için footer'daki bir metni seçiyorum. Sayfa boş veya hata şablonu dönerse monitor düşer.
- HTTP(s) - Json Query: API'nin döndüğü JSON üzerinde bir ifade çalıştırıp sonucu beklenen değerle karşılaştırır. `/health` endpoint'i `{"status": "ok", "db": "ok"}` dönüyorsa `db` alanına bakmak, yalnızca durum koduna bakmaktan çok daha fazlasını söyler.
- Push: Ters yönde çalışır. Uptime Kuma size bir URL verir, işiniz bu URL'yi düzenli aralıklarla çağırır. Belirlenen süre içinde çağrı gelmezse monitor düşer. Cron işleri, Telegram botları, trading botları gibi dışarıya port açmayan süreçler için en uygun tip bu.
- Docker Container: Konteynerin çalışır durumda olup olmadığına bakar.
- DNS ve TCP Port: Domain'in doğru IP'ye çözülüp çözülmediğine ve veritabanı gibi servislerin portunun açık olup olmadığına bakar. Port açık olması veritabanının sorgu cevapladığını göstermez, bunu unutmayın.

HTTP monitor'lerde sertifika süresi bildirimini de açıyorum. Let's Encrypt artık süre dolum e-postası göndermediği için otomatik yenileme sessizce bozulursa bunu Uptime Kuma'dan öğreniyorum.

### Push monitor ile arka plan işini izlemek

Bir Python döngüsünün hâlâ çalıştığını görmek için her turda push URL'sine istek atmak yeterli:

```python
import time

import httpx

PUSH_URL = "https://status.example.com/api/push/AbCdEf1234"


def run_once() -> None:
    ...  # asıl iş burada


while True:
    started = time.monotonic()
    try:
        run_once()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        httpx.get(PUSH_URL, params={"status": "up", "msg": "OK", "ping": elapsed_ms}, timeout=5)
    except Exception as exc:
        httpx.get(PUSH_URL, params={"status": "down", "msg": str(exc)[:200]}, timeout=5)
    time.sleep(60)
```

Monitor'ün "Heartbeat Interval" değerini döngü süresinden biraz uzun tutun, yoksa yavaş bir tur yanlış alarm üretir. Süreç tamamen ölürse istek hiç gelmez ve monitor yine düşer. Bu, süreç içi hata yakalamanın göremediği durumu da yakalıyor.

## Bildirimler

Uptime Kuma 90'dan fazla bildirim kanalını destekliyor. Ben Telegram kullanıyorum: BotFather ile bir bot açıp token'ı alıyorum, bota bir mesaj atıp chat ID'yi öğreniyorum ve Ayarlar > Bildirimler kısmına giriyorum. Bildirimi "Varsayılan olarak etkin" işaretlerseniz yeni monitor'lere otomatik eklenir.

Gürültüyü azaltmak için her monitor'de "Retries" değerini 2-3 yapıyorum. Tek bir zaman aşımı alarm üretmiyor, art arda başarısız kontroller üretiyor.

Kendi sisteminize bildirim göndermek için Webhook tipini kullanabilirsiniz. Varsayılan gövde `heartbeat`, `monitor` ve `msg` alanlarından oluşan bir JSON. "Custom Body" seçerseniz gövdeyi Liquid şablonuyla siz yazarsınız. Kullanılabilen değişkenler `name`, `status`, `hostnameOrURL`, `msg`, `monitorJSON` ve `heartbeatJSON`:

```json
{
  "text": "{{ status }} {{ name }}",
  "detail": "{{ msg }}",
  "url": "{{ hostnameOrURL }}"
}
```

## Webhook ile otomatik müdahale

Webhook'u bir alarm kanalı olarak değil, küçük bir otomasyon tetikleyicisi olarak da kullanmak mümkün. Örneğin bir servis düştüğünde onu yeniden başlatan bir alıcı yazılabilir. Bunu yaparken iki kurala uyuyorum: alıcı bir paylaşılan sırla korunur ve yalnızca önceden tanımlı servisleri yeniden başlatabilir. Gelen veriden komut üretmek, uzaktan komut çalıştırma açığı demek.

```python
import hmac
import os
import subprocess

from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI()
SECRET = os.environ["KUMA_WEBHOOK_SECRET"]
RESTARTABLE = {"Public API": "my-api.service"}  # monitor adı -> systemd unit


@app.post("/kuma")
async def kuma_hook(request: Request, x_kuma_secret: str = Header(default="")) -> dict[str, str]:
    if not hmac.compare_digest(x_kuma_secret, SECRET):
        raise HTTPException(status_code=401)

    payload = await request.json()
    heartbeat = payload.get("heartbeat") or {}
    monitor = payload.get("monitor") or {}

    # heartbeat.status: 0 = DOWN, 1 = UP
    if heartbeat.get("status") != 0:
        return {"action": "none"}

    unit = RESTARTABLE.get(monitor.get("name") or "")
    if unit is None:
        return {"action": "none"}

    subprocess.run(["sudo", "systemctl", "restart", unit], check=True, timeout=60)
    return {"action": f"restarted {unit}"}
```

Uptime Kuma tarafında webhook bildiriminin "Additional Headers" alanına `{"X-Kuma-Secret": "..."}` yazıyorum. Alıcıyı çalıştıran kullanıcıya sudoers'ta yalnızca o `systemctl restart` komutu için izin veriyorum. Otomatik restart sorunu saklar, çözmez. Aynı servis gün içinde üçüncü kez yeniden başlıyorsa asıl hata kodda veya kaynaklarda demektir. systemd'nin kendi `Restart=` ve watchdog mekanizması çoğu durumda bu alıcıya gerek bırakmıyor, onu [zarif kapanış ve systemd yazımda](/posts/zarif-kapanis-sinyal-isleme/) anlattım.

## Kurulumdan sonra güvenlik

İzleme paneli, altyapınızın haritası gibi. Hangi servisin nerede çalıştığı, iç adresler, bazen API anahtarları içeren header'lar orada duruyor. Kurulumdan hemen sonra yaptıklarım:

1. Güçlü bir yönetici şifresi ve Ayarlar > Güvenlik altından iki faktörlü doğrulama (TOTP).
2. Paneli yalnızca reverse proxy üzerinden ve mümkünse VPN veya IP kısıtlamasıyla açmak. Durum sayfası herkese açık kalabilir, yönetim paneli kalmamalı.
3. Durum sayfasına iç hostname veya IP gösteren monitor koymamak.
4. Docker Container monitor'ü için `/var/run/docker.sock` dosyasını doğrudan bağlamamak. Socket'e erişen bir süreç host üzerindeki bütün konteynerleri yönetebilir. Bunun yerine yalnızca okuma isteklerine izin veren bir Docker socket proxy'yi TCP üzerinden "Docker Host" olarak tanımlıyorum.

## Durum sayfası ve bakım pencereleri

Uptime Kuma'dan `status.example.com` gibi bir adreste herkese açık bir durum sayfası yayınlayabilirsiniz. Monitor'leri gruplara ayırıp (Web, API, Bot) yalnızca kullanıcıyı ilgilendirenleri gösteriyorum. Planlı bir çalışma yapacağım zaman bakım penceresi tanımlıyorum. O süre içinde monitor'ler alarm üretmiyor ve durum sayfasında bakım notu görünüyor.

## Yedekleme

Her şey `data` klasöründe. SQLite kullanıyorsanız çalışan bir veritabanı dosyasını `cp` ile kopyalamak, yazma sırasında yakalanırsa bozuk bir yedek verebilir. En basit güvenli yol konteyneri birkaç saniyeliğine durdurup klasörü arşivlemek:

```bash {title="backup-kuma.sh"}
#!/usr/bin/env bash
set -euo pipefail

cd /opt/uptime-kuma
BACKUP_DIR=/backups/uptime-kuma
mkdir -p "$BACKUP_DIR"

docker compose stop uptime-kuma
tar -czf "$BACKUP_DIR/kuma-$(date +%F).tar.gz" data
docker compose start uptime-kuma

find "$BACKUP_DIR" -name 'kuma-*.tar.gz' -mtime +14 -delete
```

Bu betiği gece 03:00'te cron veya bir systemd timer ile çalıştırıyorum. Gömülü MariaDB kullanıyorsanız da aynı yöntem geçerli. Harici MariaDB'de ise veritabanının kendi yedekleme aracını kullanmanız gerekir. Yedeği aynı makinede bırakmayın, başka bir yere kopyalayın.

## Sırada ne var

Ayrı bir makinede çalışan, keyword ve push monitor'leri olan, Telegram'a bildirim atan ve her gece yedeklenen bir Uptime Kuma, küçük bir projenin ihtiyacının çoğunu karşılıyor. İlk adım olarak yukarıdaki push örneğini kendi cron işlerinizden birine ekleyin. Servis tarafında [Docker healthcheck'lerini](/posts/docker-ile-python-uygulamasi-containerization/) ve [SIGTERM ile zarif kapanışı](/posts/zarif-kapanis-sinyal-isleme/) düzgün kurmak, yeniden başlatmaların alarm üretmesini de azaltıyor. Kaynak kod ve sürüm notları [Uptime Kuma GitHub reposunda](https://github.com/louislam/uptime-kuma).
