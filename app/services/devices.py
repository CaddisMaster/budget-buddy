"""Naming a push device for Profile's device list (#437, moved here in #489).

Pure: a User-Agent in, a short label out, and an endpoint in, a fingerprint
out. The push blueprint stores the label when a device subscribes; the Profile
page in auth.py shows it, and imported it from the push blueprint until #489.
"""
import hashlib

# What Profile shows for a row with no label: every row that predates sql/39,
# and any device whose User-Agent names no platform we recognise.
UNKNOWN_DEVICE = 'Unknown device'

# Checked IN ORDER — every Chromium UA also says "Safari/", and Edge also says
# "Chrome/", so the most specific token must win. On iOS every browser is
# WebKit, but each still announces itself (CriOS, FxiOS, EdgiOS).
_BROWSERS = (
    (('Edg/', 'EdgiOS/', 'EdgA/'), 'Edge'),
    (('OPR/',), 'Opera'),
    (('SamsungBrowser/',), 'Samsung Internet'),
    (('Firefox/', 'FxiOS/'), 'Firefox'),
    (('Chrome/', 'CriOS/'), 'Chrome'),
    (('Safari/',), 'Safari'),
)
# iPhone/iPad before Macintosh: an iPhone UA says "like Mac OS X". Android
# before Linux, for the same reason. ⚠️ iPadOS reports itself as a Mac by
# default, so an iPad usually reads "Mac · Safari" — the browser's choice, not
# something a server can see through.
_PLATFORMS = (
    ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'),
    ('CrOS', 'Chromebook'), ('Macintosh', 'Mac'), ('Windows', 'Windows'),
    ('Linux', 'Linux'),
)


def device_label(user_agent):
    """A short, human name for a device — "iPhone · Safari" — or None.

    #437. Derived HERE from the request's User-Agent, never accepted from the
    client: the label is shown back on Profile, so it must not be free text a
    page script chose. None means "nothing recognisable", and Profile renders
    that as UNKNOWN_DEVICE. Bounded to sql/39's varchar(64).

    ⚠️ When no browser token is present (an installed iOS home-screen app can
    report none), the platform alone is returned rather than a guessed browser.
    """
    ua = user_agent or ''
    platform = next((name for token, name in _PLATFORMS if token in ua), None)
    if platform is None:
        return None
    browser = next((name for tokens, name in _BROWSERS
                    if any(t in ua for t in tokens)), None)
    label = f'{platform} · {browser}' if browser else platform
    return label[:64]


def endpoint_fingerprint(endpoint):
    """A short hash Profile puts on each device row, so the page can mark
    "this device" by hashing its own endpoint — without the endpoint itself
    ever being written into the HTML."""
    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]
