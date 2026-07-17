"""MySQL maintenance writes (guarded).

Every reversible write reads the server's real current state **before** it
changes anything, so the harness records a faithful undo / audit trail (the
before-state is fetched, never guessed):

  * ``drop_index`` captures the index definition out of ``SHOW CREATE TABLE``
    first, so undo recreates it exactly.
  * ``create_index`` returns the created (table, name), so undo drops that index.
  * ``set_global_variable`` captures the current value, so undo sets it back.

Irreversible ops (``kill_session``, ``kill_query``, ``optimize_table``,
``analyze_table``, ``reset_query_stats``) capture prior state for the audit
trail but declare no undo.

Values (session ids, variable values) are bound parameters. The few identifiers
that cannot be parameterised (table/index/column names, variable names) are
validated and backtick-quoted via :mod:`mysql_aiops.ops._util` before the
single-line interpolation site.
"""

from __future__ import annotations

import re
from typing import Any

from mysql_aiops.ops._util import qualify, quote_ident, s

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ── session control (irreversible) ──────────────────────────────────────────


def _capture_session(conn: Any, session_id: int) -> dict:
    row = conn.query_one(
        "SELECT id, user, host, db, command, time, state, LEFT(info, 500) AS query "
        "FROM information_schema.processlist WHERE id = %(id)s",
        {"id": int(session_id)},
    ) or {}
    return {
        "id": row.get("id"),
        "user": s(row.get("user"), 128),
        "host": s(row.get("host"), 128),
        "database": s(row.get("db"), 128),
        "command": s(row.get("command"), 64),
        "ageSeconds": row.get("time"),
        "state": s(row.get("state"), 128),
        "query": s(row.get("query"), 500),
    }


def kill_session(conn: Any, session_id: int) -> dict:
    """[WRITE] Terminate a session (KILL CONNECTION). No safe inverse."""
    prior = _capture_session(conn, session_id)
    conn.execute("KILL CONNECTION %(id)s", {"id": int(session_id)})
    return {
        "action": "kill_session",
        "sessionId": int(session_id),
        "killed": True,
        "priorState": prior,
    }


def kill_query(conn: Any, session_id: int) -> dict:
    """[WRITE] Cancel a session's running statement (KILL QUERY). No inverse.

    The session stays connected; only its current statement is aborted.
    """
    prior = _capture_session(conn, session_id)
    conn.execute("KILL QUERY %(id)s", {"id": int(session_id)})
    return {
        "action": "kill_query",
        "sessionId": int(session_id),
        "cancelled": True,
        "priorState": prior,
    }


# ── optimize / analyze (irreversible; capture prior stats) ──────────────────


def _capture_table_stats(conn: Any, table: str) -> dict:
    schema_pred = "table_schema = %(s)s" if "." in table else "table_schema = DATABASE()"
    parts = table.split(".")
    params: dict[str, Any] = {"t": parts[-1]}
    if len(parts) == 2:
        params["s"] = parts[0]
    row = conn.query_one(
        "SELECT table_rows, data_length, index_length, data_free, update_time "
        f"FROM information_schema.tables WHERE {schema_pred} AND table_name = %(t)s",  # nosec B608 — predicate is a static constant
        params,
    ) or {}
    return {
        "estRows": row.get("table_rows"),
        "dataBytes": row.get("data_length"),
        "indexBytes": row.get("index_length"),
        "freeBytes": row.get("data_free"),
        "updateTime": s(row.get("update_time"), 64),
    }


def _maintenance_result(rows: list[dict]) -> list[dict]:
    return [
        {
            "table": s(r.get("Table"), 256),
            "op": s(r.get("Op"), 32),
            "msgType": s(r.get("Msg_type"), 32),
            "msgText": s(r.get("Msg_text"), 300),
        }
        for r in (rows or [])
    ]


def optimize_table(conn: Any, table: str) -> dict:
    """[WRITE] OPTIMIZE TABLE (rebuild, reclaim data_free). Records prior stats.

    No undo (a rebuild has no inverse); the prior size/fragmentation stats are
    captured for the audit trail. InnoDB maps this to ALTER TABLE ... FORCE
    (online DDL, brief locks).
    """
    ident = qualify(table)
    prior = _capture_table_stats(conn, table)
    rows = conn.query(f"OPTIMIZE TABLE {ident}")  # nosec B608 — ident validated
    return {
        "action": "optimize_table",
        "table": table,
        "result": _maintenance_result(rows),
        "priorState": prior,
    }


def analyze_table(conn: Any, table: str) -> dict:
    """[WRITE] ANALYZE TABLE to refresh index statistics. Records prior stats."""
    ident = qualify(table)
    prior = _capture_table_stats(conn, table)
    rows = conn.query(f"ANALYZE TABLE {ident}")  # nosec B608 — ident validated
    return {
        "action": "analyze_table",
        "table": table,
        "result": _maintenance_result(rows),
        "priorState": prior,
    }


# ── index create/drop ───────────────────────────────────────────────────────


def _default_index_name(table: str, columns: list[str]) -> str:
    base = "idx_" + table.split(".")[-1] + "_" + "_".join(columns)
    return base[:64]


