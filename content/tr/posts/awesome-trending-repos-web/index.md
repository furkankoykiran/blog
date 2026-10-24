---
title: "awesome-trending-repos: GitHub Trending'i Her Gün İzlemek"
description: "GitHub Trending'i her gün kazıyan, geçmişle karşılaştıran ve React arayüzüyle GitHub Pages'te yayınlayan açık kaynak projemin nasıl çalıştığını anlatıyorum."
date: 2026-03-27T18:00:00+03:00
slug: "awesome-trending-repos-web"
postType: project
tags: ["github-actions", "automation", "ci-cd", "web", "open-source"]
categories: ["Projeler"]
aliases: ["/posts/awesome-trending-repos/"]
coverAlt: "GitHub Actions cron işinin trending verisini çekip GitHub Pages'e yayınladığı akış"
draft: false
---

[awesome-trending-repos](https://github.com/furkankoykiran/awesome-trending-repos), GitHub Trending sayfasını her gün kendiliğinden takip eden küçük bir projem. Her gece UTC 00:00'da bir GitHub Actions işi trending listesini çekiyor, bir önceki günle karşılaştırıyor, sonucu JSON olarak repoya yazıyor ve React ile yazdığım arayüzü GitHub Pages'e yeniden yayınlıyor. Mart 2026'dan beri elle dokunmadan çalışıyor. Bu yazıda projenin iki sürümünü, veri hattını ve yolda yaptığım hataları anlatıyorum.

## Neden yazdım

Trending sayfasına her gün bakıyordum ama dünkü listeyi hatırlamıyordum. Hangi repo yeni girdi, hangisi yükseliyor, hangi dil öne çıkıyor, bunları görmek için geçmişi bir yerde tutmak gerekiyordu. GitHub bunu sunmuyor, bu yüzden kendim tutmaya karar verdim.

## Birinci sürüm: README'ye yazan bir script

17 Mart 2026'daki ilk sürüm tamamen Node.js'ti. `scripts/update.js` trending sayfasını kazıyıp sonucu doğrudan `README.md` dosyasına yazıyordu: dil kırılımları, sıralama değişiklikleri, ASCII grafikler, en hızlı yükselen repolar. Son 7 günün verisi `.data/` klasöründe tutuluyordu.

Çalışıyordu ama README bir veri arayüzü olarak sınırlı. Filtre yok, arama yok, grafikler metin. On gün sonra arayüzü ayırmaya karar verdim.

## İkinci sürüm: JSON veri, React arayüz

27 Mart'ta merge ettiğim [PR #5](https://github.com/furkankoykiran/awesome-trending-repos/pull/5) projeyi ikiye böldü. Script artık README'ye dokunmuyor, yalnızca `public/data/trending.json` ve `public/data/feed.json` dosyalarını üretiyor. README statik bir tanıtım sayfasına dönüştü. Arayüz ise bu JSON'u okuyan tek sayfalık bir uygulama:

- React 19 ve Vite 6
- Tailwind CSS v4 (`@tailwindcss/vite` eklentisiyle)
- Framer Motion ile geçişler, Recharts ile dil dağılımı grafiği
- Dile göre filtreleme, isim/sahip/açıklamada anlık arama ve her güncellemenin listelendiği bir akış (feed)

Canlı sitenin üst kısmı aşağıda. Trending, Stats ve Feed sekmeleri, dil filtreleri ve arama kutusu aynı JSON'dan besleniyor:

![awesome-trending-repos canlı sitesi: Trending, Stats ve Feed sekmeleri, dil filtreleri, arama kutusu ve repo kartları](img/trending-dashboard.webp "furkankoykiran.com.tr/awesome-trending-repos üzerindeki React arayüzü")

Filtreleme kodu basit, 25 repo için başka bir şeye gerek yok:

```javascript
const q = searchQuery.toLowerCase();

const filteredRepos = repos.filter((repo) => {
  const matchesLang = filter === 'All' || repo.language === filter;
  const matchesSearch =
    repo.name.toLowerCase().includes(q) ||
    repo.owner.toLowerCase().includes(q) ||
    (repo.description ?? '').toLowerCase().includes(q);
  return matchesLang && matchesSearch;
});
```

## Veri hattı

Asıl iş `scripts/update.js` içinde. Adımları sırasıyla:

1. 15 dil için `github.com/trending/<dil>?since=daily` sayfaları axios ile çekilip cheerio ile ayrıştırılıyor. Dilsiz genel sayfa yapısı değiştiği için dil dil gitmek daha sağlam çıktı.
2. 25 repoya ulaşılamazsa GitHub Search API devreye giriyor.
3. Geçersiz kayıtlar ve popüler dil listesi dışındakiler eleniyor.
4. Hâlâ eksik varsa son 7 günün snapshot'larından repo ekleniyor.
5. Sonuç dünkü snapshot'la karşılaştırılıyor: yeni girenler, yükselenler, düşenler, günün yıldızı.
6. `.data/history.json` en fazla 7 snapshot tutuyor, eski snapshot dosyaları siliniyor.

İki sayı ölçüm değil, tahmin. Search API "bugün kaç yıldız aldı" bilgisini vermiyor, bu yüzden oradan gelen repolar için günlük yıldızı toplam yıldızın %1'i olarak tahmin ediyorum. Geçmişten eklenen repolarda da eski günlük yıldızın yarısını kullanıyorum. İkisi de listeyi doldurmak için kaba tahminler, gerçek veri değil. Arayüzde bunları ayrı işaretlemek yapılacaklar listemde.

## GitHub Actions workflow'u

Workflow günde bir kez cron ile, istendiğinde elle ve `src/` ya da `scripts/` değiştiğinde push ile çalışıyor. Önce veriyi güncelliyor, sonra build alıyor, değişiklik varsa commit atıyor ve Pages'e yayınlıyor. Repodaki halinin kısaltılmışı:

```yaml
name: Update and Deploy Trends

on:
  schedule:
    - cron: '0 0 * * *'
  workflow_dispatch:
  push:
    branches: [main]
    paths: ['src/**', 'scripts/**', 'package.json', 'package-lock.json']

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  update-and-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-node@v7
        with:
          node-version: '22'
          cache: npm
      - run: npm ci
      - run: npm run update
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - run: npm run build
      - name: Commit data changes
        run: |
          if [ -z "$(git status --porcelain .data/ public/data/)" ]; then exit 0; fi
          git config user.name 'github-actions[bot]'
          git config user.email 'github-actions[bot]@users.noreply.github.com'
          git add .data/ public/data/
          git commit -m "chore: update trending repositories [skip ci]"
          git push
      - uses: actions/upload-pages-artifact@v5
        with:
          path: ./dist

  deploy:
    needs: update-and-build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v5
```

Repodaki dosya hâlâ `checkout@v4`, `setup-node@v4`, `upload-pages-artifact@v3` ve Node 20 kullanıyor. Node 20'nin desteği Nisan 2026'da bitti. Sıfırdan kuruyorsanız yukarıdaki sürümleri kullanın.

Birkaç ayrıntı işe yarıyor:

- `[skip ci]` olmadan botun commit'i workflow'u yeniden tetikleyebilir. `GITHUB_TOKEN` ile atılan push'lar zaten yeni workflow başlatmıyor ama işareti bırakmak zararsız.
- `concurrency` aynı anda iki deploy'un çakışmasını önlüyor.
- Build, update'ten sonra çalışıyor. Vite `public/` içeriğini `dist/` içine kopyaladığı için site her zaman günün JSON'uyla yayınlanıyor.

Yukarıdaki commit adımında `public/data/` klasörünün tamamını ekliyorum. Repodaki workflow ise yalnızca `public/data/trending.json` dosyasını commit ediyor ve `feed.json` dışarıda kalıyor. Sonuç: her çalıştırma repodaki eski feed'i okuyup üstüne tek bir satır ekliyor. Canlı sitedeki akış bu yüzden hep 27 Mart kaydı ile günün kaydından oluşuyor. Düzeltmesi yukarıdaki gibi tek satır.

## Vite ve GitHub Pages: base yolu

Site bir proje sayfası olarak `/awesome-trending-repos/` altında yayınlanıyor. Vite'a bunu söylemezseniz build'deki asset yolları kökü gösterir ve production'da her şey 404 verir:

```javascript
// vite.config.js
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/awesome-trending-repos/',
});
```

Vite production build'de dosya adlarına zaten içerik hash'i ekliyor, cache kırmak için ek bir `rollupOptions` ayarı gerekmiyor. Arayüz JSON'u `fetch('./data/trending.json')` ile göreli yoldan okuyor, bu da `base` ile uyumlu.

## Özel alan adı hatası

Proje sayfasında `public/CNAME` dosyasına kök alan adını yazmak sık yapılan bir hata ve bu repoda da öyle bir dosya vardı. `furkankoykiran.com.tr` kullanıcı sitemin (`furkankoykiran.github.io`) alan adı. Proje sayfaları bu alan adını kendiliğinden devralıyor ve `furkankoykiran.com.tr/awesome-trending-repos/` adresinden yayınlanıyor. Projenin kendi CNAME dosyasında kök alan adını taşıması, kullanıcı sitesiyle aynı adı sahiplenmeye çalışması demek.

[PR #7](https://github.com/furkankoykiran/awesome-trending-repos/pull/7) ile `public/CNAME` ve `dist/CNAME` dosyalarını sildim. `actions/deploy-pages` kullanıyorsanız artifact içindeki CNAME dosyası zaten yok sayılıyor. Alan adını repo ayarlarındaki Settings → Pages → Custom domain alanı belirliyor. Proje sayfası için bu alan boş kalmalı.

## Sonraki adımlar

Proje her gün çalışmaya devam ediyor. Sıradaki işler: feed hatasını kapatmak, tahmini yıldız sayılarını arayüzde işaretlemek ve workflow sürümlerini güncellemek.

Canlı siteye [furkankoykiran.com.tr/awesome-trending-repos](https://furkankoykiran.com.tr/awesome-trending-repos/) adresinden, projenin özetine [proje sayfasından](https://furkankoykiran.com.tr/projeler/awesome-trending-repos/) bakabilirsiniz. GitHub Actions dakikalarınız bitiyorsa [self-hosted runner yazım](/posts/github-actions-self-hosted-runner-tr/), daha büyük pipeline'lar için [GitHub Actions CI/CD yazısı](/posts/github-actions-cicd-pipeline/) iyi bir devam olur. Bu blogun GitHub Pages yolculuğunu da [GitLab ve GitHub macerası](/posts/gitlab-github-macerasi-jekyll-altyapisi/) yazısında anlattım.
