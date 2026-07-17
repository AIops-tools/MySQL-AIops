"""``mysql-aiops repl`` — replication reads."""

from __future__ import annotations

import json

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

repl_app = typer.Typer(
    name="repl",
    help="Replication: replica status, binlog.",
    no_args_is_help=True,
)


@repl_app.command("status")
@cli_errors
def repl_status(target: TargetOption = None) -> None:
    """Replica thread state and lag (flavor-branched)."""
    from mysql_aiops.ops import replication as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.replica_status(conn), default=str))


@repl_app.command("binlog")
@cli_errors
def repl_binlog(target: TargetOption = None) -> None:
    """Binary-log configuration, GTID mode and downstream replicas."""
    from mysql_aiops.ops import replication as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.binlog_status(conn), default=str))