def create_index(
    conn: Any,
    table: str,
    columns: list[str],
    name: str | None = None,
    unique: bool = False,
) -> dict:
    """[WRITE] Create an index. Reversible: undo drops the created (table, name).

    The index name is returned so the harness can record an undo that drops
    exactly this index (MySQL's DROP INDEX needs the table too).
    """
    cols = [str(c) for c in (columns or []) if str(c).strip()]
    if not cols:
        raise ValueError("create_index requires at least one column.")
    col_sql = ", ".join(quote_ident(c) for c in cols)
    ident_table = qualify(table)
    index_name = name or _default_index_name(table, cols)
    ident_index = quote_ident(index_name)
    unique_kw = "UNIQUE " if unique else ""
    sql = f"CREATE {unique_kw}INDEX {ident_index} ON {ident_table} ({col_sql})"  # nosec B608
    conn.execute(sql)
    return {
        "action": "create_index",
        "index": index_name,
        "table": table,
        "columns": cols,
        "unique": unique,
    }


# Shape gate for replaying a captured index definition. Server-derived (built
# from SHOW CREATE TABLE, never user-composed), but validated anyway: single
# statement, CREATE [UNIQUE] INDEX ... ON ... only.
_INDEXDEF_RE = re.compile(
    r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(`?[A-Za-z_][A-Za-z0-9_$]*`?)\s+ON\s+",
    re.IGNORECASE,
)


def create_index_from_definition(conn: Any, definition: str) -> dict:
    """[WRITE] Recreate an index from a captured definition statement.

    This is the replay path for ``drop_index``'s undo descriptor: the exact
    definition captured (from SHOW CREATE TABLE) before the drop is executed
    verbatim after a shape check (single statement, must be
    CREATE [UNIQUE] INDEX ... ON ...).
    """
    stmt = (definition or "").strip().rstrip(";").strip()
    if not stmt or ";" in stmt:
        raise ValueError("definition must be a single CREATE INDEX statement.")
    m = _INDEXDEF_RE.match(stmt)
    if not m:
        raise ValueError("definition must start with CREATE [UNIQUE] INDEX ... ON ...")
    conn.execute(stmt)  # nosec B608 — shape-validated captured definition
    return {
        "action": "create_index",
        "index": m.group(1).strip("`"),
        "fromDefinition": True,
    }


# Matches one index line inside SHOW CREATE TABLE output, e.g.
#   UNIQUE KEY `idx_email` (`email`),
#   KEY `idx_cid` (`customer_id`,`created_at` DESC) USING BTREE
_KEY_LINE_RE = re.compile(
    r"^\s*(UNIQUE\s+)?KEY\s+`(?P<name>[^`]+)`\s+\((?P<cols>.*?)\)", re.IGNORECASE
)


def _capture_index_definition(conn: Any, table: str, index_name: str) -> str | None:
    """Read SHOW CREATE TABLE and rebuild the named index's CREATE INDEX statement."""
    ident_table = qualify(table)
    row = conn.query_one(f"SHOW CREATE TABLE {ident_table}") or {}  # nosec B608 — ident validated
    ddl = str(row.get("Create Table") or row.get("Create View") or "")
    for line in ddl.splitlines():
        m = _KEY_LINE_RE.match(line)
        if m and m.group("name") == index_name:
            unique_kw = "UNIQUE " if m.group(1) else ""
            ident_index = quote_ident(index_name)
            return (
                f"CREATE {unique_kw}INDEX {ident_index} "
                f"ON {ident_table} ({m.group('cols')})"
            )
    return None


def drop_index(conn: Any, table: str, name: str) -> dict:
    """[WRITE] Drop an index. Reversible: captures its definition first so undo recreates it."""
    ident_table = qualify(table)
    ident_index = quote_ident(name)
    definition = _capture_index_definition(conn, table, name)
    if not definition:
        raise ValueError(
            f"Index '{name}' not found on '{table}' (no definition to capture)."
        )
    sql = f"DROP INDEX {ident_index} ON {ident_table}"  # nosec B608 — idents validated
    conn.execute(sql)
    return {
        "action": "drop_index",
        "index": name,
        "table": table,
        "priorState": {"definition": s(definition, 2000)},
    }


# ── global variables (reversible: capture prior value) ──────────────────────


def _validate_variable_name(name: str) -> str:
    if not isinstance(name, str) or not _VARIABLE_NAME_RE.match(name):
        raise ValueError(
            f"Invalid variable name {name!r} (letters, digits and '_' only)."
        )
    return name


def set_global_variable(conn: Any, name: str, value: str) -> dict:
    """[WRITE] SET GLOBAL a server variable. Reversible: captures the prior value.

    Runtime-only: the change does not survive a server restart (persist it in
    my.cnf — or SET PERSIST on MySQL 8 — yourself); this is reported but NOT
    performed automatically. The prior value comes from SHOW GLOBAL VARIABLES
    so the harness records an undo that sets it back.
    """
    var_name = _validate_variable_name(name)
    prior = conn.query_one(
        "SHOW GLOBAL VARIABLES LIKE %(n)s", {"n": var_name}
    ) or {}
    if not prior:
        raise ValueError(f"Unknown global variable '{var_name}'.")
    sql = f"SET GLOBAL {var_name} = %(v)s"  # nosec B608 — name validated, value bound
    conn.execute(sql, {"v": str(value)})
    return {
        "action": "set_global_variable",
        "variable": var_name,
        "newValue": str(value),
        "priorState": {"value": s(prior.get("Value"), 512)},
        "persistent": False,
        "note": (
            "Runtime-only change: persist it in my.cnf (or SET PERSIST on "
            "MySQL 8) to survive a restart."
        ),
    }
