"""Unit tests for `scripts/check_site_drift.py` (#297).

The script watches production; these tests watch the script, and they do it
**without a network**. Every fetch is a seam the tests replace, exactly as
`app/ai.py` and `app/mailer.py` are tested — so this file never depends on
`seandesmet.com` being up, which is the property that makes it safe to run in
CI and on a laptop with no connectivity.

⚠️ The two behaviours worth being strict about, because getting either wrong
makes the whole check untrustworthy rather than merely broken:

1. **`san_covers()` must not be permissive.** A wildcard covers exactly one
   label. If it accepted `*.example.com` for `example.com`, the check would
   agree with a certificate that does not serve the apex — the precise failure
   the duplicate-lineage incident consisted of.
2. **Unreachable is not drift.** A flaky runner must never report "production is
   stale". These are separate statuses with separate exit codes, and the tests
   pin that separation rather than trusting it.
3. **An HTTP status is not unreachability either** (#329). `check_health` had no
   test at all, which is how a 500 came to be classified the same as a refused
   connection — and the workflow files an issue for one and nothing for the
   other. The tests below cover BOTH sides of that line, because asserting only
   that a network fault is not drift is what left the gap.
"""

import datetime as dt
import importlib.util
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_site_drift.py"

_NOT_IN_IMAGE = "not present in the shipped image — .dockerignore excludes it"

pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason=_NOT_IN_IMAGE)


def _load():
    """Import the script by path — it is a standalone tool, not a package member."""
    spec = importlib.util.spec_from_file_location("check_site_drift", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drift = _load() if SCRIPT.exists() else None


NOW = dt.datetime(2026, 8, 24, tzinfo=dt.UTC)


def _cert(*names, not_after="Nov  2 09:55:35 2026 GMT"):
    return {
        "subjectAltName": tuple(("DNS", n) for n in names),
        "notAfter": not_after,
    }


# ---------------------------------------------------------------------------
# SAN matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "names,hostname",
    [
        (["seandesmet.com", "www.seandesmet.com"], "seandesmet.com"),
        (["seandesmet.com", "www.seandesmet.com"], "www.seandesmet.com"),
        (["SEANDESMET.COM"], "seandesmet.com"),
        (["seandesmet.com."], "seandesmet.com"),
        (["*.seandesmet.com"], "budget.seandesmet.com"),
    ],
)
def test_san_covers_accepts_a_name_the_certificate_really_serves(names, hostname):
    assert drift.san_covers(names, hostname)


@pytest.mark.parametrize(
    "names,hostname",
    [
        # The exact shape of the 2026 incident: apex-only cert, www server block.
        (["seandesmet.com"], "www.seandesmet.com"),
        (["budget.seandesmet.com"], "seandesmet.com"),
        # A wildcard covers ONE label — neither the apex nor a deeper name.
        (["*.seandesmet.com"], "seandesmet.com"),
        (["*.seandesmet.com"], "a.b.seandesmet.com"),
        # Suffix collision: a different registrable domain that merely ends the same.
        (["*.seandesmet.com"], "evilseandesmet.com"),
        ([], "seandesmet.com"),
    ],
)
def test_san_covers_rejects_a_name_the_certificate_does_not_serve(names, hostname):
    assert not drift.san_covers(names, hostname)


def test_san_hostnames_ignores_non_dns_entries():
    cert = {"subjectAltName": (("DNS", "a.example"), ("IP Address", "127.0.0.1"))}
    assert drift.san_hostnames(cert) == ["a.example"]


# ---------------------------------------------------------------------------
# Expiry arithmetic
# ---------------------------------------------------------------------------


def test_not_after_parses_openssl_single_digit_day_format():
    # Two spaces before the day is what OpenSSL emits, and what strptime needs
    # to be handed rather than split on whitespace.
    parsed = drift.parse_not_after({"notAfter": "Nov  2 09:55:35 2026 GMT"})
    assert parsed == dt.datetime(2026, 11, 2, 9, 55, 35, tzinfo=dt.UTC)


def test_days_remaining_counts_from_the_given_now():
    assert drift.days_remaining(dt.datetime(2026, 9, 3, tzinfo=dt.UTC), NOW) == 10


def test_a_certificate_nearing_expiry_is_reported_as_drift():
    soon = (NOW + dt.timedelta(days=5)).strftime("%b %d %H:%M:%S %Y GMT")
    status, detail = drift.check_certificate(
        "seandesmet.com",
        NOW,
        fetch=lambda host: _cert("seandesmet.com", not_after=soon),
        sleep=lambda _: None,
    )
    assert status == drift.DRIFT
    assert "expires in" in detail


