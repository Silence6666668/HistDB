#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import sys
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.common.io_utils import dump_json, ensure_dir
from src.common.jsonl_utils import iter_jsonl, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a JSONL file into a fixed number of shards.")
    parser.add_argument("--input", required=True, help="Input JSONL path.")
    parser.add_argument("--output-dir", required=True, help="Output directory for shard JSONLs.")
    parser.add_argument("--shard-count", required=True, type=int, help="Number of shards to produce.")
    parser.add_argument(
        "--key-field",
        default="query_id",
        help="Field used for stable hash-based sharding. Falls back to row index if missing.",
    )
    return parser.parse_args()


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "unknown"


def stable_shard_index(record: Dict[str, Any], row_index: int, shard_count: int, key_field: str) -> int:
    raw_value = str(record.get(key_field) or "").strip()
    if not raw_value:
        raw_value = f"row-{row_index}"
    digest = hashlib.md5(raw_value.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % shard_count


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if args.shard_count <= 0:
        raise ValueError("--shard-count must be positive")
    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")

    ensure_dir(output_dir)

    shards: Dict[str, list[Dict[str, Any]]] = {}
    counts: Counter[str] = Counter()

    for row_index, record in enumerate(iter_jsonl(input_path), start=1):
        shard_index = stable_shard_index(record, row_index, args.shard_count, args.key_field)
        shard_name = f"shard_{shard_index + 1:02d}"
        shards.setdefault(shard_name, []).append(record)
        counts[shard_name] += 1

    manifest: Dict[str, Any] = {
        "input": str(input_path),
        "output_dir": str(output_dir),
        "shard_count": args.shard_count,
        "key_field": args.key_field,
        "shards": {},
    }

    for shard_name in sorted(shards):
        shard_path = output_dir / f"{sanitize_filename(shard_name)}.jsonl"
        write_jsonl(shards[shard_name], shard_path)
        manifest["shards"][shard_name] = {
            "path": str(shard_path),
            "count": counts[shard_name],
        }

    dump_json(manifest, output_dir / "summary.json", pretty=True)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
