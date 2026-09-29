from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from loupe.config import Settings, get_settings
from loupe.db import get_db
from loupe.models import Repository
from loupe.timeutil import ensure_utc, utcnow

OWNER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$"
NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,100}$"

OwnerParam = Annotated[str, Path(pattern=OWNER_PATTERN, description="GitHub owner/org login")]
NameParam = Annotated[str, Path(pattern=NAME_PATTERN, description="Repository name")]
DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_repo(owner: OwnerParam, name: NameParam, db: DbDep, source: str = Query("github", pattern=r"^[a-z]{1,16}$")) -> Repository:
    repo = db.scalar(select(Repository).where(Repository.source == source, Repository.owner == owner, Repository.name == name))
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{source}:{owner}/{name} is not tracked. POST /api/v1/repos to add it.")
    return repo


RepoDep = Annotated[Repository, Depends(get_repo)]


class TimeWindow:
    """Validated [start, end) window. Defaults to the trailing 30 days ending now.
    Dates are inclusive on the calendar day for `to`, so `to=2026-03-31` covers March 31."""

    def __init__(
        self,
        settings: SettingsDep,
        from_: Annotated[datetime | None, Query(alias="from", description="ISO date/datetime, inclusive")] = None,
        to: Annotated[datetime | None, Query(description="ISO date/datetime, inclusive of that day when a bare date is given")] = None,
    ):
        now = utcnow()
        end = ensure_utc(to) if to else now
        if to is not None and to.hour == 0 and to.minute == 0 and to.second == 0:
            end = end + timedelta(days=1)
        start = ensure_utc(from_) if from_ else end - timedelta(days=30)
        if start >= end:
            raise HTTPException(422, detail="'from' must be before 'to'")
        if end - start > timedelta(days=settings.max_window_days):
            raise HTTPException(422, detail=f"window may not exceed {settings.max_window_days} days")
        if start > now:
            raise HTTPException(422, detail="'from' is in the future")
        self.start = start
        self.end = min(end, now)


WindowDep = Annotated[TimeWindow, Depends(TimeWindow)]
