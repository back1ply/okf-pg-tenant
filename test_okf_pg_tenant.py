import tempfile
from pathlib import Path

from okf_pg_tenant import (
    DEFAULT_TENANT_PATTERN,
    Catalog,
    Index,
    Run,
    Settings,
    build_bundle,
    classify_schemas,
    collapse,
    drift_counts,
    provenance,
    render_columns,
    render_diagnostics,
    render_global_doc,
    render_index,
    render_tenant_doc,
    resolve_doc_names,
    source_resource,
)

T0 = "00000000-0000-4000-8000-000000000000"
T1 = "00000000-0000-4000-8000-000000000001"
T2 = "00000000-0000-4000-8000-000000000002"
T3 = "00000000-0000-4000-8000-000000000003"
T4 = "00000000-0000-4000-8000-000000000004"


def run(tenant_total=0, trust=None, table_comments=None, column_comments=None):
    return Run(
        trust=list(trust or []),
        pattern=DEFAULT_TENANT_PATTERN,
        tenant_total=tenant_total,
        table_comments=table_comments or {},
        column_comments=column_comments or {},
    )


def settings(out):
    return Settings(out=out, pattern=DEFAULT_TENANT_PATTERN, title="saas")


def index(tenant_docs=None, tenant_files=None, global_files=None, skipped=()):
    return Index(
        title="saas",
        tenant_docs=tenant_docs or {},
        tenant_files=tenant_files or {},
        global_files=global_files or {},
        skipped=skipped,
    )


def col(name, coltype, attnum, notnull=False):
    return {
        "name": name,
        "type": coltype,
        "notnull": notnull,
        "attnum": attnum,
        "pk": False,
    }


def pk_col(name, coltype, attnum):
    return dict(col(name, coltype, attnum, notnull=True), pk=True)


def reference_columns():
    return [
        pk_col("id", "uuid", 1),
        col("status", "character varying(32)", 2),
        col("amount", "numeric", 3),
    ]


def reordered_columns():
    return [
        pk_col("id", "uuid", 1),
        col("amount", "numeric", 2),
        col("status", "character varying(32)", 3),
    ]


def retyped_columns():
    return [
        pk_col("id", "uuid", 1),
        col("status", "text", 2),
        col("amount", "numeric", 3),
    ]


def without(column):
    return [c for c in reference_columns() if c["name"] != column]


def four_tenants_one_missing(column):
    return collapse(
        {
            "invoice": {
                T1: reference_columns(),
                T2: reference_columns(),
                T3: without(column),
                T4: retyped_columns(),
            }
        }
    )["invoice"]


def test_classify_schemas_splits_uuid_from_shared():
    tenant, shared = classify_schemas([T1, T2, "public", "meta"], DEFAULT_TENANT_PATTERN)
    assert tenant == [T1, T2]
    assert shared == ["public", "meta"]


def test_column_order_alone_is_not_drift():
    result = collapse({"invoice": {T1: reference_columns(), T2: reordered_columns()}})
    assert result["invoice"]["drift"] == []
    assert result["invoice"]["schema_count"] == 2


def test_missing_and_retyped_columns_are_drift():
    invoice = four_tenants_one_missing("status")
    drift = invoice["drift"]
    assert [d["schema"] for d in drift] == [T3, T4]
    assert drift[0]["missing"] == ["status"]
    assert drift[1]["changed"] == [("status", "character varying(32)", "text")]
    assert invoice["reference_schema"] == T1
    assert drift_counts(drift)["status"] == 2


def test_majority_wins_as_reference_not_first_seen():
    odd = without("amount")
    result = collapse({"invoice": {T1: odd, T2: reference_columns(), T3: reference_columns()}})
    assert result["invoice"]["reference_schema"] == T2
    assert [d["schema"] for d in result["invoice"]["drift"]] == [T1]


def test_tenant_doc_reports_counts_and_sink_safety():
    geo = reference_columns() + [col("location", "geometry(Point,4326)", 4)]
    result = collapse({"alert": {T1: geo, T2: geo}})
    doc = render_tenant_doc("alert", result["alert"], "f", run(tenant_total=2))
    assert "schemas: 2" in doc
    assert "absent_from: 0" in doc
    assert "column_drift: 0" in doc
    assert "replica_identity: FULL" in doc
    assert "sink_safe: false" in doc
    assert "No column drift" in doc
    assert "**Not compared**" in doc


