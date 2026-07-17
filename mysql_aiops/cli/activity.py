"""``mysql-aiops activity`` — sessions, long-running queries, transactions, lock waits."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

activity_app = typer.Typer(
    name="activity",
    help="Activity: sessions, long-running queries, transactions, lock waits.",
    no_args_is_help=True,
)


@activity_app.command("sessions")
@cli_errors
def activity_sessions(
    no_sleeping: Annotated[
        bool, typer.Option("--no-sleeping", help="Hide sessions in command=Sleep")
    ] = False,
    target: TargetOption = None,
) -> None:
    """List current sessions (processlist) with per-command counts."""
    from mysql_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(
        ops.list_sessions(conn, include_sleeping=not no_sleeping), default=str))


@activity_app.command("long")
@cli_errors
def activity_long(
    min_seconds: Annotated[int, typer.Option("--min-seconds", help="Minimum age")] = 60,
    target: TargetOption = None,
) -> None:
    """List active statements running at least --min-seconds."""
    from mysql_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(
        ops.long_running_queries(conn, min_seconds=min_seconds), default=str))


@activity_app.command("transactions")
@cli_errors
def activity_transactions(target: TargetOption = None) -> None:
    """List open InnoDB transactions, oldest first."""
    from mysql_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_transactions(conn), default=str))


@activity_app.command("lock-waits")
@cli_errors
def activity_lock_waits(target: TargetOption = None) -> None:
    """List InnoDB wait-for edges (blocked -> blocking session)."""
    from mysql_aiops.ops import activity as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.lock_wait_pairs(conn), default=str))
