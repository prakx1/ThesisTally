import sys

import pytest

from thesistally.discovery_guard import confined, output_root


def test_results_worker_cannot_overwrite_frozen_or_discovery_files(tmp_path):
    root = output_root(tmp_path, workstream="fundamentals")
    other = output_root(tmp_path)
    paper = tmp_path / "a126-paper-journal"
    paper.write_bytes(b"original")
    blocked = other / "samples"
    code = """
import errno
import sys
from pathlib import Path

import pytest

out, journal, discovery = map(Path, sys.argv[1:])
(out / 'quarter').write_text('ok')
for path in (journal, discovery):
    with pytest.raises(OSError) as error:
        path.write_text('wrong')
    assert error.value.errno in {errno.EACCES, errno.EPERM, errno.EROFS}, error.value
"""
    confined([sys.executable, "-B", "-c", code, str(root), str(paper), str(blocked)], root)
    assert (root / "quarter").read_text() == "ok"
    assert paper.read_bytes() == b"original" and not blocked.exists()
    with pytest.raises(ValueError):
        output_root(tmp_path, workstream="../forward")