def test_table_absent_from_some_tenants_is_reported():
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns()}})
    doc = render_tenant_doc("invoice", result["invoice"], "d", run(tenant_total=5))
    assert "schemas: 2" in doc
    assert "tenant_schemas_total: 5" in doc
    assert "absent_from: 3" in doc
    assert "present in 2 of 5 tenant schema(s)" in doc


def test_diagnostics_reports_skipped_object_kinds():
    catalog = Catalog(
        database="saas",
        version="17.10",
        schemas=[T1, T2, "meta"],
        skipped=[
            {"kind": "v", "objects": 1500, "schemas": 500},
            {"kind": "f", "objects": 970, "schemas": 498},
        ],
    )
    doc = render_diagnostics(catalog, [T1, T2], [], run())
    assert "| view | 1500 | 500 |" in doc
    assert "| foreign table | 970 | 498 |" in doc
    assert "not** covered by the drift check" in doc
    assert "MISSING** - connected as superuser" in doc


def test_diagnostics_says_nothing_was_skipped_when_nothing_was():
    catalog = Catalog(
        database="saas",
        version="17.10",
        schemas=[T1],
        extensions=["pg_stat_statements"],
    )
    doc = render_diagnostics(catalog, [T1], [], run())
    assert "None. Every relation in scope is an ordinary table." in doc
    assert "OK** - pg_stat_statements available" in doc


def test_nullability_only_change_is_drift():
    tightened = [
        pk_col("id", "uuid", 1),
        col("status", "character varying(32)", 2, notnull=True),
        col("amount", "numeric", 3),
    ]
    result = collapse(
        {"invoice": {T1: reference_columns(), T2: reference_columns(), T3: tightened}}
    )
    drift = result["invoice"]["drift"]
    assert [d["schema"] for d in drift] == [T3]
    assert drift[0]["changed"] == [
        ("status", "character varying(32)", "character varying(32) NOT NULL")
    ]
    doc = render_tenant_doc("invoice", result["invoice"], "d", run(tenant_total=3))
    assert (
        f"- `{T3}` - `status` is `character varying(32) NOT NULL`, "
        "reference has `character varying(32)`" in doc
    )


def test_drift_report_names_missing_and_extra_columns():
    dropped = without("amount")
    added = reference_columns() + [col("refunded_at", "timestamptz", 4)]
    result = collapse(
        {
            "invoice": {
                T1: reference_columns(),
                T2: reference_columns(),
                T3: dropped,
                T4: added,
            }
        }
    )
    doc = render_tenant_doc("invoice", result["invoice"], "d", run(tenant_total=4))
    assert f"- `{T3}` - missing `amount`" in doc
    assert f"- `{T4}` - extra `refunded_at`" in doc
    assert "2 schema(s) differ from the reference." in doc


def test_filename_unsafe_characters_are_replaced():
    assert resolve_doc_names(["orders"]) == {"orders": "orders"}
    assert resolve_doc_names(["../etc/passwd"]) == {"../etc/passwd": "_etc_passwd"}
    assert resolve_doc_names(["meta.tenant"]) == {"meta.tenant": "meta.tenant"}


def test_colliding_filenames_raise_instead_of_overwriting():
    try:
        resolve_doc_names(["a/b", "a b"])
    except ValueError as error:
        assert "both map to the filename" in str(error)
    else:
        raise AssertionError("expected a ValueError for colliding filenames")


def test_name_with_no_safe_characters_is_rejected():
    try:
        resolve_doc_names(["..."])
    except ValueError as error:
        assert "no filename-safe characters" in str(error)
    else:
        raise AssertionError("expected a ValueError for an unusable table name")


