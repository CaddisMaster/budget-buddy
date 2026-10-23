"""The app factory (#490).

`create_app()` builds an app and does nothing else: in particular it NEVER
starts a scheduler. Whether this process hosts the scheduled jobs is decided by
its entry point, not by whoever imports this package:

    gunicorn     app.wsgi:app    may start the legacy in-process scheduler
    flask CLI    create_app()    `run-scheduler` starts the worker's own

Before #490 a global app was built on import, and that import could start the
in-process scheduler, so every process importing the app inherited the
decision. The worker did, and ran every job twice (#483).

The extensions live at module level, unbound, so blueprints keep importing them
(`from app import limiter`); `create_app()` binds them.
"""
import hashlib
import os
import secrets

from dotenv import load_dotenv
from flask import Flask, g
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

load_dotenv()


def csp_nonce():
    """This response's script nonce, made on first use and kept on `g`, so the
    header and every `<script nonce=…>` in the page agree (#403). Templates
    call it as `{{ csp_nonce() }}`."""
    if 'csp_nonce' not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


# #403. Scripts run only from the app's own files or from an inline block that
# carries this response's nonce. Inline `on…=` handlers, htmx `hx-on` and htmx
# trigger filters are refused outright: the first can never carry a nonce, and
# htmx compiles the other two with Function(), which would need 'unsafe-eval'.
# tests/test_csp.py keeps all three out of the templates.
#
# ⚠️ style-src keeps 'unsafe-inline', and that is a KNOWN GAP, not an oversight.
# ApexCharts injects <style> elements and sets style attributes as it draws, and
# the templates carry ~180 style="" attributes. Injected CSS can restyle a page
# but cannot run script, which is the attack this policy exists to stop.
_CSP = ("default-src 'self'; "
        "script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'")


def _secure_cookies():
    """v10.1.1 hardening. Secure cookies + HSTS are gated on COOKIE_SECURE=1
    (set in the Droplet .env) so local HTTP dev and the Werkzeug test client —
    both plain http — still set and send the session cookie."""
    return os.getenv('COOKIE_SECURE', '') == '1'


