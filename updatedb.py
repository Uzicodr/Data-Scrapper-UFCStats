try:
    from pymongo import MongoClient
except ImportError as e:
    raise ImportError("pymongo is required. Install it with 'pip install pymongo'.") from e
import os
import datetime
from dotenv import load_dotenv
load_dotenv()

def get_mongodb_uri():
    uri = os.getenv("MONGODB_CONNECTION_STRING") or os.getenv("MONGODB_URI")
    if uri and uri.startswith("MONGODB_URI="):
        uri = uri.split("=", 1)[1]

    if not uri:
        raise RuntimeError(
            "Missing MongoDB connection string. Set MONGODB_CONNECTION_STRING "
            "or MONGODB_URI in your .env file."
        )

    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise RuntimeError(
            "Invalid MongoDB connection string. It must start with "
            "mongodb:// or mongodb+srv://."
        )

    return uri


client = MongoClient(get_mongodb_uri())
db = client['MMADatabase']

rankings_collection = db['rankings']
upcomingevents_collection = db['upcomingevents']
pastevents_collection = db['pastevents']
fighterlogs_collection = db['fighterlogs']

def store_ranking(category, champion, fighters, gender):
    rankings_collection.update_one(
    {"category": category, "gender": gender},
    {"$set": {
        "category": category,
        "champion": champion,
        "fighters": fighters,
        "gender": gender,
        "last_updated": datetime.datetime.today().strftime('%Y-%m-%d')
    }},
    upsert=True
)
    print('Ranking Updated')
    
def store_upcoming_events(event):

    upcomingevents_collection.update_one(
    {"event_name": event['event_name'], "event_date": event['event_date']},
    {"$set": {
        "event_name": event['event_name'],
        "event_date": event['event_date'],
        "event_location": event['event_location'],
        "event_link": event.get('event_link', ''),
        "fights": event.get('fights', []),
        "last_updated": datetime.datetime.today().strftime('%Y-%m-%d')
    }},
    upsert=True
)
    print('Upcoming Event Updated')
    
def store_past_events(event):
    pastevents_collection.update_one(
    {"event_name": event['event_name'], "event_date": event['event_date']},
    {"$set": {
        "event_name": event['event_name'],
        "event_date": event['event_date'],
        "event_location": event['event_location'],
        "event_link": event.get('event_link', ''),
        "fights": event.get('fights', []),
        "last_updated": datetime.datetime.today().strftime('%Y-%m-%d')
    }},
    upsert=True
)
    print('Past Event Updated')

def store_fighter(fighter):
    fighterlogs_collection.update_one(
    {"first_name": fighter['first_name'], "last_name": fighter['last_name']},
    {"$set": {
        "first_name": fighter['first_name'],
        "last_name": fighter['last_name'],
        "nickname": fighter['nickname'],
        "height": fighter['height'],
        "weight": fighter['weight'],
        "reach": fighter['reach'],
        "stance": fighter['stance'],
        "wins": fighter['wins'],
        "losses": fighter['losses'],
        "draws": fighter['draws'],
        "profile_link": fighter['profile_link'],
        "dob": fighter.get('dob', ''),
        "slpm": fighter.get('slpm', ''),
        "striking_accuracy": fighter.get('striking_accuracy', ''),
        "sapm": fighter.get('sapm', ''),
        "striking_defense": fighter.get('striking_defense', ''),
        "td_avg": fighter.get('td_avg', ''),
        "td_accuracy": fighter.get('td_accuracy', ''),
        "td_defense": fighter.get('td_defense', ''),
        "submission_avg": fighter.get('submission_avg', ''),
        "last_updated": datetime.datetime.today().strftime('%Y-%m-%d')
    }},
    upsert=True
)
    print('Fighter Updated')


def run_all():
    # import fighter_profile
    # fighter_profile.scrape_fighters()
    import rankings
    import upcoming_events
    import past_events

if __name__ == "__main__":
    print("=== Running all UFC data updates ===")
    run_all()
    print("\n=== All updates completed ===")