def test_a_healthy_certificate_passes():
    status, _ = drift.check_certificate(
        "seandesmet.com",
        NOW,
        fetch=lambda host: _cert("seandesmet.com", "www.seandesmet.com"),
        sleep=lambda _: None,
    )
    assert status == drift.OK


def test_a_certificate_missing_the_hostname_is_drift_even_when_it_is_valid():
    """The incident shape: a perfectly valid cert that does not cover the name."""
    status, detail = drift.check_certificate(
        "www.seandesmet.com",
        NOW,
        fetch=lambda host: _cert("seandesmet.com"),
        sleep=lambda _: None,
    )
    assert status == drift.DRIFT
    assert "does NOT cover" in detail


# ---------------------------------------------------------------------------
# Unreachable is a separate status from drift
# ---------------------------------------------------------------------------


def test_retries_are_exhausted_before_anything_is_reported():
    calls = []

    def always_fails():
        calls.append(1)
        raise urllib.error.URLError("boom")

    value, failure = drift.with_retries(always_fails, attempts=3, sleep=lambda _: None)

    assert value is None
    assert len(calls) == 3, "gave up before exhausting its retries"
    # with_retries hands back the exception itself (#329), not a rendered string.
    assert "boom" in str(failure)


def test_a_transient_failure_that_then_succeeds_is_not_reported():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise TimeoutError("slow")
        return b"fine"

    value, failure = drift.with_retries(flaky, attempts=3, sleep=lambda _: None)

    assert value == b"fine"
    assert failure is None


# ---------------------------------------------------------------------------
# check_health — the app checking itself (#329)
#
# ⚠️ This whole section is what was missing. The file proved a network fault is
# not drift and never asserted the other half, so nothing noticed that an HTTP
# status was taking the same path. Both directions are pinned here on purpose:
# a test that only guards the permissive direction is how this happened.
# ---------------------------------------------------------------------------


def _http_error(code, url=None):
    """The exception `urlopen` raises for a status — the server ANSWERED."""
    return urllib.error.HTTPError(url or drift.HEALTH_URL, code, "nope", {}, None)


def test_a_healthy_healthz_is_ok():
    status, detail = drift.check_health(fetch=lambda _url: b"ok", sleep=lambda _: None)

    assert status == drift.OK
    assert drift.HEALTH_URL in detail


@pytest.mark.parametrize("code", [500, 502, 503])
def test_a_server_error_from_healthz_is_drift_not_unreachable(code):
    """The single most actionable thing this script can learn.

    UNREACHABLE files no issue (see the workflow tests at the bottom of this
    file), so classifying a 500 there means production being hard down is
    quieter than a certificate three weeks from expiry.
    """
    def answers_badly(_url):
        raise _http_error(code)

    status, detail = drift.check_health(fetch=answers_badly, sleep=lambda _: None)

    assert status == drift.DRIFT, "the app answered — that is not 'we could not tell'"
    assert status != drift.UNREACHABLE
    assert str(code) in detail, "the report does not say which status came back"


@pytest.mark.parametrize("code", [404, 403])
def test_a_client_error_from_healthz_is_drift_too(code):
    """/healthz should never 404 or challenge for auth.

    Restricting this to 5xx would leave a route that has been renamed, or put
    behind something, reading as a transient network fault forever.
    """
    def answers_badly(_url):
        raise _http_error(code)

    status, _ = drift.check_health(fetch=answers_badly, sleep=lambda _: None)

    assert status == drift.DRIFT


def test_a_transport_failure_from_healthz_is_still_unreachable():
    """The direction that was already right, now actually asserted.

    A refused connection from a GitHub runner must never file an issue saying
    production is broken — that is the reasoning UNREACHABLE exists for, and
    #329 must not take it away while fixing the other side.
    """
    def refuse(_url):
        raise urllib.error.URLError("connection refused")

    status, detail = drift.check_health(fetch=refuse, sleep=lambda _: None)

    assert status == drift.UNREACHABLE
    assert status != drift.DRIFT
    assert "connection refused" in detail


def test_a_status_is_judged_only_after_the_retries_are_exhausted():
    """A blip during a container swap must not file an issue.

    The classification changed in #329; the retry-before-you-judge rule the
    module docstring argues for did not.
    """
    attempts = []

    def answers_badly(_url):
        attempts.append(1)
        raise _http_error(503)

    status, _ = drift.check_health(fetch=answers_badly, sleep=lambda _: None)

    assert len(attempts) == 3, "judged the app on one answer"
    assert status == drift.DRIFT


def test_a_status_that_recovers_within_the_retries_is_not_drift():
    attempts = []

    def flaky(_url):
        attempts.append(1)
        if len(attempts) < 3:
            raise _http_error(502)
        return b"ok"

    status, _ = drift.check_health(fetch=flaky, sleep=lambda _: None)

    assert status == drift.OK


