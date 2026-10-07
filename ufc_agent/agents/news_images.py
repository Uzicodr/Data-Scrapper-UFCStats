"""Photos for news stories whose feed has none, always with a credit the app can show.

1. A freely licensed photo of a fighter the story names, from that fighter's Wikipedia article
   (CC0, public domain, CC BY or CC BY-SA only). Lookups are cached per fighter in fighter_photos and
   retried after RECHECK_DAYS, so each fighter costs a couple of API calls a month at most.
2. Otherwise one of a few hand-picked Unsplash photos of a cage, chosen by the story URL so a story
   keeps the same photo.

Uses the en.wikipedia.org API for both the article image and its Commons license details: it serves
Commons metadata too, and commons.wikimedia.org is not reachable from every network.
"""
import datetime
import json
import re
import zlib
from dataclasses import dataclass
from urllib.parse import urlencode

from ufc_agent.fetch.http import FetchError

WIKI_API = "https://en.wikipedia.org/w/api.php"
# Wikimedia's User-Agent policy wants a tool name plus a way to reach the operator; requests without
# one get HTTP 403, especially from cloud IPs such as GitHub Actions runners.
WIKI_USER_AGENT = "OctapulseNews/1.0 (https://github.com/Uzicodr/Data-Scrapper-UFCStats) httpx"
RECHECK_DAYS = 30
BATCH = 50  # API limit on titles per query
IMAGE_WIDTH = 1200

# Short descriptions that mark a Wikipedia article as being about a fighter, e.g. "Czech mixed martial artist".
FIGHTER_DESCRIPTION_RE = re.compile(r"martial art|\bmma\b|fighter|kickbox|wrestler|boxer|judoka|jiu-jitsu", re.I)
FREE_LICENSE_RE = re.compile(r"^(cc0|public domain|pd\b|cc by(-sa)? \d)", re.I)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Photo:
    url: str
    credit: str
    license: str
    credit_url: str


def _unsplash(photo_id, path, photographer):
    return Photo(
        url=f"https://images.unsplash.com/{path}?w={IMAGE_WIDTH}&h=675&fit=crop&q=80&auto=format",
        credit=f"{photographer} / Unsplash",
        license="Unsplash License",
        credit_url=f"https://unsplash.com/photos/{photo_id}",
    )


# Free (not Unsplash+) photos of fights inside a cage, picked from unsplash.com/s/photos/mma-cage.
STOCK_PHOTOS = (
    _unsplash("wEvO6fsQiG4", "photo-1680022702604-292f21514497", "Redd Francisco"),
    _unsplash("Y9B4K-wri4I", "photo-1680022547660-fd2226479b30", "Redd Francisco"),
    _unsplash("CtmroEwoSKY", "photo-1777305628889-13832df3e542", "Gabriel F Rodrigues"),
    _unsplash("2tZL1FnsC48", "photo-1680022546558-550eaf22351e", "Redd Francisco"),
)


def stock_photo(story_url):
    return STOCK_PHOTOS[zlib.crc32(story_url.encode()) % len(STOCK_PHOTOS)]


def _chunks(values, size=BATCH):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


class WikipediaPhotos:
    """Finds a free photo for fighters by name. get_json(params) returns the API's parsed JSON."""

    def __init__(self, get_json):
        self.get_json = get_json

    def _articles(self, titles):
        """title asked for -> (article image file name, short description) for articles that exist."""
        found = {}
        for chunk in _chunks(titles):
            data = self.get_json({"action": "query", "format": "json", "prop": "pageimages|description",
                                  "piprop": "name", "redirects": 1, "titles": "|".join(chunk)})
            query = data.get("query", {})
            # Follow the API's renames back to the title we asked for.
            renamed = {}
            for step in query.get("normalized", []) + query.get("redirects", []):
                renamed[step["to"]] = renamed.get(step["from"], step["from"])
            for page in query.get("pages", {}).values():
                if "missing" in page or "pageimage" not in page:
                    continue
                asked = renamed.get(page["title"], page["title"])
                found[asked] = (page["pageimage"], page.get("description") or "")
        return found

    def _files(self, names):
        """File name -> Photo, only for freely licensed files."""
        photos = {}
        for chunk in _chunks(names):
            data = self.get_json({"action": "query", "format": "json", "prop": "imageinfo",
                                  "iiprop": "url|extmetadata", "iiurlwidth": IMAGE_WIDTH,
                                  "titles": "|".join(f"File:{name}" for name in chunk)})
            for page in data.get("query", {}).get("pages", {}).values():
                info = (page.get("imageinfo") or [{}])[0]
                meta = info.get("extmetadata", {})
                license_name = meta.get("LicenseShortName", {}).get("value", "")
                url = info.get("thumburl") or info.get("url")
                if not url or not FREE_LICENSE_RE.match(license_name):
                    continue
                artist = " ".join(TAG_RE.sub(" ", meta.get("Artist", {}).get("value", "")).split())
                if not artist or artist.lower().startswith("unknown"):
                    artist = "Unknown author"
                name = page["title"].split(":", 1)[1].replace(" ", "_")
                photos[name] = Photo(url=url, credit=f"{artist[:150]} / Wikimedia Commons", license=license_name,
                                     credit_url=info.get("descriptionurl") or url)
        return photos

    def find(self, names):
        """Fighter name -> Photo for names whose Wikipedia article is about a fighter and has a free photo.

        Tries 'Name' first, then 'Name (fighter)' for names that land on another person or nowhere.
        """
        chosen = {}
        for variant in ("{}", "{} (fighter)"):
            pending = [name for name in names if name not in chosen]
            if not pending:
                break
            articles = self._articles([variant.format(name) for name in pending])
            for name in pending:
                article = articles.get(variant.format(name))
                if article and FIGHTER_DESCRIPTION_RE.search(article[1]):
                    chosen[name] = article[0].replace(" ", "_")
        files = self._files(set(chosen.values()))
        return {name: files[file] for name, file in chosen.items() if file in files}


