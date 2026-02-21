from bs4 import BeautifulSoup
import re
from typing import Any, Dict, List


SENSITIVE_NAME_RE = re.compile(r'password|pass|secret|token|api[_-]?key|cvv|card|ssn|credit', re.I)


class TabContextBridge:
    def __init__(self, max_text_length: int = 2000):
        self.max_text_length = max_text_length

    def _mask_value(self, name: str, input_type: str, value: str) -> str:
        if not value:
            return ""
        if input_type and input_type.lower() == "password":
            return "[REDACTED]"
        if SENSITIVE_NAME_RE.search(name or ""):
            return "[REDACTED]"
        # otherwise return value but truncated
        if len(value) > 200:
            return value[:200] + "..."
        return value

    def snapshot(self, html: str, url: str, selected_text: str | None = None) -> Dict[str, Any]:
        soup = BeautifulSoup(html or "", "lxml")

        title = soup.title.string.strip() if soup.title and soup.title.string else ""

        metas: List[Dict[str, str]] = []
        for m in soup.find_all("meta"):
            content = m.get("content")
            if not content:
                continue
            name = m.get("name") or m.get("property") or m.get("http-equiv") or ""
            metas.append({"name": name, "content": content})

        inputs: List[Dict[str, str]] = []
        for inp in soup.find_all(["input", "textarea", "select"]):
            tag = inp.name
            input_type = (inp.get("type") or "").lower()
            name = inp.get("name") or inp.get("id") or ""
            value = ""
            if tag == "select":
                opt = inp.find("option", selected=True)
                if opt:
                    value = opt.get("value") or opt.text
            elif tag == "textarea":
                value = inp.string or inp.get_text() or ""
            else:
                value = inp.get("value") or ""

            masked = self._mask_value(name, input_type, value)
            inputs.append({"tag": tag, "type": input_type, "name": name, "value": masked})

        # Text snapshot
        full_text = soup.get_text(separator=" ", strip=True) or ""
        if selected_text:
            selected = selected_text.strip()
        else:
            selected = ""

        text_snippet = full_text[: self.max_text_length]

        snapshot = {
            "url": url,
            "title": title,
            "meta": metas,
            "inputs": inputs,
            "selected_text": selected,
            "text_snippet": text_snippet,
        }

        return snapshot


if __name__ == "__main__":
    sample = """
    <html><head><title>Test</title><meta name="description" content="an example"></head>
    <body><input type="text" name="username" value="alice"><input type="password" name="password" value="secret"></body></html>
    """
    bridge = TabContextBridge()
    print(bridge.snapshot(sample, "https://example.com"))
