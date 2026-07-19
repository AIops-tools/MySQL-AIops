"""Shared helpers for the MySQL ops modules.

Two jobs:

  * ``s`` — pass catalog/query text through the governance ``sanitize`` (bounded
    length, control-character stripping) before it reaches an agent.
  * ``qualify`` / ``quote_ident`` — the ONLY sanctioned way to place an
    identifier (schema/table/index/column) into a statement that cannot be
    parameterised (DDL, ``OPTIMIZE TABLE``, ``ANALYZE TABLE``, ``KILL``). Every
    part is validated against a strict identifier charset and then
    backtick-quoted, so a value that is not a plain identifier is rejected
    rather than interpolated.

Values (thread ids, thresholds, limits, variable values) are ALWAYS passed as
query parameters — never string-formatted into SQL.
"""

from __future__ import annotations

import re
from typing import Any

from mysql_aiops.governance import opt_str, sanitize

# A MySQL unquoted identifier component: letter/underscore then
# letters/digits/underscore/dollar. We deliberately reject everything else
# (spaces, backticks, quotes, semicolons, operators) so interpolation cannot
# inject SQL — stricter than the server (which allows leading digits and, when
# quoted, almost anything), by design.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# Whitelist of ORDER-BY columns callers may choose from (maps a friendly name
# to a real events_statements_summary_by_digest column). Used so an ordering
# choice is never taken from raw user text.
STATEMENT_ORDER_COLUMNS = {
    "total_time": "SUM_TIMER_WAIT",
    "mean_time": "AVG_TIMER_WAIT",
    "calls": "COUNT_STAR",
    "rows_examined": "SUM_ROWS_EXAMINED",
    "lock_time": "SUM_LOCK_TIME",
    "no_index": "SUM_NO_INDEX_USED",
}


def s(value: Any, limit: int = 200) -> str:
    """Sanitize an arbitrary value to a bounded, injection-safe string."""
    return sanitize(str(value if value is not None else ""), limit)


def opt(value: Any, limit: int = 200) -> str | None:
    """Sanitize an *optional* field, preserving the difference between absent and empty.

    Companion to :func:`s`, which folds ``None`` into ``""``. MySQL catalog and
    performance_schema rows are full of genuinely-absent values — a session with
    no current statement has a NULL ``INFO``, a replica that has never errored
    has no ``Last_IO_Error``, and the 8.0 rename means ``Replica_IO_Running``
    and ``Slave_IO_Running`` are never both present. NULL is not the empty
    string, and collapsing the two hides that from the caller.

    Use this for anything read out of a result row; keep :func:`s` for values
    the caller supplied and that therefore always exist.
    """
    return opt_str(value, limit)


def quote_ident(part: str) -> str:
    """Validate a single identifier component and return it backtick-quoted.

    Raises ``ValueError`` for anything that is not a plain identifier — this is
    the boundary that makes identifier interpolation safe.
    """
    if not isinstance(part, str) or not _IDENT_RE.match(part):
        raise ValueError(
            f"Invalid SQL identifier {part!r}: only letters, digits, underscore "
            f"and '$' are allowed (must start with a letter/underscore)."
        )
    return "`" + part + "`"


def qualify(name: str) -> str:
    """Validate + quote a possibly schema-qualified name (``schema.table``).

    ``qualify('shop.orders')`` → ``` `shop`.`orders` ```; ``qualify('orders')``
    → ``` `orders` ```. Each component is validated by :func:`quote_ident`.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Empty identifier is not allowed.")
    parts = name.split(".")
    if len(parts) > 2:
        raise ValueError(f"Too many name parts in {name!r} (expected schema.table).")
    return ".".join(quote_ident(p) for p in parts)


def split_qualified(name: str) -> tuple[str | None, str]:
    """Split a validated ``schema.table`` name into (schema | None, table)."""
    qualify(name)  # validation only; raises on anything unsafe
    parts = name.split(".")
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, parts[0]


def order_column(choice: str) -> str:
    """Map a caller's order-by choice to a real column via the whitelist."""
    col = STATEMENT_ORDER_COLUMNS.get(choice)
    if col is None:
        allowed = ", ".join(sorted(STATEMENT_ORDER_COLUMNS))
        raise ValueError(f"Unknown order_by '{choice}'. Allowed: {allowed}.")
    return col


def picos_to_ms(value: Any) -> float:
    """Convert a performance_schema picosecond timer value to milliseconds."""
    try:
        return round(float(value) / 1e9, 2)
    except (TypeError, ValueError):
        return 0.0


def human_bytes(n: Any) -> str:
    """Render a byte count as a human string (e.g. 1536 -> '1.5 kB')."""
    try:
        size = float(n)
    except (TypeError, ValueError):
        return "0 bytes"
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "bytes":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
