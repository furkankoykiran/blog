---
title: {{ .Title | jsonify }}
description: {{ .Description | plainify | htmlUnescape | jsonify }}
url: {{ .Permalink }}
lang: {{ .Language.Lang }}
date: {{ .Date.Format "2006-01-02" }}
lastmod: {{ .Lastmod.Format "2006-01-02" }}
author: {{ site.Params.author.name }}
{{ with .Params.tags }}tags: {{ . | jsonify }}
{{ end }}{{ range .Translations }}translation_{{ .Language.Lang }}: {{ .Permalink }}
{{ end }}license: "Text and images © {{ site.Params.author.name }}; code samples MIT"
---

# {{ .Title }}

{{ partial "fk/abs-markdown.html" . }}
