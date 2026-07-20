"""A statement whose connection died is not a statement that failed.

The engine usually rolls back when the backend dies, so most of the time
nothing landed — but a COMMIT whose acknowledgement was lost DID land, and from
the client the two are indistinguishable. Reporting that as a definite failure
asserts something the tool cannot vouch for, so it is classified 'unknown'.

The discriminator is WHERE the error is raised, not its class or code: the
driver reports "could not connect" and "the link died mid-statement" the same
way, and only the statement-executing path knows a connection was established.
"""

from __future__ import annotations

import pymysql
import pytest

from mcp_server._shared import _UNDETERMINED_ERRORS
from mysql_aiops.config import TargetConfig
from mysql_aiops.connection import (
    MySQLConnection,
    MySQLConnectionLostError,
    MySQLError,
)


class _RaisingCursor:
    def __init__(self, exc): self._exc = exc
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): raise self._exc


class _RaisingConn:
    def __init__(self, exc): self._exc = exc
    def cursor(self): return _RaisingCursor(self._exc)


def _conn_raising(exc):
    """A real MySQLConnection whose driver raises on execute()."""
    target = TargetConfig(name="t", host="h", user="u", database="d")
    return MySQLConnection(target, connection=_RaisingConn(exc))


@pytest.mark.unit
def test_connection_lost_is_classified_undetermined():
    assert issubclass(MySQLConnectionLostError, _UNDETERMINED_ERRORS)


@pytest.mark.unit
def test_an_ordinary_failure_is_not_classified_undetermined():
    """The distinction has to be narrow, or every unreachable server cries wolf."""
    assert not issubclass(MySQLError, _UNDETERMINED_ERRORS)


@pytest.mark.unit
def test_a_lost_link_mid_statement_raises_the_dedicated_class():
    conn = _conn_raising(
        pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
    )
    with pytest.raises(MySQLConnectionLostError):
        conn.execute("UPDATE t SET c = 1")


@pytest.mark.unit
def test_a_server_side_error_stays_an_ordinary_failure():
    """The server answered, so the outcome is known — not undetermined."""
    conn = _conn_raising(pymysql.err.OperationalError(1146, "Table 'x' doesn't exist"))
    with pytest.raises(MySQLError) as caught:
        conn.execute("UPDATE t SET c = 1")
    assert not isinstance(caught.value, MySQLConnectionLostError)
