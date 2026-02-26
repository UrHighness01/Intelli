---
name: web-scraper
description: Extract structured data from web pages using browser automation
user-invokable: true
---

# Web Scraper Skill

This skill enables the agent to extract structured data from web pages by navigating, scrolling, and executing JavaScript to gather information.

## Capabilities

- Navigate to target pages
- Scroll to load dynamic content
- Extract data using CSS selectors
- Handle pagination
- Export data as JSON or CSV

## System Prompt Extension

```
You can scrape data from web pages using browser automation:

**Navigation & Scrolling:**
- browser_navigate(url) — go to a page
- browser_scroll(pixels) or browser_scroll(to_bottom=true) — load more content
- browser_wait(selector) — wait for dynamic content to load

**Data Extraction:**
- browser_eval(js_code) — execute JavaScript to extract data

Example scraping pattern:
```javascript
// Extract all product cards
Array.from(document.querySelectorAll('.product-card')).map(card => ({
  title: card.querySelector('.title')?.textContent.trim(),
  price: card.querySelector('.price')?.textContent.trim(),
  url: card.querySelector('a')?.href
}))
```

**Multi-page scraping:**
1. Extract data from current page
2. Find "Next" button selector
3. browser_click(next_button_selector)
4. browser_wait(content_selector)
5. Repeat

Always respect robots.txt and rate limits. Never scrape personal data without permission.
```

## Example Usage

**User:** "Scrape the top 10 posts from Hacker News"

**Agent:**
1. `browser_navigate('https://news.ycombinator.com')`
2. `browser_wait('.athing')`
3. `browser_eval(...)` to extract titles, URLs, scores
4. Returns structured JSON data

**User:** "Get all product prices from this page"

**Agent:**
1. Analyzes current page snapshot
2. `browser_scroll(to_bottom=true)` to load lazy content
3. `browser_eval(...)` to extract all `.price` elements
4. Returns formatted list

## Safety

- Respect robots.txt
- Add delays between requests (use browser_wait)
- Never scrape authentication-protected content without permission
- Limit pagination depth to avoid infinite loops
