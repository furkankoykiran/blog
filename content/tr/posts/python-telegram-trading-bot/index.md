---
title: "Python ile Telegram Trading Botu: python-telegram-bot v22"
description: "python-telegram-bot v22 ve CCXT ile fiyat alarmı veren Telegram trading botu: JobQueue, kullanıcı filtresi, kalıcı durum, token güvenliği ve risk."
date: 2024-07-22T12:00:00+03:00
slug: "python-telegram-trading-bot"
postType: guide
tags: ["python", "telegram", "trading", "asyncio", "automation"]
categories: ["Trading"]
series: ["Python ile Algoritmik Trading"]
coverAlt: "Telegram sohbetinde kripto fiyat alarmı gönderen bir Python botu"
draft: false
---

İlk Telegram botlarımı 2023'te yazdım: gelen mesajları anonim olarak bir gruba ileten küçük bir bot, bir oyun topluluğu için kara liste botu, bir kanaldaki gönderileri başka kanallara kopyalayan bir Pyrogram scripti. Sonra bu deneyimi trading tarafına taşıdım. DEX fiyatlarını izleyen arbitraj botumda Telegram, bulduğu fırsatları bana ulaştıran kanaldı; Telegram Wallet'ın P2P piyasası için de ayrı bir SDK yazdım.

Bu yazıda Python ile Telegram trading botu yazarken bugün kullandığım yapıyı anlatıyorum: python-telegram-bot v22 ve CCXT ile çalışan, sadece bana cevap veren, fiyat alarmı kuran bir bot. Emir gönderen kısmı bilerek dışarıda bıraktım, nedenini aşağıda açıklıyorum.

## Eski botlarımdan aldığım dersler

2023'teki botlarım python-telegram-bot'un v13 API'siyle yazılmıştı: `Updater`, `dispatcher`, senkron handler'lar ve `Filters`. Bu API v20 ile tamamen değişti. Kütüphane asyncio tabanlı oldu, `Updater` yerine `Application` geldi, `Filters` da `filters` modülüne dönüştü. İnternetteki örneklerin büyük kısmı hâlâ eski API ile yazılmış, kopyalanan kod da bu yüzden çalışmıyor.

İkinci ders daha acı: o dönemki botlardan birinde bot token'ı doğrudan kaynak kodun içindeydi ve repo herkese açıktı. Token, botun tamamının anahtarı. Kodda gördüğünüz anda BotFather üzerinden `/revoke` ile iptal edip yenisini almak gerekiyor. Artık token ve izinli kullanıcı listesi her zaman ortam değişkeninden geliyor.

Üçüncüsü bildirimlerle ilgili. Flash loan arbitraj botumda Telegram'a mesajı `aiohttp` ile doğrudan Bot API'ye `sendMessage` isteği atarak gönderiyordum. Sadece bildirim gönderecekseniz bu gayet yeterli. Komut almanız, kullanıcıya göre davranmanız ve zamanlanmış işler çalıştırmanız gerekiyorsa bir framework işinizi kolaylaştırıyor.

## Mimari

Botun yaptığı iş basit: `/price` ile anlık fiyat veriyor, `/alert` ile alarm kuruyor, arka planda 30 saniyede bir alarmları kontrol ediyor. Komutlar ve zamanlanmış iş aynı CCXT borsa nesnesini paylaşıyor.

Kurulum için Python 3.10 veya üzeri gerekiyor. JobQueue ek bir bağımlılık istediği için extra ile kuruyorum:

```bash
pip install "python-telegram-bot[job-queue]>=22" "ccxt>=4.5"
```

## Kod

Tamamı tek dosya. Önce kodu veriyorum, sonra kararları tek tek açıklıyorum.

