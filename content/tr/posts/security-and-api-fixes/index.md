---
title: "Açık Kaynakta Güvenlik ve API Düzeltmeleri: 3 PR"
description: "LightRAG'de Cypher injection, OpenAI Python SDK'da yanlış enum değeri, Coinbase AgentKit'te sabit 18 decimals: üç PR, gerçek durumları ve çıkan dersler."
date: 2026-03-11T17:00:00+03:00
slug: "security-api-fixes"
postType: contribution
tags: ["security", "open-source", "python", "api", "databases", "ethereum"]
categories: ["Açık Kaynak"]
series: ["Açık Kaynak Katkılarım"]
coverAlt: "Bir graph veritabanı sorgusuna sızan güvenilmeyen girdiyi ve onu durduran kaçış katmanını gösteren şema"
draft: false
---

Şubat 2026'nın son haftasında üç projede güvenlik ve API sözleşmesiyle ilgili hatalara PR açtım: LightRAG'de bir Cypher injection açığı, OpenAI Python SDK'da yanlış yazılmış bir enum değeri ve Coinbase AgentKit'te 18'e sabitlenmiş token decimals. Bu yazıda her birinde ne yaptığımı ve sonunda ne olduğunu anlatıyorum. Üçü de farklı bitti: birinin kodu bakımcının PR'ıyla main'e girdi, biri gereksiz çıktı, biri review bekliyor.

## Üç PR ve durumları

Durumlar ve tarihler GitHub'daki PR kayıtlarından.

