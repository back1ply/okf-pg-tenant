"""Argument parsing, the run itself, and what it prints."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from .bundle import build_bundle
from .catalog import Catalog, fetch
from .models import Settings

DEFAULT_TENANT_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser.

    ``--stale-after-days`` has no default: how fast a schema snapshot goes stale
    is a property of a team's release cadence, not of this tool.
    """
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


def settings_from(args: argparse.Namespace, catalog: Catalog) -> Settings:
    """Resolve parsed arguments into settings, using the database for defaults."""
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


def report(stats: dict[str, int], args: argparse.Namespace) -> None:
    """Print what the run produced.

    A run that matched no tenant schema is a warning, not a silent success: the
    bundle it produced is real but every table landed in ``global/``.
    """
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


def main(argv: Optional[list[str]] = None) -> int:
    """Read one database, write one bundle.

    ``psycopg`` is imported here rather than at module scope so that importing
    the package, running the test suite and running the mutation gate all work
    with nothing installed but the package itself.
    """
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
