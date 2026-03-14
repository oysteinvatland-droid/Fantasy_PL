"""Fil-I/O hjelpefunksjoner for pipeline-artifakter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: str, default: Any = None, warn_missing: bool = True) -> Any:
    """Les JSON-fil og returner default hvis fil ikke finnes."""
    file_path = Path(path)
    try:
        with file_path.open('r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        if warn_missing:
            print(f"⚠️ Finner ikke {path}")
        return default


def write_json(path: str, data: Any, *, indent: int = 2, ensure_ascii: bool = False) -> None:
    """Skriv data til JSON-fil med standard utf-8."""
    file_path = Path(path)
    with file_path.open('w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)


def write_text(path: str, content: str) -> None:
    """Skriv tekstfil i utf-8."""
    file_path = Path(path)
    with file_path.open('w', encoding='utf-8') as f:
        f.write(content)
