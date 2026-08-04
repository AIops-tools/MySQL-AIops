"""Flagship analysis tests (pure functions, no I/O)."""

from __future__ import annotations

import pytest

from mysql_aiops.ops import analysis

# Verbatim shape of real `SHOW ENGINE INNODB STATUS` output (MySQL 8.4.11, from
# a deadlock deliberately produced on a live server). The previous fixture was
# idealised — the statement followed the TRANSACTION header directly — so the
# parser was never exercised against the bookkeeping lines every real server
# emits, and folded them into the "query" field.
_INNODB_STATUS = """
=====================================
2026-07-17 12:00:00 INNODB MONITOR OUTPUT
=====================================
------------------------
LATEST DETECTED DEADLOCK
------------------------
2026-08-04 05:25:29 136464230757952
*** (1) TRANSACTION:
TRANSACTION 4213, ACTIVE 5 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1128, 2 row lock(s), undo log entries 1
MySQL thread id 30, OS thread handle 136464332559936, query id 106 localhost root updating
UPDATE orders SET status='x' WHERE id=1

*** (1) HOLDS THE LOCK(S):
RECORD LOCKS space id 2 page no 4 n bits 72 index PRIMARY of table `labdb`.`orders`
Record lock, heap no 2 PHYSICAL RECORD: n_fields 5; compact format; info bits 0

*** (2) TRANSACTION:
TRANSACTION 4214, ACTIVE 4 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1128, 2 row lock(s), undo log entries 1
MySQL thread id 31, OS thread handle 136464332559111, query id 107 localhost root updating
UPDATE orders SET status='y' WHERE id=2
*** WE ROLL BACK TRANSACTION (2)
------------
TRANSACTIONS
------------
"""

# Same deadlock without the "MySQL thread id" anchor line, to pin the fallback.
_INNODB_STATUS_NO_THREAD_LINE = """
------------------------
LATEST DETECTED DEADLOCK
------------------------
2026-07-17 11:58:03
*** (1) TRANSACTION:
TRANSACTION 4213, ACTIVE 5 sec starting index read
mysql tables in use 1, locked 1
UPDATE orders SET status='x' WHERE id=1
*** WE ROLL BACK TRANSACTION (1)
------------
TRANSACTIONS
------------
"""


@pytest.mark.unit
def test_deadlock_query_excludes_innodb_bookkeeping():
    """The `query` field must hold the statement, not InnoDB's own counters.

    Measured against a real MySQL 8.4 deadlock: every transaction block carries
    "mysql tables in use ...", "LOCK WAIT ... heap size ..." and "MySQL thread
    id ..." lines between the header and the statement. Folding those in
    produced a "query" beginning "mysql tables in use 1, locked 1 LOCK WAIT 3
    lock struct(s), heap size 1128, ..." — text a model would quote back as SQL.
    """
    out = analysis.parse_last_deadlock(_INNODB_STATUS)
    assert [t["query"] for t in out["transactions"]] == [
        "UPDATE orders SET status='x' WHERE id=1",
        "UPDATE orders SET status='y' WHERE id=2",
    ]
    assert out["victim"] == 2


@pytest.mark.unit
def test_deadlock_detected_at_is_a_timestamp_not_a_thread_handle():
    """MySQL 8.x stamps "<timestamp> <thread handle>"; only the time is the time."""
    out = analysis.parse_last_deadlock(_INNODB_STATUS)
    assert out["detectedAt"] == "2026-08-04 05:25:29"


@pytest.mark.unit
def test_deadlock_query_falls_back_without_the_thread_id_anchor():
    out = analysis.parse_last_deadlock(_INNODB_STATUS_NO_THREAD_LINE)
    assert out["transactions"][0]["query"] == "UPDATE orders SET status='x' WHERE id=1"


