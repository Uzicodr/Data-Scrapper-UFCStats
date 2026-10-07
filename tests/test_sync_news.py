"""Test RSS parsing, story kinds, fighter tagging and the sync_news job against saved feeds."""
import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ufc_agent.agents.sync_news import (
    FEEDS,
    Feed,
    FighterMatcher,
    classify,
    parse_date,
    parse_feed,
    strip_html,
    sync_news,
)
from ufc_agent.fetch.http import FetchError

FEED_DIR = Path(__file__).parent / "fixtures" / "feeds"
NOW = datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.timezone.utc)


def feed_xml(key):
    return (FEED_DIR / f"{key}.xml").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", [feed.key for feed in FEEDS])
def test_parse_saved_feeds(key):
    items = parse_feed(feed_xml(key), NOW)
    assert len(items) >= 10
    for item in items:
        assert item.url.startswith("https://")
        assert item.title and "<" not in item.title
        assert item.summary is None or "<" not in item.summary
        assert item.published_at <= NOW


def test_ufc_summary_drops_read_more_link_and_prefers_dc_date():
    item = parse_feed(feed_xml("ufc"), NOW)[0]
    assert "Read the Full Article" not in item.summary
    assert item.summary.startswith("Live Results")


def test_parse_feed_skips_items_without_https_link_or_title():
    xml = """<rss><channel>
      <item><title>Kept</title><link>https://www.espn.com/a</link><pubDate>Tue, 6 Oct 2026 09:13:53 EST</pubDate></item>
      <item><title>No link</title></item>
      <item><title>Plain http</title><link>http://www.espn.com/b</link></item>
      <item><title> </title><link>https://www.espn.com/c</link></item>
    </channel></rss>"""
    items = parse_feed(xml, NOW)
    assert [i.title for i in items] == ["Kept"]
    assert items[0].published_at == datetime.datetime(2026, 10, 6, 14, 13, 53, tzinfo=datetime.timezone.utc)


def test_parse_feed_reads_media_image():
    xml = """<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>
      <title>T</title><link>https://www.ufc.com/n</link>
      <media:content url="https://cdn.ufc.com/x.jpg" medium="image"/>
    </item></channel></rss>"""
    assert parse_feed(xml, NOW)[0].image_url == "https://cdn.ufc.com/x.jpg"


def test_strip_html():
    assert strip_html('<a href="x">Jon Jones</a> &amp; Stipe   Miocic') == "Jon Jones & Stipe Miocic"
    assert strip_html("<p></p>") is None


@pytest.mark.parametrize("text, expected", [
    ("2026-10-08T20:00:00Z", datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.timezone.utc)),  # future: clamped
    ("Wed, 7 Oct 2026 1:05:00 GMT", datetime.datetime(2026, 10, 7, 1, 5, tzinfo=datetime.timezone.utc)),
    ("not a date", None),
    (None, None),
])
def test_parse_date(text, expected):
    assert parse_date(text, NOW) == expected


@pytest.mark.parametrize("title, kind", [
    ("Jiri Prochazka withdraws from UFC 333 with knee injury", "injury"),
    ("Replacement found for Pereira", "injury"),
    ("UFC 332 results: Natalia Silva wins women's flyweight title", "result"),
    ("Everett earns UFC contract with first-round KO", "result"),
    ("Marko Martinjak vs. Matty Askin title fight added to BKB 61", "announcement"),
    ("Pereira vs. Ankalaev 3 set for UFC 334", "announcement"),
    ("Prochazka to fight Stirling at UFC Fight Night in Qatar", "announcement"),
    ("Gaethje wants to fight Topuria next", "news"),
    ("Report: Jones in talks for heavyweight return", "rumor"),
    ("Fighters on the Rise | UFC Fight Night", "news"),
])
def test_classify(title, kind):
    assert classify(title) == kind


FIGHTERS = [
    {"id": "f-proch", "name": "Jiří Procházka", "aliases": None},
    {"id": "f-blach", "name": "Jan Blachowicz", "aliases": ["Jan Błachowicz"]},
    {"id": "f-roque", "name": "Roque Conceicao Moreira Junior", "aliases": None},
    {"id": "f-silva-1", "name": "Bruno Silva", "aliases": None},
    {"id": "f-silva-2", "name": "Bruno Silva", "aliases": None},
    {"id": "f-mono", "name": "Mononym", "aliases": None},
]


def test_matcher_finds_full_names_and_aliases():
    matcher = FighterMatcher(FIGHTERS)
    text = "Jiri Prochazka wants Jan Błachowicz rematch; Roque Conceicao Moreira Junior signs"
    assert matcher.find(text) == ["f-roque", "f-proch", "f-blach"]


def test_matcher_skips_shared_and_single_word_names():
    matcher = FighterMatcher(FIGHTERS)
    assert matcher.find("Bruno Silva beats Mononym") == []


def test_matcher_respects_limit():
    matcher = FighterMatcher(FIGHTERS)
    assert matcher.find("Jiri Prochazka and Jan Blachowicz", limit=1) == ["f-proch"]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, use_cache=True):
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def fake_conn(fighters):
    conn = MagicMock()
    inserted = []

    def execute(sql, params=None):
        cursor = MagicMock()
        if sql.startswith("SELECT id, name"):
            cursor.fetchall.return_value = fighters
        elif sql.startswith("INSERT INTO news_items"):
            url = params[2]
            cursor.fetchone.return_value = None if url in conn.existing else {"id": f"n-{len(inserted)}"}
            inserted.append(params)
        elif sql.startswith("INSERT INTO news_item_fighters"):
            conn.tags.append(params)
        elif sql.startswith("DELETE"):
            cursor.rowcount = 2
        return cursor

    conn.execute.side_effect = execute
    conn.existing = set()
    conn.tags = []
    conn.inserted = inserted
    return conn


def test_sync_news_stores_new_items_and_keeps_going_after_a_broken_feed():
    good = Feed("espn", "ESPN", "https://www.espn.com/feed")
    broken = Feed("ufc", "UFC.com", "https://www.ufc.com/feed")
    xml = """<rss><channel>
      <item><title>Jiri Prochazka withdraws from UFC 333</title><link>https://www.espn.com/1</link></item>
      <item><title>Old story</title><link>https://www.espn.com/2</link></item>
    </channel></rss>"""
    conn = fake_conn(FIGHTERS)
    conn.existing = {"https://www.espn.com/2"}
    fetcher = FakeFetcher({good.url: xml, broken.url: FetchError("HTTP 403")})

    summary = sync_news(conn, fetcher, feeds=(good, broken), now=NOW)

    assert summary["feeds"] == {"espn": {"seen": 2, "new": 1}}
    assert summary["errors"] == {"ufc": "HTTP 403"}
    assert summary["deleted"] == 2
    first = conn.inserted[0]
    assert first[:3] == ("espn", "ESPN", "https://www.espn.com/1")
    assert first[6] == "injury"
    assert conn.tags == [("n-0", "f-proch")]
