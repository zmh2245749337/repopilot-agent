from pathlib import Path


def resolve_download_path(base_dir: Path, requested_path: str) -> Path:
    return base_dir / requested_path
