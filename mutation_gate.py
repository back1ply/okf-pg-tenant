import contextlib
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).parent
CONFIG = HERE / "cosmic-ray.toml"
BASELINE = HERE / "mutation-baseline.md"

ACCEPTED = re.compile(
    r"^\|\s*`(?P<module>[^`]+)`\s*\|\s*(?P<line>\d+)\s*\|"
    r"\s*`(?P<operator>[^`]+)`\s*\|\s*(?P<occurrence>\d+)\s*\|"
)


def accepted_survivors():
    if not BASELINE.exists():
        raise SystemExit(f"missing {BASELINE.name}; every survivor needs a recorded reason")
    accepted = {}
    for row in BASELINE.read_text(encoding="utf-8").splitlines():
        found = ACCEPTED.match(row.strip())
        if found:
            key = (
                found["module"],
                int(found["line"]),
                found["operator"],
                int(found["occurrence"]),
            )
            accepted[key] = row.strip()
    return accepted


def survivors(session):
    """Read one finished session, closing the database before the caller deletes it.

    The close is load-bearing on Windows: an open sqlite handle makes the
    temporary directory undeletable and the gate dies before it reports anything.
    """
    query = """
        SELECT m.module_path, m.start_pos_row, m.operator_name, m.occurrence,
               r.test_outcome
        FROM work_results r JOIN mutation_specs m ON m.job_id = r.job_id
    """
    killed = 0
    alive = []
    with contextlib.closing(sqlite3.connect(session)) as db:
        for module, line, operator, occurrence, outcome in db.execute(query):
            if outcome == "KILLED":
                killed += 1
                continue
            alive.append((pathlib.PurePath(module).as_posix(), line, operator, occurrence))
    return killed, alive


def run_cosmic_ray(session):
    for stage in ("init", "exec"):
        result = subprocess.run(
            [sys.executable, "-m", "cosmic_ray.cli", stage, str(CONFIG), session],
            cwd=HERE,
        )
        if result.returncode != 0:
            raise SystemExit(f"cosmic-ray {stage} failed with {result.returncode}")


def unaccounted_survivors(alive, accepted):
    """Split survivors into the two ways the baseline can be wrong.

    Returns ``(unexplained, stale)``: mutants that survived with no recorded
    reason, and recorded entries the tool no longer generates.
    """
    alive_keys = set(alive)
    return (
        [s for s in alive if s not in accepted],
        [key for key in accepted if key not in alive_keys],
    )


def print_mutants(label, mutants, note=""):
    """List one category of survivor, one line each."""
    for module, line, operator, occurrence in sorted(mutants):
        print(f"{label}  {module}:{line}  {operator}  #{occurrence}{note}")


def verdict(unexplained, stale):
    """Return the reason the gate fails, or an empty string if it passes."""
    if unexplained:
        return (
            f"\n{len(unexplained)} mutant(s) survived with no recorded reason. "
            f"A surviving mutant is a missing test: write the test that kills it, or "
            f"record it in {BASELINE.name} with the reason it cannot change behaviour."
        )
    if stale:
        return (
            f"\n{len(stale)} entry in {BASELINE.name} no longer matches a generated mutant. "
            f"Remove it so the file keeps describing the code as it is."
        )
    return ""


def check_tests_import_the_mutated_source():
    """Refuse to run if the suite imports a different copy than we mutate.

    The package lives under ``src/``, so a non-editable ``pip install .`` makes
    the tests import ``site-packages`` while cosmic-ray edits ``src/``. Nothing
    fails: every mutant simply survives, and the gate reports a catastrophe that
    is really a misconfigured environment. Checking costs one subprocess.
    """
    located = subprocess.run(
        [sys.executable, "-c", "import okf_pg_tenant as m; print(m.__file__)"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    if located.returncode != 0:
        raise SystemExit('cannot import okf_pg_tenant; install it first: pip install -e ".[dev]"')
    imported = pathlib.Path(located.stdout.strip()).resolve()
    expected = (HERE / "src").resolve()
    if expected not in imported.parents:
        raise SystemExit(
            f"the suite imports {imported}\n"
            f"but mutants are applied under {expected}\n"
            "Every mutant would survive for that reason alone. Reinstall in "
            'editable mode: pip install -e ".[dev]"'
        )


def main():
    """Run the suite, then the mutants, then compare survivors to the baseline."""
    check_tests_import_the_mutated_source()
    if subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=HERE).returncode != 0:
        raise SystemExit("baseline suite is not green; aborting")

    accepted = accepted_survivors()
    with tempfile.TemporaryDirectory() as workdir:
        session = str(pathlib.Path(workdir) / "session.sqlite")
        run_cosmic_ray(session)
        killed, alive = survivors(session)

    unexplained, stale = unaccounted_survivors(alive, accepted)
    print(f"\n{killed} killed, {len(alive)} survived, {len(accepted)} recorded as accepted")
    print_mutants("UNEXPLAINED", unexplained)
    print_mutants("STALE ENTRY", stale, "  (recorded but no longer generated)")

    failure = verdict(unexplained, stale)
    if failure:
        raise SystemExit(failure)
    print("no unexplained survivors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
