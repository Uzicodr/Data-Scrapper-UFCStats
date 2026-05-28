from bs4 import BeautifulSoup
from updatedb import store_fighter
from ufcstats_client import fetch_ufcstats_page
import string

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
                
                first_cell = cells[0]
                second_cell = cells[1]
                
                first_name = first_cell.text.strip()
                last_name = second_cell.text.strip()
                
                nickname_cell = cells[2]
                nickname = nickname_cell.text.strip()
                
                height_cell = cells[3]
                height = height_cell.text.strip()
                
                weight_cell = cells[4]
                weight = weight_cell.text.strip()
                
                reach_cell = cells[5]
                reach = reach_cell.text.strip()
                
                stance_cell = cells[6]
                stance = stance_cell.text.strip()
                
                wins_cell = cells[7]
                wins = wins_cell.text.strip()
                
                losses_cell = cells[8] if len(cells) > 8 else None
                losses = losses_cell.text.strip() if losses_cell else "--"
                
                draws_cell = cells[9] if len(cells) > 9 else None
                draws = draws_cell.text.strip() if draws_cell else "--"
                
                # Try to get fighter profile link
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
