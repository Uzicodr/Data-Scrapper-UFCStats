import datetime

import pytest

from ufc_agent.normalize import (
    clean,
    fight_round,
    fight_time,
    height_to_inches,
    name_key,
    parse_date,
    percent_to_fraction,
    to_float,
    to_int,
    ufcstats_id,
)


@pytest.mark.parametrize("value", [None, "", "  ", "--", "---", "N/A"])
def test_clean_missing(value):
    assert clean(value) is None


def test_clean_collapses_whitespace():
    assert clean("  Las  Vegas,\n Nevada ") == "Las Vegas, Nevada"


def test_numbers():
    assert to_int("12") == 12
    assert to_int("45 of 90") == 45
    assert to_int("--") is None
    assert to_float("155 lbs.") == 155.0
    assert to_float('72.0"') == 72.0
    assert to_float("4.50") == 4.5


def test_percent_to_fraction():
    assert percent_to_fraction("45%") == 0.45
    assert percent_to_fraction("0%") == 0.0
    assert percent_to_fraction("--") is None


def test_height_to_inches():
    assert height_to_inches("5' 11\"") == 71.0
    assert height_to_inches("6' 0\"") == 72.0
    assert height_to_inches("--") is None


def test_parse_date():
    assert parse_date("Jul 22, 1989") == datetime.date(1989, 7, 22)
    assert parse_date("October 11, 2025") == datetime.date(2025, 10, 11)
    assert parse_date("2026-10-05") == datetime.date(2026, 10, 5)
    assert parse_date("soon") is None


def test_fight_round_and_time():
    assert fight_round("3") == 3
    assert fight_round("") is None
    assert fight_round("9") is None
    assert fight_time("4:32") == "4:32"
    assert fight_time("") is None


def test_ufcstats_id():
    assert ufcstats_id("http://ufcstats.com/fighter-details/93fe7332d16c6ad9") == "93fe7332d16c6ad9"
    assert ufcstats_id("http://ufcstats.com/event-details/abc/") == "abc"
    assert ufcstats_id("") is None


def test_name_key():
    assert name_key("José Aldo") == "jose aldo"
    assert name_key("Georges St-Pierre") == "georges st pierre"
    assert name_key("  Alex  PEREIRA ") == "alex pereira"


def test_name_key_transliterates_letters_without_accent_marks():
    assert name_key("Jan Błachowicz") == "jan blachowicz"
