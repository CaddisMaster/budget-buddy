"""#402 — the scheduled jobs run in exactly one process, outside the web tier.

Where the jobs run is decided by `docker-compose.yml` (which service starts a
scheduler, and with how many gunicorn workers), so most of what matters here is
a property of that FILE. Nothing can reach the Droplet from a test. The unit
tests at the end cover the job registration both runners share.

⚠️ The compose tests SKIP when the file is absent: `.dockerignore` excludes
`docker-compose*.yml`, and CI runs this suite inside the shipped image too
(same guard as `test_deploy_pinning.py`). There is no YAML parser in the image,
so services are split by indentation, which is enough for this file's shape.
"""
import re
from pathlib import Path

import pytest

from app import scheduler as jobs

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"
RELEASE_WF = REPO_ROOT / ".github/workflows/release.yml"
_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"


def _services():
    """{service name: its block of text} for the top-level `services:` map."""
    text = COMPOSE.read_text()
    body = text.split("\nservices:\n", 1)[1] if not text.startswith("services:") \
        else text.split("services:\n", 1)[1]
    body = body.split("\nvolumes:", 1)[0]
    blocks = re.split(r"^  (?=[a-z][\w-]*:\s*$)", body, flags=re.M)
    services = {}
    for block in blocks:
        if not block.strip():
            continue
        name, _, rest = block.partition(":")
        services[name.strip()] = rest
    return services


def _uncommented(block):
    return "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))


def _gunicorn_workers(block):
    """The worker count a service's gunicorn command asks for, or None."""
    m = re.search(r'command:.*gunicorn.*?"--workers",\s*"(\d+)"', _uncommented(block), re.S)
    return int(m.group(1)) if m else None


def _scheduler_switched_off(block):
    return re.search(r'SCHEDULER_IN_PROCESS:\s*"0"', _uncommented(block)) is not None


def _runs_a_scheduler(name, block):
    """True if this service would start the scheduled jobs in production,
    where `.env` sets ENABLE_DIGEST_SCHEDULER=1 for every service reading it."""
    code = _uncommented(block)
    if "budget-buddy:" not in code:
        return False  # db, redis: not the app image
    if "run-scheduler" in code:
        return True
    # gunicorn — named in `command:`, or the image's own CMD when there is no
    # command — starts the in-process scheduler unless the switch is 0 here.
    # Any other command (a one-shot `flask run-daily`, say) starts none.
    command = re.search(r"^\s*command:(.*)$", code, re.M)
    runs_gunicorn = command is None or "gunicorn" in command.group(1)
    return runs_gunicorn and not _scheduler_switched_off(block)


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(402, "Each scheduled job runs exactly once per slot with several web workers")
def test_exactly_one_service_runs_the_scheduler():
    """Two schedulers would run every job twice: a second digest email, a
    second set of reminders. Each scheduler runs each job once per slot, so
    exactly one scheduler is exactly once per slot."""
    running = [name for name, block in _services().items() if _runs_a_scheduler(name, block)]
    assert running == ["worker"], f"services that would run the scheduler: {running}"


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(402, "The web process starts no scheduler")
def test_web_runs_several_workers_only_with_its_scheduler_off():
    """The rule the RUNBOOK used to state as "`--workers 1` stays". Any service
    asking gunicorn for more than one worker must keep the jobs out of its
    process, or each worker starts its own."""
    services = _services()
    web = services["web"]
    assert (_gunicorn_workers(web) or 1) > 1, "web should run several gunicorn workers since #402"
    for name, block in services.items():
        workers = _gunicorn_workers(block)
        if workers and workers > 1:
            assert _scheduler_switched_off(block), (
                f"{name} runs {workers} gunicorn workers without SCHEDULER_IN_PROCESS: \"0\" "
                "— every worker would start a scheduler")


def test_the_image_default_is_still_one_worker():
    """The half that makes rollout order not matter: a new image on the
    pre-#402 compose file runs the image's own CMD, with .env's scheduler switch
    on. With one worker that is exactly the old behaviour; with two it would be
    two schedulers."""
    cmd = next(line for line in DOCKERFILE.read_text().splitlines() if line.startswith("CMD"))
    assert '"--workers", "1"' in cmd, f"the image's default must stay one worker: {cmd}"


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
@pytest.mark.criterion(402, "A deploy runs the worker at the same version as web")
def test_the_worker_runs_the_same_image_as_web_and_the_release_checks_it():
    services = _services()
    image = re.compile(r"^\s*image:\s*(\S.*?)\s*$", re.M)
    assert image.search(services["worker"]).group(1) == image.search(services["web"]).group(1)
    release = RELEASE_WF.read_text()
    assert "exec -T worker printenv APP_VERSION" in release, \
        "release.yml no longer asks the worker which version it runs"
    # ...and asks BEFORE the after-pull DROPs, for #277's reason.
    assert release.index("exec -T worker printenv APP_VERSION") < \
        release.index("migrate.py --phase after-pull")


