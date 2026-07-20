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

Two writes additionally refuse targets that would destroy their own
reversibility (:class:`SelfLockout`):

  * ``kill_session`` / ``kill_query`` refuse this connection's own session id.
    ``ops/activity.py`` already hides it from every read (``WHERE id <>
    CONNECTION_ID()``); the writes have to honour the same boundary, or the one
    session an agent can reach by guessing is the one it is calling through.
  * ``set_global_variable`` refuses a static denylist of self-affecting globals.
    Unlike Postgres's ``ALTER SYSTEM``, ``SET GLOBAL`` takes effect IMMEDIATELY,
    so e.g. ``init_connect`` or ``max_connections=1`` locks out every later
    process — including the one that would replay the undo.

Values (session ids, variable values) are bound parameters. The few identifiers
that cannot be parameterised (table/index/column names, variable names) are
validated and backtick-quoted via :mod:`mysql_aiops.ops._util` before the
single-line interpolation site.
"""

from __future__ import annotations

import re
from typing import Any

from mysql_aiops.ops._util import opt, qualify, quote_ident, s

_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Globals whose effect is immediate AND lands on the tool's own ability to
# reconnect. `SET GLOBAL` is not `ALTER SYSTEM`: there is no restart between the
# write and the damage. The cached connection inside one process survives, but
# every CLI invocation and every restarted MCP server opens a NEW connection —
# which is exactly what an undo needs. The list is STATIC (no runtime
# detection), so there is no fail-open case: a name is either on it or it is not.
_SELF_AFFECTING_GLOBALS: dict[str, str] = {
    "init_connect": (
        "it runs on every new non-SUPER connection, so a statement that errors "
        "(or blocks) makes each later login fail at handshake"
    ),
    "max_connections": "it can be set below the live connection count, refusing every new login",
    "max_user_connections": "it caps this tool's own account, refusing its later logins",
    "read_only": "it makes the server reject the writes an undo is made of",
    "super_read_only": "it makes the server reject the writes an undo is made of, SUPER included",
    "skip_networking": "it stops the server listening on TCP, the transport this tool uses",
    "require_secure_transport": "it starts refusing every non-TLS client, this one included",
}
# wait_timeout / interactive_timeout are legitimate tuning knobs until they are
# small enough that a new connection is torn down before it can do any work.
_TIMEOUT_GLOBALS = ("wait_timeout", "interactive_timeout")
MIN_SAFE_TIMEOUT_SECONDS = 30


class SelfLockout(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the operation would cut this tool off from the server it manages."""


# ── session control (irreversible) ──────────────────────────────────────────


def _own_session_id(conn: Any) -> int | None:
    """This connection's own session id, or None when it cannot be determined.

    ``None`` means UNKNOWN and must never be read as "it is me" — callers fail
    open, because refusing a legitimate kill on a failed probe would be a new
    bug, while the read path (``activity.py``) already filters the same id.
    """
    try:
        own = conn.scalar("SELECT CONNECTION_ID()")
        return int(own) if own is not None else None
    except Exception:  # noqa: BLE001 — unknown identity, never a false "it is me"
        return None


def guard_kill_session(conn: Any, session_id: int, action: str = "kill_session") -> None:
    """Raise the :class:`SelfLockout` a self-targeted kill would raise, without killing.

    Called by ``kill_session`` / ``kill_query`` themselves *and* by the MCP
    wrappers ahead of their ``dry_run`` early return, so a preview of a
    self-kill reports the refusal instead of a green ``wouldKillSession``. Both
    paths run this one function, so preview and real call cannot disagree.

    Fails open on an undeterminable id: unknown is never treated as "it is me".
    """
    own = _own_session_id(conn)
    if own is None or int(session_id) != own:
        return
    raise SelfLockout(
        f"Refusing {action} on session {int(session_id)}: that is the connection "
        f"this tool is calling through. Killing it aborts the very statement "
        f"issuing the kill and drops the session the audit row is written from. "
        f"list_sessions already excludes it — pick a session id from there, or "
        f"use a separate mysql client if you really must kill this one."
    )


