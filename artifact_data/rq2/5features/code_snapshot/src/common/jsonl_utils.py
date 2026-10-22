from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List


def iter_jsonl(path: str | Path) -> Iterator[Dict[str, Any]]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSONL at {p}:{lineno}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL record at {p}:{lineno} must be an object")
            yield obj


def write_jsonl(records: Iterable[Dict[str, Any]], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        # Stage3 resume rewrites paid completed dispositions. Publish only a
        # complete replacement, so interrupted serialization never truncates
        # the previous audit trail. A process kill can leave an unused temp.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent,
                                         prefix=p.name + ".", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(p)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def append_jsonl(record: Dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_jsonl(path: str | Path) -> List[Dict[str, Any]]:
    return list(iter_jsonl(path))
