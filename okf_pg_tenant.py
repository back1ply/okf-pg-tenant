import argparse
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_TENANT_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

REPLICA_IDENTITY = {"d": "DEFAULT", "n": "NOTHING", "f": "FULL", "i": "INDEX"}
SKIPPED_KINDS = {
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
    "p": "partitioned table",
}
GEO_TYPES = ("geometry", "geography")
UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")
PRODUCER = "process:okf-pg-tenant"

SQL_SCHEMAS = r"""
SELECT nspname
FROM pg_namespace
WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema'
ORDER BY nspname
"""

SQL_COLUMNS = r"""
SELECT n.nspname, c.relname, a.attname,
       format_type(a.atttypid, a.atttypmod),
       a.attnotnull, a.attnum, c.relreplident
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind = 'r'
  AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
ORDER BY n.nspname, c.relname, a.attnum
"""

SQL_PRIMARY_KEYS = r"""
SELECT n.nspname, c.relname, a.attname
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY (i.indkey)
WHERE i.indisprimary AND c.relkind = 'r'
  AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
"""

SQL_SKIPPED = r"""
SELECT c.relkind, count(*), count(DISTINCT n.nspname)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('v', 'm', 'f', 'p')
  AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
GROUP BY c.relkind
ORDER BY c.relkind
"""

SQL_COMMENTS = r"""
SELECT n.nspname, c.relname, d.objsubid, a.attname, d.description
FROM pg_description d
JOIN pg_class c ON c.oid = d.objoid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid
WHERE c.relkind = 'r'
  AND n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
"""

SQL_ENV = r"""
SELECT current_setting('server_version'),
       current_setting('is_superuser'),
       current_database()
"""

SQL_EXTENSIONS = "SELECT extname FROM pg_extension ORDER BY extname"


@dataclass
class Catalog:
    database: str = ""
    version: str = ""
    is_superuser: str = "off"
    resource: str = ""
    schemas: list = field(default_factory=list)
    columns: dict = field(default_factory=dict)
    replica: dict = field(default_factory=dict)
    extensions: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    table_comments: dict = field(default_factory=dict)
    column_comments: dict = field(default_factory=dict)


@dataclass
class Run:
    trust: list
    pattern: str
    tenant_total: int = 0
    table_comments: dict = field(default_factory=dict)
    column_comments: dict = field(default_factory=dict)

    def column_comments_for(self, schema, table):
        return {
            column: text
            for (s, t, column), text in self.column_comments.items()
            if s == schema and t == table
        }


@dataclass
class Settings:
    out: str
    pattern: str
    title: str
    stale_after: str = ""



def classify_schemas(schemas, pattern):
    matcher = re.compile(pattern)
    tenant, shared = [], []
    for schema in schemas:
        (tenant if matcher.match(schema) else shared).append(schema)
    return tenant, shared


def doc_filename(name):
    cleaned = UNSAFE_FILENAME.sub("_", name).lstrip(".")
    if not cleaned:
        raise ValueError(f"name has no filename-safe characters: {name!r}")
    return cleaned


def resolve_doc_names(names):
    resolved = {}
    claimed = {}
    for name in names:
        filename = doc_filename(name)
        if filename in claimed:
            raise ValueError(
                f"{claimed[filename]!r} and {name!r} both map to the filename "
                f"{filename!r}; exclude one of them"
            )
        claimed[filename] = name
        resolved[name] = filename
    return resolved


def signature(columns):
    return frozenset((c["name"], c["type"], c["notnull"]) for c in columns)


def describe_column(entry):
    return entry[1] + (" NOT NULL" if entry[2] else "")


