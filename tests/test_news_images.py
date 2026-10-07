"""Test Wikipedia fighter photos, the stock fallback and how they fill news_items."""
import datetime
from unittest.mock import MagicMock

from ufc_agent.agents.news_images import STOCK_PHOTOS, NewsImages, Photo, WikipediaPhotos, stock_photo
from ufc_agent.fetch.http import FetchError

NOW = datetime.datetime(2026, 10, 7, 12, 0, tzinfo=datetime.timezone.utc)


def page(title, image=None, description=""):
    result = {"title": title, "description": description}
    if image:
        result["pageimage"] = image
    return result


def file_page(name, license_name, artist='<a href="u">MMAnytt</a>'):
    return {"title": f"File:{name.replace('_', ' ')}", "imageinfo": [{
        "thumburl": f"https://upload.wikimedia.org/thumb/{name}/1200px.jpg",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{name}",
        "extmetadata": {"LicenseShortName": {"value": license_name}, "Artist": {"value": artist}},
    }]}


class FakeWiki:
    def __init__(self):
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        titles = params["titles"].split("|")
        if params["prop"] == "imageinfo":
            files = {
                "File:Prochazka.png": file_page("Prochazka.png", "CC BY 3.0"),
                "File:Silva.jpg": file_page("Silva.jpg", "CC BY-SA 4.0", artist="Jane Roe"),
                "File:Fairuse.jpg": file_page("Fairuse.jpg", "Fair use"),
            }
            return {"query": {"pages": {str(i): files[t] for i, t in enumerate(titles) if t in files}}}
        articles = {
            "Jiří Procházka": page("Jiří Procházka", "Prochazka.png", "Czech mixed martial artist"),
            "Natalia Silva": page("Natalia Silva", "Someone.jpg", "Brazilian footballer"),
            "Natalia Silva (fighter)": page("Natalia Silva (fighter)", "Silva.jpg", "Brazilian mixed martial artist"),
            "Bad License": page("Bad License", "Fairuse.jpg", "American mixed martial artist"),
        }
        pages, redirects = {}, []
        for i, title in enumerate(titles):
            target = "Jiří Procházka" if title == "Jiri Prochazka" else title
            if target != title:
                redirects.append({"from": title, "to": target})
            pages[str(i)] = articles.get(target, {"title": target, "missing": ""})
        return {"query": {"redirects": redirects, "pages": pages}}


def test_wikipedia_photos_follow_redirects_try_fighter_suffix_and_keep_free_licenses_only():
    wiki = FakeWiki()
    found = WikipediaPhotos(wiki).find(["Jiri Prochazka", "Natalia Silva", "Bad License", "Nobody Here"])

    assert set(found) == {"Jiri Prochazka", "Natalia Silva"}
    assert found["Jiri Prochazka"] == Photo(
        url="https://upload.wikimedia.org/thumb/Prochazka.png/1200px.jpg",
        credit="MMAnytt / Wikimedia Commons",
        license="CC BY 3.0",
        credit_url="https://commons.wikimedia.org/wiki/File:Prochazka.png",
    )
    assert found["Natalia Silva"].credit == "Jane Roe / Wikimedia Commons"
    # Second pass only asks for names the first pass could not place.
    second = [c for c in wiki.calls if c["prop"] != "imageinfo"][1]["titles"].split("|")
    assert second == ["Natalia Silva (fighter)", "Nobody Here (fighter)"]


def test_stock_photo_is_stable_per_story_and_credited():
    first = stock_photo("https://www.espn.com/a")
    assert first == stock_photo("https://www.espn.com/a")
    assert first in STOCK_PHOTOS
    assert all(p.credit.endswith("/ Unsplash") and p.credit_url.startswith("https://unsplash.com/photos/")
               for p in STOCK_PHOTOS)


def test_fill_caches_fighter_lookups_then_updates_stories():
    photos = MagicMock()
    photos.find.return_value = {"Jiri Prochazka": Photo("https://img/p.jpg", "MMAnytt / Wikimedia Commons",
                                                        "CC BY 3.0", "https://commons/p")}
    conn = MagicMock()
    executed = []

    def execute(sql, params=None):
        executed.append((sql, params))
        cursor = MagicMock()
        if sql.startswith("SELECT DISTINCT f.id"):
            cursor.fetchall.return_value = [{"id": "f-proch", "name": "Jiri Prochazka"}, {"id": "f-x", "name": "No Photo"}]
        elif sql.startswith("UPDATE news_items n SET"):
            cursor.rowcount = 3
        elif sql.startswith("SELECT n.id, n.url"):
            cursor.fetchall.return_value = [{"id": "n-9", "url": "https://www.espn.com/a"}]
        return cursor

    conn.execute.side_effect = execute
    result = NewsImages(fetcher=None, photos=photos).fill(conn, now=NOW)

    assert result == {"fighter_photos_found": 1, "from_fighters": 3, "from_stock": 1}
    cached = [p for sql, p in executed if sql.startswith("INSERT INTO fighter_photos")]
    assert cached[0][:2] == ("f-proch", "https://img/p.jpg")
    assert cached[1] == ("f-x", None, None, None, None, NOW)
    stock = [p for sql, p in executed if sql.startswith("UPDATE news_items SET")][0]
    assert stock[0] == stock_photo("https://www.espn.com/a").url and stock[-1] == "n-9"


def test_fill_survives_wikipedia_errors_and_holds_back_stories_with_unchecked_fighters():
    photos = MagicMock()
    photos.find.side_effect = FetchError("HTTP 403")
    conn = MagicMock()
    executed = []

    def execute(sql, params=None):
        executed.append(sql)
        cursor = MagicMock()
        if sql.startswith("SELECT DISTINCT f.id"):
            cursor.fetchall.return_value = [{"id": "f-proch", "name": "Jiri Prochazka"}]
        elif sql.startswith("UPDATE news_items n SET"):
            cursor.rowcount = 0
        elif sql.startswith("SELECT n.id, n.url"):
            cursor.fetchall.return_value = [{"id": "n-untagged", "url": "https://www.espn.com/a"}]
        return cursor

    conn.execute.side_effect = execute
    result = NewsImages(fetcher=None, photos=photos).fill(conn, now=NOW)

    assert result == {"fighter_photos_found": 0, "from_fighters": 0, "from_stock": 1, "wikipedia_error": "HTTP 403"}
    conn.rollback.assert_called_once()
    assert not any(sql.startswith("INSERT INTO fighter_photos") for sql in executed)
    stock_query = next(sql for sql in executed if sql.startswith("SELECT n.id, n.url"))
    assert "fp.fighter_id IS NULL" in stock_query