def test_index_omits_sections_that_have_no_entries():
    tenant_only = render_index(
        index(
            tenant_docs={"invoice": {"drift": [], "schema_count": 2}},
            tenant_files={"invoice": "invoice"},
        )
    )
    assert "## Tenant tables" in tenant_only
    assert "## Global tables" not in tenant_only

    global_only = render_index(index(global_files={"meta.tenant": "meta.tenant"}))
    assert "## Tenant tables" not in global_only
    assert "## Global tables" in global_only
    assert "* [meta.tenant](global/meta.tenant.md) - shared table" in global_only


def fake_catalog():
    invoice = reference_columns()
    return Catalog(
        schemas=[T1, T2, T3, "meta"],
        columns={
            (T1, "invoice"): invoice,
            (T2, "invoice"): invoice,
            ("meta", "tenant"): [pk_col("id", "uuid", 1)],
        },
        replica={(T1, "invoice"): "f", (T2, "invoice"): "f", ("meta", "tenant"): "d"},
        version="17.10",
        is_superuser="off",
        database="saas",
        extensions=[],
        skipped=[],
        resource="postgresql://db.example:5432/saas",
    )


def test_build_bundle_writes_a_complete_okf_bundle():
    with tempfile.TemporaryDirectory() as workdir:
        check_bundle(Path(workdir))


def check_bundle(tmp_path):
    stats = build_bundle(fake_catalog(), settings(tmp_path))

    assert stats == {
        "tenant_docs": 1,
        "global_docs": 1,
        "tenant_schemas": 3,
        "empty_tenants": 1,
        "source_tables": 3,
        "drifting": 0,
    }

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert not index.startswith("---")
    assert "* [invoice](tenant/invoice.md) - 2 tenant schemas" in index
    assert "* [meta.tenant](global/meta.tenant.md) - shared table" in index

    tenant_doc = (tmp_path / "tenant" / "invoice.md").read_text(encoding="utf-8")
    assert "absent_from: 1" in tenant_doc
    assert "present in 2 of 3 tenant schema(s)" in tenant_doc

    diagnostics = (tmp_path / "_diagnostics.md").read_text(encoding="utf-8")
    assert "tenant schemas with no tables: 1" in diagnostics
    assert "None. Every relation in scope is an ordinary table." in diagnostics


def test_every_doc_carries_okf_v02_provenance():
    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir)
        build_bundle(fake_catalog(), settings(out))
        docs = [
            out / "tenant" / "invoice.md",
            out / "global" / "meta.tenant.md",
            out / "_diagnostics.md",
        ]
        for doc in docs:
            text = doc.read_text(encoding="utf-8")
            assert "status: stable" in text, doc
            assert "generated:" in text, doc
            assert "  by: process:okf-pg-tenant" in text, doc
            assert "resource: postgresql://db.example:5432/saas" in text, doc
            assert "stale_after" not in text, doc


class FakeConnInfo:
    host = "db.internal"
    port = 5432
    dbname = "saas"
    password = "hunter2"
    user = "reader"

    def __str__(self):
        return "postgresql://reader:hunter2@db.internal:5432/saas"


class FakeConn:
    info = FakeConnInfo()


def test_source_resource_never_carries_credentials():
    resource = source_resource(FakeConn())
    assert resource == "postgresql://db.internal:5432/saas"
    for secret in ("hunter2", "reader", "@"):
        assert secret not in resource, f"{secret!r} leaked into {resource!r}"


def test_source_resource_falls_back_when_info_is_absent():
    assert source_resource(object()) == "postgresql://localhost:5432/"


def test_stale_after_is_emitted_only_when_asked():
    lines = provenance(fake_catalog(), "2026-08-29T00:00:00+00:00")
    assert not any(line.startswith("stale_after") for line in lines)

    dated = provenance(fake_catalog(), "2026-08-29T00:00:00+00:00", "2026-09-28")
    assert "stale_after: 2026-09-28" in dated


def test_drift_section_names_what_it_does_not_compare():
    dropped = without("amount")
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns(), T3: dropped}})
    doc = render_tenant_doc("invoice", result["invoice"], "d", run(tenant_total=3))
    for omitted in ("defaults", "constraints", "indexes", "foreign keys", "triggers"):
        assert omitted in doc, omitted
    assert "different integrity rules" in doc


