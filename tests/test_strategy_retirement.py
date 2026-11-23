from datetime import date

from test_research import REGIME, calendar, pick

from thesistally import cli
from thesistally.journal import journal


def test_retirement_advances_the_scan_without_creating_new_retired_calls(tmp_path, monkeypatch):
    day = date(2026, 11, 23)
    monkeypatch.setattr(cli, "load_session", lambda *args: (REGIME, [pick()], calendar(day)))
    result = cli.scan_session(tmp_path, day, "retirement-test", None)
    assert result["detected"] == 0
    with journal(tmp_path / "journal.sqlite") as db:
        assert db.execute("SELECT count(*) FROM calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM scans WHERE day=?", (str(day),)).fetchone()[0] == 1
