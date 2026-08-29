<div align="center">

# okf-pg-tenant

*An [Open Knowledge Format](https://okf.md) producer for **real** production PostgreSQL — multi-tenant, undocumented, managed*

[![CI](https://img.shields.io/github/actions/workflow/status/back1ply/okf-pg-tenant/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/back1ply/okf-pg-tenant/actions)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)
![Python](https://img.shields.io/badge/python-%E2%89%A53.9-3c873a?style=flat-square)
![Dependencies](https://img.shields.io/badge/dependencies-psycopg-blue?style=flat-square)

[Why](#why) • [Features](#features) • [Install](#install) • [Quick Start](#quick-start) • [Try It](#try-it) • [Evidence](#evidence) • [Limits](#known-limits)

</div>

Existing OKF Postgres producers assume a database you own: one schema, and `COMMENT ON`
everywhere. Production SaaS databases are usually neither.

> [!NOTE]
> **OKF** is a specification for representing structured metadata as markdown files with YAML
> frontmatter. This project implements **OKF v0.2** — every document carries `status`,
> `sources` and `generated` provenance, and `stale_after` on request. v0.2 is additive, so the
> bundles stay readable by v0.1 consumers.

## Why

Many B2B SaaS products give every customer their own schema. A database with **800 tenants and
12 tables** has 9,600 physical tables — and roughly 12 distinct ones.

```
A naive producer                    okf-pg-tenant
─────────────────                   ─────────────
9,600 markdown files                12 markdown files
one per tenant, per table           one per LOGICAL table
an index nobody can read            tenant count in frontmatter
drift invisible                     drifting tenants named
```

## Features

- **Collapses schema-per-tenant** — one document per logical table, not per tenant. The
  tenant-schema pattern is a regex (`--tenant-pattern`), defaulting to UUID.
- **Detects cross-tenant column drift** — compares every tenant against the majority structure
  and names the stragglers, by column name/type/nullability **as a set**, never by column order.
  It does not compare defaults, constraints, indexes or foreign keys; every drift section says so.
- **Runs on managed Postgres, least privilege** — reads `pg_catalog`, not `information_schema`.
  No superuser, no table grants, nothing written to the database.
- **Says what it could not read** — every bundle ships `_diagnostics.md` naming missing
  capabilities *and counting the relations it chose not to document*.
- **Treats table names as untrusted input** — a name is whatever someone typed inside
  `CREATE TABLE "..."`, and Postgres permits `..` and `/`.
- **Emits OKF v0.2 provenance** — every document says what produced it, from which database,
  and when. Credentials never reach the `sources` URI.

## What each one means

### Collapses schema-per-tenant

```yaml
tenancy:
  pattern: schema-per-tenant
  schemas: 797
  tenant_schemas_total: 800
  absent_from: 3
  drift: 3
  reference_schema: <the schema the columns were read from>
```

`absent_from` matters: a table present in only some tenants is a mid-rollout migration, and a
producer that prints only "797 schemas" hides that. The body says it in words too — *present in
797 of 800 tenant schema(s)*.

### Detects cross-tenant column drift

```markdown
## Tenant drift

1 schema(s) differ from the reference.

- `a1c3e5f7-5555-…` - missing `amount`
```

Comparison is by **column name, type and nullability as a set** — not by order. A tenant where a
column was dropped and re-added has a different physical column order and an identical
structure, and is correctly *not* drift. That case has its own test, because it is exactly the
tenant population the feature exists to inspect.

> [!WARNING]
> This is **column** drift, not full schema drift. Defaults, primary keys, unique/check
> constraints, indexes, foreign keys, identity columns, triggers and row-level security are
> **not** compared. Two tenants reported identical here can still accept different writes and
> enforce different integrity rules. Every drift section in the output repeats this, so a reader
> of a single document cannot miss it. Widening the signature is on the roadmap.

When two structures tie for most common there is no majority, so the reference is chosen
alphabetically and `reference_ambiguous: true` is set — a rename or a new tenant could change it
with no DDL change at all. The document says so in the body rather than presenting an arbitrary
pick as canonical.

### Runs on managed Postgres, least privilege

`information_schema` filters rows by privilege, so a least-privilege account gets a silently
incomplete picture. `pg_catalog` does not, and it is readable on Cloud SQL, RDS and friends with
no table grants at all.

### Says what it could not read

```markdown
## Capabilities

- **OK** - read the catalog via pg_catalog (no table grants needed)
- **MISSING** - pg_stat_statements available (not installed - usage stats unavailable)
- **OK** - connected as superuser
```

A missing capability is stated, not silently skipped. An empty tenant schema is reported as
empty — and `pg_catalog` guarantees that means *empty*, not *denied*.

The same file counts every relation the producer chose **not** to document, so `drift: 0` can
never quietly mean "drift: 0 among the things I bothered to look at":

```markdown
| Kind | Objects | Schemas |
|---|---|---|
| foreign table | 970 | 498 |
| view | 1500 | 500 |
```

1500 views over 500 schemas is 3 each — even. 970 foreign tables over 498 schemas is not a whole
multiple, so those are spread unevenly: real drift, in a relation kind v0.1 does not document.
Reporting the counts is what lets you see it.

### Emits OKF v0.2 provenance

```yaml
status: stable
sources:
  - resource: postgresql://db.internal:5432/saas
    id: saas
    title: PostgreSQL catalog for saas
generated:
  by: process:okf-pg-tenant
  at: 2026-08-29T10:49:18+00:00
stale_after: 2026-09-28        # only with --stale-after-days
```

v0.2 separates *who produced* a document from *who verified* it. This producer fills
`generated` and leaves `verified` empty, because nothing here verifies anything — claiming
otherwise would be the exact failure the field exists to prevent.

`stale_after` is omitted unless you pass `--stale-after-days`. How fast a schema snapshot goes
stale is a property of your release cadence, not of this tool, and a made-up expiry date is
worse than none.

The `sources` URI is built from host, port and database only. A password in your DSN never
reaches a document — there is a test that plants one and asserts it does not appear.

### Treats table names as untrusted input

Writing `bundle/tenant/<table>.md` straight from the catalog would let a table named
`../../evil` escape the output directory. Names are sanitised to `[A-Za-z0-9._-]` with leading
dots stripped, and two names that sanitise to the same filename raise an error naming both
rather than one silently overwriting the other.

> [!TIP]
> Static analysis will not catch this class for you. `semgrep` with 1,115 Python and
> security-audit rules reports zero findings on the vulnerable pattern — a database column is
> not a taint source those rules model.

## Install

```bash
pip install git+https://github.com/back1ply/okf-pg-tenant
```

That installs an `okf-pg-tenant` command. Or clone and run the single file directly, which needs
only `pip install "psycopg[binary]"` — there are no other dependencies.

> [!TIP]
> On Windows machines with Smart App Control, the installed `.exe` launcher is unsigned and gets
> blocked. `python -m okf_pg_tenant` is the same entry point and is not affected.

Python 3.9+. CI runs the suite on 3.9, 3.12 and 3.13, so the floor is proven rather than
assumed — the code itself needs only 3.7.

## Quick Start

```bash
okf-pg-tenant --dsn "postgresql://user@host:5432/db" --out ./bundle

# equivalents, if you cloned instead of installing
python -m okf_pg_tenant --dsn "..." --out ./bundle
python okf_pg_tenant.py --dsn "..." --out ./bundle
```

| Flag | Default | Meaning |
|---|---|---|
| `--dsn` | `$DATABASE_URL` | libpq connection string |
| `--out` | `okf-bundle` | output directory |
| `--tenant-pattern` | UUID regex | which schemas are tenants |
| `--title` | `<db> knowledge bundle` | `index.md` heading |
| `--stale-after-days` | omitted | emit an OKF v0.2 `stale_after` date this many days out |

```
bundle/
├── index.md            no frontmatter, per OKF spec
├── tenant/
│   └── <table>.md      one per logical table
├── global/
│   └── <schema>.<table>.md
└── _diagnostics.md     what this run could not read
```

## Try It

Needs Docker.

```bash
docker run -d --name okf-demo-pg -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=saas \
  -p 55432:5432 postgres:16-alpine
docker exec -i okf-demo-pg psql -U postgres -d saas -v ON_ERROR_STOP=1 -f - < demo/seed.sql
okf-pg-tenant --dsn "postgresql://postgres:demo@127.0.0.1:55432/saas" --out demo-bundle
```

The seed builds six tenant schemas: four healthy, one with a dropped-and-re-added column (must
**not** count as drift), one genuinely drifted, one empty.

```
12 physical tables in 6 tenant schemas -> 2 tenant docs + 2 global docs
drift: 2 schema(s) differ from their reference
empty tenant schemas: 1
```

Clean up with `docker rm -f okf-demo-pg`.

## Evidence

```bash
python test_okf_pg_tenant.py    # 23 tests, plain asserts, no framework, no database
python mutants.py               # 10 deliberate bugs, all must be caught
```

**Coverage is 81%**, and the uncovered lines are exactly three things: `fetch()`, `main()`, and
the `__main__` guard. Every line that decides anything is covered; what is not covered is the
database and CLI shell, proven by running the tool against a real database rather than by
feeding a mock cursor its own answers back.

**The suite is mutation-checked.** `mutants.py` introduces seventeen deliberate bugs one at a time —
the reference group picked by rarest signature instead of most common, `signature` ignoring
nullability, `absent_from` adding instead of subtracting, filename sanitising disabled,
collision detection disabled, the source URI leaking a password, and others. It restores the source afterwards and exits nonzero if
any survives. All are caught.

> [!IMPORTANT]
> A test suite that has never been seen failing is not evidence. That is what `mutants.py` is
> for, and why it ships in the repo instead of being a claim in this file.
>
> Its limit is worth stating plainly: these are **hand-picked** mutants, so they cover the bugs
> the author thought of, not the space of possible bugs. They raise the floor; they do not
> survey it. A systematic mutation tool over the pure functions would be strictly better and is
> on the roadmap.

## Known limits

- **Descriptions come from `COMMENT ON` only.** Table and column comments are read and become
  the `description` field. Many production databases carry none, in which case the field stays
  empty — the useful descriptions then live in a dbt project or in migration SQL, and reading
  those is a separate module.
- **Only ordinary tables are documented** (`relkind = 'r'`). Views, materialized views, foreign
  tables and partitioned tables are counted in `_diagnostics.md` but **not drift-checked**.
- **A table absent from some tenants is counted, not itemised.** You get `absent_from: 3`, not
  the three schema names.
- **Drift names tenant schemas in the output.** That is the point of the feature, but if schema
  names are sensitive in your context, treat the bundle accordingly.
- **No usage statistics yet.** `pg_stat_user_tables` and `pg_stat_statements` would show which
  tables are actually read and how they are really joined.

## Roadmap

1. Widen the drift signature to defaults, constraints, indexes and foreign keys
2. Views and foreign tables documented and drift-checked, not merely counted
3. Descriptions merged from a dbt project's `schema.yml` where the database has none
4. Systematic mutation testing over the pure functions, replacing hand-picked mutants
5. Usage and access statistics from `pg_stat_*`
6. MySQL

## License

MIT