```python {title="bot.py"}
import logging
import os

import ccxt.async_support as ccxt
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, PicklePersistence, filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # token içeren URL'leri loglamasın
log = logging.getLogger("alert-bot")

ALLOWED_IDS = {int(x) for x in os.environ["ALLOWED_USER_IDS"].split(",")}
ONLY_ME = filters.User(user_id=ALLOWED_IDS)
USAGE = "Kullanım: /alert BTC/USDT above|below 70000"

# Borsa nesnesi pickle edilemez, bu yüzden bot_data yerine burada duruyor.
exchange = ccxt.binance({"enableRateLimit": True})


async def post_init(app: Application) -> None:
    await exchange.load_markets()
    app.bot_data.setdefault("alerts", [])
    app.job_queue.run_repeating(check_alerts, interval=30, first=10)


async def post_shutdown(app: Application) -> None:
    await exchange.close()


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Kullanım: /price BTC/USDT")
        return
    symbol = context.args[0].upper()
    if symbol not in exchange.markets:
        await update.message.reply_text(f"{symbol} bu borsada yok.")
        return
    ticker = await exchange.fetch_ticker(symbol)
    await update.message.reply_text(f"{symbol}: {ticker['last']}")


async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) != 3 or context.args[1] not in ("above", "below"):
        await update.message.reply_text(USAGE)
        return
    symbol, direction, raw_level = context.args
    try:
        level = float(raw_level)
    except ValueError:
        await update.message.reply_text(USAGE)
        return
    symbol = symbol.upper()
    if symbol not in exchange.markets:
        await update.message.reply_text(f"{symbol} bu borsada yok.")
        return
    context.bot_data["alerts"].append(
        {"chat_id": update.effective_chat.id, "symbol": symbol,
         "direction": direction, "level": level}
    )
    await update.message.reply_text(f"Tamam: {symbol} {direction} {level}")


async def check_alerts(context: ContextTypes.DEFAULT_TYPE) -> None:
    alerts = context.bot_data["alerts"]
    if not alerts:
        return
    symbols = sorted({a["symbol"] for a in alerts})
    try:
        tickers = await exchange.fetch_tickers(symbols)
    except ccxt.NetworkError as exc:  # geçici hata: bir sonraki turda tekrar dene
        log.warning("fetch_tickers başarısız: %s", exc)
        return

    fired = []
    for a in list(alerts):
        last = tickers[a["symbol"]]["last"]
        hit = last >= a["level"] if a["direction"] == "above" else last <= a["level"]
        if hit:
            await context.bot.send_message(
                a["chat_id"], f"ALARM {a['symbol']} {last} ({a['direction']} {a['level']})"
            )
            fired.append(a)
    # send_message beklenirken yeni alarm eklenmiş olabilir; listeyi yeniden kurmuyoruz
    for a in fired:
        alerts.remove(a)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Handler hatası", exc_info=context.error)


def main() -> None:
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_BOT_TOKEN"])
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .persistence(PicklePersistence(filepath="bot_state.pickle"))
        .build()
    )
    app.add_handler(CommandHandler("price", price, filters=ONLY_ME))
    app.add_handler(CommandHandler("alert", alert, filters=ONLY_ME))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
```

Çalıştırmak için:

```bash
export TELEGRAM_BOT_TOKEN="BotFather'dan aldığınız token"
export ALLOWED_USER_IDS="123456789"
python bot.py
```

Kendi kullanıcı ID'nizi öğrenmek için @userinfobot gibi bir bota yazabilir ya da handler içinde `update.effective_user.id` değerini loglayabilirsiniz.

## Kararların nedenleri

### Sadece izinli kullanıcılar

Telegram'da bot adını bulan herkes ona mesaj atabilir. `filters.User` ile filtrelenmiş handler'lar izinsiz kullanıcının mesajını hiç görmüyor. Her handler'ın içinde `if user_id not in ...` yazmaktan daha güvenli, çünkü yeni bir komut eklerken unutma ihtimali kalmıyor.

### Tek istek, çok alarm

Her alarm için ayrı bir job açmak ilk akla gelen yol, ama yirmi alarm 30 saniyede yirmi `fetch_ticker` isteği demek. Burada tek bir job, alarmlardaki sembolleri toplayıp `fetch_tickers` ile tek istekte çekiyor. Borsanın hız sınırına (rate limit) takılmamak için bu fark önemli.

Telegram'ı bağlamadan önce bu çağrıyı tek başına denemek isterseniz şu küçük script yeterli. Binance'in herkese açık verisini okuduğu için API anahtarı istemiyor:

```python {title="check_tickers.py"}
import asyncio
import ccxt.async_support as ccxt


async def main():
    exchange = ccxt.binance({"enableRateLimit": True})
    try:
        await exchange.load_markets()
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        tickers = await exchange.fetch_tickers(symbols)  # tek istek
        print(f"1 fetch_tickers call, {len(tickers)} symbols")
        for symbol in symbols:
            print(f"  {symbol}: {tickers[symbol]['last']}")
    finally:
        await exchange.close()


asyncio.run(main())
```

Kendi makinemde çalıştırdığımda üç sembolün son fiyatı tek yanıtla geldi:

![check_tickers.py çıktısı: tek fetch_tickers çağrısı BTC/USDT, ETH/USDT ve SOL/USDT için son fiyatları döndürüyor](img/ccxt-fetch-tickers-run.webp "Tek istek, üç sembol. Fiyatlar çalıştırma anına ait.")

### Borsa bağlantısının ömrü

