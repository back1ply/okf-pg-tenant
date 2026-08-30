# Working in this repo

One package, `src/okf_pg_tenant/`. It reads a PostgreSQL catalog and writes an
[OKF v0.2](https://okf.md) bundle.

`README.md` owns **what the tool does** — features, flags, output shape, current test and
coverage numbers. This file owns **how to work on it**, and deliberately restates none of that:
a second copy of a fact is a cache with no invalidation, and it is always the prose copy that
rots.

## The shape

One module per job. The import graph is acyclic and each row may import only rows above it.

| Module | What lives there | Imports from |
|---|---|---|
| `models.py` | `Run`, `Settings`, `Index`, and the `TypedDict` shapes (`Column`, `SkippedKind`, `SignatureDiff`, `DriftEntry`, `CollapsedTable`) | — |
| `naming.py` | `doc_filename`, `resolve_doc_names`, `UNSAFE_FILENAME` — the path-traversal guard | — |
| `collapse.py` | `classify_schemas`, `signature`, `diff_signature`, `collapse`, `drift_counts`, `partition_tables` — every drift decision, all pure | — |
| `catalog.py` | `SQL_*` (the entire database surface, in one place), `Catalog`, `source_resource`, the `read_*` cursor readers, and `fetch(conn)` — the only function that takes a connection | `models` |
| `render.py` | `provenance`, `yaml_scalar`, `capability_checks`, `cdc_block`, `geometry_columns`, all `render_*` — data in, string out, no IO | `catalog`, `collapse`, `models` |
| `bundle.py` | `build_bundle` — the only function that touches the filesystem | `collapse`, `models`, `naming`, `render` |
| `cli.py` | `DEFAULT_TENANT_PATTERN`, `build_parser`, `settings_from`, `report`, `main` | `bundle`, `catalog`, `models` |
| `__init__.py` | re-exports only, so `from okf_pg_tenant import build_bundle` keeps working | all of the above |
| `__main__.py` | two lines, so `python -m okf_pg_tenant` works | `cli` |

**The split is the design.** Every decision the tool makes lives in a pure function, which is why
the whole suite runs in about a second against no database. Keep new logic pure; if a change
needs to read or write, it belongs in `catalog.py` or `bundle.py`, not scattered.

`catalog.py` is the only module that touches a database. Inside it, `fetch` is the only function
that takes a *connection*; the `read_*` helpers take the cursor it opens, so the transaction
boundary stays in one place while each result set is read by something small enough to name.

The boundary is enforced twice. Once by imports: a catalog-shape change cannot reach a renderer
except through an import you can see at the top of `render.py`. Once by types: the shape of a
column record is named `Column` in `models.py` rather than being an anonymous dict, so moving it
fails `mypy` instead of silently changing rendered output. That pair is the whole reason the
split exists — the ordering it replaced was convention, not an interface.

`psycopg` is imported inside `main()` and nowhere else, so importing the package, running the
suite and running the mutation gate need nothing but the package itself. The package lives under
`src/`, so it is not importable from the checkout root until installed: run
`pip install -e ".[dev]"` first. That is deliberate. The tests then run against an installed
package, and a file missing from the wheel fails locally rather than after release.

## Rules

1. **Docstrings, never inline comments.** Every module, class and function under `src/` carries a
   docstring saying why it exists; no `#` comment explains code inside a function. Tests are
   exempt: a test's name is its docstring, and `test_a_dropped_column_is_missing_and_never_extra`
   says more than a sentence under it would. A docstring travels with
   the thing it describes and is reachable from `help()`; a comment three lines above a branch
   rots where nobody is looking. Longer reasoning goes in `README.md` or here, and the commit
   body carries the reasoning for a change. Configuration files (`pyproject.toml`, CI YAML) are
   not code and may carry comments.
2. **Base catalogs, never `information_schema`.** `information_schema` filters rows by privilege,
   so a least-privilege account silently sees less. `pg_class`, `pg_attribute`, `pg_namespace`,
   `pg_index` and `pg_description` are world-readable and do not, which is the entire reason this
   tool works on managed Postgres with no grants. **That is not true of `pg_catalog` as a whole**
   — `pg_stats` shows only rows for tables you may read, and `pg_authid` is not publicly readable
   — so any new query must have its visibility rules checked rather than assumed. The README
   claimed the blanket version until it was checked against the docs and found wrong.
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
7. **Four arguments is the ceiling.** At five, the shared values want a type. That is what `Run`,
   `Settings` and `Index` are; the first two exist because four renderers had grown to five
   arguments, and `Index` because `render_index` later did the same. A boolean flag argument is
   not an exception — split it into two functions, which is why the test suite has both `col` and
   `pk_col`.
8. **No new runtime dependency.** `psycopg` is the only one, and `[project.dependencies]` is
   where that is enforced. A bundle producer that drags in a framework does not get run against a
   production database on someone's laptop, which is the only place it is useful. Development
   tooling is a separate question: `pytest`, `ruff`, `mypy` and `cosmic-ray` live in
   `[project.optional-dependencies] dev` and never reach a user's install. Nothing under `src/`
   may import them.

## Before you call it done

```bash
ruff check .              # lint
ruff format --check .     # formatting
mypy                      # types, strict
pytest -q                 # no database
python mutation_gate.py   # mutation testing
```

**All five must pass. `mutation_gate.py` is not optional.** It is the only thing standing
between "the tests are green" and "the tests check something".

It wraps `cosmic-ray`, which derives mutants from the syntax tree rather than from a hand-written
list, so it generates them for any behaviour you add without being told. That is the whole reason
it replaced the hand-rolled script: a curated list only ever covers the bugs its author thought
of. The cost is that it cannot tell a missing test from a mutation that cannot change behaviour,
which is what `mutation-baseline.md` is for.

**A surviving mutant is a missing test, never a broken script.** When one survives, write the
test that kills it. Only when a mutation provably cannot change observable behaviour does it go
in `mutation-baseline.md`, with the argument for why. The gate fails on any survivor that is not
recorded there, and equally on any recorded entry the tool no longer generates — a baseline that
is allowed to drift stops describing the code and becomes a list of excuses.

Line coverage is not this. `collapse.py` was at 100% line coverage and still had five real test
gaps that only mutation found.

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
- **`okf_pg_tenant.collapse` is the function, not the module.** `__init__.py` re-exports the
  `collapse` function, which shadows the same-named submodule as an attribute. Every import
  form still resolves correctly — `from okf_pg_tenant.collapse import signature` gives the
  module's contents, `from okf_pg_tenant import collapse` gives the function — so this is a
  naming collision, not a broken package. Renaming either one is a public API change.
- **`catalog.fetch()`, `cli.py` and `__main__.py` are the only uncovered lines.** They are the
  database and CLI shell, proven by running the tool against a real database rather than by
  feeding a mock cursor its own answers back. Chasing them would mean asserting that a fake
  returns what it was told to. `cli.py` is excluded from the mutation config for the same reason;
  `fetch`'s surviving mutants are recorded in `mutation-baseline.md` rather than hidden.
- **The mutation gate needs `python -m` on Windows.** Smart App Control blocks the unsigned
  `.exe` launchers that `pytest`, `ruff`, `mypy` and `cosmic-ray` install. `python -m pytest`,
  `python -m ruff`, and so on are the same entry points and are not affected.

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
