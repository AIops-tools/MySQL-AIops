"""``mysql-aiops analyze`` — the four flagship analyses (pull live)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

analyze_app = typer.Typer(
    name="analyze",
    help="Flagship analyses: slow-query, lock-waits, replication, fragmentation.",
    no_args_is_help=True,
)


@analyze_app.command("slow-query")
@cli_errors
def analyze_slow_query(
    explain_sql: Annotated[str | None, typer.Option("--explain", help="SQL to EXPLAIN")] = None,
    target: TargetOption = None,
) -> None:
    """Root-cause the worst statement digest."""
    from mysql_aiops.ops import analysis, queries

    conn, _ = get_connection(target)
    statements = queries.top_queries(conn, order_by="total_time")["statements"]
    explain = queries.explain_query(conn, explain_sql) if explain_sql else None
    console.print_json(json.dumps(
        analysis.slow_query_rca(statements, explain=explain), default=str))


@analyze_app.command("lock-waits")
@cli_errors
def analyze_lock_waits(target: TargetOption = None) -> None:
    """Build the lock-wait chain, name the root blocker, parse the last deadlock."""
    from mysql_aiops.ops import activity, analysis

    conn, _ = get_connection(target)
    pairs = activity.lock_wait_pairs(conn)
    row = conn.query_one("SHOW ENGINE INNODB STATUS") or {}
    innodb_status = str(row.get("Status") or "")
    console.print_json(json.dumps(
        analysis.lock_wait_rca(pairs, innodb_status=innodb_status), default=str))


@analyze_app.command("replication")
@cli_errors
def analyze_replication(target: TargetOption = None) -> None:
    """Root-cause replica lag / stopped threads."""
    from mysql_aiops.ops import analysis, replication

    conn, _ = get_connection(target)
    status = replication.replica_status(conn)
    console.print_json(json.dumps(analysis.replication_lag_rca(status), default=str))


@analyze_app.command("fragmentation")
@cli_errors
def analyze_fragmentation(target: TargetOption = None) -> None:
    """Rank tables by reclaimable data_free into OPTIMIZE candidates."""
    from mysql_aiops.ops import analysis, tables

    conn, _ = get_connection(target)
    rows = tables.table_fragmentation(conn)["tables"]
    console.print_json(json.dumps(analysis.fragmentation_analysis(rows), default=str))