# MariaDB names ITSELF on the thread-id line — verbatim from a real deadlock on
# MariaDB 11.8. Anchoring only on the MySQL spelling made this fall through to
# the fallback, which reported the whole thread-id line as the query.
_INNODB_STATUS_MARIADB = """
------------------------
LATEST DETECTED DEADLOCK
------------------------
2026-08-04 05:29:15 0x7023bd5196c0
*** (1) TRANSACTION:
TRANSACTION 25, ACTIVE 4 sec starting index read
mysql tables in use 1, locked 1
LOCK WAIT 3 lock struct(s), heap size 1120, 2 row lock(s), undo log entries 1
MariaDB thread id 6, OS thread handle 123298802407104, query id 12 localhost root Updating
UPDATE accounts SET balance=balance-1 WHERE id=1
*** WAITING FOR THIS LOCK TO BE GRANTED:
RECORD LOCKS space id 8 page no 3 n bits 320 index PRIMARY of table `labdb`.`accounts`
*** WE ROLL BACK TRANSACTION (1)
------------
TRANSACTIONS
------------
"""


@pytest.mark.unit
def test_deadlock_query_handles_mariadbs_own_thread_id_spelling():
    out = analysis.parse_last_deadlock(_INNODB_STATUS_MARIADB)
    assert out["transactions"][0]["query"] == "UPDATE accounts SET balance=balance-1 WHERE id=1"
    assert out["detectedAt"] == "2026-08-04 05:29:15"  # hex thread handle stripped


@pytest.mark.unit
def test_slow_query_rca_picks_worst_and_flags_signals():
    statements = [
        {"digest": "a", "query": "SELECT small", "totalTimeMs": 10, "meanTimeMs": 1,
         "calls": 10, "noIndexUsedPct": 0.0, "lockTimePct": 0.0,
         "rowsExaminedPerSent": 1.0, "tmpDiskTables": 0},
        {"digest": "b", "query": "SELECT big", "totalTimeMs": 9000, "meanTimeMs": 300,
         "calls": 30, "noIndexUsedPct": 90.0, "lockTimePct": 70.0,
         "rowsExaminedPerSent": 500.0, "tmpDiskTables": 12},
    ]
    explain = {"plan": {"query_block": {"table": {"access_type": "ALL"}}}}
    out = analysis.slow_query_rca(statements, explain=explain)
    assert out["worst"]["digest"] == "b"
    signals = {f["signal"] for f in out["findings"]}
    assert "full scan / no index used" in signals
    assert "lock time dominant" in signals
    assert "high rows examined per row sent" in signals
    assert "temporary tables spilled to disk" in signals
    assert "ALL" in out["planAccessTypes"]


@pytest.mark.unit
def test_slow_query_rca_empty():
    out = analysis.slow_query_rca([])
    assert out["evaluated"] == 0 and out["worst"] is None


@pytest.mark.unit
def test_slow_query_rca_no_dominant_signal():
    out = analysis.slow_query_rca([
        {"digest": "a", "totalTimeMs": 100, "meanTimeMs": 5, "calls": 20,
         "noIndexUsedPct": 0.0, "lockTimePct": 0.0, "tmpDiskTables": 0},
    ])
    assert out["findings"][0]["signal"] == "no dominant signal"


@pytest.mark.unit
def test_lock_wait_chain_names_root_blocker():
    # 100 blocks 200; 200 blocks 300 → root is 100, blocking 2 sessions.
    pairs = [
        {"blockedId": 200, "blockingId": 100, "blockingQuery": "UPDATE a",
         "waitSeconds": 12},
        {"blockedId": 300, "blockingId": 200, "blockingQuery": "UPDATE b",
         "waitSeconds": 4},
    ]
    out = analysis.lock_wait_rca(pairs)
    assert out["worstRootId"] == 100
    assert out["roots"][0]["blockedCount"] == 2
    assert out["roots"][0]["maxWaitSeconds"] == 12
    assert "kill_session" in out["roots"][0]["action"]


@pytest.mark.unit
def test_lock_wait_chain_detects_cycle():
    pairs = [
        {"blockedId": 1, "blockingId": 2},
        {"blockedId": 2, "blockingId": 1},
    ]
    out = analysis.lock_wait_rca(pairs)
    assert out.get("deadlockSuspected") is True


@pytest.mark.unit
def test_lock_wait_no_blocking():
    out = analysis.lock_wait_rca([])
    assert out["blockedSessions"] == 0


