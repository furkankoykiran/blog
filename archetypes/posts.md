---
{{- /*
  Archetype for posts. Use scripts/new_post.py, which creates both languages and the
  shared media folder. It also works directly:
    hugo new content content/tr/posts/<bundle>/index.md
    hugo new content content/en/posts/<bundle>/index.md
  <bundle> is the English slug. The language comes from the content tree (module
  mounts in hugo.toml), so this one file serves both. Field rules: README.md and
  schemas/frontmatter.schema.json. The linter fails until every placeholder below
  is replaced; set draft to false to publish.
*/}}
{{- $tr := eq .Site.Language.Lang "tr" }}
title: ""
description: ""
date: {{ .Date }}
slug: "{{ if $tr }}turkce-baslik-slugi{{ else }}{{ .File.ContentBaseName }}{{ end }}"
postType: guide
tags: []
categories: ["Python"]
coverAlt: ""
draft: true
---

{{ if $tr -}}
Giriş (40-120 kelime): okurun sorunu, bu yazıda ne bulacağı ve neye dayandığı (kendi projem, PR'ım, ölçümüm). Ana anahtar kelime ilk iki cümlede geçsin.

## Bölümün cevabını veren başlık

İlk cümle bölümün cevabını versin, ayrıntı sonra gelsin.

![Şemanın ya da ekran görüntüsünün ne gösterdiğini anlatan alt metin](img/ornek.webp "Kısa alt yazı")

```python {title="ornek.py"}
print("merhaba")
```

## Sırada ne var

Kodun ya da PR'ın bağlantısı, denenecek bir sonraki adım, 2-4 ilgili yazı.
{{- else -}}
Intro (40-120 words): the reader's problem, what this post gives them and what it is based on (my project, my PR, my measurement). Put the main keyword in the first two sentences.

## A heading that answers the section's question

Start the section with its answer in one sentence, details after.

![Alt text saying what the diagram or screenshot shows](img/example.webp "Short caption")

```python {title="example.py"}
print("hello")
```

## What's next

Link to the code or PR, one next step to try, 2-4 related posts.
{{- end }}