class NewsImages:
    """Fills news_items.image_url (and credit columns) for stories saved without a photo."""

    def __init__(self, fetcher, photos=None):
        """fetcher must send WIKI_USER_AGENT; see sync_news.main."""
        self.photos = photos or WikipediaPhotos(
            lambda params: json.loads(fetcher.get(f"{WIKI_API}?{urlencode(params)}", use_cache=False))
        )

    def refresh_fighter_photos(self, conn, now):
        """Look up tagged fighters never checked, or checked more than RECHECK_DAYS ago.

        Raises FetchError when Wikipedia can't be reached; nothing is cached then, so the next run retries.
        """
        rows = conn.execute(
            "SELECT DISTINCT f.id, f.name FROM news_items n "
            "JOIN news_item_fighters nf ON nf.news_id = n.id JOIN fighters f ON f.id = nf.fighter_id "
            "LEFT JOIN fighter_photos fp ON fp.fighter_id = f.id "
            "WHERE n.image_url IS NULL AND (fp.fighter_id IS NULL OR fp.checked_at < %s)",
            (now - datetime.timedelta(days=RECHECK_DAYS),),
        ).fetchall()
        if not rows:
            return 0
        found = self.photos.find([row["name"] for row in rows])
        for row in rows:
            photo = found.get(row["name"])
            # A row with a null image_url records "no free photo" so the fighter isn't asked again soon.
            conn.execute(
                "INSERT INTO fighter_photos (fighter_id, image_url, credit, license, credit_url, checked_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (fighter_id) DO UPDATE SET "
                "image_url = EXCLUDED.image_url, credit = EXCLUDED.credit, license = EXCLUDED.license, "
                "credit_url = EXCLUDED.credit_url, checked_at = EXCLUDED.checked_at",
                (row["id"], photo and photo.url, photo and photo.credit, photo and photo.license,
                 photo and photo.credit_url, now),
            )
        conn.commit()
        return len(found)

    def fill(self, conn, now=None):
        """Give photo-less stories a fighter photo, else a stock photo. Never raises for Wikipedia trouble:
        stories naming a fighter not looked up yet just wait for a later run instead of getting stock."""
        now = now or datetime.datetime.now(datetime.timezone.utc)
        error = None
        try:
            found = self.refresh_fighter_photos(conn, now)
        except FetchError as exc:
            conn.rollback()
            found, error = 0, str(exc)
        fighter = conn.execute(
            "UPDATE news_items n SET image_url = p.image_url, image_credit = p.credit, "
            "image_license = p.license, image_credit_url = p.credit_url "
            "FROM (SELECT DISTINCT ON (nf.news_id) nf.news_id, fp.* FROM news_item_fighters nf "
            "      JOIN fighter_photos fp ON fp.fighter_id = nf.fighter_id AND fp.image_url IS NOT NULL "
            "      JOIN fighters f ON f.id = nf.fighter_id ORDER BY nf.news_id, f.name) p "
            "WHERE n.id = p.news_id AND n.image_url IS NULL"
        ).rowcount
        rows = conn.execute(
            "SELECT n.id, n.url FROM news_items n WHERE n.image_url IS NULL AND NOT EXISTS ("
            "  SELECT 1 FROM news_item_fighters nf LEFT JOIN fighter_photos fp ON fp.fighter_id = nf.fighter_id"
            "  WHERE nf.news_id = n.id AND fp.fighter_id IS NULL)"
        ).fetchall()
        for row in rows:
            photo = stock_photo(row["url"])
            conn.execute(
                "UPDATE news_items SET image_url = %s, image_credit = %s, image_license = %s, image_credit_url = %s "
                "WHERE id = %s",
                (photo.url, photo.credit, photo.license, photo.credit_url, row["id"]),
            )
        conn.commit()
        result = {"fighter_photos_found": found, "from_fighters": fighter, "from_stock": len(rows)}
        if error:
            result["wikipedia_error"] = error
        return result
