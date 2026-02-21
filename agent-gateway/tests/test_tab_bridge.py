from tab_bridge import TabContextBridge


def test_snapshot_redacts_password_and_meta():
    html = """
    <html>
      <head>
        <title>Example Page</title>
        <meta name="description" content="This is a description">
      </head>
      <body>
        <form>
          <input type="text" name="email" value="user@example.com" />
          <input type="password" name="password" value="hunter2" />
          <textarea name="notes">Some text here</textarea>
        </form>
        <p>Visible content</p>
      </body>
    </html>
    """

    bridge = TabContextBridge()
    snap = bridge.snapshot(html, "https://example.test")
    assert snap["url"] == "https://example.test"
    assert snap["title"] == "Example Page"
    # meta present
    assert any(m["name"] == "description" for m in snap["meta"])
    # inputs: password should be redacted
    pw_inputs = [i for i in snap["inputs"] if i["name"] == "password"]
    assert pw_inputs and pw_inputs[0]["value"] == "[REDACTED]"
    # email preserved
    email_inputs = [i for i in snap["inputs"] if i["name"] == "email"]
    assert email_inputs and email_inputs[0]["value"] == "user@example.com"
    # textarea preserved
    notes = [i for i in snap["inputs"] if i["name"] == "notes"]
    assert notes and "Some text" in notes[0]["value"]
