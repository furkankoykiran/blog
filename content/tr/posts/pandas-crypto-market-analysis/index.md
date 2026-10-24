---
title: "Pandas ile Kripto Veri Analizi: Vektörizasyon ve Polars"
description: "Kripto OHLCV verisinde pandas 3 ile döngüsüz analiz: iterrows ve apply maliyeti, rolling sinyaller, veri tipleri, Parquet ve ölçülmüş Polars kıyası."
date: 2024-12-03T14:00:00+03:00
slug: "python-pandas-kripto-para-piyasa-analizi"
postType: guide
tags: ["python", "performance", "trading"]
categories: ["Python"]
coverAlt: "Kripto para mum verisi üzerinde pandas ve Polars ile performans karşılaştırması"
draft: false
---

Trading botlarımın neredeyse hepsi bir noktada aynı işi yapıyor: borsadan OHLCV mumlarını çekip pandas ile indikatör ve sinyal hesaplıyor. Pandas ile kripto veri analizi yaparken yavaşlığın sebebi çoğu zaman Python'un kendisi olmuyor. Sebep, DataFrame'in içine gizlenmiş bir Python döngüsü oluyor.

Bu yazıda 500 bin dakikalık sentetik bir BTC/USDT serisi üzerinden gidiyorum. Kodun tamamı pandas 3.1 ve Polars 2.0 ile çalışıyor, kendi makinenizde ölçebilmeniz için veri üretimini de ekledim.

## Test verisi

Gerçek veriyi borsadan CCXT ile çekebilirsiniz ama ölçüm için tekrarlanabilir bir seri daha kullanışlı. Aşağıdaki fonksiyon dakikalık, rastgele yürüyüşlü bir fiyat serisi üretiyor:

```python {title="data.py"}
import numpy as np
import pandas as pd


def make_ohlcv(n: int = 500_000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    close = 60_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.0005, n)) * close
    return pd.DataFrame({
        "timestamp": ts,
        "open": open_,
        "high": np.maximum(open_, close) + spread,
        "low": np.minimum(open_, close) - spread,
        "close": close,
        "volume": rng.gamma(2.0, 0.8, n),  # kripto hacmi kesirlidir
    })


df = make_ohlcv()
```

Gerçek veri istiyorsanız `exchange.fetch_ohlcv("BTC/USDT", "1m")` çıktısını aynı kolon adlarıyla bir DataFrame'e çevirip `pd.to_datetime(df["timestamp"], unit="ms", utc=True)` demeniz yeterli.

## iterrows, apply ve vektörizasyon

Her mum için kapanış ile açılış arasındaki farkı hesaplayalım. Üç yol var:

```python
# 1) iterrows: her satır için bir Series nesnesi oluşturur
diffs = [row["close"] - row["open"] for _, row in df.iterrows()]

# 2) apply(axis=1): yine satır satır Python fonksiyonu çağırır
df["diff_apply"] = df.apply(lambda r: r["close"] - r["open"], axis=1)

# 3) vektörel: tek bir NumPy işlemi
df["diff"] = df["close"] - df["open"]
```

Üç yolu 500 bin satırda `time.perf_counter()` ile ölçtüm. `iterrows` 65 saniyeyi geçti, `apply` 11 saniye sürdü, vektörel çıkarma ise 14 milisaniyede bitti:

![Ölçüm çıktısı: iterrows 65,6 saniye, apply 11,0 saniye, vektörel çıkarma 0,014 saniye](img/iterrows-apply-vector-timing.webp "500 bin satırda aynı hesap, üç yol")

Kesin rakamlar makineye göre değişir ama aradaki üç büyüklük mertebesi değişmez. `apply` sırf "pandas fonksiyonu" olduğu için hızlı sanılıyor; `axis=1` ile kullanıldığında içi gizli bir `for` döngüsü.

Kodda `iterrows` ya da `apply(axis=1)` gördüğümde ilk sorum şu oluyor: bu işlem kolonlar üzerinde aritmetikle, `np.where` ile ya da `shift` ile yazılabilir mi? Çoğu zaman yazılabiliyor.

## Rolling pencereler ve sinyal üretimi

Hareketli ortalama kesişimi trading örneklerinin "merhaba dünya"sı. Döngüyle yazmaya gerek yok:

```python
df = df.set_index("timestamp")

df["ma_fast"] = df["close"].rolling(50).mean()
df["ma_slow"] = df["close"].rolling(200).mean()

above = df["ma_fast"] > df["ma_slow"]
# Sadece kesişim anı: bu mumda üstte, bir önceki mumda değil
df["golden_cross"] = above & ~above.shift(1, fill_value=False)

df["ret"] = df["close"].pct_change()
```

