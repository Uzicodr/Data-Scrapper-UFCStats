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


def test_html_to_text_keeps_table_caption():
    html = """<body><table><caption><div><h4>Flyweight</h4><h5><a>Joshua Van</a></h5>
    <h6>Champion</h6></div></caption><tr><td>1</td><td>Alexandre Pantoja</td></tr></table></body>"""
    _, text, _ = html_to_text(html)
    assert "Flyweight Joshua Van Champion\n1 | Alexandre Pantoja" in text


def test_html_to_text_truncates_on_line_boundary():
    html = "<body>" + "".join(f"<p>line {i}</p>" for i in range(200)) + "</body>"
    _, text, truncated = html_to_text(html, max_chars=100)
    assert truncated
    assert len(text) <= 100
    assert text.endswith(tuple("0123456789"))


def test_html_to_text_include_links():
    html = """<body><table>
      <tr data-link="http://ufcstats.com/fight-details/f1">
        <td><a href="http://ufcstats.com/fighter-details/a1">Brendan Allen</a>
            <a href="/fighter-details/b2">Christian Leroy Duncan</a></td>
        <td>Middleweight <img src="http://1e49bc5171d173577ecd-1323f4090557a33db01577564f60846c.r80.cf1.rackcdn.com/belt.png"></td>
      </tr></table><p><a href="#top">Top</a></p></body>"""
    _, text, _ = html_to_text(html, include_links=True, base_url="http://ufcstats.com/event-details/e1")
    assert ("Brendan Allen <http://ufcstats.com/fighter-details/a1> "
            "Christian Leroy Duncan <http://ufcstats.com/fighter-details/b2> | Middleweight [img:belt.png] | "
            "<http://ufcstats.com/fight-details/f1>") in text
    assert "Top" in text and "#top" not in text


def test_html_to_text_links_off_by_default():
    html = '<body><table><tr data-link="http://x/f"><td><a href="http://x/a">A</a></td></tr></table></body>'
    _, text, _ = html_to_text(html)
    assert text == "A"
