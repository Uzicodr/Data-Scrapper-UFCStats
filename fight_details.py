from bs4 import BeautifulSoup

from ufcstats_client import fetch_ufcstats_page


def clean_text(value):
    return " ".join(value.get_text(" ", strip=True).split()) if value else ""


def cell_lines(cell):
    return [clean_text(line) for line in cell.find_all("p") if clean_text(line)]


def pair_values(values):
    return {
        "red": values[0] if len(values) > 0 else "",
        "blue": values[1] if len(values) > 1 else "",
    }


def parse_fight_row(row, fight_order):
    cols = row.find_all("td")
    if len(cols) < 10:
        return None

    fighter_links = cols[1].find_all("a")
    fighters = [clean_text(link) for link in fighter_links]
    fighter_profile_links = [link.get("href", "") for link in fighter_links]
    if len(fighters) < 2:
        return None

    result = clean_text(cols[0]).lower()
    winner = fighters[0] if result == "win" else ""

    kd_values = cell_lines(cols[2])
    str_values = cell_lines(cols[3])
    td_values = cell_lines(cols[4])
    sub_values = cell_lines(cols[5])
    method_lines = cell_lines(cols[7])
    image_sources = [img.get("src", "") for img in row.find_all("img")]

    return {
        "fight_order": fight_order,
        "fight_detail_link": row.get("data-link", ""),
        "fighters": fighters,
        "fighter_red": fighters[0],
        "fighter_blue": fighters[1],
        "fighter_profile_links": fighter_profile_links,
        "winner": winner,
        "kd": pair_values(kd_values),
        "str": pair_values(str_values),
        "td": pair_values(td_values),
        "sub": pair_values(sub_values),
        "fighter_stats": [
            {
                "fighter_name": fighters[0],
                "corner": "red",
                "kd": kd_values[0] if len(kd_values) > 0 else "",
                "str": str_values[0] if len(str_values) > 0 else "",
                "td": td_values[0] if len(td_values) > 0 else "",
                "sub": sub_values[0] if len(sub_values) > 0 else "",
            },
            {
                "fighter_name": fighters[1],
                "corner": "blue",
                "kd": kd_values[1] if len(kd_values) > 1 else "",
                "str": str_values[1] if len(str_values) > 1 else "",
                "td": td_values[1] if len(td_values) > 1 else "",
                "sub": sub_values[1] if len(sub_values) > 1 else "",
            },
        ],
        "weight_class": clean_text(cols[6]),
        "method": method_lines[0] if len(method_lines) > 0 else "",
        "method_details": method_lines[1] if len(method_lines) > 1 else "",
        "round": clean_text(cols[8]),
        "time": clean_text(cols[9]),
        "is_championship_fight": any("belt.png" in src for src in image_sources),
    }


def fetch_event_fights(event_url):
    if not event_url:
        return []

    html = fetch_ufcstats_page(event_url)
    soup = BeautifulSoup(html, "lxml")
    rows = [
        row
        for row in soup.find_all("tr", class_="b-fight-details__table-row")
        if row.find_all("td")
    ]

    fights = []
    for index, row in enumerate(rows, start=1):
        fight = parse_fight_row(row, index)
        if fight:
            fights.append(fight)

    return fights
