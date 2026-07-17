"""``mysql-aiops index`` — index-health reads."""

from __future__ import annotations

import json

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

index_app = typer.Typer(
    name="index",
    help="Index health: unused, redundant, stats.",
    no_args_is_help=True,
)


@index_app.command("unused")
@cli_errors
def index_unused(target: TargetOption = None) -> None:
    """Secondary indexes with zero I/O events since restart."""
    from mysql_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.unused_indexes(conn), default=str))


@index_app.command("redundant")
@cli_errors
def index_redundant(target: TargetOption = None) -> None:
    """Indexes whose columns are a leading prefix of another index."""
    from mysql_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.redundant_indexes(conn), default=str))


@index_app.command("stats")
@cli_errors
def index_stats(target: TargetOption = None) -> None:
    """Per-index column lists and cardinality."""
    from mysql_aiops.ops import indexes as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.index_stats(conn), default=str))
