"""Shared MCP server primitives: the FastMCP instance, connection helper,
error sanitisation, and the ``@tool_errors`` decorator.

Tool modules under ``mcp_server/tools/`` import ``mcp`` from here and register
their ``@mcp.tool()`` functions onto it. ``mcp_server/server.py`` then imports
those modules and runs the server.

Keep ``Optional[X]`` (never PEP 604 ``X | None``) in any FastMCP-reflected
tool signature — on older mcp/pydantic the union eval'd to ``types.UnionType``
crashes FastMCP's ``issubclass`` check.
"""

import functools
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from mysql_aiops.config import load_config
from mysql_aiops.connection import ConnectionManager, MySQLConnectionLostError, MySQLError
from mysql_aiops.governance import mark_unknown, sanitize

logger = logging.getLogger(__name__)

_DOCTOR_HINT = "Run 'mysql-aiops doctor' to verify connectivity and credentials."


# Long enough to carry the remediation sentence. These messages teach the
# caller what to do instead, and that clause comes last — a 300-char cap cut
# it off silently on every refusal long enough to need one.
_ERROR_MAX = 800


# Failures that leave the statement's fate genuinely undetermined. Raised
# only from the statement-executing path, so it means an ESTABLISHED link
# died mid-statement — not that the server was unreachable. The driver gives
# both the same class, so the connection layer discriminates by position and
# raises a dedicated class; this layer only has to recognise it.
# InnoDB rolls back on connection loss, so usually nothing landed — but a
# COMMIT whose acknowledgement was lost did land.
_UNDETERMINED_ERRORS = (MySQLConnectionLostError,)


def _safe_error(exc: Exception, tool: str) -> str:
    """Return an agent-safe error string; log full detail server-side only."""
    logger.error("Tool %s failed", tool, exc_info=True)
    _passthrough = (
        ValueError,
        FileNotFoundError,
        KeyError,
        PermissionError,
        TimeoutError,
        ConnectionError,
        MySQLError,
    )
    if isinstance(exc, _passthrough):
        return sanitize(str(exc), _ERROR_MAX)
    return f"{type(exc).__name__}: operation failed."


def tool_errors(shape: str = "dict") -> Callable:
    """Wrap a tool body in the canonical try/except → ``_safe_error`` pattern.

    Place this *between* ``@governed_tool`` and the function so the audit
    decorator and FastMCP still see the original signature.
    """

    def decorator(func: Callable) -> Callable:
        name = func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 — sanitised below
                msg = _safe_error(e, name)
                if shape == "list":
                    return [{"error": msg, "hint": _DOCTOR_HINT}]
                if shape == "str":
                    return f"Error: {msg} {_DOCTOR_HINT}"
                payload = {"error": msg, "hint": _DOCTOR_HINT}
                # Flatten the exception into a dict and its type is gone
                # for good — so classify here, while it is still known,
                # whether the operation may nonetheless have taken effect.
                if isinstance(e, _UNDETERMINED_ERRORS):
                    return mark_unknown(payload)
                return payload

        return wrapper

    return decorator


mcp = FastMCP(
    "mysql-aiops",
    instructions=(
        "Governed MySQL / MariaDB DBA operations: a one-shot server "
        "'overview'; server reads (version+flavor/variables/status/databases/"
        "engines); activity (sessions, long-running queries, open transactions, "
        "lock waits); query stats (statement-digest top-N, EXPLAIN); index and "
        "table health (unused / redundant / fragmentation); replication "
        "(replica status, binlog); four flagship analyses — 'slow_query_rca', "
        "'lock_wait_rca', 'replication_lag_rca' and 'fragmentation_analysis'; "
        "and guarded writes (kill session/query, optimize/analyze table, "
        "create/drop index, SET GLOBAL). Every tool runs through the "
        "mysql-aiops governance harness (audit / budget / risk-tier / undo). "
        "Do NOT use for OT/industrial edge — see industrial-aiops."
    ),
)

_conn_mgr: Optional[ConnectionManager] = None


def _get_connection(target: Optional[str] = None) -> Any:
    """Return a MySQL connection, lazily initialising the manager."""
    global _conn_mgr  # noqa: PLW0603
    if _conn_mgr is None:
        config_path_str = os.environ.get("MYSQL_AIOPS_CONFIG")
        config_path = Path(config_path_str) if config_path_str else None
        _conn_mgr = ConnectionManager(load_config(config_path))
    return _conn_mgr.connect(target)
