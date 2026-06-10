from bs4 import BeautifulSoup
from fight_details import fetch_event_fights
from updatedb import prune_upcoming_events, store_upcoming_events
from ufcstats_client import fetch_ufcstats_page

html = fetch_ufcstats_page('http://ufcstats.com/statistics/events/upcoming?page=all')
soup = BeautifulSoup(html, 'lxml')

rows = soup.find_all('tr', class_='b-statistics__table-row')
updated_count = 0
scraped_events = []

for row in rows:
    content = row.find('i', class_='b-statistics__table-content')
    location_td = row.find(
        'td',
        class_='b-statistics__table-col b-statistics__table-col_style_big-top-padding'
    )
    if not content or not location_td:
        continue

    event_link = content.find('a').get('href', '')
    event_name = content.find('a').text.strip()
    event_date = content.find('span').text.strip()
    event_location = location_td.text.strip()
    try:
        fights = fetch_event_fights(event_link)
    except Exception as exc:
        print(f"Could not fetch fights for {event_name}: {exc}")
        fights = []

    entry = {
        'event_name' : event_name,
        'event_date' : event_date,
        'event_location' : event_location,
        'event_link': event_link,
        'fights': fights
    }

    store_upcoming_events(entry)
    scraped_events.append(entry)
    updated_count += 1

prune_upcoming_events(scraped_events)
print(f"Upcoming events updated: {updated_count}")
