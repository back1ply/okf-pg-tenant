import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
SOURCE = HERE / "okf_pg_tenant.py"

MUTANTS = [
    (
        "modal_group picks the RAREST signature instead of the most common",
        "key=lambda kv: (-len(kv[1]), min(kv[1]))",
        "key=lambda kv: (len(kv[1]), min(kv[1]))",
    ),
    (
        "signature ignores nullability",
        'return frozenset((c["name"], c["type"], c["notnull"]) for c in columns)',
        'return frozenset((c["name"], c["type"]) for c in columns)',
    ),
    (
        "signature ignores column type",
        'return frozenset((c["name"], c["type"], c["notnull"]) for c in columns)',
        'return frozenset((c["name"], c["notnull"]) for c in columns)',
    ),
    (
        "drift_entries also reports the reference group as drifted",
        "        if sig == modal_sig:\n            continue\n",
        "        if False:\n            continue\n",
    ),
    (
        "absent_from adds instead of subtracts",
        'absent = tenant_total - info["schema_count"]',
        'absent = tenant_total + info["schema_count"]',
    ),
    (
        "drift_counts ignores changed columns",
        'touched |= {c[0] for c in entry["changed"]}',
        "touched |= set()",
    ),
    (
        "doc_filename keeps leading dots (path traversal)",
        'cleaned = UNSAFE_FILENAME.sub("_", name).lstrip(".")',
        'cleaned = UNSAFE_FILENAME.sub("_", name)',
    ),
    (
        "resolve_doc_names silently overwrites colliding filenames",
        "        if filename in claimed:",
        "        if False:",
    ),
    (
        "describe_column ignores NOT NULL",
        'return entry[1] + (" NOT NULL" if entry[2] else "")',
        "return entry[1]",
    ),
    (
        "provenance emits stale_after even when none was asked for",
        "    if stale_after:\n        lines.append",
        "    if True:\n        lines.append",
    ),
    (
        "provenance credits a human instead of the producer process",
        f'f"  by: {{PRODUCER}}"',
        'f"  by: human:someone"',
    ),
    (
        "source_resource leaks the full conninfo instead of host/port/db",
        'return f"postgresql://{host}:{port}/{dbname}"',
        "return str(getattr(conn, 'info', ''))",
    ),
    (
        "reference_is_ambiguous never reports a tie",
        "    return len(sizes) > 1 and sizes[0] == sizes[1]",
        "    return False",
    ),
    (
        "render_drift drops the not-compared disclosure",
        'return f"No column drift. All schemas match the reference.\\n\\n{NOT_COMPARED}\\n"',
        'return "No column drift. All schemas match the reference.\\n"',
    ),
    (
        "column comments are dropped from the rendered table",
        'described = (comments.get(col["name"]) or "").replace("|", "\\\\|")',
        'described = ""',
    ),
    (
        "the index stops warning that the inventory is partial",
        "    if not skipped:\n        return []",
        "    if True:\n        return []",
    ),
    (
        "render_skipped drops the not-drift-checked warning",
        '"so they are **not** covered by the drift check above.",',
        '"",',
    ),
]


def run_tests():
    return subprocess.run(
        [sys.executable, "test_okf_pg_tenant.py"],
        cwd=HERE,
        capture_output=True,
        text=True,
    ).returncode


def main():
    original = SOURCE.read_text(encoding="utf-8")
    if run_tests() != 0:
        raise SystemExit("baseline suite is not green; aborting")
    killed = 0
    try:
        for name, old, new in MUTANTS:
            if original.count(old) != 1:
                raise SystemExit(f"mutant anchor is not unique, fix it first: {name}")
            SOURCE.write_text(original.replace(old, new), encoding="utf-8")
            failed = run_tests() != 0
            killed += failed
            print(f"{'KILLED  ' if failed else 'SURVIVED'}  {name}")
    finally:
        SOURCE.write_text(original, encoding="utf-8")
    print(f"\n{killed}/{len(MUTANTS)} mutants killed")
    if SOURCE.read_text(encoding="utf-8") != original:
        raise SystemExit("source was not restored")
    if run_tests() != 0:
        raise SystemExit("suite is not green after restore")
    print("source restored, suite green")
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
