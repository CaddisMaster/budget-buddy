"""The app factory: building the app starts nothing; entry points decide (#490).

Until #490 `app/__init__.py` built a global app on import, and that import
could start the in-process scheduler. Every process that imported the app paid
for that decision, including `flask run-scheduler`, which is how the worker ran
two schedulers and sent the Sunday digest twice (#483). Now `create_app()` only
builds, and each entry point decides for itself:

    gunicorn       app.wsgi:app   the legacy in-process scheduler, when the
                                  switches say so (a new image on a pre-#402
                                  compose file)
    flask CLI      create_app()   nothing; `run-scheduler` starts its own

⚠️ **Every test here runs in a FRESH subprocess.** The property is about what a
first import does, and by the time any test runs, the test session has already
imported `app` with the scheduler switch off. Checking in-process would observe
nothing and pass. The schedulers are counted the way the bug was confirmed by
hand: APScheduler names its background thread `APScheduler`.
"""
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"

# The worker's own boot, through Flask's real CLI and its real app discovery.
# BlockingScheduler.start is replaced so the process reports and exits instead
# of blocking forever: at that moment, every scheduler the boot started is
# running, plus the one run-scheduler is starting.
_WORKER = textwrap.dedent("""
    import sys, threading
    from apscheduler.schedulers.blocking import BlockingScheduler

    def start(self, *args, **kwargs):
        background = sum(t.name == "APScheduler" for t in threading.enumerate())
        print("SCHEDULERS", 1 + background)
        sys.exit(0)

    BlockingScheduler.start = start
    from flask.cli import main
    sys.argv = ["flask", "run-scheduler"]
    main()
""")

_COUNT = ('import threading; '
          'print("SCHEDULERS", sum(t.name == "APScheduler" for t in threading.enumerate()))')


def _run(code, *, scheduler_in_process=None):
    """Run `code` in a fresh interpreter, with the deployment's switch on as the
    Droplet's .env has it, and return the scheduler count it printed."""
    env = {**os.environ, "ENABLE_DIGEST_SCHEDULER": "1"}
    env.pop("SCHEDULER_IN_PROCESS", None)
    env.pop("FLASK_APP", None)
    if scheduler_in_process is not None:
        env["SCHEDULER_IN_PROCESS"] = scheduler_in_process
    # Set EMPTY rather than removed: the app's load_dotenv() would refill a
    # missing key from .env, but never overrides one that is present. With no
    # key the digest job is not even registered, so nothing can send.
    env["RESEND_API_KEY"] = ""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    counts = [line for line in result.stdout.splitlines() if line.startswith("SCHEDULERS")]
    assert counts, f"the process never reported a count:\n{result.stdout}\n{result.stderr}"
    return int(counts[-1].split()[1])


@pytest.mark.criterion(490, "Importing the app starts no scheduler")
def test_importing_the_app_and_building_it_starts_no_scheduler():
    """SCHEDULER_IN_PROCESS unset and the deployment switch on: before #490,
    this exact state started a scheduler on import."""
    code = f"import app; app.create_app(); {_COUNT}"
    assert _run(code) == 0


def test_the_legacy_app_attribute_starts_no_scheduler_either():
    """`app:app` is what the 0.12 and 0.13 compose files name for gunicorn. It
    still resolves, lazily, and builds through the same factory."""
    code = f"from app import app as legacy; assert legacy.name == 'app'; {_COUNT}"
    assert _run(code) == 0


def _worker_switch():
    """SCHEDULER_IN_PROCESS from the worker's `environment:` in compose."""
    block = COMPOSE.read_text().split("\n  worker:\n", 1)[1].split("\n  redis:\n", 1)[0]
    m = re.search(r'^\s*SCHEDULER_IN_PROCESS:\s*"?([^"\n]*)"?\s*$', block, re.M)
    return m.group(1) if m else None


@pytest.mark.skipif(not COMPOSE.exists(),
                    reason="docker-compose.yml is not in the image (.dockerignore)")
@pytest.mark.criterion(490, "The worker runs exactly one scheduler")
@pytest.mark.criterion(483, "The worker runs each scheduled job once")
def test_the_worker_runs_exactly_one_scheduler_with_its_compose_environment():
    """0.12.0's defect: the worker's import started a scheduler and
    `run-scheduler` a second, so every job ran twice and the Sunday digest
    arrived twice (#483)."""
    assert _run(_WORKER, scheduler_in_process=_worker_switch()) == 1


def test_the_worker_runs_exactly_one_scheduler_without_the_compose_switch():
    """0.12.0's compose file set no switch on the worker: the #483 state. The
    import now starts nothing, so there is nothing for it to double."""
    assert _run(_WORKER) == 1


# --- the gunicorn entry keeps the pre-#402 path -------------------------------


def test_the_gunicorn_entry_runs_the_legacy_scheduler_when_nothing_turns_it_off():
    """A new image on a pre-#402 compose file: the image's CMD, the switch on
    in .env, and no SCHEDULER_IN_PROCESS, so web hosts the jobs as it always
    did. This is the matrix row #490 was told to keep."""
    assert _run(f"import app.wsgi; {_COUNT}") == 1


def test_the_gunicorn_entry_runs_no_scheduler_when_compose_hands_jobs_to_the_worker():
    assert _run(f"import app.wsgi; {_COUNT}", scheduler_in_process="0") == 0


def test_there_is_no_wsgi_module_at_the_repo_root():
    """⚠️ Flask's CLI imports a root `wsgi.py` FIRST when looking for an app. A
    gunicorn entry there would run its scheduler start inside `flask
    run-scheduler`, which is #483 over again. It lives in `app/wsgi.py`."""
    assert not (ROOT / "wsgi.py").exists()
    assert not (ROOT / "app.py").exists()


def test_nothing_in_app_reaches_for_the_legacy_attribute():
    """`app.app` exists only for old compose files. Code inside `app/` that
    imported it would build a second app beside the one its process already
    runs. The jobs use `current_app`, which the scheduler and the CLI supply."""
    pattern = re.compile(r"^\s*from app import [^#\n]*\bapp\b", re.M)
    offenders = [str(path.relative_to(ROOT)) for path in sorted((ROOT / "app").rglob("*.py"))
                 if pattern.search(path.read_text())]
    assert offenders == []
