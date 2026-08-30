# Contributing

Thanks for looking at this. The rules that govern *how* the code is written live in
[AGENTS.md](AGENTS.md) — read that before changing anything under `src/`. This file covers only
how to get set up and what has to be green before you open a pull request.

## Setup

```bash
git clone https://github.com/back1ply/okf-pg-tenant
cd okf-pg-tenant
python -m pip install -e ".[dev]"
```

The editable install is not optional. The package lives under `src/`, so it is not importable
from the checkout root until it is installed — which is the point: the tests run against an
installed package, so a file missing from the wheel fails locally instead of after release.

## What has to pass

```bash
ruff check .              # lint
ruff format --check .     # formatting
mypy                      # types, strict
pytest -q                 # no database
python mutation_gate.py    # mutation testing
```

CI runs exactly these, plus an end-to-end job that seeds a real PostgreSQL container and asserts
the produced bundle's frontmatter.

> On Windows with Smart App Control, the `.exe` launchers these tools install are unsigned and
> get blocked. Prefix each with `python -m` — `python -m ruff check .`, `python -m pytest -q` —
> which is the same entry point and is not affected.

## The mutation gate is the important one

A green test suite says the tests ran, not that they check anything. `mutation_gate.py` changes
the source one edit at a time and requires the suite to fail for each change. A surviving mutant
is a missing test, never a broken script.

If you add behaviour, the gate will generate mutants for it automatically. If one survives,
write the test that kills it — or, if the mutation genuinely cannot change observable behaviour,
record it in `mutation-baseline.md` with the reason it is equivalent. Do not widen the exclusion
list to make the gate quiet.

## Proving output did not move

For any change that touches rendering, render the demo database before and after and diff it:

```bash
docker run -d --name okf-demo -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=saas \
  -p 55432:5432 postgres:16-alpine
psql "postgresql://postgres:demo@localhost:55432/saas" -v ON_ERROR_STOP=1 -f demo/seed.sql
python -m okf_pg_tenant --dsn "postgresql://postgres:demo@localhost:55432/saas" --out after-bundle

diff -r -I '^ *at: ' -I '^timestamp:' before-bundle after-bundle
```

A refactor that claims to change nothing has to show it.

## Pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/); the
  commit body carries the reasoning, because the code carries no comments.
- Never put real database names, hosts, or tenant counts in tests, docs, or examples. The demo
  seed and every example use invented data.
- One behavioural change per pull request. If you find a bug while refactoring, report it
  separately rather than folding the fix in.
