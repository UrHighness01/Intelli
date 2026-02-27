---
name: Page Summarize
slug: page-summarize
description: Summarize the current browser tab in a few sentences.
trigger: /summarize
---

# Page Summarize

When the user types `/summarize` or asks to summarize the page:

1. Read the active tab HTML from context (enable Page context in the chat bar).
2. Extract the main content — ignore navbars, ads, footers.
3. Return a structured summary:
   - **Title & URL**
   - **What it is** (1 sentence)
   - **Key points** (3-5 bullets)
   - **Takeaway** (1 sentence)

Be concise.  Do not pad.  If the page is code/docs, summarize the API surface.
