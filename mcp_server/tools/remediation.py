"""MySQL maintenance MCP tools (guarded writes).

The state-changing tools. Every one is wrapped with the governance harness
(audit + graduated approval tier) and takes a ``dry_run`` preview. Reversible
writes pass an ``undo=`` callback that turns the fetched before-state into an
inverse descriptor the harness records; irreversible ones record none.

Risk tiers:
  * kill_session / kill_query / drop_index = high (destructive / irreversible)
  * optimize_table / analyze_table / create_index / set_global_variable = medium
"""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import remediation as ops

# ── undo descriptors (built from the fetched before-state) ──────────────────


def _create_index_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict) or not result.get("index") or not result.get("table"):
        return None
    return {
        "tool": "drop_index",
        "params": {"table": result["table"], "name": result["index"]},
        "skill": "mysql-aiops",
        "note": "Inverse of create_index: drop the index that was just created.",
    }


def _drop_index_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    definition = (result.get("priorState") or {}).get("definition")
    if not definition:
        return None
    return {
        "tool": "create_index",
        "params": {"definition": definition},
        "skill": "mysql-aiops",
        "note": (
            "Inverse of drop_index: recreate the index from its captured "
            "definition (replay this CREATE INDEX statement)."
        ),
    }


def _set_global_variable_undo(params: dict[str, Any], result: Any) -> Optional[dict]:
    if not isinstance(result, dict):
        return None
    prior = (result.get("priorState") or {}).get("value")
    if prior is None or prior == "":
        return None
    return {
        "tool": "set_global_variable",
        "params": {"name": params.get("name"), "value": prior},
        "skill": "mysql-aiops",
        "note": "Inverse of set_global_variable: SET GLOBAL back to the prior value.",
    }


# ── session control (high; irreversible) ────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def kill_session(session_id: int, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Terminate a session (KILL CONNECTION). No safe inverse.

    Captures the session's user/host/query for the audit trail; a kill cannot
    be undone, so no undo is offered. Pass dry_run=True to preview.

    Args:
        session_id: Session/connection id (from list_sessions).
        dry_run: If True, preview without killing.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldKillSession": {"sessionId": session_id}}
    return ops.kill_session(conn, session_id)


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def kill_query(session_id: int, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Cancel a session's running statement (KILL QUERY). No inverse.

    The session stays connected; only its current statement is aborted.
    Captures the session's user/host/query for audit. Pass dry_run=True to
    preview.

    Args:
        session_id: Session/connection id (from list_sessions).
        dry_run: If True, preview without cancelling.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldKillQuery": {"sessionId": session_id}}
    return ops.kill_query(conn, session_id)


# ── optimize / analyze (medium; irreversible, record prior stats) ───────────


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def optimize_table(table: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] OPTIMIZE TABLE (rebuild, reclaim data_free). Records prior stats.

    No undo (a rebuild has no inverse); the prior size/fragmentation stats are
    captured for the audit trail. InnoDB maps this to ALTER TABLE ... FORCE
    (online DDL, brief locks) — schedule off-peak for hot tables. Pass
    dry_run=True to preview.

    Args:
        table: Table name (optionally schema-qualified, e.g. shop.orders).
        dry_run: If True, preview without running.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldOptimize": {"table": table}}
    return ops.optimize_table(conn, table)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def analyze_table(table: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] ANALYZE TABLE to refresh index statistics.

    No undo; captures prior stats for audit. Takes a brief read lock while
    sampling. Pass dry_run=True to preview.

    Args:
        table: Table name (optionally schema-qualified).
        dry_run: If True, preview without running.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldAnalyze": {"table": table}}
    return ops.analyze_table(conn, table)


# ── index create/drop ───────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_create_index_undo)
@tool_errors("dict")
def create_index(
    table: Optional[str] = None,
    columns: Optional[list[str]] = None,
    name: Optional[str] = None,
    unique: bool = False,
    definition: Optional[str] = None,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Create an index. Reversible: undo drops the created index.

    The created (table, name) is returned so the harness records an undo that
    drops exactly this index. Pass dry_run=True to preview. Alternatively pass
    ``definition`` (a captured CREATE INDEX statement — this is how
    drop_index's undo descriptor replays) INSTEAD of table/columns.

    Args:
        table: Table to index (optionally schema-qualified). Required unless
            ``definition`` is given.
        columns: Column names to index. Required unless ``definition`` is given.
        name: Index name (auto-generated from table+columns when omitted).
        unique: Create a UNIQUE index.
        definition: A full CREATE [UNIQUE] INDEX statement to execute verbatim
            (shape-validated). Mutually exclusive with table/columns.
        dry_run: If True, preview without creating.
        target: Target name from config; omit for the default.
    """
    if definition and (table or columns):
        raise ValueError("Pass either definition OR table+columns, not both.")
    if not definition and not (table and columns):
        raise ValueError("create_index requires table+columns (or a definition).")
    conn = _get_connection(target)
    if dry_run:
        if definition:
            return {"dryRun": True, "wouldExecute": definition}
        return {"dryRun": True, "wouldCreate": {"table": table, "columns": columns, "name": name}}
    if definition:
        return ops.create_index_from_definition(conn, definition)
    return ops.create_index(conn, table, columns, name=name, unique=unique)


@mcp.tool()
@governed_tool(risk_level="high", undo=_drop_index_undo)
@tool_errors("dict")
def drop_index(
    table: str,
    name: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=high] Drop an index. Reversible: captures its definition first.

    Before dropping, the exact index definition is rebuilt from SHOW CREATE
    TABLE so the harness records an undo that recreates it. Pass dry_run=True
    to preview.

    Args:
        table: Table the index belongs to (optionally schema-qualified).
        name: Index name to drop.
        dry_run: If True, preview without dropping.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldDrop": {"table": table, "name": name}}
    return ops.drop_index(conn, table, name)


# ── global variables (medium; reversible) ───────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="medium", undo=_set_global_variable_undo)
@tool_errors("dict")
def set_global_variable(
    name: str,
    value: str,
    dry_run: bool = False,
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] SET GLOBAL a server variable. Reversible: captures prior value.

    Runtime-only: the change does not survive a restart (persist it in my.cnf
    — or SET PERSIST on MySQL 8 — yourself); reported but NOT performed
    automatically. The prior value (from SHOW GLOBAL VARIABLES) is captured so
    the harness records an undo that sets it back. Pass dry_run=True to
    preview.

    Args:
        name: The global variable name (e.g. max_connections).
        value: The new value (as a string).
        dry_run: If True, preview without changing.
        target: Target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        return {"dryRun": True, "wouldSet": {"name": name, "value": value}}
    return ops.set_global_variable(conn, name, value)
