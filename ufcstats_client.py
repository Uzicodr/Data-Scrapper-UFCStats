import hashlib
import re

import requests


CHALLENGE_RE = re.compile(
    r'var nonce="(?P<nonce>[^"]+)".*?target=new Array\((?P<difficulty>\d+)\+1\)',
    re.S,
)


def fetch_ufcstats_page(url):
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            )
        }
    )

    response = session.get(url, timeout=20)
    response.raise_for_status()

    if "Checking your browser" in response.text:
        solve_browser_challenge(session, response.text, url)
        response = session.get(url, timeout=20)
        response.raise_for_status()

    return response.text


def solve_browser_challenge(session, html, url):
    match = CHALLENGE_RE.search(html)
    if not match:
        raise RuntimeError("UFCStats returned a browser check that could not be solved.")

    nonce = match.group("nonce")
    difficulty = int(match.group("difficulty"))
    prefix = "0" * difficulty
    n = 0

    while True:
        digest = hashlib.sha256(f"{nonce}:{n}".encode()).hexdigest()
        if digest.startswith(prefix):
            break
        n += 1

    challenge_url = requests.compat.urljoin(url, "/__c")
    response = session.post(
        challenge_url,
        data={"nonce": nonce, "n": n},
        timeout=20,
    )
    response.raise_for_status()
