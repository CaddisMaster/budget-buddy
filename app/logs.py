"""Production logging (#404): INFO, one format, and a request ID on every line.

Before this, nothing configured logging at all. Flask's `app.logger` fell back
to Python's WARNING default, so every `logger.info()` in the app was dropped —
including the admin backup's audit line, which exists precisely to be read, and
the daily job's `Daily tasks: …` summary. The tests never noticed because
`caplog.at_level(logging.INFO)` LOWERS the level it then captures at.

Shape:

- ONE handler, on the root logger, writing to stderr — the stream gunicorn's
  own logs already use, so `docker compose logs web` interleaves both. Library
  warnings (APScheduler, the limiter, psycopg2's pool) come through it too.
- The app's loggers (`app`, and every `app.*` child such as `ask.py`'s) log at
  INFO. The root stays at WARNING, so third-party INFO chatter stays out.
- Every app line is written once. Flask adds its own stderr handler to
  `app.logger` only if no handler exists up the hierarchy when the logger is
  first built, and `configure()` attaches the root handler before touching
  `app.logger`. The `removeHandler` is the backstop for anything that reads
  `app.logger` before `configure()` runs; left in place, it would write every
  app line a second time.
- The format mirrors gunicorn's `[time] [pid] [LEVEL]` prefix, then the request
  ID, then the logger name. Outside a request (the worker, a CLI command) the
  ID is `-`.

The ID is also sent back as the `X-Request-ID` response header, so a failing
request seen in a browser can be found in the log. It is generated here, never
read from the incoming request: a client-supplied ID would be text an attacker
writes into the log.

⚠️ Tracebacks: an unhandled exception is logged by Flask's `log_exception`,
which records the path and method and never the form. Keep it that way — never
log `request.form`, `request.values` or `request.get_data()`. The ledger is
financial data, and the log leaves the app on every `docker logs`.
"""
import logging
import secrets
import sys

from flask import g, has_request_context
from flask.logging import default_handler

FORMAT = '[%(asctime)s] [%(process)d] [%(levelname)s] [%(request_id)s] %(name)s: %(message)s'
# gunicorn's own date format, so its lines and the app's read as one stream.
DATEFMT = '%Y-%m-%d %H:%M:%S %z'


def request_id():
    """This request's ID, made on first use; `-` outside a request."""
    if not has_request_context():
        return '-'
    if 'request_id' not in g:
        g.request_id = secrets.token_hex(8)
    return g.request_id


class _RequestIdFilter(logging.Filter):
    # On the HANDLER, not a logger: a logger's filters skip records that
    # propagate up from its children, a handler's see every record it writes.
    def filter(self, record):
        record.request_id = request_id()
        return True


handler = logging.StreamHandler(sys.stderr)
handler.addFilter(_RequestIdFilter())
handler.setFormatter(logging.Formatter(FORMAT, DATEFMT))


def configure(app):
    root = logging.getLogger()
    if handler not in root.handlers:
        root.addHandler(handler)
    app.logger.removeHandler(default_handler)
    app.logger.setLevel(logging.INFO)

    @app.after_request
    def send_request_id(response):
        response.headers['X-Request-ID'] = request_id()
        return response
