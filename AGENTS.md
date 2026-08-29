# Working in this repo

One module, `okf_pg_tenant.py`. It reads a PostgreSQL catalog and writes an
[OKF v0.2](https://okf.md) bundle.

`README.md` owns **what the tool does** — features, flags, output shape, current test and
coverage numbers. This file owns **how to work on it**, and deliberately restates none of that:
a second copy of a fact is a cache with no invalidation, and it is always the prose copy that
rots.

## The shape

Read the file top to bottom; it is ordered so nothing is used before it is defined.

| Region | What lives there |
|---|---|
| SQL constants | `SQL_SCHEMAS`, `SQL_COLUMNS`, … — the entire database surface, in one place |
| Types | `Catalog`, `Run`, `Settings` |
| Pure functions | `classify_schemas`, `signature`, `diff_signature`, `collapse`, `provenance`, … |
| The one impure read | `fetch(conn)` — the only function that touches a database |
| Renderers | `render_*` — data in, string out, no IO |
| The one impure write | `build_bundle` — the only function that touches the filesystem |
| CLI | `build_parser`, `settings_from`, `report`, `main` |

**The split is the design.** Every decision the tool makes lives in a pure function, which is why
the whole suite runs in about a second against no database. Keep new logic pure; if a change
needs to read or write, it belongs in `fetch` or `build_bundle`, not scattered.

## Rules

1. **No comments in code.** Explanations go in `README.md` or here. The commit body carries the
   reasoning for a change.
2. **`pg_catalog`, never `information_schema`.** `information_schema` filters rows by privilege,
   so a least-privilege account silently sees less. That property is the entire reason this tool
   works on managed Postgres with no grants.
3. **Nothing is written to the database.** Every query is a catalog read. No temp tables, no
   `SET`, no extensions installed.
4. **Treat catalog values as untrusted.** A table name is whatever someone typed inside
   `CREATE TABLE "..."`. Anything reaching a filesystem path goes through `doc_filename`.
5. **Credentials never reach a document.** The `sources` URI is built from `conn.info`
   (host/port/dbname), never from the DSN. A test plants a password and asserts it does not
   appear.
6. **Say what you could not read.** A missing capability gets named in `_diagnostics.md`.
   Silence is never a pass — which is why the producer also counts the relations it chose not to
   document.
7. **Four arguments is the ceiling.** At five, the shared values want a type. That is what `Run`
   and `Settings` are; they exist because four renderers had grown to five arguments.
8. **No new dependency.** `psycopg` is the only one. A bundle producer that drags in a framework
   does not get run against a production database on someone's laptop, which is the only place
   it is useful.

## Before you call it done

```bash
python test_okf_pg_tenant.py    # no framework, no database
python mutants.py               # deliberate bugs, all must be caught
```

**Both must pass. `mutants.py` is not optional.** It is the only thing standing between "the
tests are green" and "the tests check something". Add a mutant for any behaviour you add — a
branch nobody can break is a branch nobody is testing.

A surviving mutant is a missing test, never a broken script. The one that survived during
development was the credential-leak mutant, because `source_resource` had no test at all.

For a change that touches rendering, prove the output did not move: render the demo database
before and after, then

```bash
diff -r -I '^ *at: ' -I '^timestamp:' before-bundle after-bundle
```

## Things that look like bugs and are not

- **`index.md` has no YAML frontmatter.** The OKF spec reserves index files and forbids it.
- **`description: ""` on some documents.** `COMMENT ON` is read for tables and columns; where a
  database carries none the field stays empty. Never generate a sentence to fill it — an
  invented description is worse than an absent one, and sourcing real ones from a dbt project is
  a planned module.
- **`verified:` is never emitted.** OKF v0.2 separates who *produced* a document from who
  *confirmed* it. Nothing here confirms anything, and filling that field is the exact failure
  the split exists to prevent.
- **`stale_after` has no default.** How fast a schema snapshot goes stale is a property of a
  team's release cadence, not of this tool. It stays opt-in behind `--stale-after-days`.
- **Drift compares sets, never column order.** A tenant where a column was dropped and re-added
  has a different physical `attnum` order and an identical structure. Reporting that as drift
  would cry wolf on precisely the mid-migration tenants the feature exists to inspect.
- **Drift is column drift only.** Defaults, constraints, indexes, foreign keys, identity
  columns, triggers and RLS are not compared, so two tenants can be reported identical and still
  behave differently. That is a real limitation, not a hidden one: `NOT_COMPARED` is printed in
  every drift section, and the frontmatter key is `column_drift`. If you widen the signature,
  widen that constant and the key with it — a claim the output does not qualify is the failure
  mode here.
- **A tie for most common is reported, not resolved.** With no majority the reference is picked
  alphabetically and `reference_ambiguous: true` is set, because a rename or a new tenant would
  otherwise silently change what the bundle calls canonical.
- **`fetch()` and `main()` are the only uncovered lines.** They are the database and CLI shell,
  proven by running the tool against a real database rather than by feeding a mock cursor its
  own answers back. Chasing them would mean asserting that a fake returns what it was told to.

## Two findings from a live run

Both were exercised against a real managed PostgreSQL instance as a non-superuser with no table
grants, not only against the demo seed. Both are now permanent features:

- `pg_catalog` really does return full structure without grants, which is what makes the
  least-privilege claim true rather than hopeful.
- The first live run reported `drift: 0` while foreign tables were unevenly spread across
  schemas. That is why `_diagnostics.md` counts the relations the producer skipped: `drift: 0`
  must never quietly mean "drift: 0 among the things I looked at".

## Never

Put real database names, hosts, or tenant counts in tests, docs, or examples. The demo seed and
every example use invented data.
