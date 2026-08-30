"""Values a single run carries, and the shapes the catalog is read into.

The ``TypedDict`` definitions here are the reason this module exists at the
bottom of the import graph. A column record is passed from :mod:`catalog`
through :mod:`collapse` into every renderer, so its shape is the one piece of
structure that a catalog change can move underneath code that never mentions a
database. Naming it once means such a change fails type-checking instead of
silently altering rendered output.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, Union

TableKey = tuple[str, str]
ColumnKey = tuple[str, str, str]


class Column(TypedDict):
    """One column of one physical table, as read from ``pg_attribute``."""

    name: str
    type: str
    notnull: bool
    attnum: int
    pk: bool


class SkippedKind(TypedDict):
    """A relation kind that exists but is not documented.

    ``objects`` not being a whole multiple of ``schemas`` means the kind is
    spread unevenly, which is undetected drift.
    """

    kind: str
    objects: int
    schemas: int


class SignatureDiff(TypedDict):
    """How one structure differs from another, before it is tied to a schema.

    ``changed`` holds ``(column, reference_description, this_description)``
    triples. Column order is deliberately not represented: see the drift note in
    ``AGENTS.md``.
    """

    missing: list[str]
    extra: list[str]
    changed: list[tuple[str, str, str]]


class DriftEntry(SignatureDiff):
    """A :class:`SignatureDiff` attributed to the schema that carries it."""

    schema: str


class CollapsedTable(TypedDict):
    """One logical table, collapsed across every tenant schema that holds it."""

    columns: list[Column]
    reference_schema: str
    reference_ambiguous: bool
    variants: int
    schema_count: int
    drift: list[DriftEntry]


@dataclass
class Index:
    """What ``index.md`` lists.

    Five values reached `render_index` as five arguments, which is over the
    ceiling in ``AGENTS.md``. They travel together and always come from one
    bundle, so they are one value.
    """

    title: str
    tenant_docs: dict[str, CollapsedTable]
    tenant_files: dict[str, str]
    global_files: dict[str, str]
    skipped: Sequence[SkippedKind] = ()


@dataclass
class Run:
    """Values shared by every document produced in a single run.

    Exists because four renderers had each grown to five arguments; see rule 7
    in ``AGENTS.md``.
    """

    trust: list[str]
    pattern: str
    tenant_total: int
    table_comments: dict[TableKey, str] = field(default_factory=dict)
    column_comments: dict[ColumnKey, str] = field(default_factory=dict)
    _by_table: dict[TableKey, dict[str, str]] = field(init=False)

    def __post_init__(self) -> None:
        """Group the column comments by table, once.

        ``column_comments`` holds every commented column in every schema, and a
        renderer asks for one table at a time. Filtering per document made the
        run cost documents times comments, the only part of the pipeline that
        grew with the square of the tenant count.
        """
        self._by_table = {}
        for (schema, table, column), text in self.column_comments.items():
            self._by_table.setdefault((schema, table), {})[column] = text

    def column_comments_for(self, schema: str, table: str) -> dict[str, str]:
        """Return ``{column: comment}`` for one table in one schema."""
        return self._by_table.get((schema, table), {})


@dataclass
class Settings:
    """What the CLI resolved before any document was rendered.

    ``out`` accepts a ``Path`` as well as a string because the test suite passes
    a temporary directory directly.
    """

    out: Union[str, Path]
    pattern: str
    title: str
    stale_after: str = ""