def _capture_session(conn: Any, session_id: int) -> dict:
    row = conn.query_one(
        "SELECT id, user, host, db, command, time, state, LEFT(info, 500) AS query "
        "FROM information_schema.processlist WHERE id = %(id)s",
        {"id": int(session_id)},
    ) or {}
    return {
        "id": row.get("id"),
        "user": opt(row.get("user"), 128),
        "host": opt(row.get("host"), 128),
        "database": opt(row.get("db"), 128),
        "command": opt(row.get("command"), 64),
        "ageSeconds": row.get("time"),
        "state": opt(row.get("state"), 128),
        "query": opt(row.get("query"), 500),
    }


def kill_session(conn: Any, session_id: int) -> dict:
    """[WRITE] Terminate a session (KILL CONNECTION). No safe inverse.

    **Refuses this connection's own session id** — a kill has no undo, and
    aiming it at the caller's own connection destroys the statement issuing it.
    If the id cannot be determined the call proceeds (unknown is never treated
    as "it is me").
    """
    guard_kill_session(conn, session_id, "kill_session")
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

    **Refuses this connection's own session id** — the statement it would cancel
    is this very call. If the id cannot be determined the call proceeds.
    """
    guard_kill_session(conn, session_id, "kill_query")
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
        "updateTime": opt(row.get("update_time"), 64),
    }


def _maintenance_result(rows: list[dict]) -> list[dict]:
    return [
        {
            "table": opt(r.get("Table"), 256),
            "op": opt(r.get("Op"), 32),
            "msgType": opt(r.get("Msg_type"), 32),
            "msgText": opt(r.get("Msg_text"), 300),
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


def _self_affecting_reason(var_name: str, value: str) -> str | None:
    """Why setting ``var_name`` to ``value`` locks this tool out, else None."""
    reason = _SELF_AFFECTING_GLOBALS.get(var_name)
    if reason is not None:
        return reason
    if var_name in _TIMEOUT_GLOBALS:
        try:
            seconds = int(str(value).strip())
        except (TypeError, ValueError):
            return None  # not a number — SET GLOBAL will reject it on its own
        if seconds < MIN_SAFE_TIMEOUT_SECONDS:
            return (
                f"at {seconds}s a new connection is torn down before it can do any "
                f"work (floor is {MIN_SAFE_TIMEOUT_SECONDS}s)"
            )
    return None


def guard_set_global_variable(name: str, value: str) -> None:
    """Raise the :class:`SelfLockout` ``set_global_variable`` would raise, without I/O.

    Called by ``set_global_variable`` itself *and* by the MCP wrapper ahead of
    its ``dry_run`` early return, so a preview of a denylisted global reports
    the refusal instead of a green ``wouldSet``. The denylist is static, so the
    preview and the real call cannot diverge and the guard costs nothing.

    Normalises the name itself, so it cannot be side-stepped by case or padding
    on either path.
    """
    var_name = str(name).strip().lower()
    lockout_reason = _self_affecting_reason(var_name, value)
    if lockout_reason is None:
        return
    raise SelfLockout(
        f"Refusing SET GLOBAL {var_name}: {lockout_reason}. SET GLOBAL takes "
        f"effect immediately, so this would lock out every later connection — "
        f"including the one the undo needs to set it back, destroying this "
        f"write's own reversibility. Set it in my.cnf and restart during a "
        f"window where you have console access to recover."
    )


def set_global_variable(conn: Any, name: str, value: str) -> dict:
    """[WRITE] SET GLOBAL a server variable. Reversible: captures the prior value.

    Runtime-only: the change does not survive a server restart (persist it in
    my.cnf — or SET PERSIST on MySQL 8 — yourself); this is reported but NOT
    performed automatically. The prior value comes from SHOW GLOBAL VARIABLES
    so the harness records an undo that sets it back.

    **Refuses the globals that would lock this tool out of the server**
    (``init_connect``, ``max_connections``, ``max_user_connections``,
    ``read_only``, ``super_read_only``, ``skip_networking``,
    ``require_secure_transport``, and ``wait_timeout`` /
    ``interactive_timeout`` below a 30s floor). Those take effect on the next
    connection, which is precisely what replaying the undo requires.
    """
    guard_set_global_variable(name, value)
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
        "priorState": {"value": opt(prior.get("Value"), 512)},
        "persistent": False,
        "note": (
            "Runtime-only change: persist it in my.cnf (or SET PERSIST on "
            "MySQL 8) to survive a restart."
        ),
    }
