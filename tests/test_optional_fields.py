"""Absent fields come back as null, not as an empty string.

An empty string reads as "this field exists and is empty"; a missing field is a
different fact. MySQL result rows are full of genuinely-absent values — a
sleeping session has a NULL ``INFO`` (no current statement), a replica that has
never failed has no ``Last_IO_Error``, and MariaDB has no ``gtid_mode`` variable
at all. Collapsing those into ``""`` hides the difference from the caller, and a
smaller local model will confidently invent one.

These tests pin the contract end-to-end: the helper, the ops normalisers, and
the truncation envelope that tells a consumer when a top-N read was cut off.
"""

from __future__ import annotations

import pytest

from mysql_aiops.governance import opt_str
from mysql_aiops.ops import activity, queries, replication, tables
from mysql_aiops.ops._util import opt, s
from tests.conftest import FakeMySQL

# ── the helper ──────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_opt_str_distinguishes_absent_from_empty():
    assert opt_str(None) is None, "absent must stay absent"
    assert opt_str("") == "", "a genuinely empty value is not the same as absent"
    assert opt_str("users", 64) == "users"


@pytest.mark.unit
def test_opt_str_still_sanitizes_and_truncates():
    assert opt_str("a\x00b") == "ab"  # control character stripped
    assert opt_str("abcdef", 3) == "abc"


@pytest.mark.unit
def test_opt_str_accepts_non_string_values():
    assert opt_str(42) == "42"


@pytest.mark.unit
def test_ops_opt_helper_preserves_absence_while_s_still_coerces():
    assert opt(None) is None
    assert s(None) == "", "s() keeps its always-present semantics"


# ── the ops layer ───────────────────────────────────────────────────────────


@pytest.mark.unit
def test_sleeping_session_reports_a_null_query_not_an_empty_one():
    """A session with no current statement has NULL INFO — not a blank query.

    This is the difference between "this connection is idle" and "this
    connection is running a statement we could not read".
    """
    conn = FakeMySQL({"information_schema.processlist": [
        {"id": 7, "user": "app", "command": "Sleep", "time": 300},
    ]})
    row = activity.list_sessions(conn)["sessions"][0]
    assert row["query"] is None
    assert row["database"] is None


@pytest.mark.unit
def test_session_keeps_empty_string_when_the_server_returned_one():
    conn = FakeMySQL({"information_schema.processlist": [{"id": 7, "db": ""}]})
    assert activity.list_sessions(conn)["sessions"][0]["database"] == ""


@pytest.mark.unit
def test_session_row_never_drops_the_key_itself():
    """Keys are always present; only their value may be null.

    Omitting a key entirely is worse than a null — the consumer cannot tell the
    field was even considered.
    """
    conn = FakeMySQL({"information_schema.processlist": [{}]})
    row = activity.list_sessions(conn)["sessions"][0]
    for key in ("user", "database", "command", "state", "query"):
        assert key in row, f"{key} must be present even when the server omitted it"


@pytest.mark.unit
def test_table_row_reports_absent_engine_as_null():
    conn = FakeMySQL({"information_schema.tables": [{"schema": "app", "table": "t"}]})
    row = tables.table_sizes(conn)["tables"][0]
    assert row["engine"] is None


@pytest.mark.unit
def test_mariadb_reports_a_null_gtid_mode_rather_than_an_empty_one():
    """MariaDB has no gtid_mode variable at all — that is not "set to empty".

    SHOW GLOBAL VARIABLES returns no row, so the value is absent. Reporting ""
    would read as a server with GTID explicitly turned off to blank.
    """
    conn = FakeMySQL({"SHOW BINARY LOGS": [], "SHOW PROCESSLIST": []})
    out = replication.binlog_status(conn)
    assert out["gtidMode"] is None
    assert out["logBin"] is False, "an absent log_bin is not 'ON'"


# ── truncation announces itself ─────────────────────────────────────────────


@pytest.mark.unit
def test_top_queries_report_truncation_when_more_digests_exist():
    """The 21st slow query may be the one causing the incident — say so.

    Truncation is measured by asking for limit + 1 rows, not guessed from the
    returned count happening to equal the limit.
    """
    rows = [{"digest": f"d{i}", "digest_text": f"SELECT {i}"} for i in range(6)]
    conn = FakeMySQL({"events_statements_summary_by_digest": rows})
    out = queries.top_queries(conn, limit=5)
    assert out["returned"] == 5
    assert out["limit"] == 5
    assert out["truncated"] is True


@pytest.mark.unit
def test_top_queries_are_not_marked_truncated_when_they_fit():
    rows = [{"digest": f"d{i}", "digest_text": f"SELECT {i}"} for i in range(3)]
    conn = FakeMySQL({"events_statements_summary_by_digest": rows})
    out = queries.top_queries(conn, limit=5)
    assert out["returned"] == 3 and out["truncated"] is False


@pytest.mark.unit
def test_top_queries_ask_the_server_for_one_extra_row():
    """Without the +1 the count could never exceed the limit, so nothing would
    ever look truncated — the measurement depends on over-fetching by one."""
    conn = FakeMySQL({"events_statements_summary_by_digest": []})
    queries.top_queries(conn, limit=20)
    _sql, params = conn.queried[-1]
    assert params == {"limit": 21}


@pytest.mark.unit
def test_table_sizes_report_truncation():
    rows = [{"schema": "app", "table": f"t{i}"} for i in range(4)]
    conn = FakeMySQL({"information_schema.tables": rows})
    out = tables.table_sizes(conn, limit=3)
    assert out["returned"] == 3 and out["truncated"] is True
    assert len(out["tables"]) == 3, "only the requested number of rows is returned"


@pytest.mark.unit
def test_undo_list_envelope_measures_truncation(monkeypatch):
    from mcp_server.tools import undo as undo_tools

    rows = [
        {
            "undo_id": f"u{i}",
            "ts": "2026-07-18T00:00:00Z",
            "tool": "some_tool",
            "undo_tool": "some_inverse_tool",
            "note": "",
        }
        for i in range(4)
    ]
    captured = {}

    class _Store:
        def list(self, *, status=None, limit=50):
            captured["limit"] = limit
            return rows[:limit]

    monkeypatch.setattr(undo_tools, "get_undo_store", lambda: _Store())
    result = undo_tools.undo_list(limit=3)
    assert captured["limit"] == 4, "one extra row is fetched to measure truncation"
    assert result["returned"] == 3
    assert result["limit"] == 3
    assert result["truncated"] is True
    assert len(result["undos"]) == 3
