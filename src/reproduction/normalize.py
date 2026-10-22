from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _type_name(value: Any) -> str:
    return f"{type(value).__module__}.{type(value).__qualname__}"


def _normalize_mapping(value: Mapping[Any, Any], active: set[int]) -> dict[str, Any]:
    items = []
    for key, item in value.items():
        normalized = _normalize_value(item, active)
        key_type = _type_name(key)
        items.append((str(key), key_type, _canonical_json(normalized), normalized))
    items.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return {key: normalized for key, _type, _json, normalized in items}


def _project_graph_value(value: Any, active: set[int]) -> dict[str, Any] | None:
    items = getattr(value, "items", None)
    labels = getattr(value, "labels", None)
    if labels is not None and callable(items):
        return {
            "$type": "node",
            "labels": sorted(str(label) for label in labels),
            "properties": _normalize_mapping(dict(items()), active),
        }

    nodes = getattr(value, "nodes", None)
    relationships = getattr(value, "relationships", None)
    if nodes is not None and relationships is not None:
        return {
            "$type": "path",
            "nodes": [_normalize_value(node, active) for node in nodes],
            "relationships": [
                _normalize_value(relationship, active) for relationship in relationships
            ],
        }

    start_node = getattr(value, "start_node", None)
    end_node = getattr(value, "end_node", None)
    if start_node is not None and end_node is not None and callable(items):
        relationship_type = getattr(value, "type", type(value).__name__)
        return {
            "$type": "relationship",
            "relationship_type": str(relationship_type),
            "start": _normalize_value(start_node, active),
            "end": _normalize_value(end_node, active),
            "properties": _normalize_mapping(dict(items()), active),
        }

    return None


def _normalize_value(value: Any, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"$float": "nan"}
        if math.isinf(value):
            return {"$float": "infinity" if value > 0 else "-infinity"}
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    identity = id(value)
    if identity in active:
        return {"$cycle": _type_name(value)}
    active.add(identity)
    try:
        if isinstance(value, (list, tuple)):
            return [_normalize_value(item, active) for item in value]
        if isinstance(value, (set, frozenset)):
            normalized = [_normalize_value(item, active) for item in value]
            return sorted(normalized, key=_canonical_json)

        graph_projection = _project_graph_value(value, active)
        if graph_projection is not None:
            return graph_projection
        if isinstance(value, Mapping):
            return _normalize_mapping(value, active)

        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            try:
                return isoformat()
            except (TypeError, ValueError):
                pass
        return str(value)
    finally:
        active.remove(identity)


def normalize_value(value: Any) -> Any:
    """Convert driver values into deterministic JSON-compatible values."""
    return _normalize_value(value, set())


def normalize_rows(rows: Iterable[Any], ordered: bool) -> list[Any]:
    """Normalize rows while preserving or explicitly discarding row order."""
    normalized = [normalize_value(row) for row in rows]
    if not ordered:
        normalized.sort(key=_canonical_json)
    return normalized
