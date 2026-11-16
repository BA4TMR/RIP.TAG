#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# RIP TAG v1.1 — Ubuntu 24.04 bootstrap installer.
#
# Run from the extracted project directory, as root:
#
#   sudo PLAY_DOMAIN=play.example.com \
#        NPC_DOMAIN=npc.example.com \
#        CONSOLE_DOMAIN=console.example.com \
#        PRODUCTION_DOMAIN=production.example.com \
#        bash deploy/install_ubuntu.sh
#
# Optional:
#   APP_DIR=/opt/riptag
#   CONSOLE_SECRET=<existing secret>   (otherwise generated)
#   CLIENT_CA_BUNDLE=/path/to/client-ca.pem
#   NPC_NAME="NPC-A"                   (create an NPC token after install)
#
# This script installs OS packages, copies the app, creates the venv, writes a
# production .env, installs systemd + backup cron, and prepares the Nginx vhost.
# It does NOT issue Let's Encrypt certificates because DNS must be live first.
# The deployment guide explains the remaining certificate steps.
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root: sudo bash deploy/install_ubuntu.sh" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/riptag}"
PLAY_DOMAIN="${PLAY_DOMAIN:-}"
NPC_DOMAIN="${NPC_DOMAIN:-}"
CONSOLE_DOMAIN="${CONSOLE_DOMAIN:-}"
PRODUCTION_DOMAIN="${PRODUCTION_DOMAIN:-}"
CONSOLE_SECRET="${CONSOLE_SECRET:-}"
PRODUCTION_SECRET="${PRODUCTION_SECRET:-}"
CLIENT_CA_BUNDLE="${CLIENT_CA_BUNDLE:-}"
NPC_NAME="${NPC_NAME:-}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"

if [[ -z "$PLAY_DOMAIN" || -z "$NPC_DOMAIN" || -z "$CONSOLE_DOMAIN" || -z "$PRODUCTION_DOMAIN" ]]; then
  cat >&2 <<'EOF'
Missing domain variables. Example:

sudo PLAY_DOMAIN=play.example.com \
     NPC_DOMAIN=npc.example.com \
     CONSOLE_DOMAIN=console.example.com \
     PRODUCTION_DOMAIN=production.example.com \
     bash deploy/install_ubuntu.sh
EOF
  exit 1
fi

if [[ "$PLAY_DOMAIN" == "$NPC_DOMAIN" || "$PLAY_DOMAIN" == "$CONSOLE_DOMAIN" || "$PLAY_DOMAIN" == "$PRODUCTION_DOMAIN" || "$NPC_DOMAIN" == "$CONSOLE_DOMAIN" || "$NPC_DOMAIN" == "$PRODUCTION_DOMAIN" || "$CONSOLE_DOMAIN" == "$PRODUCTION_DOMAIN" ]]; then
  echo "The four domains must be distinct subdomains." >&2
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$SOURCE_DIR/app/main.py" ]]; then
  :
else
  echo "Run this script from the extracted RIP TAG project directory." >&2
  exit 1
fi

echo "==> Installing OS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  python3 python3-venv python3-pip \
  nginx certbot python3-certbot-nginx \
  sqlite3 curl rsync ufw openssl ca-certificates

echo "==> Creating service user and directories"
if ! id riptag >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin riptag
fi
install -d -o riptag -g riptag -m 750 "$APP_DIR"
install -d -o riptag -g riptag -m 750 "$APP_DIR/data"
install -d -m 750 /var/backups/riptag
chown riptag:riptag /var/backups/riptag

echo "==> Copying project to $APP_DIR"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
  SEED_EXCLUDE=()
  if [[ -f "$APP_DIR/data/seed.csv" ]]; then
    SEED_EXCLUDE=(--exclude 'data/seed.csv')
  fi
  rsync -a \
    "${SEED_EXCLUDE[@]}" \
    --exclude '.venv/' \
    --exclude '.git/' \
    --exclude '.env' \
    --exclude '.local-certs/' \
    --exclude '.nginx/' \
    --exclude '.tools/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'data/activity.db*' \
    --exclude 'data/_test_*' \
    --exclude 'data/_production_*' \
    --exclude 'data/_smoke*' \
    --exclude 'data/seed-group-no.csv' \
    --exclude 'output/' \
    --exclude 'backups/' \
    --exclude 'node_modules/' \
    --exclude 'deploy/certs/riptag-certs/' \
    "$SOURCE_DIR/" "$APP_DIR/"
fi
chown -R riptag:riptag "$APP_DIR"

