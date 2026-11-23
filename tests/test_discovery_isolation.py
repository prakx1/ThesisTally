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
import errno
import os
import sqlite3
import sys
from pathlib import Path

import pytest

out, paper, config, tag = map(Path, sys.argv[1:])
write_denials = {errno.EACCES, errno.EPERM, errno.EROFS}
(out / "allowed").write_text("ok")
for path in (paper, config, tag):
    with pytest.raises(OSError) as error:
        path.open("r+b").close()
    assert error.value.errno in write_denials, error.value
with sqlite3.connect(paper) as db:
    with pytest.raises(sqlite3.OperationalError) as error:
        db.execute("insert into fills values(1)")
    assert (error.value.sqlite_errorcode & 0xFF) == sqlite3.SQLITE_READONLY, error.value
hard = out / "hard"
try:
    os.link(config, hard)
except OSError as error:
    # Linux exposes the writable output as a separate bind mount.
    assert error.errno in write_denials | {errno.EXDEV}, error
else:
    with pytest.raises(OSError) as error:
        hard.write_text("escape")
    assert error.value.errno in write_denials, error.value
alias = out / "alias"
alias.symlink_to(config)
with pytest.raises(OSError) as error:
    alias.write_text("escape")
assert error.value.errno in write_denials, error.value
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