def diff_signature(modal, other):
    modal_by_name = {c[0]: c for c in modal}
    other_by_name = {c[0]: c for c in other}
    changed = []
    for name in sorted(set(modal_by_name) & set(other_by_name)):
        want, got = modal_by_name[name], other_by_name[name]
        if want[1:] != got[1:]:
            changed.append((name, describe_column(want), describe_column(got)))
    return {
        "missing": sorted(set(modal_by_name) - set(other_by_name)),
        "extra": sorted(set(other_by_name) - set(modal_by_name)),
        "changed": changed,
    }


def group_by_signature(per_schema):
    groups = defaultdict(list)
    for schema, columns in per_schema.items():
        groups[signature(columns)].append(schema)
    return groups


def modal_group(groups):
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), min(kv[1])))
    return ranked[0]


def reference_is_ambiguous(groups):
    sizes = sorted((len(v) for v in groups.values()), reverse=True)
    return len(sizes) > 1 and sizes[0] == sizes[1]


def drift_entries(groups, modal_sig):
    entries = []
    for sig, schemas in groups.items():
        if sig == modal_sig:
            continue
        difference = diff_signature(modal_sig, sig)
        entries += [dict(difference, schema=schema) for schema in schemas]
    return sorted(entries, key=lambda d: d["schema"])


def collapse_table(per_schema):
    groups = group_by_signature(per_schema)
    modal_sig, modal_schemas = modal_group(groups)
    reference = min(modal_schemas)
    return {
        "columns": per_schema[reference],
        "reference_schema": reference,
        "reference_ambiguous": reference_is_ambiguous(groups),
        "variants": len(groups),
        "schema_count": sum(len(v) for v in groups.values()),
        "drift": drift_entries(groups, modal_sig),
    }


def collapse(per_table):
    return {
        table: collapse_table(per_schema)
        for table, per_schema in sorted(per_table.items())
    }


def drift_counts(drift):
    counts = defaultdict(int)
    for entry in drift:
        touched = set(entry["missing"]) | set(entry["extra"])
        touched |= {c[0] for c in entry["changed"]}
        for name in touched:
            counts[name] += 1
    return counts


def fetch(conn):
    with conn.cursor() as cur:
        cur.execute(SQL_SCHEMAS)
        schemas = [row[0] for row in cur.fetchall()]

        cur.execute(SQL_PRIMARY_KEYS)
        primary_keys = defaultdict(set)
        for schema, table, column in cur.fetchall():
            primary_keys[(schema, table)].add(column)

        cur.execute(SQL_COLUMNS)
        columns = defaultdict(list)
        replica = {}
        for row in cur.fetchall():
            schema, table, name, coltype, notnull, attnum, relreplident = row
            columns[(schema, table)].append(
                {
                    "name": name,
                    "type": coltype,
                    "notnull": bool(notnull),
                    "attnum": attnum,
                    "pk": name in primary_keys[(schema, table)],
                }
            )
            replica[(schema, table)] = relreplident

        cur.execute(SQL_SKIPPED)
        skipped = [
            {"kind": kind, "objects": count, "schemas": spread}
            for kind, count, spread in cur.fetchall()
        ]

        cur.execute(SQL_COMMENTS)
        table_comments = {}
        column_comments = {}
        for schema, table, objsubid, attname, description in cur.fetchall():
            if not description:
                continue
            if objsubid == 0:
                table_comments[(schema, table)] = description
            elif attname:
                column_comments[(schema, table, attname)] = description

        cur.execute(SQL_ENV)
        version, is_superuser, database = cur.fetchone()

        cur.execute(SQL_EXTENSIONS)
        extensions = [row[0] for row in cur.fetchall()]

    return Catalog(
        database=database,
        version=version,
        is_superuser=is_superuser,
        resource=source_resource(conn),
        schemas=schemas,
        columns=dict(columns),
        replica=replica,
        extensions=extensions,
        skipped=skipped,
        table_comments=table_comments,
        column_comments=column_comments,
    )


def source_resource(conn):
    info = getattr(conn, "info", None)
    host = getattr(info, "host", None) or "localhost"
    port = getattr(info, "port", None) or 5432
    dbname = getattr(info, "dbname", None) or ""
    return f"postgresql://{host}:{port}/{dbname}"


