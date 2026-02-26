---
name: form-filler
description: Automatically detect and fill web forms using browser automation
user-invokable: true
---

# Form Auto-Filler Skill

This skill enables the agent to automatically detect form fields on the current page and fill them intelligently using browser automation tools.

## Capabilities

- Detect input fields, textareas, and select elements
- Fill forms based on context and user profile
- Submit forms after confirmation
- Handle multi-step forms

## System Prompt Extension

```
You have access to browser automation tools that let you control the active tab:

- browser_click(selector) — click any element
- browser_type(selector, text) — type into input fields
- browser_scroll(pixels) — scroll the page
- browser_navigate(url) — navigate to a URL
- browser_wait(selector) — wait for an element to appear
- browser_eval(js_code) — execute JavaScript

When the user asks you to "fill this form" or "submit this", use these tools to:
1. Analyze the page snapshot to identify form fields
2. Ask the user for any missing information
3. Use browser_type() to fill each field
4. Use browser_click() to submit the form

Always confirm before clicking submit buttons.
```

## Example Usage

**User:** "Fill out this contact form with my details"

**Agent:**
1. Analyzes page snapshot, finds `<input name="email">`, `<input name="name">`, `<textarea name="message">`
2. Asks: "What message would you like to send?"
3. User provides message
4. Agent executes:
   - `browser_type('input[name="name"]', 'John Doe')`
   - `browser_type('input[name="email"]', 'john@example.com')`
   - `browser_type('textarea[name="message"]', 'Hello...')`
   - `browser_click('button[type="submit"]')` (after confirmation)

## Safety

- Always confirm before submitting forms
- Never auto-fill payment or sensitive credential fields without explicit user approval
- Validate form fields before submission
