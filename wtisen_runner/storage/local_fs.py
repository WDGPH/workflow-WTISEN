from __future__ import annotations

import fnmatch
import shutil
from pathlib import Path


class LocalFilesystemStorage:
    def __init__(self, root_dir: str):
        self.root = Path(root_dir)

    def resolve(self, relative_path: str) -> Path:
        return self.root / relative_path

    def ensure_dir(self, relative_path: str) -> Path:
        target = self.resolve(relative_path)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def list_files(self, relative_path: str, pattern: str) -> list[Path]:
        base = self.ensure_dir(relative_path)
        out = []
        for item in base.iterdir():
            if item.is_file() and fnmatch.fnmatch(item.name.lower(), pattern.lower()):
                out.append(item)
        return sorted(out, key=lambda p: p.name.lower())

    def write_bytes(self, relative_path: str, filename: str, content: bytes) -> Path:
        base = self.ensure_dir(relative_path)
        target = base / filename
        target.write_bytes(content)
        return target

    def move_file(self, source: Path, dest_relative_path: str) -> Path:
        dest_dir = self.ensure_dir(dest_relative_path)
        dest = dest_dir / source.name
        shutil.move(str(source), str(dest))
        return dest
