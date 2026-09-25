import hashlib
import os
import secrets

from dotenv import load_dotenv
from flask import Flask
from flask_bcrypt import Bcrypt
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    # Fail fast: without it the app boots fine and then 500s confusingly at the
    # first session/CSRF use. Set SECRET_KEY in .env.
    raise RuntimeError('SECRET_KEY is not set — add it to .env')

# INFO, one format, a request ID on every line (#404). First, so nothing logs
# through the unconfigured WARNING default. See app/logs.py.
from app import logs as _logs

_logs.configure(app)

# v10.1.1 hardening. Secure cookies + HSTS are gated on COOKIE_SECURE=1 (set in
# the Droplet .env) so local HTTP dev and the Werkzeug test client — both plain
# http — still set and send the session cookie.
_secure_cookies = os.getenv('COOKIE_SECURE', '') == '1'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_secure_cookies,
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE='Lax',
    REMEMBER_COOKIE_SECURE=_secure_cookies,
)

# Local development only — set by docker-compose.override.yml alongside the
# source bind mount, never in production, where Jinja's compiled-template cache
# is wanted. Without it a template edit reaches the container and is then
# ignored: gunicorn's --reload watches Python modules, not .html files, so the
# file on disk changes while the served page does not. That failure is silent
# and looks exactly like the bind mount not working.
if os.getenv('TEMPLATES_AUTO_RELOAD', '') == '1':
    app.config['TEMPLATES_AUTO_RELOAD'] = True


@app.after_request
def set_security_headers(response):
    """Defense-in-depth response headers (v10.1.1). CSP is frame-ancestors only
    — a full policy would break HTMX/Chart.js/inline styles. HSTS is sent only
    in prod (COOKIE_SECURE=1), where TLS is terminated at Nginx."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    response.headers['Referrer-Policy'] = 'no-referrer'
    if _secure_cookies:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response


# Auto cache-bust for the stylesheet: ?v= is a hash of style.css CONTENT,
# computed ONCE AT STARTUP, so nobody hand-bumps a version number again. Both
# base.html and login.html (which doesn't extend base) read this global.
#
# ⚠️ Consequence for local dev, now that the source is bind-mounted: editing
# style.css does NOT change css_v, because nothing re-imports this module —
# gunicorn's --reload watches Python files and a .css edit is not one. The
# browser keeps the old ?v= and serves the cached stylesheet. Python and
# template edits are live; a CSS edit needs `docker compose restart web`
# (~2s, still far cheaper than a rebuild).
with open(os.path.join(app.static_folder, 'style.css'), 'rb') as _css:
    app.jinja_env.globals['css_v'] = hashlib.md5(_css.read()).hexdigest()[:8]


# The brand mark, read once at startup so the sidebar inlines the SAME SVG the
# favicon/rasters are built from — no hand-copied duplicate to drift (the old
# base.html inline copy was exactly that). base.html renders it |safe inside
# a sized .brand-mark wrapper.
with open(os.path.join(app.static_folder, 'icons', 'icon.svg')) as _mark:
    app.jinja_env.globals['brand_svg'] = _mark.read()


# Display-format an amount: thousands separators, 2dp — "1234.5" → "1,234.56".
# Number only; templates write the $ and any sign styling around it. DISPLAY
# templates only: AI fact-builders pass raw numbers, chart payloads stay
# |tojson floats, and form <input value>s stay raw (a comma'd value would fail
# parse_positive_amount on resubmit).
@app.template_filter('money')
def money_filter(value):
    return f'{float(value):,.2f}'

csrf = CSRFProtect(app)

# Rate-limit counters (#402). With several gunicorn workers, memory storage
# counts per worker, so the real login limit would be 10/min × workers. The
# production compose file points this at its `redis` service. The URI names a
# compose-internal host, so it lives in docker-compose.yml, not .env: nothing
# to forget at deploy time. Unset (dev, tests, CI, the smoke job) it stays in
# memory. If Redis is unreachable, the limiter falls back to memory rather than
# failing requests: limits weaken to per-worker, and the app stays up.
limiter = Limiter(
  get_remote_address,
  app=app,
  default_limits=["60 per minute"],
  storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
  in_memory_fallback_enabled=True,
)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

bcrypt = Bcrypt(app)

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
app.register_blueprint(schedules.bp)
# ⚠️ blueprints/forecasts.py and blueprints/agent.py are deliberately NOT here
# (#232): both lost their routes with Home's AI cards and are now plain function
# modules — the forecast arithmetic feeds the month read and an Ask tool, and the
# money agent runs inside the weekly digest. They stay under blueprints/ because
# that is where their callers look for them, not because they serve requests.
app.register_blueprint(insights.bp)
app.register_blueprint(ask.bp)
app.register_blueprint(digests.bp)
app.register_blueprint(push.bp)
app.register_blueprint(reminders.bp)
app.register_blueprint(feedback.bp)
app.register_blueprint(announce.bp)

# `flask send-digests` / `flask run-daily` — run either scheduled job by hand.
# `flask announce-release` is NOT scheduled: the release workflow runs it once,
# inside the image it just deployed.
app.cli.add_command(digests.send_digests_command)
app.cli.add_command(reminders.run_daily_command)
app.cli.add_command(announce.announce_release_command)

# The scheduled jobs (#402). Production runs them in the `worker` compose
# service (`flask run-scheduler`) and turns this switch off for `web`; the
# in-process thread below is the pre-#402 path, kept so that a new image on an
# old compose file behaves exactly as before. It is only ever safe with ONE
# gunicorn worker — the image's default — because each worker would start its
# own scheduler. app/scheduler.py has the full matrix.
from app import scheduler as _jobs

app.cli.add_command(_jobs.run_scheduler_command)
if _jobs.runs_in_this_process():
    _scheduler = _jobs.start_in_process()
