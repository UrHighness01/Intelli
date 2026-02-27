---
name: web-search
description: Search the web and return top results with a synthesized answer.
metadata:
  trigger: /search
---

# Web Search

When asked to search for something or when the user types `/search <query>`:

1. Call the `web_search` tool with the user's query.
2. Review the titles and snippets returned.
3. If you need more detail on a result, call `web_fetch` on its URL.
4. Synthesize a clear answer citing the sources (title + URL).

Always cite your sources at the bottom as:
> Source: Title — URL
