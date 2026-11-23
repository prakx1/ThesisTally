import os
import sqlite3
import sys

import pytest

from thesistally.discovery_guard import confined, output_root


def test_worker_cannot_write_to_paper_journal_frozen_config_or_tag(tmp_path):
    repo = tmp_path / "research"
    repo.mkdir()
    owned = output_root(repo)
    paper = tmp_path / "paper-data/forward/a126-forward-2026-11-21/journal.sqlite"
    paper.parent.mkdir(parents=True)
    with sqlite3.connect(paper) as db:
        db.execute("create table fills(id integer)")
    config = repo / "src/thesistally/revisions/a126-forward.json"
    tag = tmp_path / ".git/refs/tags/a126-forward-2026-11-21"
    for path in (config, tag):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"frozen")
    worker = """
import os, sqlite3, sys
from pathlib import Path
out, paper, config, tag = map(Path, sys.argv[1:])
(out / "allowed").write_text("ok")
for p in [paper, config, tag]:
    try: p.open('r+b').close()
    except PermissionError: pass
    else: raise AssertionError(p)
try:
    with sqlite3.connect(paper) as db: db.execute('insert into fills values(1)')
except sqlite3.OperationalError: pass
else: raise AssertionError('SQL write escaped')
try:
    os.link(config, out/'hard')
    (out/'hard').write_text('escape')
except PermissionError: pass
else: raise AssertionError('hardlink escaped')
(out/'alias').symlink_to(config)
try: (out/'alias').write_text('escape')
except PermissionError: pass
else: raise AssertionError('symlink escaped')
"""
    result = confined(
        [sys.executable, "-B", "-c", worker, *map(str, [owned, paper, config, tag])],
        owned,
        cwd=repo,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert config.read_bytes() == tag.read_bytes() == b"frozen"
    assert (owned / "allowed").read_text() == "ok"
    with sqlite3.connect(f"{paper.as_uri()}?mode=ro&immutable=1", uri=True) as db:
        assert db.execute("select count(*) from fills").fetchone()[0] == 0


def test_existing_aliases_fail_before_worker_launch(tmp_path):
    owned = output_root(tmp_path)
    protected = tmp_path / "journal.sqlite"
    protected.write_bytes(b"test")
    os.link(protected, owned / "alias")
    with pytest.raises(ValueError, match="independent"):
        output_root(tmp_path)
    (owned / "alias").unlink()
    owned.rmdir()
    owned.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="symlink"):
        output_root(tmp_path)
