---
name: image-ocr-extract
description: Extract text from any image (URL, page element, or uploaded file) using vision AI.
---

# Image OCR Extractor

When the user asks to extract text from an image, read text from a screenshot,
or OCR something, follow these steps:

1. **Determine the image source:**
   - If the user gave a direct URL (http/https), use it as-is.
   - If they said "this image on the page" or pointed to an element, call
     `browser_exec_js` with:
     ```js
     (document.querySelector('img') || document.querySelector('[src]')).src
     ```
     Adjust the selector to match what the user described.
   - If they gave a local file path or uploaded file, use that path directly.

2. **Run OCR via vision AI:**
   Call `video_describe` with:
   ```json
   {
     "url": "<image_url_or_path>",
     "prompt": "Extract ALL readable text from this image exactly as written. Return:\nRAW_TEXT:\n<verbatim text content>\nSTRUCTURED_JSON:\n<any tables, key-value pairs, or lists detected as JSON>"
   }
   ```

3. **Parse and present the result:**
   - Present the `RAW_TEXT` block as a fenced code block so the user can copy it easily.
   - If `STRUCTURED_JSON` is present and non-empty, display it as a formatted table or JSON block below.
   - If the image contains multiple sections (e.g. a receipt with header, items, total), break them into labelled subsections.

4. **SVG / vector image fallback:**
   If the URL ends in `.svg` or `video_describe` returns empty/unsupported:
   - Call `browser_exec_js` to render the SVG to a canvas and get a PNG data-URL:
     ```js
     (function(){
       var img = new Image(); img.src = '<svg_url>';
       var c = document.createElement('canvas');
       c.width = 800; c.height = 600;
       var ctx = c.getContext('2d');
       return new Promise(r => { img.onload = () => { ctx.drawImage(img,0,0); r(c.toDataURL()); }; });
     })()
     ```
   - Use the returned data-URL as the new `url` for `video_describe`.

5. **If all methods fail:**
   Tell the user the image could not be read (reason: format unsupported, CORS
   block, or no readable text found) and suggest they paste the text manually or
   share a screenshot instead.