def test_a_tie_for_most_common_is_reported_as_ambiguous():
    other = without("amount")
    tied = collapse({"invoice": {T1: reference_columns(), T2: other}})["invoice"]
    assert tied["reference_ambiguous"] is True
    assert tied["variants"] == 2
    doc = render_tenant_doc("invoice", tied, "d", run(tenant_total=2))
    assert "reference_ambiguous: true" in doc
    assert "picked alphabetically and carries no" in doc

    clear = collapse({"invoice": {T1: reference_columns(), T2: reference_columns(), T3: other}})[
        "invoice"
    ]
    assert clear["reference_ambiguous"] is False
    assert "reference_ambiguous: false" in render_tenant_doc(
        "invoice", clear, "d", run(tenant_total=3)
    )


def test_comments_become_descriptions():
    commented = run(
        tenant_total=2,
        table_comments={(T1, "invoice"): "One row per completed order."},
        column_comments={(T1, "invoice", "amount"): "Gross total in cents."},
    )
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns()}})
    doc = render_tenant_doc("invoice", result["invoice"], "d", commented)
    assert 'description: "One row per completed order."' in doc
    assert "Gross total in cents." in doc


def test_index_warns_when_the_inventory_is_partial():
    docs = {"invoice": {"drift": [], "schema_count": 2}}
    files = {"invoice": "invoice"}
    partial = render_index(
        index(
            tenant_docs=docs,
            tenant_files=files,
            skipped=[{"kind": "v", "objects": 9, "schemas": 3}],
        )
    )
    assert "This inventory is incomplete" in partial
    assert "9 view(s)" in partial

    complete = render_index(index(tenant_docs=docs, tenant_files=files))
    assert "incomplete" not in complete


def test_bundle_creates_nested_output_directories():
    with tempfile.TemporaryDirectory() as workdir:
        nested = Path(workdir) / "a" / "b" / "c"
        build_bundle(fake_catalog(), settings(nested))
        assert (nested / "index.md").exists()
        assert (nested / "tenant" / "invoice.md").exists()


def test_bundle_can_be_rebuilt_over_itself():
    with tempfile.TemporaryDirectory() as workdir:
        out = Path(workdir)
        first = build_bundle(fake_catalog(), settings(out))
        second = build_bundle(fake_catalog(), settings(out))
        assert first == second


def test_drift_reports_a_type_that_sorts_before_the_reference():
    earlier = [
        pk_col("id", "uuid", 1),
        col("status", "bigint", 2),
        col("amount", "numeric", 3),
    ]
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns(), T3: earlier}})
    drift = result["invoice"]["drift"]
    assert drift[0]["changed"] == [("status", "character varying(32)", "bigint")]


def test_a_dropped_column_is_missing_and_never_extra():
    dropped = without("amount")
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns(), T3: dropped}})
    drift = result["invoice"]["drift"]
    assert drift[0]["missing"] == ["amount"]
    assert drift[0]["extra"] == []


def test_reference_tiebreak_is_alphabetical_by_schema_name():
    other = without("amount")
    tied = collapse({"invoice": {T2: other, T1: reference_columns()}})["invoice"]
    assert tied["reference_schema"] == T1
    assert tied["reference_ambiguous"] is True


def test_ambiguity_compares_the_two_largest_groups_not_the_smallest():
    invoice = four_tenants_one_missing("amount")
    assert invoice["variants"] == 3
    assert invoice["reference_ambiguous"] is False


def test_drift_counts_include_extra_columns():
    added = reference_columns() + [col("refunded_at", "timestamptz", 4)]
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns(), T3: added}})
    counts = drift_counts(result["invoice"]["drift"])
    assert counts["refunded_at"] == 1


def test_column_comments_are_scoped_to_one_schema_and_one_table():
    carrier = run(
        column_comments={
            (T1, "invoice", "amount"): "right one",
            (T2, "invoice", "amount"): "schema sorts after",
            (T0, "invoice", "amount"): "schema sorts before",
            (T1, "refund", "amount"): "table sorts after",
            (T1, "audit", "amount"): "table sorts before",
        }
    )
    assert carrier.column_comments_for(T1, "invoice") == {"amount": "right one"}


