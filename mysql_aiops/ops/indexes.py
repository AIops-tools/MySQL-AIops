"""Index reads: unused indexes, redundant/duplicate indexes, per-index stats.

Unused indexes come from ``performance_schema.table_io_waits_summary_by_index_usage``
(COUNT_STAR = 0 since the last restart/stats reset). Redundant indexes are
detected from ``information_schema.statistics``: an index whose column list is
a leading prefix of another index on the same table is usually droppable.
"""

from __future__ import annotations

from typing import Any

from mysql_aiops.ops._util import s

_UNUSED_SQL = """
SELECT OBJECT_SCHEMA AS `schema`,
       OBJECT_NAME AS `table`,
       INDEX_NAME AS `index`,
       COUNT_STAR AS io_count
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE INDEX_NAME IS NOT NULL
  AND INDEX_NAME <> 'PRIMARY'
  AND COUNT_STAR = 0
  AND OBJECT_SCHEMA NOT IN
      ('mysql', 'information_schema', 'performance_schema', 'sys')
ORDER BY OBJECT_SCHEMA, OBJECT_NAME, INDEX_NAME
"""

_STATISTICS_SQL = """
SELECT table_schema AS `schema`,
       table_name AS `table`,
       index_name AS `index`,
       non_unique,
       seq_in_index,
       column_name,
       cardinality
FROM information_schema.statistics
WHERE table_schema NOT IN
      ('mysql', 'information_schema', 'performance_schema', 'sys')
ORDER BY table_schema, table_name, index_name, seq_in_index
"""


def unused_indexes(conn: Any) -> dict:
    """[READ] Secondary indexes with zero I/O events since restart (drop candidates)."""
    rows = conn.query(_UNUSED_SQL)
    indexes = [
        {
            "schema": s(r.get("schema"), 128),
            "table": s(r.get("table"), 128),
            "index": s(r.get("index"), 128),
        }
        for r in rows
    ]
    return {
        "count": len(indexes),
        "indexes": indexes,
        "note": (
            "Zero I/O since the last server restart (performance_schema "
            "counters reset on restart) — confirm over a full business cycle "
            "before dropping; unique indexes may still enforce constraints."
        ),
    }


def _collect_indexes(rows: list[dict]) -> dict[tuple, dict]:
    """Fold statistics rows into {(schema, table, index): {columns, nonUnique}}."""
    indexes: dict[tuple, dict] = {}
    for r in rows:
        key = (r.get("schema"), r.get("table"), r.get("index"))
        entry = indexes.setdefault(
            key, {"columns": [], "nonUnique": bool(r.get("non_unique"))}
        )
        entry["columns"].append(str(r.get("column_name")))
    return indexes


def redundant_indexes(conn: Any) -> dict:
    """[READ] Indexes whose columns are a leading prefix of another index (dupes).

    An index (a) is redundant to (a, b): the wider index serves the same
    lookups. Exact duplicates are reported too. PRIMARY is never flagged.
    """
    rows = conn.query(_STATISTICS_SQL)
    indexes = _collect_indexes(rows)
    redundant: list[dict] = []
    for (schema, table, name), meta in indexes.items():
        if name == "PRIMARY":
            continue
        cols = meta["columns"]
        for (schema2, table2, name2), meta2 in indexes.items():
            if (schema, table) != (schema2, table2) or name == name2:
                continue
            cols2 = meta2["columns"]
            if len(cols) <= len(cols2) and cols == cols2[: len(cols)]:
                # Equal-length ties: flag only one direction (by name) so an
                # exact duplicate pair is not reported twice.
                if len(cols) == len(cols2) and str(name) > str(name2):
                    continue
                redundant.append({
                    "schema": s(schema, 128),
                    "table": s(table, 128),
                    "index": s(name, 128),
                    "columns": [s(c, 128) for c in cols],
                    "coveredBy": s(name2, 128),
                    "coveredByColumns": [s(c, 128) for c in cols2],
                    "exactDuplicate": cols == cols2,
                })
                break
    return {
        "count": len(redundant),
        "redundant": redundant,
        "note": (
            "An index that is a leading prefix of a wider index on the same "
            "table is usually droppable — verify no query hints or unique "
            "constraints depend on it first."
        ),
    }


def index_stats(conn: Any) -> dict:
    """[READ] Per-index column lists and cardinality (selectivity screening)."""
    rows = conn.query(_STATISTICS_SQL)
    indexes = _collect_indexes(rows)
    cardinality: dict[tuple, Any] = {}
    for r in rows:
        key = (r.get("schema"), r.get("table"), r.get("index"))
        cardinality[key] = r.get("cardinality")  # last column's cardinality
    out = [
        {
            "schema": s(schema, 128),
            "table": s(table, 128),
            "index": s(name, 128),
            "columns": [s(c, 128) for c in meta["columns"]],
            "unique": not meta["nonUnique"],
            "cardinality": cardinality.get((schema, table, name)),
        }
        for (schema, table, name), meta in sorted(
            indexes.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), str(kv[0][2]))
        )
    ]
    return {"count": len(out), "indexes": out}
