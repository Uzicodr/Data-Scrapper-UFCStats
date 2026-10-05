from ufc_agent.fetch.extract import html_to_text

PAGE = """
<html><head><title> UFC 322: Della Maddalena vs. Makhachev </title><style>.x{}</style></head>
<body>
  <nav>Home | Events</nav>
  <h2>Fight Card</h2>
  <p>Date: November 15, 2025</p>
  <table>
    <tr><th>W/L</th><th>Fighter</th><th>Kd</th></tr>
    <tr><td>win</td><td><p>Islam Makhachev</p><p>Jack Della Maddalena</p></td><td><p>0</p><p>0</p></td></tr>
  </table>
  <script>var tracking = 1;</script>
  <footer>Copyright</footer>
</body></html>
"""


def test_html_to_text_keeps_content_and_tables():
    title, text, truncated = html_to_text(PAGE)
    assert title == "UFC 322: Della Maddalena vs. Makhachev"
    assert "Fight Card" in text
    assert "Date: November 15, 2025" in text
    assert "W/L | Fighter | Kd" in text
    assert "win | Islam Makhachev Jack Della Maddalena | 0 0" in text
    assert not truncated


def test_html_to_text_drops_boilerplate():
    _, text, _ = html_to_text(PAGE)
    assert "tracking" not in text
    assert "Copyright" not in text
    assert "Home | Events" not in text


def test_html_to_text_truncates_on_line_boundary():
    html = "<body>" + "".join(f"<p>line {i}</p>" for i in range(200)) + "</body>"
    _, text, truncated = html_to_text(html, max_chars=100)
    assert truncated
    assert len(text) <= 100
    assert text.endswith(tuple("0123456789"))
