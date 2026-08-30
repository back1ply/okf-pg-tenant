"""The entire database surface, and the only function that reads it.

Every query here hits a base catalog rather than ``information_schema``.
``information_schema`` filters rows by privilege, so a least-privilege account
silently sees less; ``pg_class``, ``pg_attribute``, ``pg_namespace``,
``pg_index`` and ``pg_description`` are world-readable and do not, which is the
entire reason this tool works on managed Postgres with no grants.

That property belongs to those catalogs, not to ``pg_catalog`` as a whole:
``pg_stats`` shows only rows for tables you may read and ``pg_authid`` is not
publicly readable, so any query added here must have its visibility rules
checked rather than assumed. Nothing here writes: no temp tables, no ``SET``,
no extensions installed.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .models import Column, ColumnKey, SkippedKind, TableKey

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
    """Everything one run read from one database."""

    database: str = ""
    version: str = ""
    is_superuser: str = "off"
    resource: str = ""
    schemas: list[str] = field(default_factory=list)
    columns: dict[TableKey, list[Column]] = field(default_factory=dict)
    replica: dict[TableKey, str] = field(default_factory=dict)
    extensions: list[str] = field(default_factory=list)
    skipped: list[SkippedKind] = field(default_factory=list)
    table_comments: dict[TableKey, str] = field(default_factory=dict)
    column_comments: dict[ColumnKey, str] = field(default_factory=dict)


def read_primary_keys(cur: Any) -> dict[TableKey, set[str]]:
    """Which columns of each table belong to its primary key."""
    cur.execute(SQL_PRIMARY_KEYS)
    primary_keys = defaultdict(set)
    for schema, table, column in cur.fetchall():
        primary_keys[(schema, table)].add(column)
    return primary_keys


def read_columns(
    cur: Any, primary_keys: dict[TableKey, set[str]]
) -> tuple[dict[TableKey, list[Column]], dict[TableKey, str]]:
    """Every column of every ordinary table, plus each table's replica identity.

    Primary-key membership is folded in here rather than queried per column,
    which is why ``primary_keys`` is read first and passed in.
    """
    cur.execute(SQL_COLUMNS)
    columns = defaultdict(list)
    replica = {}
    for schema, table, name, coltype, notnull, attnum, relreplident in cur.fetchall():
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
    return dict(columns), replica


def read_comments(cur: Any) -> tuple[dict[TableKey, str], dict[ColumnKey, str]]:
    """``COMMENT ON`` text, split into table comments and column comments.

    ``objsubid`` is 0 for a comment on the relation itself and the column's
    ``attnum`` otherwise. An empty comment is dropped rather than stored: the
    bundle must not claim a description exists when the database has none.
    """
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
    return table_comments, column_comments


def read_skipped(cur: Any) -> list[SkippedKind]:
    """Relation kinds that exist but are not documented."""
    cur.execute(SQL_SKIPPED)
    return [
        SkippedKind(kind=kind, objects=count, schemas=spread)
        for kind, count, spread in cur.fetchall()
    ]


def fetch(conn: Any) -> Catalog:
    """Read the whole catalog through one cursor.

    The only function in the package that takes a database connection; the
    ``read_*`` helpers above take the cursor it opens. ``conn`` is a psycopg
    connection, typed loosely so importing this module never requires the driver.
    """
    with conn.cursor() as cur:
        cur.execute(SQL_SCHEMAS)
        schemas = [row[0] for row in cur.fetchall()]

        columns, replica = read_columns(cur, read_primary_keys(cur))
        skipped = read_skipped(cur)
        table_comments, column_comments = read_comments(cur)

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
        columns=columns,
        replica=replica,
        extensions=extensions,
        skipped=skipped,
        table_comments=table_comments,
        column_comments=column_comments,
    )


def source_resource(conn: Any) -> str:
    """Build the ``sources`` URI from connection metadata, never from the DSN.

    Reading ``conn.info`` field by field is what keeps a password out of every
    produced document; formatting the DSN would leak it.
    """
    info = getattr(conn, "info", None)
    host = getattr(info, "host", None) or "localhost"
    port = getattr(info, "port", None) or 5432
    dbname = getattr(info, "dbname", None) or ""
    return f"postgresql://{host}:{port}/{dbname}"
