import tempfile
from pathlib import Path

from okf_pg_tenant import (
    DEFAULT_TENANT_PATTERN,
    build_bundle,
    classify_schemas,
    collapse,
    drift_counts,
    render_diagnostics,
    render_index,
    render_tenant_doc,
    resolve_doc_names,
)

T1 = "00000000-0000-4000-8000-000000000001"
T2 = "00000000-0000-4000-8000-000000000002"
T3 = "00000000-0000-4000-8000-000000000003"
T4 = "00000000-0000-4000-8000-000000000004"


def col(name, coltype, attnum, notnull=False, pk=False):
    return {
        "name": name,
        "type": coltype,
        "notnull": notnull,
        "attnum": attnum,
        "pk": pk,
    }


def reference_columns():
    return [
        col("id", "uuid", 1, notnull=True, pk=True),
        col("status", "character varying(32)", 2),
        col("amount", "numeric", 3),
    ]


def reordered_columns():
    return [
        col("id", "uuid", 1, notnull=True, pk=True),
        col("amount", "numeric", 2),
        col("status", "character varying(32)", 3),
    ]


def test_classify_schemas_splits_uuid_from_shared():
    tenant, shared = classify_schemas(
        [T1, T2, "public", "meta"], DEFAULT_TENANT_PATTERN
    )
    assert tenant == [T1, T2]
    assert shared == ["public", "meta"]


def test_column_order_alone_is_not_drift():
    result = collapse({"invoice": {T1: reference_columns(), T2: reordered_columns()}})
    assert result["invoice"]["drift"] == []
    assert result["invoice"]["schema_count"] == 2


def test_missing_and_retyped_columns_are_drift():
    dropped = [c for c in reference_columns() if c["name"] != "status"]
    retyped = [
        col("id", "uuid", 1, notnull=True, pk=True),
        col("status", "text", 2),
        col("amount", "numeric", 3),
    ]
    result = collapse(
        {
            "invoice": {
                T1: reference_columns(),
                T2: reference_columns(),
                T3: dropped,
                T4: retyped,
            }
        }
    )
    drift = result["invoice"]["drift"]
    assert [d["schema"] for d in drift] == [T3, T4]
    assert drift[0]["missing"] == ["status"]
    assert drift[1]["changed"] == [("status", "character varying(32)", "text")]
    assert result["invoice"]["reference_schema"] == T1
    assert drift_counts(drift)["status"] == 2


def test_majority_wins_as_reference_not_first_seen():
    odd = [c for c in reference_columns() if c["name"] != "amount"]
    result = collapse(
        {"invoice": {T1: odd, T2: reference_columns(), T3: reference_columns()}}
    )
    assert result["invoice"]["reference_schema"] == T2
    assert [d["schema"] for d in result["invoice"]["drift"]] == [T1]


def test_tenant_doc_reports_counts_and_sink_safety():
    geo = reference_columns() + [col("location", "geometry(Point,4326)", 4)]
    result = collapse({"alert": {T1: geo, T2: geo}})
    doc = render_tenant_doc("alert", result["alert"], "f", tenant_total=2)
    assert "schemas: 2" in doc
    assert "absent_from: 0" in doc
    assert "drift: 0" in doc
    assert "replica_identity: FULL" in doc
    assert "sink_safe: false" in doc
    assert "No structural drift" in doc


def test_table_absent_from_some_tenants_is_reported():
    result = collapse({"invoice": {T1: reference_columns(), T2: reference_columns()}})
    doc = render_tenant_doc("invoice", result["invoice"], "d", tenant_total=5)
    assert "schemas: 2" in doc
    assert "tenant_schemas_total: 5" in doc
    assert "absent_from: 3" in doc
    assert "present in 2 of 5 tenant schema(s)" in doc


def test_diagnostics_reports_skipped_object_kinds():
    catalog = {
        "database": "saas",
        "version": "17.10",
        "schemas": [T1, T2, "meta"],
        "extensions": [],
        "is_superuser": "off",
        "skipped": [
            {"kind": "v", "objects": 1500, "schemas": 500},
            {"kind": "f", "objects": 970, "schemas": 498},
        ],
    }
    doc = render_diagnostics(catalog, [T1, T2], [], DEFAULT_TENANT_PATTERN)
    assert "| view | 1500 | 500 |" in doc
    assert "| foreign table | 970 | 498 |" in doc
    assert "not** covered by the drift check" in doc
    assert "MISSING** - connected as superuser" in doc


def test_diagnostics_says_nothing_was_skipped_when_nothing_was():
    catalog = {
        "database": "saas",
        "version": "17.10",
        "schemas": [T1],
        "extensions": ["pg_stat_statements"],
        "is_superuser": "off",
        "skipped": [],
    }
    doc = render_diagnostics(catalog, [T1], [], DEFAULT_TENANT_PATTERN)
    assert "None. Every relation in scope is an ordinary table." in doc
    assert "OK** - pg_stat_statements available" in doc


def test_nullability_only_change_is_drift():
    tightened = [
        col("id", "uuid", 1, notnull=True, pk=True),
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
    doc = render_tenant_doc("invoice", result["invoice"], "d", tenant_total=3)
    assert (
        f"- `{T3}` - `status` is `character varying(32) NOT NULL`, "
        "reference has `character varying(32)`" in doc
    )


def test_drift_report_names_missing_and_extra_columns():
    dropped = [c for c in reference_columns() if c["name"] != "amount"]
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
    doc = render_tenant_doc("invoice", result["invoice"], "d", tenant_total=4)
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
    tenant_only = render_index("saas", {"invoice": {"drift": [], "schema_count": 2}},
                               {"invoice": "invoice"}, {})
    assert "## Tenant tables" in tenant_only
    assert "## Global tables" not in tenant_only

    global_only = render_index("saas", {}, {}, {"meta.tenant": "meta.tenant"})
    assert "## Tenant tables" not in global_only
    assert "## Global tables" in global_only
    assert "* [meta.tenant](global/meta.tenant.md) - shared table" in global_only


def fake_catalog():
    invoice = reference_columns()
    return {
        "schemas": [T1, T2, T3, "meta"],
        "columns": {
            (T1, "invoice"): invoice,
            (T2, "invoice"): invoice,
            ("meta", "tenant"): [col("id", "uuid", 1, notnull=True, pk=True)],
        },
        "replica": {(T1, "invoice"): "f", (T2, "invoice"): "f", ("meta", "tenant"): "d"},
        "version": "17.10",
        "is_superuser": "off",
        "database": "saas",
        "extensions": [],
        "skipped": [],
    }


def test_build_bundle_writes_a_complete_okf_bundle():
    with tempfile.TemporaryDirectory() as workdir:
        check_bundle(Path(workdir))


def check_bundle(tmp_path):
    stats = build_bundle(fake_catalog(), tmp_path, DEFAULT_TENANT_PATTERN, "saas")

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


def demo():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")


if __name__ == "__main__":
    demo()
