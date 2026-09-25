"""The scheduled jobs, and the two ways they can run (#402).

**Where they run is decided by `docker-compose.yml`, not here.** Since #402 the
production compose file runs them in a dedicated `worker` service
(`flask run-scheduler`) and sets `SCHEDULER_IN_PROCESS=0` on `web`, which lets
`web` run more than one gunicorn worker. Before #402 they ran as a thread
inside the single gunicorn worker, and that path is kept on purpose.

⚠️ **Two variables, because they answer two questions.** `ENABLE_DIGEST_SCHEDULER`
(in `.env`) is the DEPLOYMENT's switch: are the jobs meant to run on this box?
`/settings` reads it in the web process to decide whether a silent job is a
fault (#151). `SCHEDULER_IN_PROCESS` (in compose) only says whether THIS process
hosts them. The first cut overrode `ENABLE_DIGEST_SCHEDULER` to 0 on web
instead, and `/settings` then reported every job as "not scheduled", which is
exactly how a dead worker would have hidden.

⚠️ **The compose file reaches the Droplet by hand (scp), the image by the
release pipeline, so for a while either can be new while the other is old.**
Every combination has to be safe:

    new image + old compose   no SCHEDULER_IN_PROCESS, and the image's default
                              of ONE gunicorn worker, so the in-process
                              scheduler runs exactly as before
    new image + new compose   web: several workers, no scheduler;
                              worker: the scheduler, once
    old image + new compose   (a rollback across #402) the old image ignores
                              SCHEDULER_IN_PROCESS, so EACH of web's gunicorn
                              workers starts a scheduler, and the worker
                              crash-loops (no `run-scheduler`). Duplicate jobs.
                              RUNBOOK §5: restore the pre-#402 compose file
                              before rolling back across #402

What must never happen is two schedulers: every one of them would run every
job. That is why the worker count lives in compose next to the switch that
turns web's scheduler off, rather than in the image.

Each JOB carries its own gate (#33), never the scheduler as a whole:
  • weekly digest  → registered only when mail_enabled()
  • daily tasks    → always registered; push_enabled() gates only the reminder
                     half, inside the job, so materialization always runs.
"""
import os
import time

import click

from app.mailer import mail_enabled

TIMEZONE = 'America/New_York'


def scheduler_enabled():
    """The master switch. Unset everywhere but the Droplet, so nothing runs
    under pytest or in local dev unless deliberately enabled."""
    return os.getenv('ENABLE_DIGEST_SCHEDULER') == '1'


def add_jobs(scheduler):
    """Register every scheduled job on `scheduler`. Shared by both runners, so
    the in-process thread and the worker can never disagree about the jobs."""
    from app.blueprints.digests import send_weekly_digests
    from app.blueprints.reminders import run_daily_tasks

    if mail_enabled():
        # The users.last_digest_sent_on guard + misfire_grace_time make it safe
        # across restarts. Sunday 18:00 America/New_York.
        scheduler.add_job(send_weekly_digests, 'cron', day_of_week='sun', hour=18,
                          id='weekly_digest', replace_existing=True,
                          misfire_grace_time=3600)
    # Daily 18:00 — the evening before a bill is due. reminder_log makes the
    # send idempotent per occurrence across restarts and re-runs.
    scheduler.add_job(run_daily_tasks, 'cron', hour=18,
                      id='daily_tasks', replace_existing=True,
                      misfire_grace_time=3600)
    return scheduler


def runs_in_this_process():
    """Start the in-process scheduler here? Only when the deployment switch is
    on AND compose has not handed the jobs to the worker service."""
    return scheduler_enabled() and os.getenv('SCHEDULER_IN_PROCESS', '1') != '0'


def start_in_process():
    """The pre-#402 path: a daemon thread inside the web process. Only safe
    with exactly one gunicorn worker — see the module docstring."""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = add_jobs(BackgroundScheduler(timezone=TIMEZONE, daemon=True))
    scheduler.start()
    return scheduler


@click.command('run-scheduler')
def run_scheduler_command():
    """Run the scheduled jobs in the foreground — the `worker` service (#402).

    With the switch off it idles instead of exiting: compose restarts a
    container that exits, so exiting would turn "deliberately off" (local dev,
    or a Droplet with the switch unset) into a crash loop."""
    if not scheduler_enabled():
        click.echo('Scheduler disabled (ENABLE_DIGEST_SCHEDULER is not 1); idling.')
        while True:
            time.sleep(3600)

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = add_jobs(BlockingScheduler(timezone=TIMEZONE))
    jobs = ', '.join(job.id for job in scheduler.get_jobs())
    click.echo(f'Scheduler running: {jobs}')
    scheduler.start()
