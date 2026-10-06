"""Pydantic schemas for UFC data validation."""
import re
from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ufc_agent.normalize import clean, height_to_inches, name_key, parse_date, to_float

WEIGHT_DIVISIONS = (
    "Flyweight", "Bantamweight", "Featherweight", "Lightweight", "Welterweight", "Middleweight",
    "Light Heavyweight", "Heavyweight", "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
)
POUND_FOR_POUND = ("Men's Pound-for-Pound", "Women's Pound-for-Pound")
DIVISIONS = POUND_FOR_POUND + WEIGHT_DIVISIONS
RANKED_PER_DIVISION = 15


class RankedFighter(BaseModel):
    """One name on a rankings list, as printed by the source."""
    name: str = Field(min_length=1)
    fighter_id: Optional[str] = Field(None, description="fighters.id; set only to settle a name the tool could not match")


class Rankings(BaseModel):
    """A full rankings list for one division. ranked[0] is rank 1."""
    division: str
    champion: Optional[RankedFighter] = None
    ranked: list[RankedFighter]
    sources: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_list(self):
        errors = []
        if self.division not in DIVISIONS:
            errors.append(f"Unknown division {self.division!r}; expected one of {list(DIVISIONS)}")
        if self.division in POUND_FOR_POUND and self.champion:
            errors.append("Pound-for-pound lists have no champion")
        if len(self.ranked) != RANKED_PER_DIVISION:
            errors.append(f"Expected {RANKED_PER_DIVISION} ranked fighters, got {len(self.ranked)}")
        names = [entry.name for entry in ([self.champion] if self.champion else []) + self.ranked]
        keys = [name_key(name) for name in names]
        duplicates = sorted({name for name, key in zip(names, keys) if keys.count(key) > 1})
        if duplicates:
            errors.append(f"Names appear more than once: {duplicates}")
        bad_sources = [s for s in self.sources if not s.startswith(("http://", "https://"))]
        if bad_sources:
            errors.append(f"Sources must be URLs: {bad_sources}")
        if errors:
            raise ValueError("; ".join(errors))
        return self


# ---------------------------------------------------------------------------
# Upcoming cards (sync_upcoming)
# ---------------------------------------------------------------------------
UFCSTATS_EVENT_URL = re.compile(r"^https?://(www\.)?ufcstats\.com/event-details/[0-9a-f]+/?$")
UFCSTATS_FIGHT_URL = re.compile(r"^https?://(www\.)?ufcstats\.com/fight-details/[0-9a-f]+/?$")
UFCSTATS_FIGHTER_URL = re.compile(r"^https?://(www\.)?ufcstats\.com/fighter-details/[0-9a-f]+/?$")


class Bout(BaseModel):
    """One scheduled bout as listed on the event page. Red corner is the first fighter listed."""
    red_name: str = Field(min_length=1)
    blue_name: str = Field(min_length=1)
    red_url: Optional[str] = None
    blue_url: Optional[str] = None
    weight_class: Optional[str] = None
    is_title_fight: bool = False
    fight_url: Optional[str] = None


class EventCard(BaseModel):
    """A full upcoming card. bouts[0] is the main event."""
    event_url: str
    name: str = Field(min_length=1)
    date: date
    location: Optional[str] = None
    bouts: list[Bout] = Field(min_length=1)
    sources: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_card(self):
        errors = []
        if not UFCSTATS_EVENT_URL.match(self.event_url):
            errors.append(f"event_url must be a ufcstats.com/event-details/ URL, got {self.event_url!r}")
        if self.date < date.today() - timedelta(days=1):
            errors.append(f"date {self.date} is in the past; only upcoming events belong here")
        for number, bout in enumerate(self.bouts, start=1):
            if name_key(bout.red_name) == name_key(bout.blue_name):
                errors.append(f"bout {number}: both corners are {bout.red_name!r}")
            for field, pattern in (("red_url", UFCSTATS_FIGHTER_URL), ("blue_url", UFCSTATS_FIGHTER_URL),
                                   ("fight_url", UFCSTATS_FIGHT_URL)):
                value = getattr(bout, field)
                if value and not pattern.match(value):
                    errors.append(f"bout {number}: {field} {value!r} is not a ufcstats {field.split('_')[0]} URL")
        names = [name_key(n) for bout in self.bouts for n in (bout.red_name, bout.blue_name)]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            errors.append(f"Fighters listed in more than one bout: {repeated}")
        bad_sources = [s for s in self.sources if not s.startswith(("http://", "https://"))]
        if bad_sources:
            errors.append(f"Sources must be URLs: {bad_sources}")
        if errors:
            raise ValueError("; ".join(errors))
        return self


# ---------------------------------------------------------------------------
# Fighter profiles (refresh_fighters)
# ---------------------------------------------------------------------------
RECORD_RE = re.compile(r"^(\d+)-(\d+)-(\d+)(?:\s*\((\d+)\s*NC\))?$")
PERCENT_RE = re.compile(r"^\d{1,3}%$")
STANCES = {"Orthodox", "Southpaw", "Switch", "Open Stance", "Sideways"}
MISSING = "--"


