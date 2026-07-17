"""``mysql-aiops overview`` — one-shot server health snapshot."""

from __future__ import annotations

import json

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection


@cli_errors
def overview_cmd(target: TargetOption = None) -> None:
    """One-shot server health: version+flavor, connections, long queries, fragmentation."""
    from mysql_aiops.ops import overview as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.snapshot(conn)))
