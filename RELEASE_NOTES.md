# MySQL AIops v0.1.0 — preview

Governed AI-ops for **MySQL 8.x / MariaDB 10.6+ DBA operations** for AI agents
— connecting via **PyMySQL** and reading `information_schema` /
`performance_schema` — with a built-in governance harness (audit, policy,
token/runaway budget, undo-token recording, graduated risk tiers) and an
encrypted credential store. Standalone — no external skill-family dependency.

> **Preview / mock-only.** All behaviour is validated against a mocked PyMySQL
> cursor/connection; it has **not** been run against a live MySQL or MariaDB
> server. The fastest live check is `mysql-aiops doctor`.
>
> Community-maintained; **not affiliated with or endorsed by Oracle Corporation
> or the MariaDB Foundation.** "MySQL" and "MariaDB" trademarks belong to their
> owners.

## Highlights

- **33 MCP tools** (25 read, 8 write), every one wrapped with `@governed_tool`.
  - Read: server `overview`; server reads (6); activity (4); query stats (2);
    index health (3); table health (3); replication (2); and four flagship
    analyses.
  - Write: `kill_session`/`kill_query`/`drop_index` (high);
    `optimize_table`/`analyze_table`/`create_index`/`set_global_variable`/
    `reset_query_stats` (medium).
- **Four signature analyses** — `slow_query_rca` (worst statement digest +
  EXPLAIN → cited cause/action), `lock_wait_rca` (InnoDB wait-for tree with the
  root blocker named + last deadlock parsed from `SHOW ENGINE INNODB STATUS`),
  `replication_lag_rca` (thread state / lag / error fields → cause + action),
  and `fragmentation_analysis` (`data_free` → OPTIMIZE TABLE candidates).
- **Flavor-aware** — mysql vs mariadb detected from `version()`;
  `SHOW REPLICA STATUS` vs `SHOW SLAVE STATUS` and the lock-wait source branch
  automatically; doctor and overview report the flavor.
- **Encrypted password store** (`~/.mysql-aiops/secrets.enc`, Fernet + scrypt)
  — never plaintext on disk; legacy `MYSQL_<TARGET>_PASSWORD` env fallback.
- **CLI** with an `init` onboarding wizard (TLS modes incl. verify_ca /
  verify_identity), `secret` management, and `doctor`.
- **PyMySQL connection layer** — parameterised catalog reads, `DictCursor`
  results, autocommit for maintenance commands, 30s timeouts, and teaching
  error translation (`MySQLError`). Reversible writes fetch the real
  before-state first.

## Install

```bash
uv tool install mysql-aiops
mysql-aiops init
mysql-aiops doctor
```

## Caveats

- The `information_schema` / `performance_schema` queries are modelled from the
  documented MySQL 8.x / MariaDB 10.6+ shapes and need live verification.
- `top_queries` / `slow_query_rca` require `performance_schema=ON`; the read
  account should have `PROCESS`, `REPLICATION CLIENT` and `SELECT` on
  `performance_schema`.
- Out of scope by design: application-schema migrations, ORM management,
  logical backup/restore orchestration, and any bulk destructive DDL.
- Missing a view, metric, or maintenance command? Open an issue or PR.
