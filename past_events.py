from bs4 import BeautifulSoup
import requests
from updatedb import store_past_events

webpage = requests.get('http://ufcstats.com/statistics/events/completed?page=all')
soup = BeautifulSoup(webpage.text, 'lxml')

rows = soup.find_all('tr', class_='b-statistics__table-row')

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

    store_past_events(entry)