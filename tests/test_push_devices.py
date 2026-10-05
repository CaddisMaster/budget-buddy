"""#437 — the pure halves of the device list: naming a device from its
User-Agent, and describing when it was last seen.

The request-driven behaviour (subscribe, /push/seen, removal, the Home prompt's
gate) is transcribed from the issue's criteria in
tests/features/push_devices.feature. These are not criteria, so they stay here
(docs/testing.md, #357).

⚠️ The User-Agent strings are real ones, not shapes made up to fit the parser.
The Mac Safari line is the one the Droplet's Nginx log recorded for Sean's Mac
on 2026-10-05 — the request that settled which of his subscriptions was alive.
"""
from datetime import date

import pytest

from app.blueprints.auth import _seen_phrase
from app.blueprints.push import device_label

MAC_SAFARI = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/26.6.2 Safari/605.1.15")
IPHONE_SAFARI = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1")
# A home-screen web app on iOS can omit the Version/Safari tokens entirely.
IPHONE_HOME_SCREEN = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
IPHONE_CHROME = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) CriOS/140.0.7339.122 Mobile/15E148 Safari/604.1")
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
WINDOWS_EDGE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0")
ANDROID_CHROME = ("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/140.0.0.0 Mobile Safari/537.36")
LINUX_FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0"


@pytest.mark.parametrize("ua,expected", [
    (MAC_SAFARI, "Mac · Safari"),
    (IPHONE_SAFARI, "iPhone · Safari"),
    (IPHONE_HOME_SCREEN, "iPhone"),
    (IPHONE_CHROME, "iPhone · Chrome"),
    (MAC_CHROME, "Mac · Chrome"),
    (WINDOWS_EDGE, "Windows · Edge"),
    (ANDROID_CHROME, "Android · Chrome"),
    (LINUX_FIREFOX, "Linux · Firefox"),
], ids=["mac-safari", "iphone-safari", "iphone-home-screen", "iphone-chrome",
        "mac-chrome", "windows-edge", "android-chrome", "linux-firefox"])
def test_a_real_user_agent_names_its_device(ua, expected):
    assert device_label(ua) == expected


def test_the_more_specific_token_wins():
    """Every Chromium UA also says Safari/, and Edge also says Chrome/; an
    iPhone says "like Mac OS X" and Android says Linux. Order is the rule."""
    assert "Safari/" in MAC_CHROME and device_label(MAC_CHROME).endswith("Chrome")
    assert "Chrome/" in WINDOWS_EDGE and device_label(WINDOWS_EDGE).endswith("Edge")
    assert "Mac OS X" in IPHONE_SAFARI and device_label(IPHONE_SAFARI).startswith("iPhone")
    assert "Linux" in ANDROID_CHROME and device_label(ANDROID_CHROME).startswith("Android")


@pytest.mark.parametrize("ua", [None, "", "curl/8.5.0", "Safari/605.1.15"],
                         ids=["none", "empty", "curl", "browser-without-platform"])
def test_an_unrecognisable_agent_gets_no_label(ua):
    """None, and Profile renders it as "Unknown device" — a guessed platform
    would be a confident wrong answer about which device to remove."""
    assert device_label(ua) is None


def test_a_label_fits_its_column():
    """sql/39's varchar(64) refuses longer, and the label is derived from a
    header the client controls — so the bound is enforced before the write."""
    hostile = "(iPhone) " + "Safari/1 " * 200
    label = device_label(hostile)
    assert label is not None and len(label) <= 64


@pytest.mark.parametrize("seen_on,expected", [
    (None, "Not seen since tracking began"),
    (date(2026, 10, 5), "Last seen today"),
    (date(2026, 10, 4), "Last seen yesterday"),
    (date(2026, 7, 31), "Last seen Jul 31, 2026"),
], ids=["never", "today", "yesterday", "older"])
def test_last_seen_is_described_honestly(seen_on, expected):
    assert _seen_phrase(seen_on, date(2026, 10, 5)) == expected
