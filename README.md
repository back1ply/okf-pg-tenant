# okf-pg-tenant

An [Open Knowledge Format](https://okf.md) producer for **real** production PostgreSQL:
multi-tenant, undocumented, and managed.

Existing OKF Postgres producers assume a database you own, with one schema and
`COMMENT ON` everywhere. Production SaaS databases are usually neither.

## What it does differently

### 1. Collapses schema-per-tenant databases

Many B2B SaaS products give every customer their own schema. A database with 800
tenant schemas and 12 tables each has 9,600 physical tables — and roughly 12
distinct ones. A naive producer emits 9,600 near-identical markdown files and an
index nobody can read.

`okf-pg-tenant` emits **one document per logical table**, with the tenant count in
frontmatter:

```yaml
tenancy:
  pattern: schema-per-tenant
  schemas: 797
  tenant_schemas_total: 800
  absent_from: 3
  drift: 3
  reference_schema: <the schema the columns were read from>
```

`absent_from` matters: a table that exists in only some tenants is a mid-rollout
migration, and a producer that only prints "797 schemas" hides that. The body says
it in words too — *present in 797 of 800 tenant schema(s)*.

The tenant-schema pattern is a regex (`--tenant-pattern`). It defaults to UUID
because that is the common convention, but any naming scheme works.

### 2. Detects cross-tenant schema drift

Collapsing assumes all tenants share a structure. In practice they don't —
migrations stall, some tenants are mid-rollout. The producer compares every
tenant schema against the majority structure and reports the stragglers by name:

```markdown
## Tenant drift

1 schema(s) differ from the reference.

- `a1c3e5f7-5555-…` - missing `amount`
```

Comparison is by **column name, type and nullability as a set** — not by column
order. A tenant where a column was dropped and re-added has a different physical
column order but identical structure, and is correctly *not* reported as drift.
That case is covered by a test, because it is exactly the tenant population the
feature exists to inspect.

### 3. Works on managed Postgres, with least privilege

Structure is read from `pg_catalog`, not `information_schema`.
`information_schema` filters rows by privilege, so a least-privilege account gets
a silently incomplete picture. `pg_catalog` does not, and it is readable on Cloud
SQL, RDS and friends without any table grants.

No superuser required. Nothing is written to the database. Every query is a
catalog read.

### 4. Says what it could not read

Every bundle ships a `_diagnostics.md`:

```markdown
## Capabilities

- **OK** - read the catalog via pg_catalog (no table grants needed)
- **MISSING** - pg_stat_statements available (not installed - usage stats unavailable)
- **OK** - connected as superuser
```

A missing capability is stated, not silently skipped. An empty tenant schema is
reported as empty — and `pg_catalog` guarantees that means *empty*, not *denied*.

The same file counts every relation the producer chose **not** to document, so
"drift: 0" can never quietly mean "drift: 0 among the things I bothered to look
at":

```markdown
| Kind | Objects | Schemas |
|---|---|---|
| foreign table | 970 | 498 |
| view | 1500 | 500 |
```

1500 views over 500 schemas is 3 each — even. 970 foreign tables over 498 schemas
is not a whole multiple, so those are spread unevenly: real drift, in a relation
kind v0.1 does not document. Reporting the counts is what lets you see it.

## Install

```bash
pip install psycopg[binary]
```

Single-file, no other dependencies. Python 3.9+.

## Use

```bash
python okf_pg_tenant.py --dsn "postgresql://user@host:5432/db" --out ./bundle
```

| Flag | Default | Meaning |
|---|---|---|
| `--dsn` | `$DATABASE_URL` | libpq connection string |
| `--out` | `okf-bundle` | output directory |
| `--tenant-pattern` | UUID regex | which schemas are tenants |
| `--title` | `<db> knowledge bundle` | index.md heading |

Output:

```
bundle/
├── index.md            no frontmatter, per OKF spec
├── tenant/<table>.md   one per logical table
├── global/<schema>.<table>.md
└── _diagnostics.md
```

## Try it

Needs Docker.

```bash
docker run -d --name okf-demo-pg -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=saas \
  -p 55432:5432 postgres:16-alpine
docker exec -i okf-demo-pg psql -U postgres -d saas -v ON_ERROR_STOP=1 -f - < demo/seed.sql
python okf_pg_tenant.py --dsn "postgresql://postgres:demo@127.0.0.1:55432/saas" --out demo-bundle
```

The seed builds six tenant schemas: four healthy, one with a dropped-and-re-added
column (must **not** count as drift), one genuinely drifted, one empty. Output:

```
12 physical tables in 6 tenant schemas -> 2 tenant docs + 2 global docs
drift: 2 schema(s) differ from their reference
empty tenant schemas: 1
```

Clean up with `docker rm -f okf-demo-pg`.

## Tests

```bash
python test_okf_pg_tenant.py
```

15 tests, plain asserts, no framework, no database needed.

Line coverage is 83%, and the uncovered lines are exactly three things:
`fetch()`, `main()`, and the `__main__` guard. Every line that decides anything
is covered; what is not covered is the database and CLI shell. Those are proven
by running the tool against a real database, not by feeding a mock cursor its
own answers back.

The suite is mutation-checked. Reproduce it:

```bash
python mutants.py
```

Ten deliberate bugs are introduced one at a time
(reference group picked by rarest signature instead of most common, `signature`
ignoring nullability, `absent_from` adding instead of subtracting, filename
sanitising disabled, collision detection disabled, and others). The script restores the
source afterwards and exits nonzero if any bug survives. All ten are caught.
A test suite that has never been seen failing is not evidence.

### 5. Treats table names as untrusted input

A table name is whatever someone typed inside `CREATE TABLE "..."`, and Postgres
permits `/`, `..`, and worse. Writing `bundle/tenant/<table>.md` directly would
let a table named `../../evil` escape the output directory. Names are sanitised
to `[A-Za-z0-9._-]` with leading dots stripped, and two names that sanitise to
the same filename raise an error naming both rather than one silently
overwriting the other.

## Known limits

- **Descriptions are empty.** Real product databases rarely carry `COMMENT ON`.
  The useful descriptions usually live in a dbt project or in migration SQL.
  Reading those is the next module, not this one.
- **Drift names tenant schemas in the output.** That is the point of the feature,
  but if schema names are sensitive in your context, treat the bundle accordingly.
- **Only ordinary tables are documented** (`relkind = 'r'`). Views, materialized
  views, foreign tables and partitioned tables are counted and reported in
  `_diagnostics.md`, but not documented and **not drift-checked**. The report
  makes uneven ones visible: if a kind's object count is not a whole multiple of
  its schema count, it is spread unevenly, and that is drift this version cannot
  see. Documenting them is v0.2.
- **A table absent from some tenants is counted, not itemised.** You get
  `absent_from: 3`, not the three schema names. Naming them is v0.2.
- **No usage statistics yet.** `pg_stat_user_tables` and `pg_stat_statements` would
  show which tables are actually read and how they are really joined. Planned.

## Roadmap

1. Descriptions merged from a dbt project's `schema.yml`.
2. Usage and access statistics from `pg_stat_*`.
3. MySQL.

## License

MIT.
