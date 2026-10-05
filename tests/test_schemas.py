from ufc_agent.schemas.models import FightStats, FightResult, FightRoundStats


def test_fight_stats_validation():
    stats = FightStats(
        fighter_id="ufc123",
        knockdowns=2,
        sig_strikes_landed=45,
        sig_strikes_attempted=120,
        total_strikes_landed=80,
        total_strikes_attempted=200,
        takedowns_landed=3,
        takedowns_attempted=5,
        submission_attempts=1,
        reversals=0,
        control_time_seconds=180,
    )
    assert stats.knockdowns == 2
    assert stats.sig_strikes_landed == 45


def test_fight_stats_rejects_negative():
    try:
        FightStats(
            fighter_id="ufc123",
            knockdowns=-1,  # Invalid
            sig_strikes_landed=45,
            sig_strikes_attempted=120,
            total_strikes_landed=80,
            total_strikes_attempted=200,
            takedowns_landed=0,
            takedowns_attempted=0,
            submission_attempts=0,
            reversals=0,
            control_time_seconds=0,
        )
        assert False, "Should reject negative knockdowns"
    except ValueError:
        pass


def test_fight_result_validation():
    result = FightResult(
        source="ufcstats",
        source_id="123",
        event_id="ufc-456",
        winner_id="ufc789",
        method="win",
        round=2,
        time_seconds=145,
    )
    assert result.method == "win"
    assert result.round == 2