def test_column_comments_match_by_value_not_identity():
    carrier = run(column_comments={(T1, "invoice", "amount"): "matched"})
    schema = "".join([T1[:8], T1[8:]])
    table = "".join(["inv", "oice"])
    assert schema is not T1
    assert table is not "invoice"  # noqa: F632
    assert carrier.column_comments_for(schema, table) == {"amount": "matched"}


def test_column_table_marks_primary_keys_and_nullability():
    table = render_columns(reference_columns())
    assert "| id | uuid | NO |" in table
    assert "| amount | numeric | YES |" in table
    assert table.count("PK") == 1


def test_columns_with_no_drift_carry_no_differs_note():
    table = render_columns(reference_columns(), {}, {})
    assert "differs in" not in table


def test_geometry_columns_get_a_sink_warning():
    geo = reference_columns() + [col("location", "geometry(Point,4326)", 4)]
    result = collapse({"alert": {T1: geo}})
    doc = render_tenant_doc("alert", result["alert"], "d", run(tenant_total=1))
    assert "## Sink warning" in doc
    assert "Most CDC sinks cannot map these." in doc

    plain = collapse({"invoice": {T1: reference_columns()}})
    assert "## Sink warning" not in render_tenant_doc(
        "invoice", plain["invoice"], "d", run(tenant_total=1)
    )


def test_global_doc_reports_sink_safety():
    geo = reference_columns() + [col("shape", "geography(Point)", 4)]
    assert "sink_safe: false" in render_global_doc(("meta", "site"), geo, "d", run())
    assert "sink_safe: true" in render_global_doc(("meta", "site"), reference_columns(), "d", run())


def test_diagnostics_recognises_a_superuser_connection():
    elevated = Catalog(database="saas", is_superuser="on")
    assert "OK** - connected as superuser" in render_diagnostics(elevated, [], [], run())

    normal = Catalog(database="saas", is_superuser="off")
    assert "MISSING** - connected as superuser" in render_diagnostics(normal, [], [], run())


def test_diagnostics_names_every_capability_and_why_each_one_failed():
    doc = render_diagnostics(Catalog(database="saas"), [], [], run())
    assert "OK** - read the catalog via pg_catalog" in doc
    assert "(not installed - usage stats unavailable)" in doc
    assert "(not superuser - normal on managed Postgres, not an error)" in doc


def test_index_entry_reports_how_many_schemas_drift():
    drifting = {"invoice": {"drift": [{"schema": T1}], "schema_count": 3}}
    entry = render_index(index(tenant_docs=drifting, tenant_files={"invoice": "invoice"}))
    assert "* [invoice](tenant/invoice.md) - 3 tenant schemas, 1 drifting" in entry

    clean = {"invoice": {"drift": [], "schema_count": 3}}
    quiet = render_index(index(tenant_docs=clean, tenant_files={"invoice": "invoice"}))
    assert "* [invoice](tenant/invoice.md) - 3 tenant schemas" in quiet
    assert "drifting" not in quiet


def test_reference_tiebreak_uses_schema_names_not_column_names():
    tied = collapse(
        {
            "invoice": {
                T1: [col("zulu", "text", 1)],
                T2: [col("alpha", "text", 1)],
            }
        }
    )["invoice"]
    assert tied["reference_ambiguous"] is True
    assert tied["reference_schema"] == T1


def test_diagnostics_compares_superuser_by_value_not_identity():
    elevated = Catalog(database="saas", is_superuser="".join(["o", "n"]))
    assert elevated.is_superuser is not "on"  # noqa: F632
    assert "OK** - connected as superuser" in render_diagnostics(elevated, [], [], run())


def test_an_available_capability_carries_no_failure_note():
    installed = Catalog(database="saas", extensions=["pg_stat_statements"])
    doc = render_diagnostics(installed, [], [], run())
    assert "OK** - pg_stat_statements available" in doc
    assert "not installed - usage stats unavailable" not in doc


def test_drift_never_reports_a_column_as_both_missing_and_extra():
    invoice = four_tenants_one_missing("amount")
    for entry in invoice["drift"]:
        assert not set(entry["missing"]) & set(entry["extra"])


def demo():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")


if __name__ == "__main__":
    demo()