def test_an_unhealthy_app_exits_the_way_a_stale_certificate_does(monkeypatch):
    """The end of the chain, not just the classification.

    DRIFT is only louder than UNREACHABLE because of what main() returns and
    what the workflow does with it. Asserting the status alone would pass even
    if the exit code did not follow.
    """
    def answers_badly(_url):
        raise _http_error(500)

    # ⚠️ Patching drift._fetch_page would do nothing: it is check_health's
    # DEFAULT ARGUMENT, bound once when the function was defined. run_all()
    # looks check_health up as a module global, so that is the seam.
    real_check_health = drift.check_health
    monkeypatch.setattr(
        drift, "check_health",
        lambda: real_check_health(fetch=answers_badly, sleep=lambda _: None),
    )
    monkeypatch.setattr(drift, "check_certificate", lambda *a, **kw: (drift.OK, "fine"))

    assert drift.main([]) == 1, "an unhealthy app must exit the way drift does"
    assert drift.main(["--allow-unreachable"]) == 1, (
        "--allow-unreachable exists for flaky networks and must not swallow this"
    )


def test_an_unreachable_host_is_unreachable_not_drift():
    def refuse(_host):
        raise OSError("connection refused")

    status, detail = drift.check_certificate(
        "seandesmet.com", NOW, fetch=refuse, sleep=lambda _: None
    )

    assert status == drift.UNREACHABLE, "a network fault must never read as a stale deploy"
    assert status != drift.DRIFT
    assert "connection refused" in detail


# ---------------------------------------------------------------------------
# Exit codes — what the workflow keys off
# ---------------------------------------------------------------------------


def test_drift_and_unreachable_have_distinct_exit_codes(monkeypatch, capsys):
    """1 = production disagrees. 2 = we could not tell. 0 = agreement.

    They have to differ, because --allow-unreachable tolerates one and not the
    other. A single non-zero code would make that flag meaningless.
    """
    monkeypatch.setattr(drift, "run_all", lambda **kw: [("x", drift.DRIFT, "d")])
    assert drift.main([]) == 1

    monkeypatch.setattr(drift, "run_all", lambda **kw: [("x", drift.UNREACHABLE, "u")])
    assert drift.main([]) == 2
    assert drift.main(["--allow-unreachable"]) == 0

    monkeypatch.setattr(drift, "run_all", lambda **kw: [("x", drift.OK, "fine")])
    assert drift.main([]) == 0


def test_allow_unreachable_does_not_also_swallow_drift(monkeypatch, capsys):
    """The flag exists for flaky networks, not for silencing a stale deploy."""
    monkeypatch.setattr(
        drift,
        "run_all",
        lambda **kw: [("a", drift.UNREACHABLE, "u"), ("b", drift.DRIFT, "d")],
    )
    assert drift.main(["--allow-unreachable"]) == 1


# ---------------------------------------------------------------------------
# The workflow's shell logic — testable as text, without spending a runner
# ---------------------------------------------------------------------------

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "site-drift.yml"


@pytest.mark.skipif(not WORKFLOW.exists(), reason=_NOT_IN_IMAGE)
def test_the_workflow_reads_the_scripts_exit_code_not_the_pipes():
    """`$?` after `cmd | tee` is tee's status, which is always 0.

    That exact mistake once made a failed restore report success with
    complete-looking data. Here it would turn every drift report into a green
    run — the check would exist, cost money, and never fire.
    """
    body = WORKFLOW.read_text(encoding="utf-8")

    assert "PIPESTATUS" in body, "the piped exit code is not being captured"
    assert "exit_code=$?" not in body, "reads the pipeline's status instead of the script's"


@pytest.mark.skipif(not WORKFLOW.exists(), reason=_NOT_IN_IMAGE)
def test_only_drift_files_an_issue_never_unreachability():
    """Pinning a design decision a future edit could reasonably undo.

    Exit 2 means "we could not reach it", which a flaky runner produces. Filing
    an issue for that fills the tracker with noise until nobody reads it, and
    the one real drift report gets lost among them. A genuine outage is visible
    as a run of red scheduled runs instead.
    """
    body = WORKFLOW.read_text(encoding="utf-8")

    file_step = body.split("File an issue when production has DRIFTED", 1)
    assert len(file_step) == 2, "the issue-filing step has been renamed — re-check this assertion"

    guard = file_step[1].split("- name:", 1)[0]
    assert "outputs.exit_code == '1'" in guard, "the issue step is not gated on drift alone"
    assert "'2'" not in guard, "unreachability must not file an issue"
