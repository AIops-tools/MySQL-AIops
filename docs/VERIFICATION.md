# Live verification — MySQL / MariaDB

`mysql-aiops` is exercised by a **mock-only** test suite (`uv run pytest`, no real
server). It has **not** yet been validated end-to-end against a live MySQL or MariaDB
instance. This document says exactly what the mock suite already guarantees, and what a
live run has to prove before anyone may describe this tool as verified against a real
server.

It is deliberately checklist-shaped so the result is reproducible and auditable — not a
subjective "seems fine".

## What the mock suite already guarantees

- Every module imports; the CLI builds; **all 35 MCP tools** carry the `@governed_tool`
  harness marker (`tests/test_smoke.py`, which also asserts the tool count and that
  `__version__` matches `pyproject.toml`).
- The four flagship analyses (`slow_query_rca`, `lock_wait_rca`,
  `replication_lag_rca`, `fragmentation_analysis`) are unit-tested against synthetic
  records: each classification fires on the right signal (full scan, lock-time dominant,
  rows-examined ratio, tmp-disk spill; stopped IO vs SQL thread vs plain applier lag vs
  intentional `SQL_Delay`), findings cite the measured number, and partial rows do not
  crash the analysis.
- **Flavor branching** is tested: `version()` detection routes to `SHOW REPLICA STATUS`
  + `performance_schema.data_lock_waits` (MySQL) or `SHOW SLAVE STATUS` +
  `information_schema.innodb_lock_waits` (MariaDB).
- **SQL-injection surface** is tested: values are bound parameters, and the identifiers
  that cannot be parameterised (schema/table/index/column/variable names, ORDER BY
  columns) are validated against a strict charset / allow-list and backtick-quoted.
  `explain_query` rejects multi-statement input; the `drop_index` undo replay path is
  shape-gated to `CREATE [UNIQUE] INDEX` statements.
- Reversible writes record a faithful **inverse** undo descriptor built from a fetched
  before-state: `create_index` ↔ `drop_index`, where `drop_index` rebuilds the index
  definition out of `SHOW CREATE TABLE`; `set_global_variable` captures the prior value
  from `SHOW GLOBAL VARIABLES`. Irreversible ops declare **no** undo.
- Governance persistence is tested against a real on-disk SQLite audit DB: calls land as
  rows, failures record `status=error` and no undo, and the secure-by-default approver
  gate refuses high-risk ops when no `rules.yaml` exists.

What it does **not** guarantee: that the concrete `information_schema` /
`performance_schema` column names, view availability, and privilege requirements match a
real MySQL 8.x or MariaDB 10.6+ build. Those queries are modelled from each project's
documentation and are the **largest verification debt in this repo** — and the two
flavors diverge exactly where the analyses are most interesting (lock waits and
replication).

## Prerequisites for a live run

Both servers are free and trivially containerised, so this costs nothing but time:

```bash
docker run -d --name mysql-verify -e MYSQL_ROOT_PASSWORD=... -p 3306:3306 mysql:8.4
docker run -d --name maria-verify -e MARIADB_ROOT_PASSWORD=... -p 3307:3306 mariadb:11
```

Use a **throwaway instance with throwaway data**. The checklist kills sessions, drops
indexes, rebuilds tables, and changes global variables — never run it against a server
holding data you need.

Set up the instance so the reads have something to find:

- `performance_schema = ON` (required by `top_queries` / `slow_query_rca`; if it is off
  those tools have nothing to read and `doctor` should say so).
- A read account with `PROCESS`, `REPLICATION CLIENT`, and `SELECT` on
  `performance_schema` — verify the tool degrades with a clear error, not a stack trace,
  when a privilege is missing.
- A configured **replica** for section 4 (a second container replicating from the first).
- A table with a few million rows and a deliberately unindexed `WHERE` column, so the
  slow-query RCA has a genuine full scan to find.

```bash
uv tool install mysql-aiops
mysql-aiops init      # wizard: add a target, store the password encrypted
```

Record the exact versions tested (e.g. "MySQL 8.4.3", "MariaDB 11.4") — a tick is only
meaningful with the build it was ticked against.

## Verification checklist

Tick every box, **on both flavors**. A box that cannot be ticked is a verification gap —
record it, do not silently pass.

### 1. Connectivity (the fastest live gate)
- [ ] `mysql-aiops doctor` → all green: config parsed, secret store unlocks, connection
      established, **flavor correctly detected**, `performance_schema` state reported,
      and the replica role identified.
- [ ] `mysql-aiops doctor --skip-auth` → passes offline (config/secret checks only).
- [ ] With `performance_schema = OFF`, `doctor` reports it as a limitation and
      `top_queries` fails with a clear message rather than a driver traceback.

### 2. Reads return real, well-shaped data
- [ ] `mysql-aiops overview` → real version + flavor, connection counts matching
      `SHOW STATUS`, correct replica role.
- [ ] `mysql-aiops server variables` / `server status` / `server databases` /
      `server engines` / `server connections` → match the values `mysql` client shows.
- [ ] `mysql-aiops activity sessions` → the real processlist; `--no-sleeping` filters
      correctly. Open a deliberate long query and confirm
      `mysql-aiops activity long --min-seconds 5` finds it.
- [ ] `mysql-aiops activity transactions` → an open InnoDB transaction you started shows
      up with the right age.
- [ ] `mysql-aiops query top --limit 10` → real digests with plausible totals;
      `mysql-aiops query explain "<sql>"` returns a parseable `FORMAT=JSON` plan.
