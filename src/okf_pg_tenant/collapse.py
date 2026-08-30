"""Every decision the tool makes about tenant structure, as pure functions.

Nothing here touches a database or a filesystem, which is why the whole suite
runs in about a second against no database. A structural comparison is a
``frozenset`` of ``(name, type, notnull)``: it deliberately ignores column
order, because a tenant where a column was dropped and re-added has a different
physical ``attnum`` order and an identical structure.
"""

import re
from collections import defaultdict
from collections.abc import Set as AbstractSet

from .models import CollapsedTable, Column, DriftEntry, SignatureDiff, TableKey

Signature = frozenset[tuple[str, str, bool]]


def classify_schemas(schemas: list[str], pattern: str) -> tuple[list[str], list[str]]:
    """Split schema names into ``(tenant, shared)`` by regex match."""
    matcher = re.compile(pattern)
    tenant: list[str] = []
    shared: list[str] = []
    for schema in schemas:
        (tenant if matcher.match(schema) else shared).append(schema)
    return tenant, shared


def signature(columns: list[Column]) -> Signature:
    """Reduce a column list to the structure used for drift comparison.

    Name, type and nullability only. Widening this means widening the
    ``NOT_COMPARED`` disclosure and the ``column_drift`` frontmatter key with it.
    """
    return frozenset((c["name"], c["type"], c["notnull"]) for c in columns)


def describe_column(entry: tuple[str, str, bool]) -> str:
    """Render one signature entry the way a drift line reports it."""
    return entry[1] + (" NOT NULL" if entry[2] else "")


def diff_signature(modal: Signature, other: Signature) -> SignatureDiff:
    """Describe how ``other`` differs from the reference structure ``modal``."""
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


def group_by_signature(per_schema: dict[str, list[Column]]) -> dict[Signature, list[str]]:
    """Group schema names by the structure they share."""
    groups = defaultdict(list)
    for schema, columns in per_schema.items():
        groups[signature(columns)].append(schema)
    return groups


def modal_group(groups: dict[Signature, list[str]]) -> tuple[Signature, list[str]]:
    """Return the most common structure, breaking ties alphabetically."""
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), min(kv[1])))
    return ranked[0]


def reference_is_ambiguous(groups: dict[Signature, list[str]]) -> bool:
    """Report whether two or more structures are tied for most common.

    A tie is reported rather than resolved, because otherwise a rename or a new
    tenant could silently change what the bundle calls canonical.
    """
    sizes = sorted((len(v) for v in groups.values()), reverse=True)
    return len(sizes) > 1 and sizes[0] == sizes[1]


def drift_entries(groups: dict[Signature, list[str]], modal_sig: Signature) -> list[DriftEntry]:
    """One entry per schema whose structure differs from the reference."""
    entries: list[DriftEntry] = []
    for sig, schemas in groups.items():
        if sig == modal_sig:
            continue
        difference = diff_signature(modal_sig, sig)
        entries += [
            DriftEntry(
                schema=schema,
                missing=difference["missing"],
                extra=difference["extra"],
                changed=difference["changed"],
            )
            for schema in schemas
        ]
    return sorted(entries, key=lambda d: d["schema"])


def collapse_table(per_schema: dict[str, list[Column]]) -> CollapsedTable:
    """Collapse one table across every schema that holds it."""
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


def collapse(per_table: dict[str, dict[str, list[Column]]]) -> dict[str, CollapsedTable]:
    """Collapse every table, in name order."""
    return {table: collapse_table(per_schema) for table, per_schema in sorted(per_table.items())}


def drift_counts(drift: list[DriftEntry]) -> dict[str, int]:
    """Count how many schemas each column differs in."""
    counts: dict[str, int] = defaultdict(int)
    for entry in drift:
        touched = set(entry["missing"]) | set(entry["extra"])
        touched |= {c[0] for c in entry["changed"]}
        for name in touched:
            counts[name] += 1
    return counts


def partition_tables(
    columns: dict[TableKey, list[Column]], tenant_set: AbstractSet[str]
) -> tuple[dict[str, dict[str, list[Column]]], dict[TableKey, list[Column]]]:
    """Split physical tables into per-tenant-table groups and global tables."""
    per_tenant_table: dict[str, dict[str, list[Column]]] = defaultdict(dict)
    global_tables = {}
    for (schema, table), table_columns in columns.items():
        if schema in tenant_set:
            per_tenant_table[table][schema] = table_columns
        else:
            global_tables[(schema, table)] = table_columns
    return per_tenant_table, global_tables
