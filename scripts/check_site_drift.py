#!/usr/bin/env python3
"""Detect drift between what this repository says and what production serves (#297).

This watches **TLS and application health** across every hostname this project
serves.

⚠️ **The landing-page byte check lived here until #299** and is deliberately
gone. `landing/` moved to its own repo (`CaddisMaster/seandesmet.com`), which
deploys on push and verifies the served bytes itself — so the drift this script
existed to catch is now impossible by construction rather than detected after
the fact. Do not re-add a check for `seandesmet.com`'s content here: two repos
watching one page means two issues filed for one fault.

The apex and `www` **certificates** stay in scope. This repo remains the
operational one — it holds `RUNBOOK.md` and the certbot reasoning — and a
certificate is a property of the Droplet, not of whichever repo supplies the
HTML.

TLS is unmonitored otherwise. Uptime Kuma was retired on 2026-07-27,
so nothing watches certificate expiry. Certbot renews automatically, but a
*broken* renewal is silent right up until the handshake fails.

Deliberately standalone, the same reasoning as ``restore_check.py``,
``migrate.py`` and ``release_prep.py``: no Flask, no app import, **standard
library only**. It has to run anywhere — a GitHub runner, the VM, a laptop —
and cannot assume this project's virtualenv or its containers are present.

⚠️ **Every check is PUBLIC HTTP/TLS. It needs no Droplet access, no key and no
secret.** That is a deliberate design constraint, not a happy accident: the
Droplet is unreachable from the development VM on purpose, and a monitoring
script that needed privileged access would either be useless there or become an
argument for weakening that boundary.

⚠️ **The network lives behind two seams** — ``_fetch_page()`` and
``_fetch_cert()`` — and everything else is a pure function. Tests monkeypatch
the seams, exactly as ``app/ai.py`` and ``app/mailer.py`` are structured. This
is what makes drift logic testable without a network or a live site.

⚠️ **UNREACHABLE IS NOT DRIFT.** A transient failure must never be reported as
"production is stale" — that would file issues on every flaky runner. Fetches
retry, and an exhausted retry is reported as its own distinct status.

⚠️ **Nothing here reads a file from the working tree any more** (#299), so the
result no longer depends on which branch you run it from. Every check queries
the live host and judges it against a rule stated in this file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

HEALTH_URL = "https://budget.seandesmet.com/healthz"

# Every hostname this project serves, and therefore every certificate that has
# to cover it. Kept explicit rather than derived: the point is to notice when
# reality stops matching the intent, and deriving the intent from reality would
# make the check agree with whatever is deployed.
HOSTNAMES = ("seandesmet.com", "www.seandesmet.com", "budget.seandesmet.com")

# Let's Encrypt certificates last 90 days and certbot renews at 30 remaining.
# Alerting at 21 leaves a week of failed renewals before anything is user-facing.
DEFAULT_EXPIRY_WARNING_DAYS = 21

USER_AGENT = "budget-buddy-site-drift/1 (+https://github.com/CaddisMaster/budget-buddy)"

OK = "ok"
DRIFT = "drift"
UNREACHABLE = "unreachable"


# ---------------------------------------------------------------------------
# The two network seams. Nothing else in this file touches the network.
# ---------------------------------------------------------------------------


def _fetch_page(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


def _fetch_cert(hostname: str, port: int = 443, timeout: float = 20.0) -> dict:
    context = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as tls:
            return tls.getpeercert()


# ---------------------------------------------------------------------------
# Retry — the line between "unreachable" and "drifted"
# ---------------------------------------------------------------------------


def with_retries(call, attempts: int = 3, delay: float = 2.0, sleep=time.sleep):
    """Return ``(value, None)`` or, once retries are exhausted, ``(None, reason)``.

    Retrying is what keeps a flaky runner from being reported as a stale
    deployment. ``sleep`` is injected so tests do not actually wait.
    """
    last = ""
    for attempt in range(attempts):
        try:
            return call(), None
        except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                sleep(delay)
    return None, last


# ---------------------------------------------------------------------------
# Pure logic — no network below this line
# ---------------------------------------------------------------------------


def san_hostnames(cert: dict) -> list[str]:
    """DNS names from a parsed certificate's subjectAltName."""
    return [value for kind, value in cert.get("subjectAltName", ()) if kind == "DNS"]


