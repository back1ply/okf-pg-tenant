"""The only function that touches the filesystem."""

from datetime import datetime, timezone
from pathlib import Path

from .catalog import Catalog
from .collapse import classify_schemas, collapse, partition_tables
from .models import Index, Run, Settings
from .naming import resolve_doc_names
from .render import (
    provenance,
    render_diagnostics,
    render_global_doc,
    render_index,
    render_tenant_doc,
)


def build_bundle(catalog: Catalog, settings: Settings) -> dict[str, int]:
    """Write a complete OKF bundle and return what it contains.

    Every decision was already made by the pure functions above; this arranges
    the results into files. The returned counts are what :func:`~.cli.report`
    prints, and what the test suite asserts on.
    """
    out, pattern, title = settings.out, settings.pattern, settings.title
    tenant_schemas, _shared = classify_schemas(catalog.schemas, pattern)
    tenant_set = set(tenant_schemas)
    run = Run(
        trust=provenance(catalog, datetime.now(timezone.utc).isoformat(), settings.stale_after),
        pattern=pattern,
        tenant_total=len(tenant_schemas),
        table_comments=catalog.table_comments,
        column_comments=catalog.column_comments,
    )

    per_table, global_tables = partition_tables(catalog.columns, tenant_set)
    collapsed = collapse(per_table)
    non_empty = {key[0] for key in catalog.columns if key[0] in tenant_set}
    empty_tenants = sorted(tenant_set - non_empty)

    out = Path(out)
    (out / "tenant").mkdir(parents=True, exist_ok=True)
    (out / "global").mkdir(parents=True, exist_ok=True)

    tenant_files = resolve_doc_names(collapsed)
    global_files = resolve_doc_names(f"{schema}.{table}" for schema, table in sorted(global_tables))

    for table, info in collapsed.items():
        ident = catalog.replica[(info["reference_schema"], table)]
        (out / "tenant" / f"{tenant_files[table]}.md").write_text(
            render_tenant_doc(table, info, ident, run),
            encoding="utf-8",
        )

    for target, columns in sorted(global_tables.items()):
        schema, table = target
        ident = catalog.replica[target]
        (out / "global" / f"{global_files[f'{schema}.{table}']}.md").write_text(
            render_global_doc(target, columns, ident, run), encoding="utf-8"
        )

    (out / "index.md").write_text(
        render_index(
            Index(
                title=title,
                tenant_docs=collapsed,
                tenant_files=tenant_files,
                global_files=global_files,
                skipped=catalog.skipped,
            )
        ),
        encoding="utf-8",
    )
    (out / "_diagnostics.md").write_text(
        render_diagnostics(catalog, tenant_schemas, empty_tenants, run),
        encoding="utf-8",
    )
    return {
        "tenant_docs": len(collapsed),
        "global_docs": len(global_tables),
        "tenant_schemas": len(tenant_schemas),
        "empty_tenants": len(empty_tenants),
        "source_tables": len(catalog.columns),
        "drifting": sum(len(i["drift"]) for i in collapsed.values()),
    }