@pytest.mark.skipif(not COMPOSE.exists(), reason=_NOT_IN_IMAGE)
def test_web_keeps_rate_limits_in_redis():
    """With several workers, memory storage counts per worker, so the login
    limit would silently become 10/min × workers. The limits holding across
    workers was verified by hand against Redis in #402's PR."""
    services = _services()
    assert "RATELIMIT_STORAGE_URI: redis://redis:6379" in _uncommented(services["web"])
    assert "redis" in services


# --- the jobs both runners register -------------------------------------------


class _RecordingScheduler:
    def __init__(self):
        self.ids = []

    def add_job(self, func, trigger, **kwargs):
        self.ids.append(kwargs["id"])


def test_both_jobs_are_registered_when_mail_is_configured(monkeypatch):
    monkeypatch.setattr(jobs, "mail_enabled", lambda: True)
    assert jobs.add_jobs(_RecordingScheduler()).ids == ["weekly_digest", "daily_tasks"]


def test_the_daily_job_runs_even_without_mail(monkeypatch):
    """#33: materialization must never hang off a Resend key."""
    monkeypatch.setattr(jobs, "mail_enabled", lambda: False)
    assert jobs.add_jobs(_RecordingScheduler()).ids == ["daily_tasks"]


def test_the_worker_idles_rather_than_exiting_when_switched_off(monkeypatch):
    """Compose restarts a container that exits, so exiting would turn
    "deliberately off" into a crash loop."""
    from click.testing import CliRunner

    class Idled(Exception):
        pass

    def fake_sleep(seconds):
        raise Idled

    monkeypatch.delenv("ENABLE_DIGEST_SCHEDULER", raising=False)
    monkeypatch.setattr(jobs.time, "sleep", fake_sleep)
    result = CliRunner().invoke(jobs.run_scheduler_command)
    assert isinstance(result.exception, Idled), "it exited instead of idling"
    assert "idling" in result.output


# --- /settings still sees the deployment's switch (#151) ----------------------


@pytest.mark.criterion(402, "The web process starts no scheduler")
def test_handing_the_jobs_to_the_worker_keeps_web_out_of_them(monkeypatch):
    monkeypatch.setenv("ENABLE_DIGEST_SCHEDULER", "1")
    monkeypatch.setenv("SCHEDULER_IN_PROCESS", "0")
    assert not jobs.runs_in_this_process()
    monkeypatch.delenv("SCHEDULER_IN_PROCESS")
    assert jobs.runs_in_this_process(), "without the compose override it is the pre-#402 path"


def _daily_row(html):
    """The jobs-table row for the daily job, from its name to the row's end."""
    start = html.index("Daily tasks")
    return html[start:html.index("</tr>", start)]


def test_settings_still_reports_the_jobs_when_the_worker_hosts_them(admin_client, monkeypatch):
    """⚠️ The regression the first cut of #402 had. It kept web out of the jobs
    by overriding ENABLE_DIGEST_SCHEDULER to 0, and /settings reads that very
    variable in the web process to decide whether a silent job is a fault. The
    panel then said the scheduler was "not running" and the daily job was
    "Not scheduled here", so a dead worker would have looked like a deliberate
    choice: the failure #151 exists to surface."""
    monkeypatch.setenv("ENABLE_DIGEST_SCHEDULER", "1")
    monkeypatch.setenv("SCHEDULER_IN_PROCESS", "0")
    html = admin_client.get("/settings").get_data(as_text=True)
    assert "Background scheduler:" in html, "/settings did not render the scheduler line"
    assert "<strong>running</strong>" in html, "the scheduler reads as not running"
    assert "Not scheduled here" not in _daily_row(html), \
        "the daily job reads as unscheduled while the worker hosts it"


def test_settings_does_say_unscheduled_when_the_switch_is_off(admin_client, monkeypatch):
    """The positive control for the test above: the same assertions really do
    fail when the deployment switch is off."""
    monkeypatch.setenv("ENABLE_DIGEST_SCHEDULER", "0")
    html = admin_client.get("/settings").get_data(as_text=True)
    assert "<strong>not running</strong>" in html
    assert "Not scheduled here" in _daily_row(html)
