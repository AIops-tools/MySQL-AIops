"""Shared test doubles for the ops layer (no live database).

``FakeMySQL`` mimics :class:`mysql_aiops.connection.MySQLConnection`'s surface
(``query``/``query_one``/``scalar``/``execute``/``flavor``). Responses are
matched by substring of the SQL, so a single fake can serve the several queries
a flagship analysis issues, and every executed write is recorded for assertions.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _default_approver(monkeypatch):
    """Record a synthetic approver annotation globally.

    The harness authorizes nothing, so this gates nothing; it only ensures the
    optional ``approved_by`` audit field is populated for tests that do not set
    their own. The governance-persistence tests clear it to show the annotation
    is genuinely optional."""
    monkeypatch.setenv("MYSQL_AUDIT_APPROVED_BY", "pytest")


class FakeMySQL:
    def __init__(
        self,
        responses: dict[str, list[dict]] | None = None,
        scalars: dict[str, Any] | None = None,
        flavor: str = "mysql",
    ) -> None:
        self.responses = responses or {}
        self.scalars = scalars or {}
        self.flavor = flavor
        self.executed: list[tuple[str, Any]] = []
        self.queried: list[tuple[str, Any]] = []

    @staticmethod
    def _match(table: dict, sql: str) -> Any:
        for key, value in table.items():
            if key in sql:
                return value
        return None

    def query(self, sql: str, params: Any | None = None) -> list[dict]:
        self.queried.append((sql, params))
        rows = self._match(self.responses, sql)
        return list(rows) if rows is not None else []

    def query_one(self, sql: str, params: Any | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Any | None = None) -> Any:
        self.queried.append((sql, params))
        return self._match(self.scalars, sql)

    def execute(self, sql: str, params: Any | None = None) -> int:
        self.executed.append((sql, params))
        return 1


# Statements that CHANGE the server. For a SQL tool the mutating call is a
# statement, not an HTTP verb — and it does not always arrive via ``execute``:
# OPTIMIZE/ANALYZE TABLE return rows, so ops issues them through ``query``.
# Asserting only on ``fake.executed`` would miss exactly those two writes.
_MUTATING_SQL = (
    "KILL ",
    "TRUNCATE ",
    "CREATE INDEX",
    "CREATE UNIQUE INDEX",
    "DROP INDEX",
    "SET GLOBAL ",
    "OPTIMIZE TABLE",
    "ANALYZE TABLE",
    "ALTER ",
    "INSERT ",
    "UPDATE ",
    "DELETE ",
)


def mutating_statements(fake: FakeMySQL) -> list[str]:
    """Every statement ``fake`` was asked to run that would change the server.

    A dry_run MAY read — it connects, it may fetch before-state — but it must
    never write. This is the assertion that states that rule precisely, across
    both ``execute`` and ``query``.
    """
    seen = [sql for sql, _ in fake.executed] + [sql for sql, _ in fake.queried]
    return [sql for sql in seen if any(kw in sql.upper() for kw in _MUTATING_SQL)]


@pytest.fixture
def fake_mysql():
    return FakeMySQL
