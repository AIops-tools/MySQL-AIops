"""``mysql-aiops table`` — table-health reads."""

from __future__ import annotations

import json

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

table_app = typer.Typer(
    name="table",
    help="Table health: sizes, fragmentation, status.",
    no_args_is_help=True,
)


@table_app.command("sizes")
@cli_errors
def table_sizes(target: TargetOption = None) -> None:
    """Largest tables by data + index size."""
    from mysql_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.table_sizes(conn), default=str))


@table_app.command("fragmentation")
@cli_errors
def table_fragmentation(target: TargetOption = None) -> None:
    """data_free per table (space OPTIMIZE TABLE could reclaim)."""
    from mysql_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.table_fragmentation(conn), default=str))


@table_app.command("status")
@cli_errors
def table_status(target: TargetOption = None) -> None:
    """Per-table engine, row format and last update time."""
    from mysql_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.table_status(conn), default=str))