def provenance(catalog, generated_at, stale_after=None):
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


def yaml_scalar(value):
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def render_columns(columns, counts=None, comments=None):
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
            f"| {col['name']} | {col['type']} | {null} | {described} | "
            f"{'; '.join(notes)} |"
        )
    return "\n".join(lines)


def render_drift(drift):
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


def render_tenant_doc(table, info, replica_ident, run):
    columns = info["columns"]
    counts = drift_counts(info["drift"])
    geo = [c["name"] for c in columns if any(g in c["type"] for g in GEO_TYPES)]
    has_pk = any(c["pk"] for c in columns)
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
        + list(trust or [])
        + [
            "tenancy:",
            "  pattern: schema-per-tenant",
            f"  schemas: {info['schema_count']}",
            f"  tenant_schemas_total: {tenant_total}",
            f"  absent_from: {absent}",
            f"  column_drift: {len(info['drift'])}",
            f"  variants: {info.get('variants', 1)}",
            f"  reference_schema: {reference}",
            f"  reference_ambiguous: {str(bool(info.get('reference_ambiguous'))).lower()}",
            "cdc:",
            f"  replica_identity: {REPLICA_IDENTITY.get(replica_ident, replica_ident)}",
            f"  primary_key: {str(has_pk).lower()}",
            f"  sink_safe: {str(not geo).lower()}",
            "---",
        ]
    )
    body = [
        "",
        "",
        f"# {table}",
        "",
        f"One logical table, present in {info['schema_count']} of "
        f"{tenant_total} tenant schema(s).",
        "",
        "## Columns",
        "",
        render_columns(columns, counts, run.column_comments_for(reference, table)),
        "",
        "## Tenant column drift",
        "",
        render_drift(info["drift"]),
    ]
    if info.get("reference_ambiguous"):
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