# On a re-run, keep the existing console/production path secrets. Otherwise
# the generated Nginx locations and the running application would disagree.
if [[ -f "$APP_DIR/.env" ]]; then
  [[ -z "$CONSOLE_SECRET" ]] && CONSOLE_SECRET="$(grep '^CONSOLE_SECRET=' "$APP_DIR/.env" | cut -d= -f2- || true)"
  [[ -z "$PRODUCTION_SECRET" ]] && PRODUCTION_SECRET="$(grep '^PRODUCTION_SECRET=' "$APP_DIR/.env" | cut -d= -f2- || true)"
fi
if [[ -z "$CONSOLE_SECRET" ]]; then
  CONSOLE_SECRET="$(openssl rand -hex 24)"
fi
if [[ -z "$PRODUCTION_SECRET" ]]; then
  PRODUCTION_SECRET="$(openssl rand -hex 24)"
fi
if [[ "${#CONSOLE_SECRET}" -lt 20 || "${#PRODUCTION_SECRET}" -lt 20 ]]; then
  echo "CONSOLE_SECRET and PRODUCTION_SECRET must be at least 20 characters." >&2
  exit 1
fi
if [[ "$CONSOLE_SECRET" == "$PRODUCTION_SECRET" ]]; then
  echo "CONSOLE_SECRET and PRODUCTION_SECRET must differ." >&2
  exit 1
fi

echo "==> Writing production .env"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cat > "$APP_DIR/.env" <<EOF
APP_ENV=production
APP_NAME=撕名牌现场技术系统
PUBLIC_BASE_URL=https://${PLAY_DOMAIN}
NPC_BASE_URL=https://${NPC_DOMAIN}
DATABASE_URL=sqlite:///${APP_DIR}/data/activity.db
COOKIE_SECURE=true
SESSION_COOKIE_NAME=sm_session
SESSION_TTL_HOURS=168
CONSOLE_SECRET=${CONSOLE_SECRET}
PRODUCTION_SECRET=${PRODUCTION_SECRET}
ALLOW_INSECURE_CONSOLE=false
ALLOW_INSECURE_PRODUCTION=false
SEED_CSV=data/seed.csv
SEED_ON_STARTUP=false
SSE_HEARTBEAT_SECONDS=15
KILL_LOOKUP_LIMIT=30
KILL_LOOKUP_WINDOW_SECONDS=60
KILL_FAILURE_LIMIT=8
KILL_FAILURE_WINDOW_SECONDS=300
EOF
  chown riptag:riptag "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  echo "    generated CONSOLE_SECRET / PRODUCTION_SECRET (keep them secret)"
else
  ENV_PUBLIC="$(grep '^PUBLIC_BASE_URL=' "$APP_DIR/.env" | cut -d= -f2- || true)"
  ENV_NPC="$(grep '^NPC_BASE_URL=' "$APP_DIR/.env" | cut -d= -f2- || true)"
  if [[ "$ENV_PUBLIC" != "https://${PLAY_DOMAIN}" || "$ENV_NPC" != "https://${NPC_DOMAIN}" ]]; then
    echo "ERROR: existing .env has PUBLIC_BASE_URL=$ENV_PUBLIC / NPC_BASE_URL=$ENV_NPC" >&2
    echo "       but this run requested https://${PLAY_DOMAIN} / https://${NPC_DOMAIN}." >&2
    echo "       Edit $APP_DIR/.env (or move it away) and rerun the installer." >&2
    exit 1
  fi
  if ! grep -q '^PRODUCTION_SECRET=' "$APP_DIR/.env"; then
    printf '\nPRODUCTION_SECRET=%s\n' "$PRODUCTION_SECRET" >> "$APP_DIR/.env"
    chown riptag:riptag "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo "    added PRODUCTION_SECRET to existing .env"
  fi
  echo "    existing .env preserved"
fi

echo "==> Installing Python dependencies"
sudo -u riptag python3 -m venv "$APP_DIR/.venv"
PIP_INDEX_ARGS=()
if [[ -n "$PIP_INDEX_URL" ]]; then
  PIP_INDEX_ARGS=(--index-url "$PIP_INDEX_URL")
fi
if ! sudo -u riptag "$APP_DIR/.venv/bin/pip" install "${PIP_INDEX_ARGS[@]}" --upgrade pip; then
  echo "WARNING: pip upgrade failed; continuing with the venv pip" >&2
fi
if ! sudo -u riptag "$APP_DIR/.venv/bin/pip" install \
  "${PIP_INDEX_ARGS[@]}" -r "$APP_DIR/requirements.txt"; then
  echo "WARNING: configured pip index failed; retrying with https://pypi.org/simple" >&2
  sudo -u riptag "$APP_DIR/.venv/bin/pip" install \
    --index-url https://pypi.org/simple -r "$APP_DIR/requirements.txt"
