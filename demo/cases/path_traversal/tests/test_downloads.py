from pathlib import Path

import pytest

from downloads import resolve_download_path


def test_child_path_is_allowed(tmp_path: Path):
    assert resolve_download_path(tmp_path, "reports/report.txt") == (tmp_path / "reports/report.txt").resolve()


def test_parent_path_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="outside"):
        resolve_download_path(tmp_path, "../secret.txt")