class FighterProfile(BaseModel):
    """A fighter's ufcstats.com profile, every value copied exactly as the page prints it ('--' when blank)."""
    fighter_id: str
    profile_url: str
    name_on_page: str = Field(min_length=1)
    nickname: Optional[str] = None
    record: str = Field(description="e.g. '27-7-0' or '27-7-0 (1 NC)'")
    height: str = MISSING
    weight: str = MISSING
    reach: str = MISSING
    stance: str = MISSING
    dob: str = MISSING
    slpm: str = MISSING
    str_acc: str = MISSING
    sapm: str = MISSING
    str_def: str = MISSING
    td_avg: str = MISSING
    td_acc: str = MISSING
    td_def: str = MISSING
    sub_avg: str = MISSING

    @model_validator(mode="after")
    def check_values(self):
        errors = []
        if not UFCSTATS_FIGHTER_URL.match(self.profile_url):
            errors.append(f"profile_url must be a ufcstats.com/fighter-details/ URL, got {self.profile_url!r}")
        if not RECORD_RE.match(self.record.strip()):
            errors.append(f"record {self.record!r} must look like '27-7-0' or '27-7-0 (1 NC)'")
        if clean(self.height) and not 48 <= (height_to_inches(self.height) or 0) <= 96:
            errors.append(f"height {self.height!r} is not a height like 6' 2\"")
        if clean(self.reach) and not 48 <= (to_float(self.reach) or 0) <= 100:
            errors.append(f"reach {self.reach!r} is not a reach in inches")
        if clean(self.weight) and not 100 <= (to_float(self.weight) or 0) <= 400:
            errors.append(f"weight {self.weight!r} is not a weight in lbs")
        if clean(self.stance) and self.stance not in STANCES:
            errors.append(f"stance {self.stance!r} must be one of {sorted(STANCES)}")
        if clean(self.dob):
            born = parse_date(self.dob)
            if born is None or not date(1940, 1, 1) <= born <= date.today() - timedelta(days=16 * 365):
                errors.append(f"dob {self.dob!r} must be a date like 'Dec 28, 1995'")
        for field in ("str_acc", "str_def", "td_acc", "td_def"):
            value = getattr(self, field)
            if clean(value) and not (PERCENT_RE.match(value) and int(value[:-1]) <= 100):
                errors.append(f"{field} {value!r} must be a percentage like '52%'")
        for field in ("slpm", "sapm", "td_avg", "sub_avg"):
            value = getattr(self, field)
            if clean(value) and (to_float(value) is None or not 0 <= to_float(value) <= 50):
                errors.append(f"{field} {value!r} must be a number like '3.75'")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def record_counts(self):
        """(wins, losses, draws, no_contests)"""
        match = RECORD_RE.match(self.record.strip())
        wins, losses, draws, no_contests = match.groups()
        return int(wins), int(losses), int(draws), int(no_contests or 0)


# ---------------------------------------------------------------------------
# Completed event results (post_event_stats)
# ---------------------------------------------------------------------------
METHODS = ("KO/TKO", "SUB", "U-DEC", "S-DEC", "M-DEC", "DQ", "CNC", "Overturned", "Other")
TIME_RE = re.compile(r"^([0-5]?\d):([0-5]\d)$")


class FighterLine(BaseModel):
    """One fighter's row values on a completed event page."""
    name: str = Field(min_length=1)
    url: str
    kd: int = Field(ge=0, le=20, description="Kd column")
    sig_str: int = Field(ge=0, le=600, description="Str column (significant strikes landed)")
    td: int = Field(ge=0, le=40, description="Td column (takedowns landed)")
    sub: int = Field(ge=0, le=40, description="Sub column (submission attempts)")


class FightOutcome(BaseModel):
    """One finished fight. fighters are in page order; with outcome 'win' the first one won."""
    fight_url: str
    outcome: Literal["win", "draw", "nc"]
    fighters: list[FighterLine] = Field(min_length=2, max_length=2)
    weight_class: Optional[str] = None
    is_title_fight: bool = False
    method: str
    method_details: Optional[str] = None
    round: int = Field(ge=1, le=5)
    time: str


class EventResults(BaseModel):
    """Every fight of one completed event, in page order."""
    event_url: str
    fights: list[FightOutcome] = Field(min_length=1)
    sources: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_results(self):
        errors = []
        if not UFCSTATS_EVENT_URL.match(self.event_url):
            errors.append(f"event_url must be a ufcstats.com/event-details/ URL, got {self.event_url!r}")
        seen_fights, seen_fighters = set(), set()
        for number, fight in enumerate(self.fights, start=1):
            label = f"fight {number}"
            if not UFCSTATS_FIGHT_URL.match(fight.fight_url):
                errors.append(f"{label}: fight_url {fight.fight_url!r} is not a ufcstats fight-details URL")
            if fight.fight_url in seen_fights:
                errors.append(f"{label}: fight_url listed twice")
            seen_fights.add(fight.fight_url)
            for line in fight.fighters:
                if not UFCSTATS_FIGHTER_URL.match(line.url):
                    errors.append(f"{label}: {line.name} url {line.url!r} is not a ufcstats fighter-details URL")
                if line.url in seen_fighters:
                    errors.append(f"{label}: {line.name} appears in more than one fight")
                seen_fighters.add(line.url)
            if fight.fighters[0].url == fight.fighters[1].url:
                errors.append(f"{label}: both fighters have the same url")
            if fight.method not in METHODS:
                errors.append(f"{label}: method {fight.method!r} must be one of {list(METHODS)}; "
                              "put the rest in method_details")
            match = TIME_RE.match(fight.time.strip())
            if not match or int(match.group(1)) * 60 + int(match.group(2)) > 300:
                errors.append(f"{label}: time {fight.time!r} must be m:ss, at most 5:00")
            elif fight.method.endswith("-DEC") and fight.time.strip() != "5:00":
                errors.append(f"{label}: a decision ends at 5:00, got {fight.time!r}")
            if fight.weight_class and "[img" in fight.weight_class:
                errors.append(f"{label}: weight_class must be plain text without [img:...] marks")
        bad_sources = [s for s in self.sources if not s.startswith(("http://", "https://"))]
        if bad_sources:
            errors.append(f"Sources must be URLs: {bad_sources}")
        if errors:
            raise ValueError("; ".join(errors))
        return self
