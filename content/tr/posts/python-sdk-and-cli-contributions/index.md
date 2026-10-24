---
title: "SDK ve CLI Katkılarım: Jules, NotebookLM, Mission Control"
description: "Jules SDK, notebooklm-py, cli-agent ve Mission Control'e açtığım dört PR: ikisi merge edildi, ikisi kapandı. Kod örnekleri ve neden öyle bittikleri."
date: 2026-03-11T18:00:00+03:00
slug: "python-sdk-cli"
postType: contribution
tags: ["python", "typescript", "open-source", "tooling", "ai-agents", "asyncio"]
categories: ["Açık Kaynak"]
series: ["Açık Kaynak Katkılarım"]
coverAlt: "Bir terminal penceresinde SDK çağrıları ve CLI komutlarını gösteren çizim"
draft: false
---

Mart 2026'nın ilk iki haftasında yapay zekâ araçlarının SDK ve CLI katmanlarına dört PR açtım: ikisi Python, ikisi TypeScript projesine. İkisi merge edildi. Diğer ikisi kapandı: biri bakımcının istemediği bir özellikti, diğerindeki fikir bakımcının kendi PR'ıyla projeye girdi. Bu yazıda her PR'ın kodunu, verdiğim tasarım kararlarını ve neden o şekilde bittiğini anlatıyorum.

## Dört PR ve sonuçları

Durumlar ve tarihler GitHub'daki PR kayıtlarından.