fi

echo "==> Installing systemd unit"
sed "s#/opt/riptag#${APP_DIR}#g" "$APP_DIR/deploy/riptag.service" \
  > /etc/systemd/system/riptag.service
systemctl daemon-reload
systemctl enable --now riptag

echo "==> Installing backup job"
install -d -m 755 /var/backups/riptag
touch /var/log/riptag-backup.log
chown riptag:riptag /var/log/riptag-backup.log
sed "s#/opt/riptag#${APP_DIR}#g" "$APP_DIR/deploy/riptag-backup.cron" \
  > /etc/cron.d/riptag-backup
chmod 644 /etc/cron.d/riptag-backup

echo "==> Preparing Nginx vhost"
if [[ ! -f "$APP_DIR/deploy/nginx.conf.example" ]]; then
  echo "Nginx template missing" >&2
  exit 1
fi
sed \
  -e "s/play\.example\.com/${PLAY_DOMAIN}/g" \
  -e "s/npc\.example\.com/${NPC_DOMAIN}/g" \
  -e "s/console\.example\.com/${CONSOLE_DOMAIN}/g" \
  -e "s/production\.example\.com/${PRODUCTION_DOMAIN}/g" \
  -e "s/__CONSOLE_SECRET__/${CONSOLE_SECRET}/g" \
  -e "s/__PRODUCTION_SECRET__/${PRODUCTION_SECRET}/g" \
  "$APP_DIR/deploy/nginx.conf.example" \
  > /etc/nginx/sites-available/riptag

if [[ -n "$CLIENT_CA_BUNDLE" ]]; then
  install -d -m 755 /etc/nginx/certs
  install -m 644 "$CLIENT_CA_BUNDLE" /etc/nginx/certs/riptag-client-ca.crt
  echo "    installed client CA bundle"
fi

CERT_OK=0
CA_OK=0
[[ -f "/etc/letsencrypt/live/${PLAY_DOMAIN}/fullchain.pem" ]] && CERT_OK=1
[[ -f /etc/nginx/certs/riptag-client-ca.crt ]] && CA_OK=1
if [[ "$CERT_OK" -eq 1 && "$CA_OK" -eq 1 ]]; then
  ln -sfn /etc/nginx/sites-available/riptag /etc/nginx/sites-enabled/riptag
  nginx -t
  systemctl reload nginx
  echo "    Nginx vhost enabled"
else
  echo "    Nginx vhost written but NOT enabled yet:"
  [[ "$CERT_OK" -eq 0 ]] && echo "      missing /etc/letsencrypt/live/${PLAY_DOMAIN}/fullchain.pem"
  [[ "$CA_OK" -eq 0 ]] && echo "      missing /etc/nginx/certs/riptag-client-ca.crt"
  echo "    Follow DEPLOYMENT.md steps 4-6, then enable and reload Nginx."
fi

if [[ -n "$NPC_NAME" ]]; then
  echo "==> Creating NPC token"
  sudo -u riptag "$APP_DIR/.venv/bin/python" -m scripts.manage create-npc --name "$NPC_NAME"
fi

cat <<EOF

============================================================
Installation finished.

App directory : $APP_DIR
Service       : systemctl status riptag
Health        : curl http://127.0.0.1:8000/healthz
Console page  : https://${CONSOLE_DOMAIN}/console-${CONSOLE_SECRET}/
Production    : https://${PRODUCTION_DOMAIN}/production-${PRODUCTION_SECRET}/
Secrets       : stored in $APP_DIR/.env

Next:
  1. Point DNS A/AAAA records to this server:
       ${PLAY_DOMAIN} ${NPC_DOMAIN} ${CONSOLE_DOMAIN} ${PRODUCTION_DOMAIN}
  2. Issue HTTPS certificate (see DEPLOYMENT.md):
       certbot --nginx -d ${PLAY_DOMAIN} -d ${NPC_DOMAIN} \\
         -d ${CONSOLE_DOMAIN} -d ${PRODUCTION_DOMAIN}
  3. Install the client CA bundle (intermediate first, root last):
       /etc/nginx/certs/riptag-client-ca.crt
  4. Enable and test Nginx:
       ln -sfn /etc/nginx/sites-available/riptag /etc/nginx/sites-enabled/riptag
       nginx -t && systemctl reload nginx
  5. Open the console and complete the first-run setup wizard
       (group count / people per group); no seed CSV is required.
  6. Read DEPLOYMENT.md for acceptance and operations.

Firewall (optional):
  ufw allow 80/tcp; ufw allow 443/tcp; ufw enable
============================================================
EOF
