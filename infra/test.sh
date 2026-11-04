#!/usr/bin/env bash
# Test Budget Buddy's roles in a throwaway LXD system container on the VM, on top of
# droplet-infra's host layer at the pin (#577):
#   launch -> host layer -> refusals -> run -> run again (changed=0)
#     -> a deploy is not drift -> verify -> a real backup into a real Postgres container
#   then delete
#
# The host layer's and backup-export's own tests live in droplet-infra.
#
#   infra/test.sh            # keeps nothing
#   KEEP=1 infra/test.sh     # leaves the container up to poke at
#
# It creates and deletes ONLY containers named bb-*. Containers, never --vm:
# the VM has no /dev/kvm. LXD must be initialised first (droplet-infra's README).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
ansible_dir=$here/ansible
venv=$here/.venv
name="bb-app-$(date +%s)"

die() { echo "infra/test.sh: $*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

delete_container() {
  [[ $1 == bb-* ]] || die "refusing to delete '$1': not a bb- container"
  lxc delete --force "$1" >/dev/null 2>&1 || true
}

command -v lxc >/dev/null || die "lxc not found; LXD is not installed"
lxc storage show default >/dev/null 2>&1 || die "LXD is not initialised; see droplet-infra's README"

# --- the control node: pinned ansible-core and collections ------------------
step "control node"
if [[ ! -x $venv/bin/ansible-playbook ]] || ! cmp -s "$here/requirements-infra.txt" "$venv/.requirements"; then
  # --clear: over an old venv, a package already installed would satisfy pip check
  # and hide a dependency this list has lost (#553).
  python3 -m venv --clear "$venv"
  "$venv/bin/pip" install -q --no-deps -r "$here/requirements-infra.txt"
  "$venv/bin/pip" check || die "requirements-infra.txt is missing a dependency; pin it there"
  cp "$here/requirements-infra.txt" "$venv/.requirements"
fi
export PATH="$venv/bin:$PATH"
"$here/fetch_host_role.sh"
cd "$ansible_dir"
ansible-galaxy collection install -r requirements.yml -p ./collections >/dev/null
ansible --version | head -1

# --- nothing private is committable ------------------------------------------
step "the real inventory is gitignored"
git -C "$here/.." check-ignore -q infra/ansible/inventory.yml \
  || die "infra/ansible/inventory.yml is NOT gitignored; the Droplet's address would be committable"
echo "ok: infra/ansible/inventory.yml is ignored"

# --- what this repo shares with droplet-infra -----------------------------------
step "the backup script archives what backup-export's configs command archives"
# Dropping a path from one is invisible: the tarball still builds and passes gzip -t.
# Only here are both files present, so only here can the two be compared.
tar_paths() { sed -nE 's/^[[:space:]]+(\/(opt|etc)\/[^[:space:]]+).*$/\1/p' "$1"; }
export_paths=$(tar_paths "$ansible_dir/vendor/droplet-infra/infra/ansible/roles/droplet_export/files/backup-export")
backup_paths=$(tar_paths "$ansible_dir/roles/budget_buddy_backup/files/budget-buddy-backup")
grep -qx '/etc/nginx/sites-enabled' <<<"$export_paths" || die "found no paths in backup-export: is the pattern still matching?"
[[ $export_paths == "$backup_paths" ]] || die "the path lists differ: backup-export has [$export_paths], budget-buddy-backup has [$backup_paths]"
echo "ok: both archive the same $(wc -l <<<"$export_paths") paths"

# --- launch ------------------------------------------------------------------
step "launch $name"
[[ ${KEEP:-} == 1 ]] || trap 'delete_container "$name"' EXIT
# </dev/null: off a terminal, lxc launch reads its config from stdin, so an open
# pipe or socket hangs it forever with no LXD operation running. Measured from a
# Claude Code session: 5 of 5 hung without it, 5 of 5 took 2s with it. The timeout
# stays so any other hang is a failure that says so.
timeout 300 lxc launch -q ubuntu:24.04 "$name" -c security.nesting=true </dev/null \
  || die "lxc launch failed or took over 5 minutes (try: lxc image list; lxc launch by hand)"
lxc exec "$name" -- cloud-init status --wait >/dev/null || true

# Docker on the VM sets the FORWARD policy to DROP, which silently blackholes
# LXD's bridge. Fail here, naming the fix, rather than in apt ten minutes later.
if ! lxc exec "$name" -- timeout 15 curl -fsS -o /dev/null https://download.docker.com/; then
  die "the container has no internet. Docker's FORWARD DROP is blocking lxdbr0; see droplet-infra's README, 'Docker and LXD on one host'"
fi

# A request to Let's Encrypt's production endpoint cannot succeed from here,
# whatever runs. The role is also checked below never to have registered.
lxc exec "$name" -- sh -c 'echo "127.0.0.1 acme-v02.api.letsencrypt.org" >> /etc/hosts'

# --- the inventory, with one declared key ------------------------------------
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"; [[ ${KEEP:-} == 1 ]] || delete_container "$name"' EXIT
ssh-keygen -q -t ed25519 -N '' -C 'bb-test declared' -f "$tmp/declared"
cat > "$tmp/inventory.yml" <<EOF
droplet:
  hosts:
    $name:
      ansible_connection: community.general.lxd
  vars:
    droplet_host_deploy_authorized_keys:
      - key: "$(cat "$tmp/declared.pub")"
        key_options: "no-agent-forwarding,no-port-forwarding,no-X11-forwarding"
EOF

# --- the host layer: droplet-infra's host.yml, at the pin -----------------------
step "the host layer"
ansible-playbook -i "$tmp/inventory.yml" vendor/droplet-infra/infra/ansible/host.yml >"$tmp/host.log" \
  || { cat "$tmp/host.log"; die "the host layer failed at the pin"; }
tail -2 "$tmp/host.log"
ct() { lxc exec "$name" -- "$@"; }

step "plant an Nginx site this repo's roles did not create"
ct sh -c '
  printf "server {\n  listen 127.0.0.1:8099;\n  return 204;\n}\n" > /etc/nginx/sites-available/bb-unmanaged
  ln -s /etc/nginx/sites-available/bb-unmanaged /etc/nginx/sites-enabled/bb-unmanaged'

# --- the app layer (#531) -------------------------------------------------------
play() { ansible-playbook -i "$tmp/inventory.yml" "$@"; }
recap_ok() { grep -qE "^$name +: ok=[0-9]+ +changed=0 +unreachable=0 +failed=0" "$1"; }
refused() { # refused <log name> <text the failure must name>
  if play app.yml >"$tmp/$1.log" 2>&1; then die "the app playbook passed: $1"; fi
  grep -qF "$2" "$tmp/$1.log" || { cat "$tmp/$1.log"; die "$1: the failure doesn't name '$2'"; }
  echo "ok: refused, named $2"
}

step "no .env: the run must fail and name the file"
refused no-env '/opt/budget-buddy/.env must exist'
ct test ! -e /opt/budget-buddy/.env || die "the role created .env"

step ".env mode 644: the run must fail and leave it alone"
ct sh -c 'printf "SECRET_KEY=test-only-not-a-secret\nTAG=0.0.1\n" > /opt/budget-buddy/.env && chown deploy:deploy /opt/budget-buddy/.env && chmod 644 /opt/budget-buddy/.env'
before=$(ct sha256sum /opt/budget-buddy/.env)
refused bad-mode '/opt/budget-buddy/.env must exist'
[[ $(ct stat -c %a /opt/budget-buddy/.env) == 644 ]] || die "the role changed .env's mode"
[[ $(ct sha256sum /opt/budget-buddy/.env) == "$before" ]] || die "the role changed .env's contents"
! grep -q 'test-only-not-a-secret' "$tmp/bad-mode.log" || die "the run printed .env's contents"
ct chmod 600 /opt/budget-buddy/.env

step "no docker-compose.yml: the run must fail and name it"
refused no-compose '/opt/budget-buddy/docker-compose.yml is missing'
ct sh -c 'echo "name: as-release-left-it" > /opt/budget-buddy/docker-compose.yml && chown deploy:deploy /opt/budget-buddy/docker-compose.yml'

step "no certificate: the run must fail and name it"
refused no-budget-cert 'live/budget.seandesmet.com/fullchain.pem is missing'
ct sh -c '
  set -e
  d=/etc/letsencrypt/live/budget.seandesmet.com && mkdir -p "$d"
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=budget.seandesmet.com" \
    -keyout "$d/privkey.pem" -out "$d/fullchain.pem" 2>/dev/null
  cp "$(find /usr/lib/python3/dist-packages -name options-ssl-nginx.conf | head -1)" /etc/letsencrypt/
  cp "$(find /usr/lib/python3/dist-packages -name ssl-dhparams.pem | head -1)" /etc/letsencrypt/'

step "no ping URL file: the run must fail and name it"
refused no-ping-url '/etc/backup-export/healthchecks-url must exist'
ct test ! -e /etc/backup-export/healthchecks-url || die "the role created the ping URL file"

step "ping URL file mode 644: the run must fail and leave it alone"
# A stand-in healthchecks.io on the container's loopback, which records each ping.
ct sh -c 'echo http://127.0.0.1:8098/hc > /etc/backup-export/healthchecks-url && chown root:deploy /etc/backup-export/healthchecks-url && chmod 644 /etc/backup-export/healthchecks-url'
before=$(ct sha256sum /etc/backup-export/healthchecks-url)
refused bad-url-mode '/etc/backup-export/healthchecks-url must exist'
[[ $(ct sha256sum /etc/backup-export/healthchecks-url) == "$before" ]] || die "the role changed the ping URL file"
! grep -q '127.0.0.1:8098' "$tmp/bad-url-mode.log" || die "the run printed the ping URL"
ct chmod 640 /etc/backup-export/healthchecks-url

step "first run of the app playbook"
play app.yml

step "second run of the app playbook (must report changed=0)"
play app.yml | tee "$tmp/app-second.log"
recap_ok "$tmp/app-second.log" || die "the second run changed something or failed; see the recap above"

step "a deploy is not drift (check mode must report changed=0)"
# As release.yml would leave them: a new compose file and a new TAG= line.
ct sh -c 'echo "name: the-next-release" > /opt/budget-buddy/docker-compose.yml && sed -i "s/^TAG=.*/TAG=0.0.2/" /opt/budget-buddy/.env'
deployed=$(ct sha256sum /opt/budget-buddy/docker-compose.yml /opt/budget-buddy/.env)
play app.yml --check --diff | tee "$tmp/app-check.log"
recap_ok "$tmp/app-check.log" || die "check mode reports a change after a deploy"
play app.yml | tee "$tmp/app-third.log"
recap_ok "$tmp/app-third.log" || die "a real run changed something after a deploy"
[[ $(ct sha256sum /opt/budget-buddy/docker-compose.yml /opt/budget-buddy/.env) == "$deployed" ]] \
  || die "the role changed a file release.yml owns"

step "verify the app layer"
[[ $(ct stat -c '%a %U:%G' /opt/budget-buddy) == '755 deploy:deploy' ]] || die "/opt/budget-buddy is not 755 deploy:deploy"
[[ $(ct stat -c '%a %U:%G' /opt/budget-buddy/sql) == '755 deploy:deploy' ]] || die "sql/ is not 755 deploy:deploy"
[[ $(ct stat -c '%a %U:%G' /opt/budget-buddy/backups) == '700 deploy:deploy' ]] || die "backups/ is not 700 deploy:deploy"
ct test ! -e /opt/budget-buddy/.git || die "the deploy directory has a git checkout"
[[ $(ct stat -c '%a %U:%G' /opt/budget-buddy/.env) == '600 deploy:deploy' ]] || die ".env changed"
[[ $(ct readlink /etc/nginx/sites-enabled/budget-buddy) == /etc/nginx/sites-available/budget-buddy ]] || die "budget-buddy is not enabled"
cmp -s <(ct cat /etc/nginx/sites-available/budget-buddy) "$ansible_dir/roles/budget_buddy_app/files/budget-buddy" || die "the budget site differs from the role's"
ct nginx -t
ct test -L /etc/nginx/sites-enabled/bb-unmanaged || die "the app roles removed a site they did not create"

# The budget site proxies to 5001, and only /transactions/import takes a large body.
ct sh -c 'mkdir -p /tmp/be && echo budget-backend > /tmp/be/index.html && cd /tmp/be &&
  nohup python3 -m http.server 5001 --bind 127.0.0.1 >/dev/null 2>&1 &'
sleep 1
budget=(--resolve budget.seandesmet.com:443:127.0.0.1 --resolve budget.seandesmet.com:80:127.0.0.1)
body=$(ct curl -sk "${budget[@]}" https://budget.seandesmet.com/)
[[ $body == budget-backend ]] || die "https://budget.seandesmet.com/ did not reach 127.0.0.1:5001 (got: $body)"
redirect=$(ct curl -s -o /dev/null -w '%{http_code} %{redirect_url}' "${budget[@]}" http://budget.seandesmet.com/x)
[[ $redirect == '301 https://budget.seandesmet.com/x' ]] || die "port 80 does not redirect to HTTPS (got: $redirect)"
ct sh -c 'head -c 2097152 /dev/zero > /tmp/2mb'
outside=$(ct curl -sk -o /dev/null -w '%{http_code}' "${budget[@]}" --data-binary @/tmp/2mb https://budget.seandesmet.com/x)
[[ $outside == 413 ]] || die "a 2 MB POST outside the import route got $outside, not Nginx's 413"
import=$(ct curl -sk -o /dev/null -w '%{http_code}' "${budget[@]}" --data-binary @/tmp/2mb https://budget.seandesmet.com/transactions/import)
[[ $import != 413 ]] || die "a 2 MB POST to /transactions/import was refused by Nginx"
echo "ok: 2 MB POST got $outside elsewhere and $import at the import route"

# --- the backups (#532) ----------------------------------------------------------
step "a rebuilt host takes backups"
cmp -s <(ct cat /usr/local/bin/budget-buddy-backup) "$ansible_dir/roles/budget_buddy_backup/files/budget-buddy-backup" || die "budget-buddy-backup differs from the role's"
[[ $(ct stat -c '%a %U:%G' /usr/local/bin/budget-buddy-backup) == '755 root:root' ]] || die "budget-buddy-backup is not 755 root:root"
[[ $(ct stat -c '%a %U:%G' /var/backups/budget-buddy) == '750 deploy:deploy' ]] || die "the backup root is not 750 deploy:deploy"
[[ $(ct systemctl is-enabled budget-buddy-backup.timer) == enabled ]] || die "the backup timer is not enabled"
[[ $(ct systemctl is-active budget-buddy-backup.timer) == active ]] || die "the backup timer is not active"

# What the scripts need and the roles don't provide: the database, under the
# container name and user production has, with the real schema in it.
lxc file push -q "$here/../sql/schema.sql" "$name/opt/budget-buddy/sql/schema.sql"
ct chown deploy:deploy /opt/budget-buddy/sql/schema.sql
ct docker run -d -q --name budget-buddy-db-1 -e POSTGRES_USER=admin -e POSTGRES_PASSWORD=test-only -e POSTGRES_DB=budget postgres:16 >/dev/null
for _ in $(seq 1 60); do
  ct docker exec budget-buddy-db-1 pg_isready -q -U admin -d budget && break
  sleep 2
done
ct docker exec budget-buddy-db-1 pg_isready -q -U admin -d budget || die "the stand-in database never came up"
ct sh -c 'docker exec -i budget-buddy-db-1 psql -q -U admin -d budget < /opt/budget-buddy/sql/schema.sql' >/dev/null 2>&1

ct sh -c 'cat > /tmp/hc.py <<PY
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        open("/tmp/pings", "a").write(self.path + "\n")
        self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 8098), H).serve_forever()
PY
nohup python3 /tmp/hc.py >/dev/null 2>&1 &'
sleep 1

ct systemctl start budget-buddy-backup.service \
  || { ct tail -20 /var/backups/budget-buddy/backup.log || true; die "budget-buddy-backup.service failed"; }
ct sh -c 'gzip -t /var/backups/budget-buddy/budget-buddy/budget_*.sql.gz' || die "no valid gzipped dump in /var/backups/budget-buddy"
dump_tables=$(ct sh -c 'gunzip -c /var/backups/budget-buddy/budget-buddy/budget_*.sql.gz')
grep -q 'CREATE TABLE public.transactions' <<<"$dump_tables" || die "the dump doesn't hold the transactions table"
archive=$(ct sh -c 'tar tzf /var/backups/budget-buddy/configs/configs_*.tar.gz')
for want in opt/budget-buddy/.env opt/budget-buddy/docker-compose.yml opt/budget-buddy/sql/schema.sql etc/nginx/sites-available/budget-buddy; do
  grep -qx "$want" <<<"$archive" || die "the configs tarball is missing $want"
done
[[ $(ct cat /tmp/pings | tr '\n' ' ') == '/hc/start /hc ' ]] || die "the pings were not start then success: $(ct cat /tmp/pings)"
echo "ok: a dump and a configs tarball landed, and healthchecks got start then success"

[[ $(ct docker ps -q | wc -l) == 1 ]] || die "expected only the stand-in database running"
ct test ! -e /etc/letsencrypt/accounts || die "an ACME account exists; something asked Let's Encrypt"

echo
echo "PASS: $name: droplet-infra's host layer at the pin, then the app roles refused bad input,"
echo "      second runs changed=0, a deploy is not drift, and a real backup landed"
if [[ ${KEEP:-} == 1 ]]; then
  echo "KEEP=1: left $name running; delete it with: lxc delete --force $name"
fi