def render_global_doc(target, columns, replica_ident, run):
    schema, table = target
    trust = run.trust
    has_pk = any(c["pk"] for c in columns)
    geo = [c["name"] for c in columns if any(g in c["type"] for g in GEO_TYPES)]
    front = "\n".join(
        [
            "---",
            "type: Postgres Table",
            f"title: {schema}.{table}",
            f"description: {yaml_scalar(run.table_comments.get(target))}",
            f"schema: {schema}",
        ]
        + list(trust or [])
        + [
            "cdc:",
            f"  replica_identity: {REPLICA_IDENTITY.get(replica_ident, replica_ident)}",
            f"  primary_key: {str(has_pk).lower()}",
            f"  sink_safe: {str(not geo).lower()}",
            "---",
        ]
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


def render_skipped(skipped):
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


def render_diagnostics(catalog, tenant_schemas, empty_tenants, run):
    pattern = run.pattern
    trust = run.trust
    ext = set(catalog.extensions)
    checks = [
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
    lines = (
        [
            "---",
            "type: Diagnostics",
            "title: Producer diagnostics",
            'description: "What this run could and could not read."',
            f"timestamp: {datetime.now(timezone.utc).isoformat()}",
        ]
        + list(trust or [])
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
        "Structure comes from `pg_catalog`, which does not hide rows you lack",
        "SELECT on. So an empty tenant schema means the schema really has no",
        "tables, not that access was denied. New tenants are legitimately empty.",
        "",
    ]
    return "\n".join(lines)


def render_tenant_entries(tenant_docs, filenames):
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


def render_global_entries(filenames):
    if not filenames:
        return []
    lines = ["## Global tables", ""]
    for label, filename in filenames.items():
        lines.append(f"* [{label}](global/{filename}.md) - shared table")
    return lines + [""]


def render_incomplete_notice(skipped):
    if not skipped:
        return []
    parts = ", ".join(
        f"{row['objects']} {SKIPPED_KINDS.get(row['kind'], row['kind'])}(s)"
        for row in skipped
    )
    return [
        "> **This inventory is incomplete.** Only ordinary tables are documented. This",
        f"> database also holds {parts}, which are neither documented here nor",
        "> drift-checked. Counts and the uneven-spread test are in",
        "> [_diagnostics.md](_diagnostics.md).",
        "",
    ]


def render_index(title, tenant_docs, tenant_files, global_files, skipped=None):
    header = [
        f"# {title}",
        "",
        "Generated by okf-pg-tenant from a PostgreSQL catalog.",
        "",
    ] + render_incomplete_notice(skipped or [])
    footer = [
        "## Meta",
        "",
        "* [Diagnostics](_diagnostics.md) - what this run could not read",
        "",
    ]
    return "\n".join(
        header
        + render_tenant_entries(tenant_docs, tenant_files)
        + render_global_entries(global_files)
        + footer
    )


def partition_tables(columns, tenant_set):
    per_tenant_table = defaultdict(dict)
    global_tables = {}
    for (schema, table), table_columns in columns.items():
        if schema in tenant_set:
            per_tenant_table[table][schema] = table_columns
        else:
            global_tables[(schema, table)] = table_columns
    return per_tenant_table, global_tables


def build_bundle(catalog, settings):
    out, pattern, title = settings.out, settings.pattern, settings.title
    tenant_schemas, _shared = classify_schemas(catalog.schemas, pattern)
    tenant_set = set(tenant_schemas)
    run = Run(
        trust=provenance(
            catalog, datetime.now(timezone.utc).isoformat(), settings.stale_after
        ),
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
    global_files = resolve_doc_names(
        f"{schema}.{table}" for schema, table in sorted(global_tables)
    )

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
        render_index(title, collapsed, tenant_files, global_files, catalog.skipped),
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="okf-pg-tenant",
        description="Produce an OKF bundle from a multi-tenant PostgreSQL database.",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("DATABASE_URL"),
        help="libpq connection string (default: $DATABASE_URL)",
    )
    parser.add_argument("--out", default="okf-bundle", help="output directory")
    parser.add_argument(
        "--tenant-pattern",
        default=DEFAULT_TENANT_PATTERN,
        help="regex matching tenant schema names (default: UUID)",
    )
    parser.add_argument("--title", default=None, help="bundle title for index.md")
    parser.add_argument(
        "--stale-after-days",
        type=int,
        default=None,
        help="emit an OKF v0.2 stale_after date this many days out (default: omitted)",
    )
    return parser


def settings_from(args, catalog):
    stale_after = ""
    if args.stale_after_days is not None:
        expiry = datetime.now(timezone.utc) + timedelta(days=args.stale_after_days)
        stale_after = expiry.date().isoformat()
    return Settings(
        out=args.out,
        pattern=args.tenant_pattern,
        title=args.title or f"{catalog.database} knowledge bundle",
        stale_after=stale_after,
    )


def report(stats, args):
    if stats["tenant_schemas"] == 0:
        print(
            f"WARNING: no schema matched {args.tenant_pattern!r}. "
            "Nothing was collapsed; every table was written to global/.",
            file=sys.stderr,
        )

    print(
        f"{stats['source_tables']} physical tables in "
        f"{stats['tenant_schemas']} tenant schemas -> "
        f"{stats['tenant_docs']} tenant docs + {stats['global_docs']} global docs"
    )
    print(f"drift: {stats['drifting']} schema(s) differ from their reference")
    print(f"empty tenant schemas: {stats['empty_tenants']}")
    print(f"bundle: {args.out}")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("no connection string: pass --dsn or set DATABASE_URL")

    import psycopg

    with psycopg.connect(args.dsn) as conn:
        catalog = fetch(conn)

    stats = build_bundle(catalog, settings_from(args, catalog))
    report(stats, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