| Proje | PR | Konu | Durum |
| :--- | :--- | :--- | :--- |
| HKUDS/LightRAG | [#2713](https://github.com/HKUDS/LightRAG/pull/2713) | Workspace label'ı ile Cypher injection | Kapandı. Commit'lerim [#2792](https://github.com/HKUDS/LightRAG/pull/2792) ile 18 Mart 2026'da merge edildi |
| openai/openai-python | [#2893](https://github.com/openai/openai-python/pull/2893) | `prompt_cache_retention` değeri | Kapandı, düzeltme ayrı bir commit'le zaten gelmişti |
| coinbase/agentkit | [#965](https://github.com/coinbase/agentkit/pull/965) | Morpho withdraw'da sabit 18 decimals | Açık |

## LightRAG: workspace adıyla Cypher injection

### Açık nerede

LightRAG graph verisini Neo4j ya da Memgraph'ta tutabiliyor ve her workspace'i ayrı bir node label'ıyla ayırıyor. API sunucusunda workspace adı `LIGHTRAG-WORKSPACE` HTTP header'ından okunabiliyor. `Neo4JStorage` ve `MemgraphStorage` sınıflarındaki `_get_workspace_label()` bu değeri hiçbir işlemden geçirmeden döndürüyordu. Değer de sorgulara f-string ile giriyordu. Örüntü şuna benziyor:

```python
query = f"MATCH (n:`{workspace_label}` {{entity_id: $entity_id}}) RETURN n"
```

`$entity_id` gibi değerler parametreyle geçiyor, bu kısım doğru. Label ise sorgu metnine gömülüyor. Header'a ters tırnak (`` ` ``) içeren bir değer yazan biri label'ın dışına çıkıp sorguya kendi Cypher'ını ekleyebiliyordu. Açığı [#2698](https://github.com/HKUDS/LightRAG/issues/2698) numaralı issue'da lighthousekeeper1212 bildirmişti, düzeltmesini ben üstlendim.

### İlk commit ve çakışma sorunu

PostgreSQL tarafı zaten `re.sub(r"[^a-zA-Z0-9_]", "_", ...)` ile temizlik yapıyordu. İlk commit'imde aynısını Neo4j ve Memgraph'a uyguladım ve 20 test yazdım. Sonra bunun bir yan etkisi ortaya çıktı: `team-a` ile `team_a` aynı label'a düşüyor. İki farklı workspace'in verisi böylece birbirine karışabilir.

İkinci commit'te karakter silmeyi bıraktım ve ters tırnakları ikiye katlayarak kaçırdım. Böylece her workspace adı tek bir label'a karşılık geliyor:

```python
def _get_workspace_label(self) -> str:
    workspace = self.workspace.strip()
    if not workspace:
        return "base"
    return workspace.replace("`", "``")
```

### Review: kaçış bağlama göre değişir

Bakımcı danielaskdd review'da üç sorun buldu. Üçü de kaçırılmış değeri her yerde aynı şekilde kullanmaktan çıkıyordu.

1. Sürücüden dönen label'lar ham hâliyle geliyor (`foo`bar`). Kod bunları kaçırılmış değerle (`foo``bar`) karşılaştırıyordu, eşitlik hiç tutmuyordu.
2. Memgraph'ta index oluşturan sorgu label'ı ters tırnak içine almadan kullanıyordu. Kaçırılmış değer orada geçersiz Cypher üretiyordu.
3. Bir sorguda label `WHERE '{label}' IN labels(n)` şeklinde string literal olarak geçiyordu. String içinde çift ters tırnağın kaçış anlamı yok, bu yüzden sorgu hiç eşleşmiyordu.

Ters tırnak kaçışı yalnızca ters tırnak içindeki bir identifier için geçerli. String bağlamında ham değer parametre olarak bağlanmalı. Resmî `neo4j` Python sürücüsüyle ikisi yan yana:

```python
from neo4j import GraphDatabase

def identifier_label(workspace: str) -> str:
    """Ters tırnak içinde kullanılacak label."""
    return (workspace.strip() or "base").replace("`", "``")

def raw_label(workspace: str) -> str:
    """Parametre olarak bağlanacak ham label."""
    return workspace.strip() or "base"

def find_entity(driver, workspace: str, entity_id: str):
    label = identifier_label(workspace)
    # Identifier bağlamı: kaçırılmış değer ters tırnak içinde
    by_label = f"MATCH (n:`{label}` {{entity_id: $entity_id}}) RETURN n"
    # String bağlamı: ham değer parametre olarak, asla f-string ile değil
    by_filter = (
        "MATCH (n) WHERE $label IN labels(n) "
        "AND n.entity_id = $entity_id RETURN n"
    )
    with driver.session() as session:
        first = session.run(by_label, entity_id=entity_id).single()
        second = session.run(
            by_filter, label=raw_label(workspace), entity_id=entity_id
        ).single()
    return first, second

if __name__ == "__main__":
    with GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password")) as driver:
        print(find_entity(driver, "team-a", "Alan Turing"))
```

### Sonuç: girişte de doğrulama

danielaskdd benim branch'imi alıp üzerine bir commit ekledi. Workspace değeri artık hem `--workspace` / `WORKSPACE` ayarında hem de `LIGHTRAG-WORKSPACE` header'ında temizleniyor: `[a-zA-Z0-9_]` dışındaki karakterler `_` oluyor ve değer değiştiyse bir uyarı loglanıyor. Bu birleşik değişiklik [#2792](https://github.com/HKUDS/LightRAG/pull/2792) olarak 18 Mart 2026'da merge edildi:

![LightRAG PR #2792'nin başlığı: danielaskdd tarafından merge edildi, yedi commit, +305 −72](img/lightrag-pr-2792-header.webp "LightRAG PR #2792. Kaynak: GitHub")

Benim #2713'üm bu yüzden "closed" görünüyor, commit'lerim ise main'de. `_get_workspace_label()` yazdığım kaçış mantığını kullanıyor. String bağlamları için ham değeri parametre olarak bağlayan ayrı bir yardımcı var. Sonuçta iki katman birlikte çalışıyor.

Tek bir savunma katmanına güvenmeyin. Girişi allowlist ile doğrulayın, sorguda da bağlama uygun kaçış ya da parametre kullanın.

## OpenAI Python SDK: `in-memory` mi, `in_memory` mi?

[#2883](https://github.com/openai/openai-python/issues/2883) numaralı issue şunu söylüyordu: SDK `prompt_cache_retention` parametresinin tipini `Literal["in-memory", "24h"]` olarak tanımlıyor, API ise `"in-memory"` değerini 400 hatasıyla reddedip yalnızca `"in_memory"` kabul ediyor. Tip denetleyicisi yanlış değeri öneriyor, doğru değeri hata olarak işaretliyordu. Hata parametrenin adında değil, değerin yazımındaydı.

PR'ım literal'i dokuz dosyada düzeltti (+41/−41, Chat Completions ve Responses tipleri ve testleri). Altı ay sonra bir bakımcı PR'ı kapattı: aynı düzeltme SDK'nın kod üretim sürecinden gelen bir commit'le zaten main'e girmişti. openai-python elle yazılmıyor, OpenAI'ın OpenAPI tanımından Stainless ile üretiliyor. Kalıcı düzeltmenin yeri o tanım.

Üretilmiş bir SDK'ya PR açmadan önce README'ye ve CONTRIBUTING dosyasına bakmak gerekiyor. Böyle projelerde iyi yazılmış bir issue çoğu zaman PR'dan daha işe yarar. Doğru kullanım şöyle:

```python
from openai import OpenAI

client = OpenAI()

response = client.responses.create(
    model="gpt-5.1",
    input="Şu metni üç maddede özetle: ...",
    prompt_cache_retention="24h",  # ya da "in_memory"
)
print(response.output_text)
```

## Coinbase AgentKit: 18'e sabitlenmiş decimals

AgentKit, AI ajanlarının zincir üstünde işlem yapmasını sağlayan bir araç seti. Morpho vault'larından çekim yapan `withdraw` aksiyonu miktarı `Web3.to_wei(assets, "ether")` ile çeviriyordu. `"ether"` birimi her zaman 18 basamak demek. USDC ise 6 decimals kullanıyor. 100 USDC çekmek isteyen bir ajan 100 × 10¹⁸ birim istemiş oluyordu, yani olması gerekenin 10¹² katı. Hata [#946](https://github.com/coinbase/agentkit/issues/946) numaralı issue'da bildirilmişti.

Aynı provider'daki `deposit` aksiyonu bunu zaten doğru yapıyordu: token kontratından `decimals()` okuyordu. PR'ım `withdraw` aksiyonunu aynı yola soktu. Şemaya `token_address` alanını ekledim, decimals değerini `wallet_provider.read_contract(...)` ile okuyup miktarı ona göre çevirdim ve 6 decimals'lı bir token için test yazdım.

Dönüşümün kendisi, AgentKit'ten bağımsız ve çalıştırılabilir hâliyle:

```python
from decimal import Decimal

def to_atomic(amount: str, decimals: int) -> int:
    value = Decimal(amount)
    if value <= 0:
        raise ValueError("amount must be positive")
    scaled = value.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{amount} has more than {decimals} decimal places")
    return int(scaled)

assert to_atomic("100", 6) == 100_000_000                 # USDC
assert to_atomic("0.5", 18) == 500_000_000_000_000_000    # WETH
```

Miktarı `float` ile değil, string'den kurulan `Decimal` ile tutuyorum. Böylece `0.1` gibi değerlerde yuvarlama hatası olmuyor. Decimals değerini web3.py ile okumak da kısa:

```python
from web3 import Web3

ERC20_DECIMALS_ABI = [{
    "name": "decimals", "type": "function", "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "uint8"}],
}]

w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
usdc = w3.eth.contract(
    address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # Base üzerindeki USDC
    abi=ERC20_DECIMALS_ABI,
)
print(usdc.functions.decimals().call())  # 6
```

PR açık ve review bekliyor:

![coinbase/agentkit PR #965'in başlığı: açık, fix/morpho-withdraw-decimals branch'inden bir commit, +92 −29](img/agentkit-pr-965-header.webp "AgentKit PR #965. Kaynak: GitHub")

PR merge edilmeden main'deki `withdraw` `to_wei(..., "ether")` kullanmaya devam ediyor. 18 dışında decimals kullanan bir token'la bu aksiyonu çağırıyorsanız miktarı kendiniz kontrol edin.

## Üç PR, üç farklı son

- LightRAG'de kodum girdi, düzeltme ise bakımcı girişte doğrulama ekleyince tamamlandı.
- OpenAI SDK'da doğru hatayı yanlış yerde düzelttim. Üretilmiş bir kod tabanında yamanın yeri kaynak tanım.
- AgentKit'te düzeltme ve testi hazır, PR review bekliyor.

## Sırada ne var

Aynı dönemin diğer katkıları [MCP ekosistemi katkılarım](/posts/mcp-ai-contributions/) ve [SDK ve CLI katkılarım](/posts/python-sdk-cli/) yazılarında. Zincir tarafına yeniyseniz [web3.py ile Ethereum etkileşimi](/posts/web3py-ethereum-blockchain-etkilesimi/) yazısı decimals ve kontrat çağrılarını daha ayrıntılı anlatıyor. Kendi kodunuzda graph veritabanı kullanıyorsanız ilk iş olarak f-string ile kurulan sorgularda label ve relationship tiplerinin nereden geldiğine bakın.
