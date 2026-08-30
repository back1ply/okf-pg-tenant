"""Data in, string out. Nothing here performs IO.

Every renderer takes already-decided values and returns Markdown. Keeping them
pure is what lets the suite assert on exact output without a database, and what
makes the byte-diff check in ``AGENTS.md`` meaningful.
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Optional

from .catalog import Catalog
from .collapse import drift_counts
from .models import (
    CollapsedTable,
    Column,
    DriftEntry,
    Index,
    Run,
    SkippedKind,
    TableKey,
)

REPLICA_IDENTITY = {"d": "DEFAULT", "n": "NOTHING", "f": "FULL", "i": "INDEX"}
SKIPPED_KINDS = {
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
    "p": "partitioned table",
}
GEO_TYPES = ("geometry", "geography")
PRODUCER = "process:okf-pg-tenant"


def provenance(catalog: Catalog, generated_at: str, stale_after: Optional[str] = None) -> list[str]:
    """Build the OKF v0.2 provenance block every document carries.

    ``verified:`` is deliberately never emitted: OKF separates who produced a
    document from who confirmed it, and nothing here confirms anything.
    """
    lines = [
        "status: stable",
        "sources:",
        f"  - resource: {catalog.resource}",
        f"    id: {catalog.database}",
        f"    title: PostgreSQL catalog for {catalog.database}",
        "generated:",
        f"  by: {PRODUCER}",
        f"  at: {generated_at}",
    ]
    if stale_after:
        lines.append(f"stale_after: {stale_after}")
    return lines


NOT_COMPARED = (
    "Compared: column name, type, nullability. **Not compared**: defaults, primary keys, "
    "unique/check/exclusion constraints, indexes, foreign keys, identity/generated columns, "
    "triggers and row-level security. Two schemas reported identical here can still accept "
    "different writes and enforce different integrity rules."
)


def yaml_scalar(value: Optional[str]) -> str:
    """Quote a value for YAML frontmatter, escaping backslashes and quotes."""
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def render_columns(
    columns: list[Column],
    counts: Optional[dict[str, int]] = None,
    comments: Optional[dict[str, str]] = None,
) -> str:
    """Render the column table, in physical ``attnum`` order."""
    counts = counts or {}
    comments = comments or {}
    lines = ["| Column | Type | Null | Description | Notes |", "|---|---|---|---|---|"]
    for col in sorted(columns, key=lambda c: c["attnum"]):
        notes = []
        if col["pk"]:
            notes.append("PK")
        hits = counts.get(col["name"], 0)
        if hits:
            notes.append(f"differs in {hits} schema(s)")
        null = "NO" if col["notnull"] else "YES"
        described = (comments.get(col["name"]) or "").replace("|", "\\|")
        lines.append(
            f"| {col['name']} | {col['type']} | {null} | {described} | {'; '.join(notes)} |"
        )
    return "\n".join(lines)


def render_drift(drift: list[DriftEntry]) -> str:
    """Render the drift section, always followed by what it does not compare."""
    if not drift:
        return f"No column drift. All schemas match the reference.\n\n{NOT_COMPARED}\n"
    lines = [f"{len(drift)} schema(s) differ from the reference.", ""]
    for entry in drift:
        parts = []
        if entry["missing"]:
            parts.append("missing " + ", ".join(f"`{n}`" for n in entry["missing"]))
        if entry["extra"]:
            parts.append("extra " + ", ".join(f"`{n}`" for n in entry["extra"]))
        for name, want, got in entry["changed"]:
            parts.append(f"`{name}` is `{got}`, reference has `{want}`")
        lines.append(f"- `{entry['schema']}` - " + "; ".join(parts))
    return "\n".join(lines) + f"\n\n{NOT_COMPARED}\n"


def geometry_columns(columns: list[Column]) -> list[str]:
    """Names of columns most CDC sinks cannot map."""
    return [c["name"] for c in columns if any(g in c["type"] for g in GEO_TYPES)]


def cdc_block(columns: list[Column], replica_ident: str, geo: list[str]) -> list[str]:
    """The ``cdc:`` frontmatter block.

    Tenant and global documents make the same three claims about replication, so
    the claims are made in one place: a reader comparing two documents should
    never find them phrased differently. ``geo`` is passed in because the tenant
    document needs the same list again for its sink warning.
    """
    return [
        "cdc:",
        f"  replica_identity: {REPLICA_IDENTITY.get(replica_ident, replica_ident)}",
        f"  primary_key: {str(any(c['pk'] for c in columns)).lower()}",
        f"  sink_safe: {str(not geo).lower()}",
    ]


def render_tenant_doc(table: str, info: CollapsedTable, replica_ident: str, run: Run) -> str:
    """Render one logical table collapsed across its tenant schemas."""
    columns = info["columns"]
    counts = drift_counts(info["drift"])
    geo = geometry_columns(columns)
    tenant_total = run.tenant_total
    trust = run.trust
    absent = tenant_total - info["schema_count"]
    reference = info["reference_schema"]
    description = run.table_comments.get((reference, table))
    front = "\n".join(
        [
            "---",
            "type: Postgres Table (multi-tenant)",
            f"title: {table}",
            f"description: {yaml_scalar(description)}",
        ]
        + list(trust)
        + [
            "tenancy:",
            "  pattern: schema-per-tenant",
            f"  schemas: {info['schema_count']}",
            f"  tenant_schemas_total: {tenant_total}",
            f"  absent_from: {absent}",
            f"  column_drift: {len(info['drift'])}",
            f"  variants: {info['variants']}",
            f"  reference_schema: {reference}",
            f"  reference_ambiguous: {str(info['reference_ambiguous']).lower()}",
        ]
        + cdc_block(columns, replica_ident, geo)
        + ["---"]
    )
    body = [
        "",
        "",
        f"# {table}",
        "",
        f"One logical table, present in {info['schema_count']} of {tenant_total} tenant schema(s).",
        "",
        "## Columns",
        "",
        render_columns(columns, counts, run.column_comments_for(reference, table)),
        "",
        "## Tenant column drift",
        "",
        render_drift(info["drift"]),
    ]
    if info["reference_ambiguous"]:
        body += [
            "> **The reference schema is ambiguous.** Two or more structures are tied for",
            "> most common, so the one above was picked alphabetically and carries no",
            "> authority. Renaming or adding a tenant can change it without any DDL change.",
            "",
        ]
    if geo:
        body += [
            "## Sink warning",
            "",
            "Geometry column(s) present: " + ", ".join(f"`{n}`" for n in geo) + ".",
            "Most CDC sinks cannot map these.",
            "",
        ]
    return front + "\n".join(body)


def render_global_doc(target: TableKey, columns: list[Column], replica_ident: str, run: Run) -> str:
    """Render one shared (non-tenant) table."""
    schema, table = target
    trust = run.trust
    geo = geometry_columns(columns)
    front = "\n".join(
        [
            "---",
            "type: Postgres Table",
            f"title: {schema}.{table}",
            f"description: {yaml_scalar(run.table_comments.get(target))}",
            f"schema: {schema}",
        ]
        + list(trust)
        + cdc_block(columns, replica_ident, geo)
        + ["---"]
    )
    return front + "\n".join(
        [
            "",
            "",
            f"# {schema}.{table}",
            "",
            "## Columns",
            "",
            render_columns(columns, None, run.column_comments_for(schema, table)),
            "",
        ]
    )


def render_skipped(skipped: Sequence[SkippedKind]) -> list[str]:
    """Report relation kinds that exist but are not documented.

    Silence is never a pass, so a run that documented nothing still says what it
    chose to skip and how unevenly those objects are spread.
    """
    if not skipped:
        return ["None. Every relation in scope is an ordinary table."]
    rows = [
        f"| {SKIPPED_KINDS.get(r['kind'], r['kind'])} | {r['objects']} | {r['schemas']} |"
        for r in skipped
    ]
    return (
        [
            "Only ordinary tables are documented. These exist and were skipped,",
            "so they are **not** covered by the drift check above.",
            "",
            "| Kind | Objects | Schemas |",
            "|---|---|---|",
        ]
        + rows
        + [
            "",
            "If a kind's object count is not a whole multiple of its schema count,",
            "it is spread unevenly across schemas. That is undetected drift.",
        ]
    )


def capability_checks(catalog: Catalog) -> list[tuple[str, bool, str]]:
    """What the run could do, and what each failure actually means.

    The note beside a failed check matters as much as the check: "not
    superuser" is normal on managed Postgres and must not read as an error.
    """
    ext = set(catalog.extensions)
    return [
        ("read the catalog via pg_catalog (no table grants needed)", True, ""),
        (
            "pg_stat_statements available",
            "pg_stat_statements" in ext,
            "not installed - usage stats unavailable",
        ),
        (
            "connected as superuser",
            catalog.is_superuser == "on",
            "not superuser - normal on managed Postgres, not an error",
        ),
    ]


def render_diagnostics(
    catalog: Catalog,
    tenant_schemas: Sequence[str],
    empty_tenants: Sequence[str],
    run: Run,
) -> str:
    """Render what this run could and could not read.

    A missing capability gets named here rather than being passed over, which is
    why the producer also counts the relations it chose not to document.
    """
    pattern = run.pattern
    trust = run.trust
    checks = capability_checks(catalog)
    lines = (
        [
            "---",
            "type: Diagnostics",
            "title: Producer diagnostics",
            'description: "What this run could and could not read."',
            f"timestamp: {datetime.now(timezone.utc).isoformat()}",
        ]
        + list(trust)
        + [
            "---",
            "",
            "# Producer diagnostics",
            "",
            f"- database: `{catalog.database}`",
            f"- server version: `{catalog.version}`",
            f"- tenant pattern: `{pattern}`",
            f"- schemas seen: {len(catalog.schemas)}",
            f"- tenant schemas matched: {len(tenant_schemas)}",
            f"- tenant schemas with no tables: {len(empty_tenants)}",
            "",
            "## Capabilities",
            "",
        ]
    )
    for label, ok, note in checks:
        mark = "OK" if ok else "MISSING"
        suffix = f" ({note})" if not ok and note else ""
        lines.append(f"- **{mark}** - {label}{suffix}")
    lines += ["", "## Objects not documented", ""]
    lines += render_skipped(catalog.skipped)
    lines += [
        "",
        "## Read this before trusting the bundle",
        "",
        "Structure comes from `pg_class`, `pg_attribute`, `pg_namespace`, `pg_index`",
        "and `pg_description`. Those are world-readable and do not filter by",
        "privilege, so an empty tenant schema means the schema really has no tables,",
        "not that access was denied. New tenants are legitimately empty.",
        "",
        "That property belongs to those catalogs, not to `pg_catalog` as a whole:",
        "`pg_stats` shows only rows for tables you may read, and `pg_authid` is not",
        "publicly readable at all. Anything added here that reads a different catalog",
        "must re-check its visibility rules.",
        "",
    ]
    return "\n".join(lines)


def render_tenant_entries(
    tenant_docs: dict[str, CollapsedTable], filenames: dict[str, str]
) -> list[str]:
    """Index entries for tenant tables, or nothing if there are none."""
    if not tenant_docs:
        return []
    lines = ["## Tenant tables", ""]
    for table, info in tenant_docs.items():
        drift = len(info["drift"])
        suffix = f", {drift} drifting" if drift else ""
        lines.append(
            f"* [{table}](tenant/{filenames[table]}.md) - "
            f"{info['schema_count']} tenant schemas{suffix}"
        )
    return lines + [""]


def render_global_entries(filenames: dict[str, str]) -> list[str]:
    """Index entries for shared tables, or nothing if there are none."""
    if not filenames:
        return []
    lines = ["## Global tables", ""]
    for label, filename in filenames.items():
        lines.append(f"* [{label}](global/{filename}.md) - shared table")
    return lines + [""]


def render_incomplete_notice(skipped: Sequence[SkippedKind]) -> list[str]:
    """Warn on the index that the inventory omits whole relation kinds."""
    if not skipped:
        return []
    parts = ", ".join(
        f"{row['objects']} {SKIPPED_KINDS.get(row['kind'], row['kind'])}(s)" for row in skipped
    )
    return [
        "> **This inventory is incomplete.** Only ordinary tables are documented. This",
        f"> database also holds {parts}, which are neither documented here nor",
        "> drift-checked. Counts and the uneven-spread test are in",
        "> [_diagnostics.md](_diagnostics.md).",
        "",
    ]


def render_index(index: Index) -> str:
    """Render ``index.md``.

    No YAML frontmatter: the OKF spec reserves index files and forbids it.
    """
    header = [
        f"# {index.title}",
        "",
        "Generated by okf-pg-tenant from a PostgreSQL catalog.",
        "",
    ] + render_incomplete_notice(index.skipped)
    footer = [
        "## Meta",
        "",
        "* [Diagnostics](_diagnostics.md) - what this run could not read",
        "",
    ]
    return "\n".join(
        header
        + render_tenant_entries(index.tenant_docs, index.tenant_files)
        + render_global_entries(index.global_files)
        + footer
    )
