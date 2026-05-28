from bs4 import BeautifulSoup
from updatedb import store_upcoming_events
from ufcstats_client import fetch_ufcstats_page

html = fetch_ufcstats_page('http://ufcstats.com/statistics/events/upcoming?page=all')
soup = BeautifulSoup(html, 'lxml')

rows = soup.find_all('tr', class_='b-statistics__table-row')
updated_count = 0

for row in rows:
    content = row.find('i', class_='b-statistics__table-content')
    location_td = row.find(
        'td',
        class_='b-statistics__table-col b-statistics__table-col_style_big-top-padding'
    )
    if not content or not location_td:
        continue

    event_name = content.find('a').text.strip()
    event_date = content.find('span').text.strip()
    event_location = location_td.text.strip()

    entry = {
        'event_name' : event_name,
        'event_date' : event_date,
        'event_location' : event_location
    }

    store_upcoming_events(entry)
    updated_count += 1

print(f"Upcoming events updated: {updated_count}")
