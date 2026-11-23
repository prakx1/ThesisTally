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
from pathlib import Path
import sys
out, journal, discovery = map(Path, sys.argv[1:])
(out / 'quarter').write_text('ok')
for path in (journal, discovery):
    try:
        path.write_text('wrong')
    except PermissionError:
        continue
    raise ValueError(f'escaped: {path}')
"""
    confined([sys.executable, "-B", "-c", code, str(root), str(paper), str(blocked)], root)
    assert paper.read_bytes() == b"original" and not blocked.exists()
    with pytest.raises(ValueError):
        output_root(tmp_path, workstream="../forward")
