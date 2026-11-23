import os
import sys

import pytest

from thesistally.discovery_guard import confined, output_root
from thesistally.fundamental_sources import run, worker_command


def test_public_collectors_still_cannot_modify_paper_and_database(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").write_text("gitdir: ../test")
    out = output_root(work, workstream="fundamentals")
    paper = tmp_path / "frozen-journal"
    paper.write_text("protected")
    package = work / "thesistally"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "fundamental_sources.py").write_text("""
from pathlib import Path
import os
root=Path(os.environ["TEST_ROOT"])
(root/"work/data/fundamentals/request").write_text("ok")
try:
    (root/"frozen-journal").write_text("bad")
except PermissionError:
    pass
else:
    raise ValueError("escaped collector boundary")
""")
    command = worker_command("structured")
    confined(
        command,
        out,
        cwd=work,
        public_collector="structured",
        env={
            **os.environ,
            "PYTHONPATH": str(work),
            "TEST_ROOT": str(tmp_path),
            "TMPDIR": str(out),
        },
    )
    assert paper.read_text() == "protected" and (out / "request").read_text() == "ok"
    with pytest.raises(ValueError, match="Public networking"):
        confined([sys.executable, "-c", "pass"], out, cwd=work, public_collector="structured")
    with pytest.raises(ValueError, match="Public networking"):
        confined(command, output_root(work), cwd=work, public_collector="structured")
    with pytest.raises(ValueError, match="Unknown"):
        run("arbitrary_module")
