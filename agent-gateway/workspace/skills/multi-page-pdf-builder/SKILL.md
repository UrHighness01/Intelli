---
name: multi-page-pdf-builder
description: Help the user build a multi-page document by collecting content page-by-page and returning each page formatted in chat for copy-pasting into a PDF tool.
---

# Multi-page PDF Builder

Help the user assemble a multi-page document. Collect content page by page, then
return every page formatted in the chat — the user copies each block into their
PDF tool of choice.

**No file saving. No shell commands. Everything comes back in chat.**

---

## Step 1 — Start a session

When the user says "start a PDF", "build a PDF", or "new document":

1. Greet the session:
   ```
   Ready to build your PDF. Send me the content for each page one at a time.
   Say "Page 1: <content>", "Page 2: <content>", etc.
   When you're done adding pages, say "done" or "show all pages".
   ```
2. Track pages in a numbered mental list (no file writes needed).

---

## Step 2 — Collect pages

When the user sends a page (e.g. "Page 2: Here is the text for page two..."):

1. Acknowledge it immediately:
   ```
   ✅ Page 2 saved. Send Page 3 or say "done" when finished.
   ```
2. Keep an internal ordered list of all pages received so far.

If the user sends plain text, HTML, or a mix — accept all formats as-is.
If the user pastes a URL and says "page X is this URL", call `web_fetch` on the
URL and use the returned text/HTML as the page content.

---

## Step 3 — Output all pages in chat

When the user says "done", "show pages", "show all", "build", or "give me the pages":

Return **every page as a clearly labelled block**, one after another.
Use this exact format for each page:

```
---

## Page 1

<content of page 1 here>

---

## Page 2

<content of page 2 here>

---
```

- If the content is HTML, render it inside a fenced html block.
- If the content is plain text, render it as-is (no code block).
- If the content came from a URL, add a `> Source: <url>` line below.

After the last page, add:
```
All X pages above. Copy each section into your PDF editor (Word, Google Docs,
Canva, etc.) and export as PDF when ready.
```

---

## Step 4 — Revisions

If the user asks to change a page ("replace page 2 with this new text"):
1. Update that page in the mental list.
2. Confirm: `✅ Page 2 updated.`
3. Re-output all pages if the user says "show all" again.

If the user says "remove page 3":
1. Drop that page and renumber.
2. Confirm: `✅ Page 3 removed. You now have X pages.`

---

## Error handling

- If the user sends a URL for a page and `web_fetch` fails: tell them and ask
  them to paste the content directly.
- If the user asks to "export" or "save as PDF": explain that this skill outputs
  pages in chat for copy-paste — suggest they use their preferred PDF tool.
