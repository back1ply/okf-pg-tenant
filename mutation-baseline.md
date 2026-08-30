# Accepted surviving mutants

`mutation_gate.py` fails on any mutant that survives the test suite unless it is listed here.
Every row needs an argument for why the mutation **cannot change observable behaviour**, or why
the unit suite is deliberately not the thing that covers it. A surviving mutant is a missing
test; this file is for the cases where that is provably not true.

Two rules keep it from becoming a list of excuses:

- The gate also fails on a row here that the tool no longer generates, so the file cannot drift
  away from the code.
- "I could not think of a test" is not a reason. If the argument below is not a proof, write the
  test instead.

The columns are the mutant's identity as cosmic-ray reports it: module, line, operator,
occurrence.

## Reading the database is not covered by the unit suite, by design

`AGENTS.md` states that the database read is proven by running the tool against a real database
rather than by feeding a mock cursor its own answers back. `cosmic-ray` runs only `pytest`, so
every mutant inside `fetch` and its `read_*` helpers survives here by construction. These are
covered by the end-to-end CI job, which seeds a real PostgreSQL instance and asserts the produced
bundle's frontmatter.

This is the honest position, not a strong one: the end-to-end job asserts the bundle is correct,
it does not assert that each of these eighteen mutations breaks it. Narrowing that gap means a
test that connects to a real database, which is the roadmap item, not a unit test with a fake
cursor.

| Module | Line | Operator | # | Why it is here |
|---|---|---|---|---|
| `src/okf_pg_tenant/catalog.py` | 101 | `core/ZeroIterationForLoop` | 0 | `read_primary_keys`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 117 | `core/ZeroIterationForLoop` | 1 | `read_columns`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 141 | `core/ZeroIterationForLoop` | 2 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 142 | `core/AddNot` | 0 | Double negation: `not not description` is `not description` |
| `src/okf_pg_tenant/catalog.py` | 142 | `core/ReplaceUnaryOperator_Delete_Not` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 143 | `core/ReplaceContinueWithBreak` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/AddNot` | 1 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/NumberReplacer` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/NumberReplacer` | 1 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/ReplaceComparisonOperator_Eq_Gt` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/ReplaceComparisonOperator_Eq_GtE` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/ReplaceComparisonOperator_Eq_Lt` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/ReplaceComparisonOperator_Eq_LtE` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 144 | `core/ReplaceComparisonOperator_Eq_NotEq` | 0 | `read_comments`; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 169 | `core/NumberReplacer` | 2 | `fetch` unpacking the env row; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 169 | `core/NumberReplacer` | 3 | `fetch` unpacking the env row; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 179 | `core/NumberReplacer` | 4 | `fetch` reading extension names; unit suite does not reach it |
| `src/okf_pg_tenant/catalog.py` | 179 | `core/NumberReplacer` | 5 | `fetch` reading extension names; unit suite does not reach it |

## Mutations that cannot change behaviour

| Module | Line | Operator | # | Why it is here |
|---|---|---|---|---|
| `src/okf_pg_tenant/bundle.py` | 40 | `core/ReplaceBinaryOperator_Sub_BitXor` | 0 | `non_empty` is built by filtering `tenant_set`, so it is a subset of it. For `B ⊆ A`, `A - B` and `A ^ B` are the same set |
| `src/okf_pg_tenant/bundle.py` | 44 | `core/ReplaceTrueWithFalse` | 2 | `parents=False` on the `global` directory. The line above creates `out / "tenant"` with `parents=True`, so `out` itself always exists by the time this runs |
| `src/okf_pg_tenant/collapse.py` | 69 | `core/ReplaceUnaryOperator_USub_Invert` | 0 | `~n == -n - 1`. Both are strictly decreasing in `n`, so the sort key orders the groups identically |
| `src/okf_pg_tenant/collapse.py` | 80 | `core/ReplaceComparisonOperator_Eq_LtE` | 0 | `sizes` is sorted descending, so `sizes[0] >= sizes[1]` always holds. `<=` is therefore true exactly when `==` is |
| `src/okf_pg_tenant/collapse.py` | 80 | `core/ReplaceComparisonOperator_Gt_NotEq` | 0 | `collapse_table` is never called with an empty mapping, so `len(sizes) >= 1` and `> 1` agrees with `!= 1` over the whole domain |
| `src/okf_pg_tenant/collapse.py` | 80 | `core/ReplaceComparisonOperator_Eq_Is` | 0 | Compares two small `int` group sizes. CPython caches these, so `is` and `==` agree. This is an implementation guarantee rather than a language one, and it is the weakest argument in this file |
| `src/okf_pg_tenant/collapse.py` | 87 | `core/ReplaceComparisonOperator_Eq_Is` | 1 | `modal_sig` is a key taken out of `groups`, and the loop iterates the same dict, so the reference group yields the identical object |
| `src/okf_pg_tenant/collapse.py` | 126 | `core/ReplaceBinaryOperator_BitOr_BitXor` | 0 | `missing` is `modal - other` and `extra` is `other - modal`, which are disjoint by construction. Union and symmetric difference coincide on disjoint sets |
| `src/okf_pg_tenant/render.py` | 275 | `core/ReplaceComparisonOperator_Eq_GtE` | 0 | `is_superuser` is `"on"` or `"off"`. `"off" >= "on"` is False because `'f' < 'n'`, so `>=` agrees with `==` across the whole domain |

## Known weak spots

Every remaining entry is an algebraic identity or a documented coverage boundary, not a claim
about surrounding context. Two contextual claims used to sit here and both were wrong to keep:

`render.py` once carried a row arguing that `not ok and note` could not differ from
`not ok or note`, "because no row in `capability_checks` is both OK and annotated". That premise
was **false**: the note beside `pg_stat_statements available` is a constant, so when the extension
is installed the row is both. The mutant rendered
`- **OK** - pg_stat_statements available (not installed - usage stats unavailable)` — a line that
contradicts itself — and survived only because the existing assertion matched a prefix. Writing
the premise down as a test is what exposed it. There is now a test, and no row.

A second entry used to sit here: `render_tenant_doc` read `info.get("variants", 1)` on a
`CollapsedTable` that always carries the key, so two mutants landed on a default nothing could
reach. Documenting why they could not be killed was the wrong move — indexing the key directly
deleted the rows, and five mutants with them. **A baseline entry that explains dead code is a
request to delete the code.**
