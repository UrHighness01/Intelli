"""Simple fuzzing harness for TabContextBridge preview endpoint.

This is a lightweight fuzzer used in CI/dev to send random-ish HTML to
`/tab/preview` and ensure the gateway handles untrusted HTML without error.
"""
import random
import string
import httpx


def random_html():
    # create a small random HTML document with some inputs
    parts = ["<html><head><title>Fuzz</title></head><body>"]
    for i in range(random.randint(1, 5)):
        name = ''.join(random.choices(string.ascii_lowercase, k=6))
        value = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        parts.append(f'<input type="text" name="{name}" value="{value}"/>')
    parts.append("<p>" + ''.join(random.choices(string.printable, k=50)) + "</p>")
    parts.append("</body></html>")
    return '\n'.join(parts)


def run_once(url: str = 'http://127.0.0.1:8080/tab/preview') -> dict:
    html = random_html()
    payload = {'html': html, 'url': 'https://fuzz.test'}
    with httpx.Client(timeout=5.0) as c:
        r = c.post(url, json=payload)
        return {'status_code': r.status_code, 'json': r.json()}


if __name__ == '__main__':
    print(run_once())