def set_security_headers(response):
    """Defense-in-depth response headers (v10.1.1, a real CSP since #403). HSTS
    is sent only in prod (COOKIE_SECURE=1), where TLS is terminated at Nginx."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = _CSP.format(nonce=csp_nonce())
    response.headers['Referrer-Policy'] = 'no-referrer'
    if _secure_cookies():
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# Display-format an amount: thousands separators, 2dp — "1234.5" → "1,234.56".
# Number only; templates write the $ and any sign styling around it. DISPLAY
# templates only: AI fact-builders pass raw numbers, chart payloads stay
# |tojson floats, and form <input value>s stay raw (a comma'd value would fail
# parse_positive_amount on resubmit).
def money_filter(value):
    return f'{float(value):,.2f}'


csrf = CSRFProtect()

# Rate-limit counters (#402). With several gunicorn workers, memory storage
# counts per worker, so the real login limit would be 10/min × workers. The
# production compose file points this at its `redis` service. The URI names a
# compose-internal host, so it lives in docker-compose.yml, not .env: nothing
# to forget at deploy time. Unset (dev, tests, CI, the smoke job) it stays in
# memory. If Redis is unreachable, the limiter falls back to memory rather than
# failing requests: limits weaken to per-worker, and the app stays up.
limiter = Limiter(
  get_remote_address,
  default_limits=["60 per minute"],
  storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
  in_memory_fallback_enabled=True,
)

login_manager = LoginManager()
login_manager.login_view = 'auth.login'

bcrypt = Bcrypt()

from app.models import User


@login_manager.user_loader
def load_user(session_id):
  """Resolve the `"<id>:<session_token>"` Flask-Login stores in the cookie.

  ⚠️ FAILS CLOSED on anything it cannot fully verify — a bad value is an
  ANONYMOUS request, never an exception (#272). That is not an edge case: the
  pre-#272 cookie format is a bare `"42"` with no token, so on the deploy that
  ships this, EVERY outstanding cookie in existence takes this path. A raised
  exception here would be a 500 on every page for every logged-in user until
  they cleared their cookies, and `load_user` runs before any route's own error
  handling.

  Rotating `users.session_token` is therefore what signs a device out; see
  `models.User.get_id`, and `auth.change_password`, which rotates it.
  """
  raw_id, _, token = (session_id or '').partition(':')
  if not token:
    return None
  try:
    user = User.get_by_id(int(raw_id))
  except (TypeError, ValueError):
    return None
  if user is None:
    return None
  # compare_digest rather than == : the token is a credential, and a
  # constant-time compare costs nothing here.
  #
  # ⚠️ ENCODED TO BYTES, and that is not stylistic. `compare_digest` raises
  # `TypeError: comparing strings with non-ASCII characters is not supported`
  # when handed str — so `load_user("<real id>:ü")` RAISED, defeating the
  # fail-closed contract this function exists to keep. `.encode()` accepts any
  # str and compare_digest has no such restriction on bytes.
  #
  # ⚠️ It was missed at first because the obvious probe uses a made-up id: the
  # `User.get_by_id` above then returns None and the function exits before ever
  # reaching this line, so every non-ASCII case "passed". It only reproduces
  # against an id that EXISTS. Same vacuity as the bare-id test — see
  # `test_a_non_ascii_token_does_not_raise`, which uses a real user.
  if not secrets.compare_digest(str(user.session_token).encode(), token.encode()):
    return None
  return user

def create_app(config=None):
    """Build the app: configuration, extensions, blueprints, CLI commands.

    ⚠️ Starts NO scheduler, whatever the environment says (#490). That decision
    belongs to the entry point: `app/wsgi.py` for gunicorn's legacy in-process
    path, `flask run-scheduler` for the worker. `config` is applied last, so a
    caller (the tests) can override anything set here.
    """
    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY')
    if not app.secret_key:
        # Fail fast: without it the app boots fine and then 500s confusingly at
        # the first session/CSRF use. Set SECRET_KEY in .env.
        raise RuntimeError('SECRET_KEY is not set — add it to .env')

    # INFO, one format, a request ID on every line (#404). First, so nothing
    # logs through the unconfigured WARNING default. See app/logs.py.
    from app import logs

    logs.configure(app)

    secure = _secure_cookies()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=secure,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE='Lax',
        REMEMBER_COOKIE_SECURE=secure,
    )

    # Local development only — set by docker-compose.override.yml alongside the
    # source bind mount, never in production, where Jinja's compiled-template
    # cache is wanted. Without it a template edit reaches the container and is
    # then ignored: gunicorn's --reload watches Python modules, not .html files,
    # so the file on disk changes while the served page does not. That failure
    # is silent and looks exactly like the bind mount not working.
    if os.getenv('TEMPLATES_AUTO_RELOAD', '') == '1':
        app.config['TEMPLATES_AUTO_RELOAD'] = True

    app.jinja_env.globals['csp_nonce'] = csp_nonce
    app.after_request(set_security_headers)
    app.add_template_filter(money_filter, 'money')

    # Auto cache-bust for the stylesheet: ?v= is a hash of style.css CONTENT,
    # computed ONCE AT STARTUP, so nobody hand-bumps a version number again.
    # Both base.html and login.html (which doesn't extend base) read this global.
    #
    # ⚠️ Consequence for local dev, now that the source is bind-mounted: editing
    # style.css does NOT change css_v, because nothing rebuilds the app —
    # gunicorn's --reload watches Python files and a .css edit is not one. The
    # browser keeps the old ?v= and serves the cached stylesheet. Python and
    # template edits are live; a CSS edit needs `docker compose restart web`
    # (~2s, still far cheaper than a rebuild).
    with open(os.path.join(app.static_folder, 'style.css'), 'rb') as css:
        app.jinja_env.globals['css_v'] = hashlib.md5(css.read()).hexdigest()[:8]

    # The brand mark, read once at startup so the sidebar inlines the SAME SVG
    # the favicon/rasters are built from — no hand-copied duplicate to drift
    # (the old base.html inline copy was exactly that). base.html renders it
    # |safe inside a sized .brand-mark wrapper.
    with open(os.path.join(app.static_folder, 'icons', 'icon.svg')) as mark:
        app.jinja_env.globals['brand_svg'] = mark.read()

    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)
    bcrypt.init_app(app)

    from app import scheduler
    from app.blueprints import (
        accounts,
        admin,
        analytics,
        announce,
        ask,
        auth,
        budgets,
        categories,
        digests,
        feedback,
        goals,
        imports,
        insights,
        main,
        push,
        reminders,
        schedules,
        transactions,
        transfers,
    )

    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(transactions.bp)
    app.register_blueprint(categories.bp)
    app.register_blueprint(accounts.bp)
    app.register_blueprint(budgets.bp)
    app.register_blueprint(analytics.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(transfers.bp)
    app.register_blueprint(goals.bp)
    app.register_blueprint(imports.bp)
    app.register_blueprint(schedules.bp)
    # ⚠️ blueprints/forecasts.py and blueprints/agent.py are deliberately NOT
    # here (#232): both lost their routes with Home's AI cards and are now plain
    # function modules — the forecast arithmetic feeds the month read and an Ask
    # tool, and the money agent runs inside the weekly digest. They stay under
    # blueprints/ because that is where their callers look for them, not because
    # they serve requests.
    app.register_blueprint(insights.bp)
    app.register_blueprint(ask.bp)
    app.register_blueprint(digests.bp)
    app.register_blueprint(push.bp)
    app.register_blueprint(reminders.bp)
    app.register_blueprint(feedback.bp)
    app.register_blueprint(announce.bp)

    # `flask send-digests` / `flask run-daily` — run either scheduled job by
    # hand. `flask announce-release` is NOT scheduled: the release workflow runs
    # it once, inside the image it just deployed. `flask run-scheduler` is the
    # worker service (#402).
    app.cli.add_command(digests.send_digests_command)
    app.cli.add_command(reminders.run_daily_command)
    app.cli.add_command(announce.announce_release_command)
    app.cli.add_command(scheduler.run_scheduler_command)

    if config:
        app.config.update(config)
    return app


_legacy_app = None


def __getattr__(name):
    """`app.app`, built on first use, for the compose files that name `app:app`.

    0.12.0's and 0.13.0's compose files run gunicorn on `app:app` explicitly,
    with the in-process scheduler switched off, so a new image under one of
    them needs this attribute and must start no scheduler: exactly what
    `create_app()` does. Lazy, so importing the package (every test, every CLI
    command, `from app import limiter`) builds nothing.

    ⚠️ Nothing in `app/` may use it: a second app built in a process that
    already has one is a second set of everything. `tests/test_app_factory.py`
    checks that. New entry points use `app.wsgi:app` or `create_app()`.
    """
    global _legacy_app
    if name == 'app':
        if _legacy_app is None:
            _legacy_app = create_app()
        return _legacy_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
