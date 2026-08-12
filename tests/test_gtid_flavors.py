"""GTID reporting must differ by flavour, because the servers genuinely differ.

Measured against real servers (MariaDB 11.8.8 and MySQL 8.4.11):

* MySQL has a global ``gtid_mode`` switch and reports ``Executed_Gtid_Set`` /
  ``Retrieved_Gtid_Set`` per channel.
* MariaDB has **no ``gtid_mode`` at all**. It reports ``gtid_current_pos`` /
  ``gtid_strict_mode`` globally and ``Using_Gtid`` / ``Gtid_IO_Pos`` per channel.

Before this, a MariaDB replica came back with **every** GTID field null — so a
GTID-based replica (`Using_Gtid: Slave_Pos`, `Gtid_IO_Pos: 0-1-6`) was
indistinguishable from one not using GTID at all. Both configurations were
measured on the same live server to confirm that.
"""

from __future__ import annotations

from typing import Any

import pytest

from mysql_aiops.ops import replication

pytestmark = pytest.mark.unit


class _VarFake:
    """A fake that answers SHOW GLOBAL VARIABLES by the *parameter*, not the SQL.

    The shared fixture matches on SQL text, and every variable lookup uses the
    same statement — so it cannot distinguish gtid_mode from gtid_current_pos.
    """

    def __init__(self, variables: dict[str, str], flavor: str = "mysql") -> None:
        self.variables = variables
        self.flavor = flavor

    def query(self, sql: str, params: Any | None = None) -> list[dict]:
        if "SHOW GLOBAL VARIABLES" in sql:
            name = (params or {}).get("n")
            if name in self.variables:
                return [{"Variable_name": name, "Value": self.variables[name]}]
            return []
        return []

    def query_one(self, sql: str, params: Any | None = None) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


#: Exactly what MariaDB 11.8.8 returned.
MARIADB_VARS = {
    "log_bin": "ON", "server_id": "2", "binlog_format": "ROW",
    "gtid_current_pos": "0-1-5", "gtid_strict_mode": "OFF",
}
#: Exactly what MySQL 8.4.11 returned.
MYSQL_VARS = {
    "log_bin": "ON", "server_id": "10", "binlog_format": "ROW", "gtid_mode": "ON",
}


def test_mariadb_reports_its_own_gtid_facts_not_just_a_null_mysql_field():
    out = replication.binlog_status(_VarFake(MARIADB_VARS, flavor="mariadb"))
    assert out["gtidMode"] is None            # MariaDB has no such variable
    assert out["gtidCurrentPos"] == "0-1-5"   # …but it does have this
    assert out["gtidStrictMode"] == "OFF"
    # And the payload says why the null is not "GTID is off".
    assert "does NOT mean GTID is off" in out["gtidNote"]


def test_mysql_still_reports_gtid_mode_and_nulls_the_mariadb_fields():
    """The fix must not have moved MySQL's answer."""
    out = replication.binlog_status(_VarFake(MYSQL_VARS))
    assert out["gtidMode"] == "ON"
    assert out["gtidCurrentPos"] is None
    assert out["gtidStrictMode"] is None
    assert "MariaDB-only" in out["gtidNote"]


def test_a_mariadb_replica_row_says_whether_it_is_using_gtid():
    """The row as MariaDB actually returns it: no Executed/Retrieved columns, but
    Using_Gtid and Gtid_IO_Pos present."""
    row = replication._normalize_replica_row({
        "Master_Host": "mdb-p", "Master_Port": 3306,
        "Slave_IO_Running": "Yes", "Slave_SQL_Running": "Yes",
        "Seconds_Behind_Master": 0, "Relay_Log_Space": 865,
        "Using_Gtid": "Slave_Pos", "Gtid_IO_Pos": "0-1-6",
    })
    assert row["usingGtid"] == "Slave_Pos"
    assert row["gtidIoPos"] == "0-1-6"
    # The MySQL-only columns stay null — absent, not empty.
    assert row["executedGtidSet"] is None
    assert row["retrievedGtidSet"] is None


def test_a_mariadb_replica_not_using_gtid_is_distinguishable():
    """The whole point: file/position replication must not look like GTID
    replication. Measured — the same server reported Using_Gtid: No before the
    switch and Slave_Pos after."""
    row = replication._normalize_replica_row({
        "Master_Host": "mdb-p", "Slave_IO_Running": "Yes",
        "Using_Gtid": "No", "Gtid_IO_Pos": "",
    })
    assert row["usingGtid"] == "No"
    assert row["gtidIoPos"] == ""   # present and empty, which is what it was


def test_a_mysql_replica_row_keeps_its_gtid_sets():
    row = replication._normalize_replica_row({
        "Source_Host": "p", "Replica_IO_Running": "Yes",
        "Executed_Gtid_Set": "aaa:1-5", "Retrieved_Gtid_Set": "aaa:1-5",
    })
    assert row["executedGtidSet"] == "aaa:1-5"
    assert row["usingGtid"] is None   # MySQL has no such column
