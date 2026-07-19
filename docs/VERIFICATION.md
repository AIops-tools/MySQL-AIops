# Live verification status

This document records what has and has not been validated against a real
MySQL / MariaDB server, so the maturity claim is auditable rather than a vibe.

## Already live-verified ✅ — MySQL 8.4.10, 2026-07-19

Exercised end-to-end against a real **MySQL 8.4.10** server (Docker), with a
100,000-row table carrying a deliberately unindexed filter column:

- `doctor` against a live server: flavor correctly detected as `mysql`,
  `performance_schema` reported ON, replica role identified as
  primary/standalone.
- Reads cross-checked against the values the `mysql` client reports:
  `overview`, `server status/variables/engines/connections/databases`,
  `activity sessions/long/transactions`, `table sizes/fragmentation`,
  `index unused/stats`, `query top`, `repl status`.
- `analyze slow-query` on genuine full scans (5 executions, 500,000 rows
  examined): correctly classified `full scan / no index used` at
  `noIndexUsedPct 100.0%`, citing the measured number.
- Governance loop end-to-end: `create_index` via the MCP tool actually created
  the index, the audit row landed in `audit.db` with approver + rationale, an
  undo token was recorded with `drop_index` as the inverse, and `undo_apply`
  really dropped it — capturing the exact `CREATE INDEX` definition as
  `priorState` so the undo is itself reversible.
- `remediate ... --dry-run` made no change (verified against `SHOW INDEX`).
- Read-only mode: `MYSQL_READ_ONLY=1` took the registry from 35 tools to 26,
  and an in-process write call was refused by the harness with a teaching
  message — both layers confirmed on a live server.

**A real bug was found and fixed by this run**: `server databases` crashed with
`TypeError: Object of type Decimal is not JSON serializable`. MySQL returns
`SUM()` aggregates as `decimal.Decimal`; the mock suite handed back plain ints,
so no unit test could see it. Fixed with `as_int()` plus a regression test that
models the driver's real types.

## Not yet live-verified ⚠️

- **MariaDB** — the flavor branch (`SHOW SLAVE STATUS`,
  `information_schema.innodb_lock_waits`) is unit-tested only. This is now the
  largest remaining gap in this repo.
- **Replication** (`replication_lag_rca`, `repl status` against a real replica)
  — the verified instance was standalone.
- **Lock waits** (`lock_wait_rca`) — needs deliberately contended transactions.
- Privilege-degradation paths and `performance_schema = OFF` behaviour.
