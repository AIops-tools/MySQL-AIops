"""Flagship analysis tests (pure functions, no I/O)."""

from __future__ import annotations

import pytest

from mysql_aiops.ops import analysis

_INNODB_STATUS = """
=====================================
2026-07-17 12:00:00 INNODB MONITOR OUTPUT
=====================================
------------------------
LATEST DETECTED DEADLOCK
------------------------
2026-07-17 11:58:03
*** (1) TRANSACTION:
TRANSACTION 4213, ACTIVE 5 sec starting index read
UPDATE orders SET status='x' WHERE id=1
*** (2) TRANSACTION:
TRANSACTION 4214, ACTIVE 4 sec starting index read
UPDATE orders SET status='y' WHERE id=2
*** WE ROLL BACK TRANSACTION (2)
------------
TRANSACTIONS
------------
"""


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
