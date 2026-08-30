"""Read a PostgreSQL catalog, write an OKF v0.2 bundle.

Re-exports only. Every name below has a caller today: the seventeen the test suite
imports, plus ``main`` for the ``okf-pg-tenant`` console script. Anything else
lives in its module and is reached as ``okf_pg_tenant.<module>.<name>`` — a name
re-exported here is a name this package promises to keep.
"""

from .bundle import build_bundle
from .catalog import Catalog, source_resource
from .cli import DEFAULT_TENANT_PATTERN, main
from .collapse import classify_schemas, collapse, drift_counts
from .models import Index, Run, Settings
from .naming import resolve_doc_names
from .render import (
    provenance,
    render_columns,
    render_diagnostics,
    render_global_doc,
    render_index,
    render_tenant_doc,
)

__all__ = [
    "DEFAULT_TENANT_PATTERN",
    "Catalog",
    "Index",
    "Run",
    "Settings",
    "build_bundle",
    "classify_schemas",
    "collapse",
    "drift_counts",
    "main",
    "provenance",
    "render_columns",
    "render_diagnostics",
    "render_global_doc",
    "render_index",
    "render_tenant_doc",
    "resolve_doc_names",
    "source_resource",
]