Sinyali `np.where(ma_fast > ma_slow, "BUY", "SELL")` ile kurmak sık görülen bir yöntem. Bu, her mumda bir "durum" verir ama kesişim anını vermez. Bot her mumda yeniden al emri göndermesin diye `shift` ile önceki mumu karşılaştırmak gerekiyor.

Burada önemli bir tuzak var: `rolling(50).mean()` o mumun kapanışını da içeriyor. Sinyali mum kapandıktan sonra kullanırsanız sorun yok. Aynı mumun kapanış fiyatından işleme girdiğinizi varsayan bir backtest ise geleceği görmüş olur (look-ahead bias). Backtest yazarken sinyali bir mum kaydırmak (`signal.shift(1)`) en basit korunma yolu.

Dakikalık veriyi saatliğe çevirmek de tek satır:

```python
hourly = df.resample("1h").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
)
```

pandas 2.2'den beri frekans takma adları değişti: saat için `"H"` yerine `"h"`, dakika için `"T"` yerine `"min"` kullanılıyor. Eski kodlarda bu uyarıyı sık görüyorum.

## pandas 3 ile gelen değişiklikler

[pandas 3.0 sürüm notlarına](https://pandas.pydata.org/docs/whatsnew/v3.0.0.html) göre Copy-on-Write artık varsayılan. Pratikteki etkisi şu: bir DataFrame'den filtreyle aldığınız parça artık bağımsız bir nesne gibi davranıyor.

```python
sub = df[df["volume"] > 3]
sub["flag"] = True        # sadece sub değişir, df etkilenmez
```

pandas 2'de bu satır meşhur `SettingWithCopyWarning` uyarısını verirdi. Artık uyarı yok, davranış net. Bunun bir bedeli de var: `df["close"][0] = 1` gibi zincirleme atamalar artık orijinal DataFrame'i hiçbir zaman değiştirmiyor. Değiştirmek istiyorsanız `df.loc[...]` kullanın.

Diğer büyük değişiklik metin kolonları. pyarrow kuruluysa string kolonlar artık `object` yerine `str` tipiyle geliyor ve bellekte çok daha az yer tutuyor. Sembol adları gibi az sayıda tekrar eden değerler için `category` hâlâ en küçük seçenek.

## Veri tipleri ve bellek

Hacmi `int32`'ye çevirmek sık önerilen bir bellek optimizasyonu, ama kripto için yanlış: BTC hacmi kesirli bir sayı, tamsayıya çevirdiğinizde veriyi kesersiniz. Fiyatları `float32`'ye düşürmek de riskli; float32 yaklaşık 7 anlamlı basamak tutuyor ve 60.000 dolarlık bir fiyatta kuruş seviyesindeki farklar kaybolabiliyor.

Benim kuralım şu:

- Fiyat ve hacim `float64` kalır. Emir gönderirken zaten borsanın hassasiyetine göre yuvarlıyorum.
- Sembol, borsa adı, sinyal etiketi gibi tekrar eden metinler `category` olur.
- Zaman damgası her zaman UTC ve timezone bilgili tutulur.

```python
df["symbol"] = "BTC/USDT"
print(df.memory_usage(deep=True).sum() / 1e6, "MB")
df["symbol"] = df["symbol"].astype("category")
print(df.memory_usage(deep=True).sum() / 1e6, "MB")
```

Bellek gerçekten sorun olduğunda tipleri küçültmek yerine ihtiyacınız olan kolonları okumak çok daha fazla kazandırıyor. Bu da bizi dosya formatına getiriyor.

## CSV yerine Parquet

CSV metin tabanlı, tip bilgisi tutmuyor ve her okumada baştan ayrıştırılıyor. Zaman damgası kolonunu her seferinde yeniden `to_datetime` ile çevirmeniz gerekiyor. Parquet ise sütun bazlı, sıkıştırılmış ve tipleri saklıyor:

```python
df.reset_index().to_parquet("ohlcv.parquet")

# Sadece ihtiyacınız olan kolonları okuyun
closes = pd.read_parquet("ohlcv.parquet", columns=["timestamp", "close"])
print(closes.dtypes)  # timestamp UTC olarak geri gelir
```

`columns=` parametresi diskten sadece o kolonları çekiyor. Aylarca biriken mum verisini Parquet'te tutup analizde sadece kapanışı okumak benim botlarımda en büyük farkı yaratan değişiklik oldu.

## Method chaining

Ara değişkenlerle dolu kod yerine işlemleri zincirlemek okunabilirliği artırıyor:

```python
result = (
    df.reset_index()
    .dropna()
    .query("volume > 1")
    .assign(range_pct=lambda x: (x["high"] - x["low"]) / x["close"])
    .sort_values("range_pct", ascending=False)
    .head(5)
)
```

Bir adımı debug etmek istediğimde araya `.pipe(lambda d: print(d.shape) or d)` ekliyorum. Copy-on-Write sayesinde bu tarz zincirler pandas 3'te gereksiz kopyalar da üretmiyor.

## Önce ölçün: profiling

Optimizasyondan önce darboğazın nerede olduğunu bilmek gerekiyor. Jupyter'da tek satır için `%timeit`, bütün script için `cProfile` kullanıyorum:

```bash
python -m cProfile -o profile.out analysis.py
snakeviz profile.out
```

SnakeViz sonucu tarayıcıda iç içe halkalar olarak çiziyor. Terminalde aynı rakamları `pstats` ile görebilirsiniz:

```python
import pstats

stats = pstats.Stats("profile.out")
stats.sort_stats("cumulative").print_stats(10)  # en pahalı 10 çağrı yolu
# ncalls  : fonksiyonun kaç kez çağrıldığı
# tottime : fonksiyonun kendi içinde geçen süre
# cumtime : çağırdığı her şey dahil süre (SnakeViz'deki halka genişliği)
```

Yavaşlık her zaman hesaplamada olmuyor; dosya okuma ya da tarih ayrıştırma da sürenin büyük kısmını yiyebiliyor. Tahmin etmek yerine ölçmek bu yüzden önemli. Profiling konusunu [Python performans profiling yazımda](/posts/python-performans-profiling-optimizasyon/) daha ayrıntılı anlattım.

## Polars ne zaman mantıklı?

[Polars](https://docs.pola.rs/) Rust ile yazılmış, çok çekirdekli çalışan ve lazy (tembel) değerlendirme yapan bir DataFrame kütüphanesi. Aynı iş Polars'ta şöyle görünüyor:

```python
import polars as pl

hourly = (
    pl.scan_parquet("ohlcv.parquet")
    .sort("timestamp")
    .group_by_dynamic("timestamp", every="1h")
    .agg(
        pl.col("open").first(),
        pl.col("high").max(),
        pl.col("low").min(),
        pl.col("close").last(),
        pl.col("volume").sum(),
    )
    .with_columns(pl.col("close").rolling_mean(window_size=24).alias("ma_24h"))
    .collect()
)
```

`scan_parquet` dosyayı hemen okumuyor, sorgu planı kuruyor. `collect()` çağrıldığında Polars hangi kolonların gerektiğini kendisi çıkarıp sadece onları okuyor.

Bu boyutta (500 bin satır) Polars hiç öne geçmedi. Aynı okuma, saatlik toplama ve 24 saatlik ortalama işini pandas tarafında `read_parquet`, `resample` ve `rolling(24)` ile yazıp ikisini de beşer kez çalıştırdım. En iyi süreler pandas'ta 0,062, Polars'ta 0,087 saniye:

![Ölçüm çıktısı: 500 bin satırlık saatlik toplamada pandas 0,062 saniye, Polars 0,087 saniye, beş denemenin en iyisi](img/pandas-polars-hourly-timing.webp "Küçük veride pandas ile Polars arasında anlamlı fark yok")

Polars'ın gerçek avantajı veri birkaç milyon satırı geçtiğinde, çok sembollü group-by işlemlerinde ve RAM'e sığmayan dosyalarda ortaya çıkıyor.

Benim tercihim şöyle: botun içinde, birkaç bin mumluk pencerelerle çalışan kod pandas ile kalıyor çünkü CCXT, Freqtrade ve backtest kütüphanelerinin hepsi pandas konuşuyor. Aylarca biriken ham veriyi temizleyip özetleyen batch işleri için Polars kullanıyorum. İkisi arasında `df.to_pandas()` ve `pl.from_pandas(df)` ile geçiş yapmak mümkün.

## Sırada ne var?

Kendi kodunuzda şu üç şeyi arayın: `iterrows` ve `apply(axis=1)`, CSV ile saklanan geçmiş veri, `float32` ya da `int` tipine çevrilmiş fiyat ve hacim kolonları. Bunları temizlemek çoğu zaman yeni bir kütüphaneye geçmekten daha çok kazandırıyor.

Bu verinin bir bota nasıl bağlandığını [Python ile otomatik trading sistemi yazımda](/posts/building-automated-trading-systems-python/), sinyallerin makine öğrenmesiyle nasıl test edildiğini de [yapay zekâ destekli trading stratejileri yazımda](/posts/yapay-zeka-destekli-trading-stratejileri/) anlattım. Buradaki örnekler eğitim amaçlı; hiçbiri yatırım tavsiyesi değil.
