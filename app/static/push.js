/* Web Push on this device — Profile's toggle and Home's prompt (#33, #437).
 *
 * The subscription lives in the BROWSER, so only this script can know whether
 * the device in your hand is subscribed; the server only knows which devices
 * have registered. Everything it needs arrives as data on #push-config
 * (partials/_push_config.html), because the CSP (#403) refuses inline script.
 *
 * Three rules, each load-bearing:
 *
 * 1. NOTHING SUBSCRIBES WITHOUT A TAP. "Turn off on this device" leaves the
 *    browser's permission at "granted", so a page that re-subscribed whenever
 *    it found permission but no subscription would undo an opt-out silently.
 *    Permission must be asked from a real user gesture anyway.
 *
 * 2. HOME ONLY TOUCHES. A live subscription is reported to /push/seen, which
 *    refreshes the row and creates nothing. If the server answers
 *    known:false, the device was removed (from Profile) or forgotten, and
 *    the prompt asks — it never re-registers on its own.
 *
 * 3. A localStorage flag records "not on this device". It is set by Profile's
 *    "Turn off" and by the prompt's "Not on this device", and cleared by any
 *    "Turn on". If whatever wiped a subscription also wiped the flag, the cost
 *    is one extra prompt, never an undone opt-out.
 *
 * iOS delivers push only to an app added to the home screen; in a Safari tab
 * PushManager does not exist, and both surfaces stand down.
 */
(() => {
  const cfg = document.getElementById('push-config');
  if (!cfg) return;

  const OPT_OUT = 'bb-push-optout';
  const optedOut = {
    get() { try { return localStorage.getItem(OPT_OUT) === '1'; } catch (e) { return false; } },
    set(on) {
      try { on ? localStorage.setItem(OPT_OUT, '1') : localStorage.removeItem(OPT_OUT); }
      catch (e) { /* storage blocked: the prompt may ask again, which is safe */ }
    }
  };

  const supported = 'serviceWorker' in navigator && 'PushManager' in window;
  const CSRF = JSON.parse(document.body.getAttribute('hx-headers'))['X-CSRFToken'];

  const post = (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
    body: JSON.stringify(body)
  }).then((r) => r.json());

  /* The VAPID public key travels as base64url; the subscribe API wants bytes. */
  const toBytes = (base64) => {
    const pad = '='.repeat((4 - base64.length % 4) % 4);
    const raw = atob((base64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
  };

  const currentSubscription = () =>
    navigator.serviceWorker.ready.then((reg) => reg.pushManager.getSubscription());

  /* Does the server know this browser's subscription? Touches it if so. */
  const knownToServer = async (sub) => {
    if (!sub) return false;
    const res = await post(cfg.dataset.seenUrl, { endpoint: sub.endpoint });
    return !!(res && res.known);
  };

  /* Only ever called from a click. Reuses a subscription the browser still
   * holds (the server had merely forgotten it), else asks for a new one. */
  const turnOn = async () => {
    if (await Notification.requestPermission() !== 'granted') {
      throw new Error('Permission denied.');
    }
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    const fresh = !sub;
    if (fresh) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: toBytes(cfg.dataset.key)
      });
    }
    const res = await post(cfg.dataset.subscribeUrl, sub.toJSON());
    if (!res.ok) {
      if (fresh) await sub.unsubscribe();
      throw new Error(res.error || 'failed');
    }
    optedOut.set(false);
    return sub;
  };

  const turnOff = async (sub) => {
    optedOut.set(true);
    if (!sub) return;
    await post(cfg.dataset.unsubscribeUrl, { endpoint: sub.endpoint });
    await sub.unsubscribe();
  };

  /* Profile marks its own row: hash the endpoint the way the server did
   * (push.endpoint_fingerprint) so the endpoint never has to be in the HTML. */
  const fingerprint = async (endpoint) => {
    if (!(window.crypto && crypto.subtle)) return null;
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(endpoint));
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0'))
      .join('').slice(0, 16);
  };

  const markThisDevice = async (sub) => {
    document.querySelectorAll('.push-device-this').forEach((el) => { el.hidden = true; });
    document.querySelectorAll('.push-device form').forEach((el) => { el.hidden = false; });
    if (!sub) return;
    const fp = await fingerprint(sub.endpoint);
    const row = fp && document.querySelector(`.push-device[data-fingerprint="${fp}"]`);
    if (!row) return;
    row.querySelector('.push-device-this').hidden = false;
    row.querySelector('form').hidden = true;
  };

  /* ── Profile: the per-device toggle ───────────────────────────────────── */
  const btn = document.getElementById('push-toggle');
  if (btn) {
    const status = document.getElementById('push-status');
    let current = null;  // a subscription the SERVER knows about, or null

    const render = () => {
      btn.disabled = false;
      btn.textContent = current ? 'Turn off on this device' : 'Turn on for this device';
      status.textContent = current
        ? 'This device will get notifications.'
        : (Notification.permission === 'denied'
            ? 'Notifications are blocked for this site in your browser settings.'
            : '');
    };

    if (!supported) {
      btn.textContent = 'Not supported';
      status.textContent = 'This browser cannot receive push notifications. ' +
        'On iPhone, add Budget Buddy to your home screen first.';
    } else {
      currentSubscription()
        .then(async (sub) => {
          current = (await knownToServer(sub)) ? sub : null;
          await markThisDevice(current);
          render();
        })
        .catch(() => { btn.textContent = 'Unavailable'; status.textContent = ''; });

      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          if (current) {
            await turnOff(current);
            current = null;
          } else {
            current = await turnOn();
          }
          await markThisDevice(current);
          render();
        } catch (e) {
          status.textContent = 'Could not change the setting: ' + e.message;
          btn.disabled = false;
        }
      });
    }
  }

  /* ── Home: the prompt for a device that has fallen off ────────────────── */
  const nudge = document.getElementById('push-nudge');
  if (nudge && supported) {
    const status = nudge.querySelector('[data-push-status]');
    let held = null;

    currentSubscription()
      .then(async (sub) => {
        held = sub;
        if (await knownToServer(sub)) return;          // healthy: say nothing
        if (optedOut.get()) return;                     // the user said no here
        if (Notification.permission === 'denied') return;
        nudge.hidden = false;
      })
      .catch(() => { /* no service worker, no prompt */ });

    const onBtn = nudge.querySelector('[data-push-on]');
    onBtn.addEventListener('click', async () => {
      onBtn.disabled = true;
      try {
        await turnOn();
        nudge.querySelectorAll('button').forEach((b) => { b.hidden = true; });
        status.textContent = 'Notifications are on for this device.';
      } catch (err) {
        status.textContent = 'Could not turn them on: ' + err.message;
        onBtn.disabled = false;
      }
    });

    nudge.querySelector('[data-push-decline]').addEventListener('click', async () => {
      // The server does not know `held` (or the prompt would not be showing),
      // so only the browser's copy needs dropping.
      optedOut.set(true);
      nudge.hidden = true;
      if (held) {
        try { await held.unsubscribe(); } catch (err) { /* already gone */ }
      }
    });
  }
})();
