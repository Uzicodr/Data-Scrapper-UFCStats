"""Pydantic schemas for UFC data validation."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class FightStats(BaseModel):
    """Per-fight stats from ufcstats.com."""
    fighter_id: str = Field(..., description="Source ID of fighter")
    knockdowns: int = Field(ge=0, description="Total knockdowns")
    sig_strikes_landed: int = Field(ge=0, description="Significant strikes landed")
    sig_strikes_attempted: int = Field(ge=0, description="Significant strikes attempted")
    total_strikes_landed: int = Field(ge=0, description="Total strikes landed")
    total_strikes_attempted: int = Field(ge=0, description="Total strikes attempted")
    takedowns_landed: int = Field(ge=0, description="Takedowns landed")
    takedowns_attempted: int = Field(ge=0, description="Takedowns attempted")
    submission_attempts: int = Field(ge=0, description="Submission attempts")
    reversals: int = Field(ge=0, description="Reversals")
    control_time_seconds: int = Field(ge=0, description="Control time in seconds")


class FightRoundStats(BaseModel):
    """Per-round stats when a source provides them."""
    fighter_id: str
    round_num: int = Field(ge=1)
    knockdowns: int = Field(ge=0)
    sig_strikes_landed: int = Field(ge=0)
    sig_strikes_attempted: int = Field(ge=0)
    total_strikes_landed: int = Field(ge=0)
    total_strikes_attempted: int = Field(ge=0)
    takedowns_landed: int = Field(ge=0)
    takedowns_attempted: int = Field(ge=0)
    submission_attempts: int = Field(ge=0)
    reversals: int = Field(ge=0)
    control_time_seconds: int = Field(ge=0)


class FightResult(BaseModel):
    """Result of a single fight with source reference."""
    source: str = Field(description="Source identifier (e.g., 'ufcstats')")
    source_id: str = Field(description="Source's internal ID")
    event_id: str = Field(description="Event source ID")
    winner_id: Optional[str] = Field(None, description="Winner source ID; None if draw/no-contest")
    method: str = Field(description="win, draw, no_contest, etc.")
    round: int = Field(ge=1, description="Round fight ended")
    time_seconds: int = Field(ge=0, description="Time in round where fight ended")
    stats_red: Optional[FightStats] = None
    stats_blue: Optional[FightStats] = None
    round_stats: list[FightRoundStats] = Field(default_factory=list)


class EventSummary(BaseModel):
    """Minimal event data for queries."""
    source: str
    source_id: str
    name: str
    date: datetime


class Fighter(BaseModel):
    """Fighter profile."""
    source: str
    source_id: str
    name: str
    nickname: Optional[str] = None
    weight_class: Optional[str] = None
    record_wins: Optional[int] = None
    record_losses: Optional[int] = None
    record_draws: Optional[int] = None