@pytest.mark.unit
def test_lock_wait_parses_last_deadlock_from_innodb_status():
    out = analysis.lock_wait_rca([], innodb_status=_INNODB_STATUS)
    dl = out["lastDeadlock"]
    assert dl is not None
    assert dl["victim"] == 2
    assert len(dl["transactions"]) == 2
    assert "UPDATE orders SET status='x'" in dl["transactions"][0]["query"]


@pytest.mark.unit
def test_parse_last_deadlock_none_when_absent():
    assert analysis.parse_last_deadlock("INNODB MONITOR OUTPUT\nno deadlock here") is None
    assert analysis.parse_last_deadlock("") is None


@pytest.mark.unit
def test_replication_rca_not_a_replica():
    out = analysis.replication_lag_rca({"isReplica": False, "replicas": []})
    assert out["isReplica"] is False and out["findings"] == []


@pytest.mark.unit
def test_replication_rca_io_thread_stopped():
    status = {"replicas": [{
        "ioThreadRunning": "No", "sqlThreadRunning": "Yes",
        "secondsBehindSource": None,
        "lastIoError": "error connecting to master",
    }]}
    out = analysis.replication_lag_rca(status)
    signals = {f["signal"] for f in out["findings"]}
    assert "IO thread not running" in signals
    assert any("lastIoError" in f["detail"] for f in out["findings"])


@pytest.mark.unit
def test_replication_rca_sql_thread_stopped_with_error():
    status = {"replicas": [{
        "ioThreadRunning": "Yes", "sqlThreadRunning": "No",
        "secondsBehindSource": None,
        "lastSqlError": "Duplicate entry '7' for key 'PRIMARY'",
    }]}
    out = analysis.replication_lag_rca(status)
    signals = {f["signal"] for f in out["findings"]}
    assert "SQL thread not running" in signals


@pytest.mark.unit
def test_replication_rca_lagging():
    status = {"replicas": [{
        "ioThreadRunning": "Yes", "sqlThreadRunning": "Yes",
        "secondsBehindSource": 900, "sqlDelay": 0,
    }]}
    out = analysis.replication_lag_rca(status)
    assert out["findings"][0]["signal"] == "replica lagging"
    assert "900" in out["findings"][0]["detail"]


@pytest.mark.unit
def test_replication_rca_intentional_delay():
    status = {"replicas": [{
        "ioThreadRunning": "Yes", "sqlThreadRunning": "Yes",
        "secondsBehindSource": 3600, "sqlDelay": 3600,
    }]}
    out = analysis.replication_lag_rca(status)
    assert out["findings"][0]["signal"] == "intentional apply delay configured"


@pytest.mark.unit
def test_replication_rca_healthy():
    status = {"replicas": [{
        "ioThreadRunning": "Yes", "sqlThreadRunning": "Yes",
        "secondsBehindSource": 0,
    }]}
    out = analysis.replication_lag_rca(status)
    assert out["findings"][0]["signal"] == "replication healthy"


@pytest.mark.unit
def test_fragmentation_flags_high_free():
    tables = [
        {"schema": "shop", "table": "hot", "engine": "InnoDB",
         "freePct": 40.0, "freeBytes": 500 * 1024 * 1024, "freePretty": "500.0 MB"},
        {"schema": "shop", "table": "cold", "engine": "InnoDB",
         "freePct": 5.0, "freeBytes": 1024, "freePretty": "1.0 kB"},
    ]
    out = analysis.fragmentation_analysis(tables)
    assert out["needsAttentionCount"] == 1
    assert out["recommendations"][0]["table"] == "hot"
    assert "optimize_table" in out["recommendations"][0]["action"]


@pytest.mark.unit
def test_fragmentation_needs_both_pct_and_bytes():
    # High percentage but tiny absolute free space → not worth an OPTIMIZE.
    tables = [
        {"schema": "shop", "table": "tiny", "engine": "InnoDB",
         "freePct": 60.0, "freeBytes": 4096},
    ]
    out = analysis.fragmentation_analysis(tables)
    assert out["needsAttentionCount"] == 0
