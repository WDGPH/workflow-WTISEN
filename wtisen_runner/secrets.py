from __future__ import annotations

import os
from pathlib import Path


def resolve_secret(env_key: str, file_path: str | None = None) -> str:
    value = os.getenv(env_key)
    if value:
        return value
    if file_path:
        path = Path(file_path)
        if path.exists():
            content = path.read_text().strip()
            if content:
                return content
    raise ValueError(f"Missing required secret for {env_key}")