- [ ] `mysql-aiops index unused` / `index redundant` / `index stats` → correct against a
      table where you *know* the answer (add a duplicate index and confirm it is named).
- [ ] `mysql-aiops table sizes` / `table fragmentation` / `table status` → sizes and
      `data_free` match `information_schema.TABLES`.
- [ ] `mysql-aiops repl status` / `repl binlog` → the real replica record and binlog
      position, on **both** flavors (this is the biggest flavor divergence).

### 3. The analyses are right, not just non-crashing
- [ ] Run the unindexed query against the large table; `mysql-aiops analyze slow-query`
      names that digest, classifies it as a full scan / no index used, and cites the real
      rows-examined-per-row-sent ratio.
- [ ] Create a genuine lock pile-up (two sessions, one holding an uncommitted `UPDATE`);
      `mysql-aiops analyze lock-waits` resolves the wait-for tree to the **correct root
      blocker** session id.
- [ ] Force a deadlock; the analysis attaches the last deadlock with the right victim and
      both statements, parsed out of `SHOW ENGINE INNODB STATUS`.
- [ ] Stop the replica's SQL thread; `mysql-aiops analyze replication` names that cause
      and quotes the real `Last_SQL_Error`. Restart it and set an `SQL_Delay`; the
      analysis reports it as **intentional**, not a fault.
- [ ] Delete a large chunk of a table; `mysql-aiops analyze fragmentation` reports
      reclaimable `data_free` matching `information_schema`.

### 4. A reversible write + its undo (governance closes the loop)
- [ ] `mysql-aiops remediate create-index <table> <col> --name idx_v --dry-run` → prints
      the DDL, creates nothing (confirm with `SHOW CREATE TABLE`).
- [ ] `mysql-aiops remediate create-index <table> <col> --name idx_v` → the index exists,
      the result carries an `_undo_id`, and a row lands in `~/.mysql-aiops/audit.db`.
- [ ] `mysql-aiops undo list` shows it; `mysql-aiops undo apply <id>` drops exactly that
      index and nothing else.
- [ ] `mysql-aiops remediate drop-index <table> <existing-composite-unique-index>` then
      `undo apply` → the index is recreated **with its original column order and UNIQUE
      flag** (proves the definition was captured from `SHOW CREATE TABLE`, not guessed —
      a naive rebuild would silently lose uniqueness or column order).
- [ ] `mysql-aiops remediate set max_connections 200` then `undo apply` → the **prior**
      value is restored, not a default.

### 5. Irreversible writes behave as declared
- [ ] `mysql-aiops remediate kill-query <id>` cancels the statement but leaves the
      session connected; `remediate kill <id>` drops the connection. Both record **no**
      undo and are tagged `high` in the audit row.
- [ ] Killing a long-running transaction triggers a rollback — confirm the tool returns
      promptly and does not hang waiting for it.
- [ ] `mysql-aiops remediate optimize <table>` actually reclaims the `data_free` that
      `analyze fragmentation` predicted; it records no undo.

### 6. Governance actually gates
- [ ] With no `~/.mysql-aiops/rules.yaml`, the high-risk writes (`kill_session`,
      `kill_query`, `drop_index`) are **refused** unless `MYSQL_AUDIT_APPROVED_BY` is set
      (secure-by-default); with it plus `MYSQL_AUDIT_RATIONALE`, both appear in the audit
      row.
- [ ] A tight poll loop trips the runaway budget guard rather than hammering the server.
- [ ] A failed call (nonexistent table) is audited `status=error` and records no undo.
- [ ] Passing a hostile identifier (e.g. a table name containing a backtick or `;`) is
      **rejected by validation before any SQL is sent** — confirm against the real server
      that nothing executes.

### 7. Cleanup
- [ ] Drop every index you created, restore every global variable you changed, and remove
      the throwaway tables.
- [ ] `mysql-aiops overview` matches the baseline you captured before starting.
- [ ] Skim `~/.mysql-aiops/audit.db` — every write is there with the right risk tier.

## Criteria to consider it live-verified

All of the following must hold:

1. Every box above is ticked against **both** a real MySQL 8.x and a real MariaDB 10.6+
   instance, with the exact builds recorded (e.g. "MySQL 8.4.3 + MariaDB 11.4").
2. Every column-name, view-availability, or privilege mismatch found is **fixed and
   covered by a regression test**, so the mock suite would now catch it.
3. Section 4 passed — in particular the `drop_index` → `undo apply` replay on a composite
   UNIQUE index. Recording an undo descriptor is not the same as the undo working, and
   this product line has shipped broken undo pairs before.
4. Section 3's replication checks passed on **both** flavors, since `SHOW REPLICA STATUS`
   and `SHOW SLAVE STATUS` return different field names and are the likeliest divergence.
5. The run is written up in the release notes / product-line memory with the date and
   package version, matching how the line records its other live-verified tools.

Until then, this repo says only what is true: mock-validated, live-unverified. Claiming
otherwise would break that promise.

## Notes for maintainers

- `mysql-aiops doctor` is the single fastest live entry point; start there.
- Cloud-managed MySQL (RDS / Aurora / Cloud SQL) is a **separate** verification target:
  the wire-compatible reads should work, but `KILL` and `SET GLOBAL` are restricted or
  replaced by provider-specific procedures. Do not infer managed-service support from a
  self-hosted run.
- Add this tool's result to the product-line verification ledger once green, so the
  central "verification debt" list stays accurate.
