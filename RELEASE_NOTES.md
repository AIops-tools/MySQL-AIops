# Release notes — mysql-aiops 0.2.1

Previous release: 0.2.0.

## Fixed: `server databases` crashed against a real server

`SUM()` aggregates come back from MySQL as `decimal.Decimal`, which `json` cannot
encode — so `server databases` raised
`TypeError: Object of type Decimal is not JSON serializable` on every live server.
The mock suite could not see this: its fixtures returned plain ints.

Numeric aggregates now go through a shared `as_int()` helper, and the regression test
models the driver's real types. Absent stays `None` rather than becoming `0` — an
unknown count and a zero count are different facts.

## Live-verified

This release was exercised end-to-end against a real **MySQL 8.4.10** server: reads
cross-checked against the `mysql` client, `analyze slow-query` on genuine full scans,
the full governance loop (real `create_index` → audit row → `undo_apply` actually
dropping it), and both layers of read-only mode. See
[docs/VERIFICATION.md](docs/VERIFICATION.md) for what is confirmed and what is still
open — **MariaDB remains mock-only** and is now the largest gap in this repo.
