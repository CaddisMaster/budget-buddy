---
name: verify
description: Drive Budget Buddy's real HTTP surface at localhost:5001 to verify a change end-to-end (build, throwaway login, curl the flows, clean up).
---

# Verifying Budget Buddy changes at the real surface

## Build + launch

```bash
docker compose up -d --build web    # override builds web from local source
# app: http://localhost:5001 ; dev db container must be up (it usually is)
```

## Get a logged-in session (throwaway user)

No open registration — create the user directly (bcrypt via the app's own
extension), then curl with a cookie jar. CSRF is ON in the real app.

```bash
docker compose exec -T web python -c "
from app import app, bcrypt
import app.db as db
h = bcrypt.generate_password_hash('verify-pass-123').decode()
conn = db.get_db_connection(); cur = conn.cursor()
cur.execute(\"DELETE FROM users WHERE username='__verify__'\")
cur.execute(\"INSERT INTO users (username, password_hash) VALUES ('__verify__', %s) RETURNING id\", (h,))
print(cur.fetchone()[0]); conn.commit(); cur.close(); conn.close()"

JAR=$SCRATCHPAD/cookies.txt
TOKEN=$(curl -s -c $JAR http://localhost:5001/login | grep -o 'name="csrf_token" value="[^"]*"' | sed 's/.*value="//;s/"//')
curl -s -b $JAR -c $JAR -d "username=__verify__&password=verify-pass-123&csrf_token=$TOKEN" http://localhost:5001/login
```

## ⚠️ CSRF: every request needs BOTH `-b` and `-c`

**This is the one that wastes an hour.** Use this helper and never scrape by hand:

```bash
# Scrape a form CSRF token from a page. BOTH flags, always.
tok() { curl -s -b $JAR -c $JAR "$1" \
        | grep -o 'name="csrf_token" value="[^"]*"' | head -1 | sed 's/.*value="//;s/"//'; }

T=$(tok http://localhost:5001/accounts)
curl -s -b $JAR -c $JAR -H "HX-Request: true" -d "name=x&type=Bank Account&csrf_token=$T" \
     http://localhost:5001/accounts
```

**Why `-c` on a GET that only reads?** `auth.login` calls `session.clear()`, so the
post-login session carries no CSRF raw token. The next page render mints one and
stores it in the session — i.e. **a GET can change your cookie**. With `-b` alone
curl sends the old cookie and throws the updated one away, so the token you just
scraped belongs to a session that was never saved, and the next POST is a **400**.

It bites only on the **first** scrape after login, which is what makes it
confusing: add one `-c` anywhere early and every later `-b`-only call appears to
work. Verified A/B — `-b` alone → 400, `-b -c` → 200.

**Not the problem, so don't "fix" it:** a raw space in `-d` (`type=Bank Account`)
is handled fine — measured 200. If you see a 400, it is the cookie jar, not the
encoding. `--data-urlencode` is harmless but changes nothing.

### HTMX endpoints with no form on the page

`/ask`, `/insights/generate` etc. take the token from the `hx-headers` attribute
on `<body>`. **Scrape it from `/`, not `/dashboard`** — `/dashboard` is a 302 stub
that redirects to `/`, so the body is empty and the token comes back blank:

```bash
TOKEN=$(curl -s -b $JAR -c $JAR http://localhost:5001/ | grep -o 'X-CSRFToken[": ]*[^"}]*' | head -1 | sed 's/.*: *//' | tr -d '"')
curl -s -b $JAR -c $JAR -H "HX-Request: true" -H "X-CSRFToken: $TOKEN" -d "..." http://localhost:5001/<route>
```

Send `HX-Request: true` to get the fragment (row partial) instead of a redirect.

## Useful flows

- Account create: POST `/accounts` (`name`, `type` = exact `Credit Card|Debit Card|Bank Account`, `credit_limit`, `apr`) → returns the `_account_row` fragment under HTMX.
- Transaction: POST `/transactions/new` (`amount`, `description`, `transaction_date`, `account_id`, `transaction_type`) → 302.
- Schedule: POST `/scheduled` (`transaction_type`, `amount`, `account_id`, `frequency`, `next_due`, optional `end_date`) → row fragment under HTMX.
- Recurring transfers need **two** accounts — the whole `/transfers` UI is hidden below that, so a one-account user sees no form.
- Scheduled jobs: `docker compose exec -T web flask run-daily` (materialize + bill reminders), `flask send-digests` (weekly email).
- Live AI (real key in `.env`, calls are cheap Haiku): `/ask` with `question=`, `/insights/generate` with `year`+`month`.

## Clean up (FK-safe: children first)

Mirrors `tests/conftest.py::_delete_user`, which is the proven order — keep them
in step. **`schedules`/`transfer_schedules` are `ON DELETE RESTRICT` against
`account`**, so skipping them makes the `account` delete fail.

```bash
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "
DO \$\$
DECLARE uid int;
BEGIN
  SELECT id INTO uid FROM users WHERE username = '"'"'__verify__'"'"';
  IF uid IS NULL THEN RETURN; END IF;
  DELETE FROM transactions        WHERE user_id = uid;
  DELETE FROM budget_history      WHERE user_id = uid;
  DELETE FROM budgets             WHERE user_id = uid;
  DELETE FROM goals               WHERE user_id = uid;
  DELETE FROM schedules           WHERE user_id = uid;
  DELETE FROM transfer_schedules  WHERE user_id = uid;
  DELETE FROM insights            WHERE user_id = uid;
  DELETE FROM forecasts           WHERE user_id = uid;
  DELETE FROM goal_coach          WHERE user_id = uid;
  DELETE FROM agent_runs          WHERE user_id = uid;
  DELETE FROM push_subscriptions  WHERE user_id = uid;
  DELETE FROM reminder_log        WHERE user_id = uid;
  DELETE FROM categories          WHERE user_id = uid;
  DELETE FROM account             WHERE user_id = uid;
  DELETE FROM users               WHERE id = uid;
END \$\$;"'
```

Verify it actually went: `SELECT count(*) FROM users WHERE username='__verify__'` → 0.

Don't touch the `sean` account's ~6-month demo data — it's intentional.
New `sql/` migration in the diff? Apply it to the dev DB first:
`docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/NN_x.sql`

⚠️ **Never run this skill against production** — it creates a database user. To
exercise the prod auth path read-only, scrape the CSRF token from `/login` and
POST deliberately wrong credentials: that traverses templates → session → CSRF →
DB → bcrypt and writes nothing.
