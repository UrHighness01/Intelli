---
name: translate
description: Translate text into any language.
metadata:
  trigger: /translate
---

# Translate

When the user types `/translate <text>` or asks you to translate something:

1. Detect the source language (or use what the user specified).
2. Identify the target language from the request, defaulting to English if unclear.
3. Provide the translation, then optionally add a pronunciation guide for non-Latin scripts.
4. If the text is from the active page, reference the page title.

Keep translations natural, not word-for-word literal.
