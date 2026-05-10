import requests
from bs4 import BeautifulSoup

def fetch_fight_card(event_url):
    response = requests.get(event_url)
    soup = BeautifulSoup(response.text, "lxml")

    fights = soup.find_all("tr", class_="b-fight-details__table-row")

    print("Fight Card:")
    for fight in fights:
        cols = fight.find_all("td")
        if len(cols) < 7:
            continue

        # fighters
        red = cols[1].find("p", class_="b-fight-details__table-text_red")
        blue = cols[1].find("p", class_="b-fight-details__table-text_blue")

        if not red or not blue:
            continue

        fighter_red = red.text.strip()
        fighter_blue = blue.text.strip()

        # weight class
        weight_class = cols[6].text.strip()

        print(f"  {fighter_red} vs {fighter_blue}  ({weight_class})")
