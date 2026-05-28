from bs4 import BeautifulSoup
from updatedb import store_fighter
from ufcstats_client import fetch_ufcstats_page
import string


STAT_LABELS = {
    "DOB": "dob",
    "SLpM": "slpm",
    "Str. Acc.": "striking_accuracy",
    "SApM": "sapm",
    "Str. Def": "striking_defense",
    "TD Avg.": "td_avg",
    "TD Acc.": "td_accuracy",
    "TD Def.": "td_defense",
    "Sub. Avg.": "submission_avg",
}


def parse_fighter_detail_stats(profile_link):
    if not profile_link:
        return {}

    html = fetch_ufcstats_page(profile_link)
    soup = BeautifulSoup(html, 'lxml')
    stats = {}

    for item in soup.select('.b-list__box-list-item'):
        label = item.find('i', class_='b-list__box-item-title')
        if not label:
            continue

        label_text = label.get_text(' ', strip=True).rstrip(':')
        key = STAT_LABELS.get(label_text)
        if not key:
            continue

        value = item.get_text(' ', strip=True).replace(label.get_text(' ', strip=True), '', 1)
        stats[key] = value.strip()

    return stats


def scrape_fighters():
    characters = list(string.ascii_lowercase)
    total_fighters = 0

    for char in characters:
        url = f'http://ufcstats.com/statistics/fighters?char={char}&page=all'
        print(f"\nFetching fighters starting with '{char.upper()}'...")

        try:
            html = fetch_ufcstats_page(url)
            soup = BeautifulSoup(html, 'lxml')

            rows = soup.find_all('tr', class_='b-statistics__table-row')

            print(f"Found {len(rows)} fighters starting with '{char.upper()}'")

            for row in rows:
                try:
                    cells = row.find_all('td', class_='b-statistics__table-col')

                    if len(cells) < 8:
                        continue

                    first_name = cells[0].text.strip()
                    last_name = cells[1].text.strip()
                    nickname = cells[2].text.strip()
                    height = cells[3].text.strip()
                    weight = cells[4].text.strip()
                    reach = cells[5].text.strip()
                    stance = cells[6].text.strip()
                    wins = cells[7].text.strip()
                    losses = cells[8].text.strip() if len(cells) > 8 else "--"
                    draws = cells[9].text.strip() if len(cells) > 9 else "--"

                    fighter_link = ""
                    link_tag = row.find('a')
                    if link_tag and link_tag.get('href'):
                        fighter_link = link_tag.get('href')

                    entry = {
                        'first_name': first_name,
                        'last_name': last_name,
                        'nickname': nickname,
                        'height': height,
                        'weight': weight,
                        'reach': reach,
                        'stance': stance,
                        'wins': wins,
                        'losses': losses,
                        'draws': draws,
                        'profile_link': fighter_link
                    }
                    entry.update(parse_fighter_detail_stats(fighter_link))

                    store_fighter(entry)
                    total_fighters += 1

                except Exception as e:
                    print(f"Error processing row: {e}")
                    continue

        except Exception as e:
            print(f"Error fetching character '{char}': {e}")
            continue

    print(f"\n\nFighter profile scraping completed!")
    print(f"Total fighters stored: {total_fighters}")


if __name__ == "__main__":
    scrape_fighters()
