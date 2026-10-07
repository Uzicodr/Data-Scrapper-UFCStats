"""Job to pull MMA headlines from publisher RSS feeds into news_items. Runs hourly.

No LLM: feeds are structured, and fighter tags and story kinds come from plain matching rules.
Only what a feed provides is stored, unchanged apart from removing HTML, because publishers such as
ESPN forbid modifying headlines or summaries. The app links out to the original article.

Run once by hand with: python -m ufc_agent.agents.sync_news
"""
import datetime
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from ufc_agent.fetch.http import FetchError
from ufc_agent.normalize import name_key


@dataclass(frozen=True)
class Feed:
    key: str
    name: str  # shown in the app as the credit line
    url: str


# Feeds whose terms allow showing headlines with a credit and a link back. Google News is left out:
# its feeds are for personal, non-commercial use only. The last three carry a photo for every story.
# MMA News is left out: its Cloudflare setup blocks non-browser clients.
FEEDS = (
    Feed("espn", "ESPN", "https://www.espn.com/espn/rss/mma/news"),
    Feed("ufc", "UFC.com", "https://www.ufc.com/rss/news"),
    Feed("sherdog", "Sherdog", "https://www.sherdog.com/rss/news.xml"),
    Feed("mmaweekly", "MMA Weekly", "https://www.mmaweekly.com/feed"),
    Feed("bbc", "BBC Sport", "https://www.bbc.co.uk/sport/mixed-martial-arts/rss.xml"),
    Feed("guardian", "The Guardian", "https://www.theguardian.com/sport/ufc/rss"),
)

# An honest feed-reader agent. ESPN answers the shared browser-like agent with an empty 202.
FEED_USER_AGENT = "Octapulse/1.0 (RSS reader; links back to the publisher)"

KEEP_DAYS = 30
MAX_FIGHTERS_PER_ITEM = 6

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}

# Checked in order against the headline; the first match wins, anything else is plain "news".
KIND_RULES = (
    ("injury", re.compile(
        r"\b(injur\w*|withdraw\w*|pulls? out|pulled out|forced out|out of (ufc|the|his|her)|replace(s|d|ment)?"
        r"|surgery|scrapped|cancel+ed|off the card)\b", re.I)),
    ("result", re.compile(
        r"\b(results?|def\.|defeats?|beats?|knock(s|ed)? out|t?ko|stops|submits|retains?|scorecards?|recap|wins?"
        r"|(earns?|lands?) (an? )?(ufc )?(contract|deal))\b", re.I)),
    ("announcement", re.compile(
        r"\b(booked|set for|(?<!wants )(?<!hopes )to (face|fight|meet|rematch)|added to|announc\w*|signs?|signed"
        r"|headline\w*"
        r"|title (fight|shot|bout) (set|added|booked)|official|confirmed)\b", re.I)),
    ("rumor", re.compile(r"\b(reports?|reportedly|rumou?rs?|in talks|targeting|eyeing|calls? out|callout)\b", re.I)),
)

READ_MORE_RE = re.compile(r"<a\b[^>]*>\s*read the full article[^<]*</a>", re.I)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class NewsItem:
    url: str
    title: str
    summary: str | None
    image_url: str | None
    published_at: datetime.datetime
    image_credit: str | None = None
    kind: str = "news"
    fighter_ids: list[str] = field(default_factory=list)


def strip_html(text):
    """Feed HTML to plain text: drop tags and 'Read the Full Article' links, unescape, collapse spaces."""
    if not text:
        return None
    text = " ".join(html.unescape(TAG_RE.sub(" ", READ_MORE_RE.sub(" ", text))).split())
    return text or None


def parse_date(text, now):
    """RFC 822 (pubDate) or ISO 8601 (dc:date) to an aware UTC datetime; None when unparseable."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            value = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    # Previews are sometimes dated ahead; never sort a story above things already published.
    return min(value.astimezone(datetime.timezone.utc), now)


def _text(item, path):
    node = item.find(path, NS)
    return node.text.strip() if node is not None and node.text else None


# BBC thumbnails are 240px wide; the same image is served at 976px by changing the size segment.
BBC_THUMB_RE = re.compile(r"(ichef\.bbci\.co\.uk/ace/standard/)\d+/")


def _image(item):
    """The widest https image the item offers (media:content, media:thumbnail or enclosure), or None.

    Some feeds put junk in type (MMA Weekly sends type="false"), so only an explicit non-image type or
    medium rules a candidate out.
    """
    best, best_width = None, -1
    for path in ("media:content", "media:thumbnail", "enclosure"):
        for node in item.findall(path, NS):
            url = node.get("url")
            kind = (node.get("medium") or node.get("type") or "image").lower()
            if not url or not url.startswith("https://") or kind.startswith(("video", "audio")):
                continue
            width = int(node.get("width") or 0) if (node.get("width") or "").isdigit() else 0
            if width > best_width:
                best, best_width = url, width
    return BBC_THUMB_RE.sub(r"\g<1>976/", best) if best else None


def _image_credit(item):
    """Photographer credit from media:credit, e.g. 'Photograph: Dean Lewins/AAP' -> 'Dean Lewins/AAP'
    and 'Photo by Chris Unger&sol;Zuffa LLC' -> 'Chris Unger/Zuffa LLC'."""
    credit = _text(item, ".//media:credit")
    if not credit:
        return None
    credit = html.unescape(credit)
    return re.sub(r"^(photograph|photo|image)( by)?\s*:?\s*", "", credit, flags=re.I).strip()[:200] or None


def parse_feed(xml_text, now):
    """Return the NewsItems in an RSS 2.0 document. Items without a title or https link are skipped."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = []
    for item in root.iter("item"):
        url, title = _text(item, "link"), strip_html(_text(item, "title"))
        if not url or not url.startswith("https://") or not title:
            continue
        published = parse_date(_text(item, "dc:date"), now) or parse_date(_text(item, "pubDate"), now) or now
        summary = strip_html(_text(item, "description") or _text(item, "content:encoded"))
        image = _image(item)
        items.append(NewsItem(url=url, title=title, summary=summary, image_url=image, published_at=published,
                              image_credit=_image_credit(item) if image else None))
    return items


