"""Published exchange calendars available on the signal date."""

import json
from datetime import timedelta
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=96)
def schedule(folder, year):
    path = folder / f"{year}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def holding_horizon(root, day, sessions):
    packages = Path(__file__).with_name("calendars")
    cursor = day
    for _ in range(sessions * 5 + 30):
        cursor += timedelta(days=1)
        cal = schedule(root / "calendars", cursor.year) or schedule(packages, cursor.year)
        if not cal or cal["issued"] > str(day):
            return None
        if str(cursor) in cal["special_sessions"] or (
            cursor.weekday() < 5 and str(cursor) not in cal["holidays"]
        ):
            sessions -= 1
            if not sessions:
                return (cursor - day).days
    return None
