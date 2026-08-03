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
  the index, the audit row landed in `audit.db` with the optional approver +
  rationale annotation set, an undo token was recorded with `drop_index` as
  the inverse, and `undo_apply` really dropped it — capturing the exact
  `CREATE INDEX` definition as `priorState` so the undo is itself reversible.
- `remediate ... --dry-run` made no change (verified against `SHOW INDEX`).
- The harness authorizes nothing — there is no read-only, deny-rule, or
  approver gate to test.

**A real bug was found and fixed by this run**: `server databases` crashed with
`TypeError: Object of type Decimal is not JSON serializable`. MySQL returns
`SUM()` aggregates as `decimal.Decimal`; the mock suite handed back plain ints,
so no unit test could see it. Fixed with `as_int()` plus a regression test that
models the driver's real types.

## Not yet live-verified ⚠️

- **MariaDB** — the flavor branch (`SHOW SLAVE STATUS`,
  `information_schema.innodb_lock_waits`) is unit-tested only. This is now the
  largest remaining gap in this repo.
- ~~**Replication** (`replication_lag_rca`, `repl status` against a real replica)~~
  — **closed 2026-08-03 against a real MySQL 8.4.11 primary/replica pair with
  GTID replication. No defects found.** Recorded because a clean result is
  evidence too:
  - `doctor` identified each end correctly (`role: replica (seconds behind
    source: 0)` vs `primary/standalone`); `repl status` returned the full
    record (GTID sets, relay-log space, thread states) and the primary side
    listed its connected downstream replica.
  - A **deliberate `SOURCE_DELAY=120`** was reported as *"intentional apply
    delay configured"*, not as a lag incident — the distinction that decides
    whether anyone gets paged.
  - Replication was then genuinely **broken** by colliding a row written
    directly on the replica with one replicated from the primary
    (`Last_SQL_Errno: 1062`). The RCA reported `sqlThreadRunning: false`, named
    duplicate-key/schema divergence as the cause, and advised repairing rather
    than skipping the event. `secondsBehindSource` came back **null, not 0** —
    the difference between "no lag" and "unknown while stopped".
- **Lock waits** (`lock_wait_rca`) — needs deliberately contended transactions.
- Privilege-degradation paths and `performance_schema = OFF` behaviour.