def san_covers(names, hostname: str) -> bool:
    """Whether ``hostname`` is covered, honouring a single leading wildcard.

    ``*.example.com`` matches one label: it covers ``www.example.com`` and not
    ``example.com`` or ``a.b.example.com``. Getting this wrong in the permissive
    direction would make the check agree with a certificate that does not
    actually serve the name.
    """
    host = hostname.casefold().rstrip(".")
    for raw in names:
        name = raw.casefold().rstrip(".")
        if name == host:
            return True
        if name.startswith("*."):
            suffix = name[1:]  # ".example.com"
            if host.endswith(suffix) and "." not in host[: -len(suffix)]:
                return True
    return False


def parse_not_after(cert: dict, now: dt.datetime | None = None) -> dt.datetime:
    """``notAfter`` as an aware UTC datetime.

    OpenSSL renders it as ``'Nov  2 09:55:35 2026 GMT'`` — note the two spaces
    before a single-digit day, which is why this is parsed rather than split.
    """
    raw = cert.get("notAfter")
    if not raw:
        raise ValueError("certificate carries no notAfter")
    naive = dt.datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z")
    return naive.replace(tzinfo=dt.UTC)


def days_remaining(not_after: dt.datetime, now: dt.datetime) -> int:
    return (not_after - now).days


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_certificate(
    hostname: str,
    now: dt.datetime,
    warning_days: int = DEFAULT_EXPIRY_WARNING_DAYS,
    fetch=_fetch_cert,
    sleep=time.sleep,
) -> tuple[str, str]:
    cert, failure = with_retries(lambda: fetch(hostname), sleep=sleep)
    if failure:
        return UNREACHABLE, f"{hostname}: {failure}"

    names = san_hostnames(cert)
    if not san_covers(names, hostname):
        return DRIFT, (
            f"{hostname}: the certificate served does NOT cover it. "
            f"SAN = {', '.join(names) or '(none)'}. "
            "Check `certbot certificates` for a lineage that covers every name "
            "in this server block — see RUNBOOK.md."
        )

    remaining = days_remaining(parse_not_after(cert), now)
    if remaining < warning_days:
        return DRIFT, (
            f"{hostname}: certificate expires in {remaining} day(s) "
            f"({cert.get('notAfter')}). Renewal has not run, or is failing."
        )

    return OK, f"{hostname}: SAN covers it, {remaining} days remaining"


def check_health(fetch=_fetch_page, sleep=time.sleep) -> tuple[str, str]:
    _, failure = with_retries(lambda: fetch(HEALTH_URL), sleep=sleep)
    if failure:
        return UNREACHABLE, f"{HEALTH_URL}: {failure}"
    return OK, f"{HEALTH_URL} answers"


def run_all(now: dt.datetime | None = None, warning_days: int = DEFAULT_EXPIRY_WARNING_DAYS):
    now = now or dt.datetime.now(dt.UTC)
    results = [("app health", *check_health())]
    for hostname in HOSTNAMES:
        results.append((f"tls {hostname}", *check_certificate(hostname, now, warning_days)))
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--expiry-warning-days",
        type=int,
        default=DEFAULT_EXPIRY_WARNING_DAYS,
        help=f"fail when a certificate has fewer days left (default {DEFAULT_EXPIRY_WARNING_DAYS})",
    )
    parser.add_argument(
        "--allow-unreachable",
        action="store_true",
        help="exit 0 when a target is unreachable; drift still fails. For a runner "
        "that should not page anyone over a transient network fault.",
    )
    args = parser.parse_args(argv)

    results = run_all(warning_days=args.expiry_warning_days)

    drifted = [r for r in results if r[1] == DRIFT]
    unreachable = [r for r in results if r[1] == UNREACHABLE]

    for label, status, detail in results:
        marker = {OK: "ok  ", DRIFT: "DRIFT", UNREACHABLE: "????"}[status]
        print(f"[{marker}] {label}: {detail}")

    print()
    if drifted:
        print(f"DRIFT: {len(drifted)} check(s) disagree with the rules in this file.")
    if unreachable:
        print(f"UNREACHABLE: {len(unreachable)} target(s) could not be reached after retries.")
    if not drifted and not unreachable:
        print("All checks agree with the rules in this file.")

    if drifted:
        return 1
    if unreachable and not args.allow_unreachable:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
