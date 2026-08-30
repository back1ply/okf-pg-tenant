"""Turning catalog names into filenames, safely.

A table name is whatever someone typed inside ``CREATE TABLE "..."``, so every
name that reaches a filesystem path goes through :func:`doc_filename` first.
"""

import re
from collections.abc import Iterable

UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


def doc_filename(name: str) -> str:
    """Return a filename-safe form of ``name``.

    Anything outside ``[A-Za-z0-9._-]`` becomes an underscore and leading dots
    are stripped, so a name like ``../etc/passwd`` cannot escape the bundle
    directory.

    Raises:
        ValueError: if nothing filename-safe remains.
    """
    cleaned = UNSAFE_FILENAME.sub("_", name).lstrip(".")
    if not cleaned:
        raise ValueError(f"name has no filename-safe characters: {name!r}")
    return cleaned


def resolve_doc_names(names: Iterable[str]) -> dict[str, str]:
    """Map each name to its filename, refusing to let two names collide.

    Sanitising is lossy, so distinct names can produce the same filename.
    Overwriting one document with another would lose data silently, so a
    collision raises instead.

    Raises:
        ValueError: if two names map to the same filename.
    """
    resolved: dict[str, str] = {}
    claimed: dict[str, str] = {}
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
