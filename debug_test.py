from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

options = Options()
options.add_argument('--headless=new')
options.add_argument('--no-sandbox')
options.add_argument('--disable-dev-shm-usage')
options.add_argument('--window-size=1920,1080')
options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36')
driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)
driver.get('http://ufcstats.com/statistics/events/completed?page=all')
import time
time.sleep(5)
html = driver.page_source
driver.quit()

if 'Checking your browser' in html:
    print('BLOCKED by Cloudflare')
elif 'b-statistics__table' in html:
    print('PASSED - got real content')
    soup = BeautifulSoup(html, 'lxml')
    trs = soup.find_all('tr')
    classes = set()
    for tr in trs:
        if tr.get('class'):
            classes.add(' '.join(tr.get('class')))
    print('Unique tr classes: {}'.format(sorted(classes)))
    rows = soup.find_all('tr', class_='b-statistics__table-row')
    print('Rows with b-statistics__table-row: {}'.format(len(rows)))
    if rows:
        for row in rows[:2]:
            content = row.find('i', class_='b-statistics__table-content')
            if content:
                a = content.find('a')
                print('  Event: {}'.format(a.text.strip() if a else None))
    else:
        tables = soup.find_all('table')
        print('Tables found: {}'.format(len(tables)))
        for t in tables[:2]:
            print(str(t)[:500])
else:
    print('UNKNOWN - first 1000 chars: {}'.format(html[:1000]))
