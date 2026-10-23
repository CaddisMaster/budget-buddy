"""gunicorn's entry point: `gunicorn app.wsgi:app` (#490).

The one place the in-process scheduler can start: the pre-#402 path, kept so a
new image on a pre-#402 compose file behaves exactly as before. The production
compose file sets SCHEDULER_IN_PROCESS=0 on `web` and hands the jobs to the
`worker` service. `app/scheduler.py` has the full matrix.

It is only ever safe with ONE gunicorn worker, the image's default: gunicorn
imports this module in each worker, and each would start its own scheduler.

⚠️ This lives in `app/`, NOT at the repo root. Flask's CLI imports a root
`wsgi.py` first when it looks for an app, so a copy there would run this
scheduler start inside `flask run-scheduler`: the worker's two schedulers again
(#483). `tests/test_app_factory.py` checks the root stays empty.
"""
from app import create_app, scheduler

app = create_app()

if scheduler.runs_in_this_process():
    scheduler.start_in_process(app)
