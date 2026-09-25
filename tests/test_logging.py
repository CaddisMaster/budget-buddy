"""Production logging (#404) — the parts no scenario in production_logs.feature
reaches: one line per event, the ID outside a request, and the behave runner
not lowering the level the scenarios check.
"""
import io
import logging

from flask.logging import default_handler

from app import app as flask_app
from app import logs
from tests import run_behave


def _capture():
    buffer = io.StringIO()
    return buffer, logs.handler.setStream(buffer)


def test_the_app_logger_is_at_info_and_the_root_is_not_lowered():
    """The pair that makes INFO reach the log without flooding it with library
    INFO: the app's own loggers at INFO, the root left at Python's WARNING."""
    assert flask_app.logger.level == logging.INFO
    assert logging.getLogger("app.blueprints.ask").getEffectiveLevel() == logging.INFO
    assert logs.handler in logging.getLogger().handlers


def test_an_app_line_is_written_once():
    """Flask's own stderr handler is removed from app.logger. Left in place,
    every app line is written twice: once by it, once by the root handler."""
    assert default_handler not in flask_app.logger.handlers
    buffer, previous = _capture()
    try:
        flask_app.logger.warning("logged-once-marker")
    finally:
        logs.handler.setStream(previous)
    assert buffer.getvalue().count("logged-once-marker") == 1


def test_a_line_outside_a_request_carries_a_dash():
    """The worker and CLI commands log with no request. The filter must not
    raise there, since a raising filter loses the line."""
    buffer, previous = _capture()
    try:
        flask_app.logger.warning("outside-a-request")
    finally:
        logs.handler.setStream(previous)
    assert "[WARNING] [-] app: outside-a-request" in buffer.getvalue()


def test_the_id_is_never_taken_from_the_client(anon_client):
    """A client-supplied ID would be attacker-written text in the log."""
    response = anon_client.get("/login", headers={"X-Request-ID": "forged-by-client"})
    assert response.headers["X-Request-ID"] != "forged-by-client"
    assert len(response.headers["X-Request-ID"]) == 16


def test_the_behave_runner_turns_off_behaves_log_capture(monkeypatch):
    """behave's log capture sets the ROOT logger to INFO for every scenario, so
    a scenario can never see an INFO line production drops. It hid #404's own
    defect from #404's own scenario until the runner passed this flag."""
    seen = {}
    monkeypatch.setattr(run_behave, "Configuration",
                        lambda command_args: seen.setdefault("argv", command_args))
    monkeypatch.setattr(run_behave, "run_behave", lambda config, runner_class: 1)
    run_behave.main(["--tags=@wip"])
    assert "--no-logcapture" in seen["argv"]