def classify(title):
    for kind, pattern in KIND_RULES:
        if pattern.search(title):
            return kind
    return "news"


class FighterMatcher:
    """Finds fighters named in text by their full name or an alias, using the same keys as NameIndex.

    Single-word names are ignored (too many false hits), and so is any key shared by two fighters.
    """

    def __init__(self, fighters):
        owners = {}
        for fighter in fighters:
            for name in [fighter["name"], *(fighter.get("aliases") or [])]:
                key = name_key(name)
                if len(key.split()) >= 2:
                    owners.setdefault(key, set()).add(str(fighter["id"]))
        self.keys = {key: next(iter(ids)) for key, ids in owners.items() if len(ids) == 1}
        self.max_words = max((len(key.split()) for key in self.keys), default=0)

    def find(self, text, limit=MAX_FIGHTERS_PER_ITEM):
        words = name_key(text).split()
        found = []
        for size in range(min(self.max_words, len(words)), 1, -1):
            for start in range(len(words) - size + 1):
                fighter_id = self.keys.get(" ".join(words[start:start + size]))
                if fighter_id and fighter_id not in found:
                    found.append(fighter_id)
        return found[:limit]


def save_items(conn, feed, items):
    """Insert items not stored yet, with their fighter tags. Returns how many were new."""
    new = 0
    for item in items:
        row = conn.execute(
            "INSERT INTO news_items (source, source_name, url, title, summary, image_url, image_credit, kind, "
            "published_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (url) DO NOTHING RETURNING id",
            (feed.key, feed.name, item.url, item.title[:500], item.summary, item.image_url, item.image_credit,
             item.kind, item.published_at),
        ).fetchone()
        if row is None:
            continue
        new += 1
        for fighter_id in item.fighter_ids:
            conn.execute(
                "INSERT INTO news_item_fighters (news_id, fighter_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (row["id"], fighter_id),
            )
    conn.commit()
    return new


def sync_news(conn, fetcher, feeds=FEEDS, now=None, fill_images=None):
    """Fetch every feed and store new items. One broken feed does not stop the others.

    fill_images(conn) gives stories without a feed photo a fighter photo or a stock photo; see news_images.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    fighters = conn.execute(
        "SELECT id, name, raw_payload->'aliases' AS aliases FROM fighters WHERE name IS NOT NULL"
    ).fetchall()
    matcher = FighterMatcher([dict(row) for row in fighters])

    per_feed, errors = {}, {}
    for feed in feeds:
        try:
            body = fetcher.get(feed.url, use_cache=False)
            if not body.strip():
                raise FetchError(f"{feed.url} returned an empty body")
            items = parse_feed(body, now)
        except (FetchError, ET.ParseError) as exc:
            errors[feed.key] = str(exc)
            continue
        for item in items:
            item.kind = classify(item.title)
            item.fighter_ids = matcher.find(f"{item.title} {item.summary or ''}")
        per_feed[feed.key] = {"seen": len(items), "new": save_items(conn, feed, items)}

    deleted = conn.execute(
        "DELETE FROM news_items WHERE published_at < %s", (now - datetime.timedelta(days=KEEP_DAYS),)
    ).rowcount
    conn.commit()
    summary = {"feeds": per_feed, "errors": errors, "deleted": deleted}
    if fill_images is not None:
        summary["images"] = fill_images(conn)
    return summary


def main():
    import httpx

    from urllib.parse import urlparse

    from ufc_agent.agents.news_images import WIKI_USER_AGENT, NewsImages
    from ufc_agent.db import connect
    from ufc_agent.fetch.http import DEFAULT_ALLOWED_DOMAINS, Fetcher
    from ufc_agent.runlog import RunLog

    conn = connect()
    run_log = RunLog(conn, "sync_news", {"feeds": [feed.key for feed in FEEDS]})
    try:
        client = httpx.Client(headers={"User-Agent": FEED_USER_AGENT}, timeout=20, follow_redirects=True)
        domains = {*DEFAULT_ALLOWED_DOMAINS, *(urlparse(feed.url).hostname.removeprefix("www.") for feed in FEEDS)}
        fetcher = Fetcher(allowed_domains=domains, min_interval=1.0, client=client, cache_dir=None)
        wiki_client = httpx.Client(headers={"User-Agent": WIKI_USER_AGENT}, timeout=20, follow_redirects=True)
        wiki = Fetcher(min_interval=1.0, client=wiki_client, cache_dir=None)
        summary = sync_news(conn, fetcher, fill_images=NewsImages(wiki).fill)
    except Exception as exc:
        conn.rollback()
        run_log.finish(status="error", error=str(exc))
        raise
    status = "completed" if not summary["errors"] else ("partial" if summary["feeds"] else "error")
    run_log.finish(status=status, summary=summary)
    print(json.dumps(summary, indent=2))
    if status == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
