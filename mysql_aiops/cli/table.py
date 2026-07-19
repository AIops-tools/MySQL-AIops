"""``mysql-aiops table`` — table-health reads."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

table_app = typer.Typer(
    name="table",
    help="Table health: sizes, fragmentation, status.",
    no_args_is_help=True,
)


@table_app.command("sizes")
@cli_errors
def table_sizes(
    limit: Annotated[int, typer.Option("--limit", help="Rows to return")] = 20,
    target: TargetOption = None,
) -> None:
    """Largest tables by data + index size."""
    from mysql_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    result = ops.table_sizes(conn, limit=limit)
    console.print_json(json.dumps(result, default=str))
    if result.get("truncated"):
        console.print(
            f"[yellow]… truncated at {result.get('limit')} rows — "
            f"re-run with a higher --limit to see the rest.[/yellow]"
        )


@table_app.command("fragmentation")
@cli_errors
def table_fragmentation(
    limit: Annotated[int, typer.Option("--limit", help="Rows to return")] = 50,
    target: TargetOption = None,
) -> None:
    """data_free per table (space OPTIMIZE TABLE could reclaim)."""
    from mysql_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    result = ops.table_fragmentation(conn, limit=limit)
    console.print_json(json.dumps(result, default=str))
    if result.get("truncated"):
        console.print(
            f"[yellow]… truncated at {result.get('limit')} rows — "
            f"re-run with a higher --limit to see the rest.[/yellow]"
        )


@table_app.command("status")
@cli_errors
def table_status(
    limit: Annotated[int, typer.Option("--limit", help="Rows to return")] = 50,
    target: TargetOption = None,
) -> None:
    """Per-table engine, row format and last update time."""
    from mysql_aiops.ops import tables as ops

    conn, _ = get_connection(target)
    result = ops.table_status(conn, limit=limit)
    console.print_json(json.dumps(result, default=str))
    if result.get("truncated"):
        console.print(
            f"[yellow]… truncated at {result.get('limit')} rows — "
            f"re-run with a higher --limit to see the rest.[/yellow]"
        )
