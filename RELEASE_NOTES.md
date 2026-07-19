# Release notes — mysql-aiops 0.2.2

Previous release: 0.2.1.

## Live-verified: MariaDB

No behaviour changes. The MariaDB flavor branch was the largest verification debt in
this repo and had never been run against a live server. It has now been exercised
against **MariaDB 11.8.8**:

- Flavor correctly detected as `mariadb`; the MariaDB-specific paths
  (`SHOW SLAVE STATUS`, `information_schema.innodb_lock_waits`) execute and return
  well-shaped data.
- **`analyze lock-waits` on real contention**: with two transactions deliberately
  fighting over one row, it correctly identified the root blocker (the session
  holding locks while waiting on nobody), the blocked session, an 18-second measured
  wait, and named the exact tools to resolve it.
- With `performance_schema = OFF`, `doctor` reports it as a limitation and says how
  to enable it — no driver traceback, exactly as the checklist requires.
- All 16 commands return well-formed JSON, including the `Decimal` aggregate path
  fixed in 0.2.1.

See [docs/VERIFICATION.md](docs/VERIFICATION.md). Replication against a real replica
remains unverified.
