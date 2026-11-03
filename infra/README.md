# infra/: Budget Buddy's layer of the shared Droplet

One server runs several apps, and no app owns it. The server itself is in
[`CaddisMaster/droplet-infra`](https://github.com/CaddisMaster/droplet-infra): the host role,
Terraform, `backup-export` and the VM's pull key, how to set up LXD, and the recorded differences
from production. Budget Buddy is a tenant, and this directory holds only what is its own.

| Role | Layer | Issue |
|---|---|---|
| `ansible/roles/budget_buddy_app/` | `/opt/budget-buddy` and its Nginx site | #531 |
| `ansible/roles/budget_buddy_backup/` | The dump script, its unit and timer. The ping URL is only checked, never written | #532 |

`seandesmet.com`'s site is in [its own repo](https://github.com/CaddisMaster/seandesmet.com).

⚠️ **A change to the host layer or to `backup-export` is made in `droplet-infra`.**
`tests/test_infra_pins.py` fails if a moved path comes back here, because two copies is the failure
this split exists to end.

## The pin

`host-role.pin` names `droplet-infra` by full commit SHA, and `fetch_host_role.sh` puts that commit
in `ansible/vendor/droplet-infra/` (gitignored). The pin is **for testing only**: this repo never
runs `host.yml` on production. Bump it in its own PR, after reading `droplet-infra`'s diff.

`backup-export`'s `budget` and `configs` commands name this app's database and paths.
`budget-buddy-backup` archives the same paths as `configs`, and `infra/test.sh` compares the two
lists, since only there are both files present. If you change one, change the other in the same
sitting and bump the pin.

## Testing in LXD (on the `jupiter` VM)

```bash
infra/test.sh           # launch → host layer → refusals → run → run again (changed=0) → a real dump → delete
KEEP=1 infra/test.sh    # leave the container up to poke at
```

⚠️ **Warn Sean before a run.** The VM is on his Mac, and each run is about 5 minutes of load.

It builds the control node from the pins (`requirements-infra.txt` and `ansible/requirements.yml`,
both checked by `tests/test_infra_pins.py`) into `infra/.venv`. It creates and deletes **only**
containers named `bb-*`. LXD's setup, and the `DOCKER-USER` rules a VM reboot clears, are in
`droplet-infra`'s README.

## Checking production

**Check mode first, always.** A real run is a separate decision for Sean, on a diff he has read.
`app.yml` has run for real once (2026-11-02, #535: `backup-export` only, which this repo no longer
installs). This runs from a **Mac terminal**: the VM can't reach production (since 2026-11-01). The
Mac keeps **no clone** of this repo, and Sean's decision on #530 (2026-10-29) was **a throwaway clone
pinned to the reviewed commit, deleted afterwards.**

Run these one at a time. `app.yml` needs no vendored host layer, and `#` isn't a comment in an
interactive zsh, so these boxes hold none. ansible-core needs Python 3.12 or newer, and the Mac's
default `python3` is older.

```bash
SHA=<the reviewed commit on main>
tmp=$(mktemp -d) && git -C "$tmp" init -q \
  && git -C "$tmp" fetch -q --depth 1 https://github.com/CaddisMaster/budget-buddy.git "$SHA" \
  && git -C "$tmp" checkout -q FETCH_HEAD && cd "$tmp/infra/ansible"
brew install python@3.12
"$(brew --prefix python@3.12)/bin/python3.12" -m venv ../.venv \
  && ../.venv/bin/pip install -q --no-deps -r ../requirements-infra.txt && ../.venv/bin/pip check
../.venv/bin/ansible-galaxy collection install -r requirements.yml -p ./collections
cp inventory.example.yml inventory.yml
nano inventory.yml
../.venv/bin/ansible-playbook app.yml --check --diff
cd ~ && rm -rf "$tmp"
```

In `nano`, the one thing to fill in is the Droplet's address. Every difference it reports gets one
of two outcomes: **fix the role**, or **record it as deliberate**. Production is never changed just
to match a role.