`ccxt.async_support` bir aiohttp oturumu açıyor. `post_shutdown` içinde `exchange.close()` çağırmazsanız bot kapanırken "Unclosed client session" uyarısı alırsınız. `load_markets()` da bir kere, açılışta çağrılıyor. Sembol doğrulaması bu sayede ağ isteği olmadan yapılıyor.

### Kalıcı durum

`PicklePersistence`, `bot_data` içeriğini diske yazıyor. Bot yeniden başladığında kurulmuş alarmlar kaybolmuyor. Borsa nesnesini `bot_data` içine koymamamın sebebi de bu: pickle edilemiyor. Tek kullanıcılı bir bot için pickle dosyası yeterli. Birden fazla süreç ya da sunucu söz konusuysa durumu Redis veya PostgreSQL'e taşımak gerekir.

### Ağ hataları ve loglar

`NetworkError` geçici bir sorun, o turu atlayıp sonrakini bekliyorum. Diğer hatalar `on_error` handler'ına düşüyor ve loglanıyor. httpx logger'ını WARNING seviyesine çekmemin nedeni şu: INFO seviyesinde her isteğin URL'si loglanıyor ve Bot API URL'si token'ı içeriyor.

## Polling mi, webhook mu?

`run_polling` dışarıya port açmadan çalışıyor, ev sunucusunda ya da küçük bir VPS'te benim tercihim bu. Webhook için HTTPS ile erişilebilir bir adres gerekiyor. python-telegram-bot bunu `app.run_webhook(listen="0.0.0.0", port=8443, url_path=..., webhook_url=..., secret_token=...)` ile destekliyor. Webhook'u, bot zaten bir web servisinin arkasında çalışıyorsa ya da çok sayıda kullanıcıya hizmet veriyorsa tercih ediyorum. Tek kişilik bir alarm botu için gerek yok.

Botu ayakta tutmak için systemd servisi ya da Docker container'ı kullanıyorum. SIGTERM geldiğinde `run_polling` düzgün kapanıyor, `post_shutdown` da çalışıyor. Bu konuyu [graceful shutdown yazımda](/posts/zarif-kapanis-sinyal-isleme/) ayrıca anlattım.

## Emir gönderme konusu

Bu botu bilerek alarm botu olarak bıraktım. Telegram'dan `/buy` komutuyla emir göndermek kulağa pratik geliyor ama riskleri büyük: yanlış yazılmış bir miktar, ele geçirilmiş bir Telegram hesabı ya da çift işlenen bir mesaj doğrudan para kaybı demek.

Gerçekten emir göndermem gerektiğinde iki şeye dikkat ediyorum:

- Borsa API anahtarında para çekme yetkisi kapalı oluyor ve anahtar sadece sunucunun IP adresine kısıtlanıyor.
- Strateji ve emir yönetimini sıfırdan yazmak yerine Freqtrade gibi test edilmiş bir bot kullanıyorum. Freqtrade'in kendi Telegram entegrasyonu `/status`, `/profit` ve `/forceexit` gibi komutlarla geliyor. Aynı REST API'yi yapay zekâ ajanlarına açmak için [freqtrade-mcp sunucusunu](/posts/freqtrade-mcp-server-tr/) yazdım.

Telegram üzerinden piyasa verisi okumanın bir başka örneği de [Telegram Wallet P2P SDK'm](https://furkankoykiran.com.tr/projeler/telegram-wallet-p2p-sdk/): P2P ilanlarını, en iyi fiyatı ve spread'i okuyan, tamamen salt okunur bir istemci. Geliştirme sürecini [Telegram Wallet P2P SDK ve MCP sunucusu yazımda](/posts/telegram-wallet-p2p-mcp-sdk-gelistirme-hikayesi/) anlattım.

Son bir not: alarm kurmak tek başına bir strateji sayılmaz ve burada anlatılanların hiçbiri yatırım tavsiyesi değil. Kripto piyasaları çok oynak ve otomatik bir sistem, kötü bir kararı sadece daha hızlı uygular.

## Sırada ne var?

Bu botu kendi makinenizde test kullanıcı ID'nizle çalıştırın ve önce birkaç alarm kurun. Sonraki adım olarak alarmların yanına basit bir indikatör (örneğin RSI eşiği) eklemek ya da bildirimleri bir backtest sonucuna bağlamak mantıklı. Botun parçası olacağı daha büyük sistemi [Python ile otomatik trading sistemi yazımda](/posts/building-automated-trading-systems-python/) anlattım. python-telegram-bot'un güncel belgeleri için [resmi dokümantasyona](https://docs.python-telegram-bot.org/) bakabilirsiniz.
