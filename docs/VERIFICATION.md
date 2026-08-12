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

- **MariaDB** — partially closed. The **`information_schema.innodb_lock_waits`
  branch is live-verified** (2026-08-04, real MariaDB 11.8.8): flavor detected
  as `mariadb`, a genuinely contended row lock produced the right wait-for edge
  (blocked thread 11 on blocking thread 10, matching the server's own
  `innodb_lock_waits`), the RCA named the root blocker with its real wait, and
  the MySQL-only columns came back **`null` rather than `""`** — the branch
  reports "this flavour cannot supply it", not "it is empty". A real MariaDB
  deadlock was parsed correctly too (and exposed the `MariaDB thread id`
  spelling bug, below). **`SHOW SLAVE STATUS` on a real MariaDB replica is now verified**
  (2026-08-12, real MariaDB 11.8.8 primary/replica pair): `doctor` identified
  each end correctly, `repl status` matched the server's own output field for
  field, and a genuinely broken applier (`Table 'shop.orders' doesn't exist`)
  surfaced as `lastSqlErrno: 1146` with the full message and the right RCA cause
  — with `secondsBehindSource: null` rather than `0` while the SQL thread was
  stopped. **That run also found a real defect**: on MariaDB every GTID field was
  null, so a replica running `Using_Gtid: Slave_Pos` / `Gtid_IO_Pos: 0-1-6` was
  indistinguishable from one not using GTID. `usingGtid` / `gtidIoPos` (per
  channel) and `gtidCurrentPos` / `gtidStrictMode` (global) are now reported, and
  MySQL 8.4.11 was re-checked to confirm its own answer did not move.
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
- ~~**Lock waits** (`lock_wait_rca`) — needs deliberately contended transactions.~~
  **Verified 2026-08-04 on BOTH flavours** with genuinely contended
  transactions: a real row-lock wait (MySQL 8.4.11) and a real deadlock produced
  on MySQL 8.4.11 **and** MariaDB 11.8.
  - The wait-for read matched ground truth exactly (blocked thread 21 waiting on
    thread 20, matching `performance_schema.data_lock_waits`), and the RCA named
    the right root blocker with its real 47-second wait. The MySQL-only columns
    are genuinely populated (`objectSchema: labdb`, `objectName: accounts`) —
    the data the MariaDB branch cannot supply, so the flavour split is real and
    not just two code paths returning the same thing.
  - **Two defects were found in the deadlock parser, which had never been run
    against real `SHOW ENGINE INNODB STATUS` output.** (1) `transactions[].query`
    carried InnoDB's own bookkeeping — it began "mysql tables in use 1, locked 1
    LOCK WAIT 3 lock struct(s), heap size 1128, ..." before reaching the actual
    UPDATE — because every line before the lock listing was folded into a field
    named `query`. (2) `detectedAt` carried the trailing thread handle
    ("2026-08-04 05:25:29 136464230757952"), so it was not a parseable
    timestamp. The unit fixture was **idealised**: it put the statement directly
    under the TRANSACTION header, a shape no real server emits, which is why the
    parser passed. Fixed by anchoring on the thread-id line and extracting the
    timestamp; the fixture is now verbatim real output.
  - **The first fix was MySQL-only and MariaDB caught it**: MariaDB writes
    `MariaDB thread id`, MySQL writes `MySQL thread id`, so anchoring on the
    MySQL spelling sent MariaDB down the fallback path and reported the entire
    thread-id line as the query. Both spellings now match, verified live on
    both servers.
- Privilege-degradation paths and `performance_schema = OFF` behaviour.