| Proje | PR | Dil | Konu | Durum |
| :--- | :--- | :--- | :--- | :--- |
| google-labs-code/jules-sdk | [#184](https://github.com/google-labs-code/jules-sdk/pull/184) | TypeScript | `session.delete()` | Merge edildi, 16 Mart 2026 |
| teng-lin/notebooklm-py | [#174](https://github.com/teng-lin/notebooklm-py/pull/174) | Python | Notebook metadata dışa aktarma | Merge edildi, 11 Mart 2026 |
| amranu/cli-agent | [#7](https://github.com/amranu/cli-agent/pull/7) | Python | `/health` komutu | Kapandı |
| builderz-labs/mission-control | [#271](https://github.com/builderz-labs/mission-control/pull/271) | TypeScript | Gateway'siz çalışma modu | Kapandı, #291'e dahil edildi |

## Jules SDK: oturum silme

Jules, Google Labs'in asenkron kodlama ajanı. jules-sdk de onun TypeScript istemcisi. API'de oturum silmek için bir endpoint vardı ama SDK'da karşılığı yoktu. Bu eksik [#105](https://github.com/google-labs-code/jules-sdk/issues/105) numaralı issue'da duruyordu. PR'ım `SessionClient`'a bir `delete()` metodu ekledi:

```ts
async delete(): Promise<void> {
  try {
    await this.request(`sessions/${this.id}`, { method: 'DELETE' });
  } catch (error: any) {
    // 404: oturum zaten silinmiş, yerel cache'i yine de temizle
    if (error.status !== 404) {
      throw error;
    }
  }
  await this.sessionStorage.delete(this.id);
}
```

Burada iki karar var. API 404 dönerse oturum zaten yok demektir, yerel cache'i temizleyip devam ediyorum. Başka bir hata gelirse cache'e hiç dokunmadan hatayı fırlatıyorum. Böylece API ile cache birbirinden kopmuyor. İstemcideki `method` tipinde sadece `'GET' | 'POST'` vardı, oraya `'DELETE'` eklemem gerekti. Üç test yazdım: başarılı silme, 404 ve 403. Kullanımı tek satır: `await session.delete();`. PR'ı davideast 16 Mart'ta merge etti:

![jules-sdk PR #184'ün başlığı: davideast tarafından merge edildi, dört commit, +134 −1](img/jules-sdk-pr-184-header.webp "Jules SDK PR #184. Kaynak: GitHub")

## notebooklm-py: notebook metadata dışa aktarma

notebooklm-py, Google NotebookLM için resmî olmayan bir Python API'si ve CLI. Google ürünün adını sonradan Gemini Notebook olarak değiştirdi, kütüphane ise adını korudu. [#171](https://github.com/teng-lin/notebooklm-py/issues/171) numaralı issue'da bir kullanıcı notebook bilgilerini ve kaynak listesini tek çağrıyla almak istiyordu. PR'ım iki şey ekledi:

- `client.notebooks.get_metadata(notebook_id)`: notebook detayını ve kaynak listesini `asyncio.gather` ile paralel çekiyor ve bir `NotebookMetadata` döndürüyor. Kaynaklar `SourceSummary(kind, title, url)` olarak sadeleşiyor. `to_dict()` her kaynakta aynı anahtarları veriyor, eksik alanlar `null` oluyor.
- CLI tarafında `notebooklm metadata` komutu ve `--json` seçeneği.

Bir de küçük bir güvenlik ağı var. Notebook kaynak sayısını sıfırdan büyük gösterip liste boş dönerse kod bir uyarı logluyor, böylece olası bir veri kaybı sessiz kalmıyor.

Kullanmak için önce `notebooklm login` ile oturum açmak gerekiyor. Sonrası şöyle:

```python
import asyncio
import json

from notebooklm import NotebookLMClient

async def main() -> None:
    async with NotebookLMClient.from_storage() as client:
        notebooks = await client.notebooks.list()
        meta = await client.notebooks.get_metadata(notebooks[0].id)
        print(f"{meta.title}: {len(meta.sources)} kaynak")
        print(json.dumps(meta.to_dict(), indent=2, ensure_ascii=False, default=str))

asyncio.run(main())
```

Terminalden aynı şeyi `notebooklm metadata --json` ile alabilirsiniz. PR 11 Mart'ta merge edildi. Kütüphane o günden beri çok büyüdü ve metot artık ayrı bir servis katmanına delege ediyor. Dışarıdan görünen API aynı kaldı.

## cli-agent: /health komutu (kapandı)

cli-agent, birden fazla LLM sağlayıcısıyla çalışan bir terminal ajanı. Hangi API anahtarının çalıştığını tek bakışta görmek için bir `/health` komutu önerdim. Komut altı sağlayıcıyı (Anthropic, OpenAI, DeepSeek, Google, OpenRouter, Ollama) `asyncio.gather` ile paralel kontrol edip sonucu ✓, ✗ ve ○ işaretleriyle özetliyordu. Diff testler ve README ile birlikte 850 satırı geçti.

Bakımcının cevabı kısaydı: buna gerek yok, işine yarıyorsa fork'la, lisans zaten serbest.

Hatanın bir kısmı bendeydi. [#6](https://github.com/amranu/cli-agent/issues/6) numaralı issue'yu PR'dan 16 saniye önce açmışım, yani fikri sormak yerine haber vermişim. Yeni bir özellik için önce issue açıp bakımcının onayını beklemek gerekiyor, hele diff bu kadar büyükse.

Fikrin kendisi yine de kullanışlı. Üç sağlayıcıyla sade bir hâli şöyle:

```python
import asyncio
import os

import httpx

def targets() -> dict[str, tuple[str, dict[str, str]] | None]:
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    return {
        "openai": (
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {openai_key}"},
        ) if openai_key else None,
        "anthropic": (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"},
        ) if anthropic_key else None,
        "ollama": ("http://localhost:11434/api/tags", {}),
    }

async def check(client: httpx.AsyncClient, name: str, target) -> str:
    if target is None:
        return f"○ {name}: yapılandırılmamış"
    url, headers = target
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return f"✓ {name}: {response.elapsed.total_seconds() * 1000:.0f} ms"
    except httpx.HTTPError as exc:
        return f"✗ {name}: {exc}"

async def main() -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        results = await asyncio.gather(
            *(check(client, name, target) for name, target in targets().items())
        )
    print("\n".join(results))

asyncio.run(main())
```

`asyncio.gather` sayesinde en yavaş sağlayıcı kadar bekliyorsunuz, toplam süre kadar değil. `httpx.HTTPError` hem bağlantı hatalarını hem 4xx/5xx yanıtlarını yakalıyor.

## Mission Control: gateway olmadan çalışmak

Mission Control, AI ajanlarını yönetmek için self-hosted bir kontrol paneli (Next.js). [#270](https://github.com/builderz-labs/mission-control/issues/270) numaralı issue'da bir kullanıcı, paneli VPS'e kurduğunda gateway'in hep "offline" göründüğünü bildirdi. Sebep şuydu: tarayıcı gateway'e 18789 ve 18790 gibi standart dışı portlardan WebSocket ile bağlanmaya çalışıyor, çoğu VPS güvenlik duvarı da bu portları kapalı tutuyor.

PR'ım iki şey getirdi. İlki `NEXT_PUBLIC_GATEWAY_OPTIONAL=true` ile açılan bir "standalone" moddu. Bu modda HUD kırmızı "Disconnected" yerine mavi bir "Gateway Optional (Standalone)" rozeti gösteriyor. Görev panosu, projeler, ajan kaydı, cron ve webhook'lar gateway olmadan da çalışıyor. Canlı oturum güncellemeleri ve ajanlar arası mesajlaşma ise çalışmıyor.

İkincisi, kalıcı çözüm için dokümandı: WebSocket trafiğini reverse proxy ile 443 üzerinden geçirmek. nginx'te genel kalıp şöyle (yol ve port kendi kurulumunuza göre değişir):

```nginx
location /gateway/ {
    proxy_pass http://127.0.0.1:18789/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 1h;
}
```

`Upgrade` ve `Connection` header'ları olmadan nginx WebSocket el sıkışmasını iletmiyor. `proxy_read_timeout` da uzun süre sessiz kalan bağlantıların 60 saniyelik varsayılan süreyle kopmasını engelliyor.

Aynı gün bakımcı 0xNyk açıktaki deployment sorunlarını tek bir PR'da topladı. [#291](https://github.com/builderz-labs/mission-control/pull/291), iki Docker düzeltmesiyle birlikte gateway-optional davranışını ve dokümanını da içeriyordu. 11 Mart'ta merge edildi ve benim açtığım [#289](https://github.com/builderz-labs/mission-control/issues/289) numaralı issue'yu da kapattı. #271 "#291'de birleştirildi" notuyla kapandı:

![mission-control PR #271'in başlığı: kapandı, fix/270-gateway-optional-mode branch'inden dört commit, +173](img/mission-control-pr-271-header.webp "Mission Control PR #271. Kaynak: GitHub")

Projenin `.env.example` dosyasında `NEXT_PUBLIC_GATEWAY_OPTIONAL` satırı duruyor.

## Dört PR'dan çıkanlar

- Merge edilen iki PR da başkasının açtığı, kapsamı net bir issue'yu çözüyordu.
- Jules SDK'da yeni metot mevcut `request` yardımcısını ve test düzenini kullandı. Review'da bu yüzden konuşacak pek bir şey çıkmadı.
- Mission Control'de PR'ım kapandı, fikir ise bakımcının PR'ıyla girdi ve kullanıcının sorunu çözüldü.

## Sırada ne var

`asyncio.gather` ve async istemciler üzerine daha fazlası için [Python asyncio ile paralel işlem](/posts/python-asyncio-paralel-islem/) yazısına bakabilirsiniz. Aynı dönemdeki diğer katkılarım [MCP ekosistemi](/posts/mcp-ai-contributions/) ve [güvenlik ve API düzeltmeleri](/posts/security-api-fixes/) yazılarında. Bir SDK'ya ilk katkınızı yapmak istiyorsanız "API'de var ama SDK'da yok" türünden issue'larla başlayın. Kapsamları net ve test etmeleri kolay. Go tarafında bir örnek için [Keploy katkımı](/posts/keploy-acik-kaynak-katkim/) okuyabilirsiniz.
