#!/usr/bin/env bash
# Install Recap Myanmar's browser UI on Ubuntu 22.04 or 24.04.
# Safe to run again: it keeps .env, uploaded videos, and finished jobs.
#
#   sudo DOMAIN=recap.example.com WEB_PASSWORD='a-long-password' \
#        GEMINI_API_KEY='your-key' CERTBOT_EMAIL='you@email.com' \
#        bash deploy/install.sh
#
# DOMAIN is optional. Without it, the site listens only on http://127.0.0.1:8765
# and you add Nginx yourself later. See DEPLOY.md (English and Burmese).

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this with sudo:  sudo bash deploy/install.sh"
  echo "ဒီ script ကို sudo နဲ့ run ပါ။  sudo bash deploy/install.sh"
  exit 1
fi

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${APP_DIR:-/opt/recap-myanmar}"
APP_USER="${APP_USER:-recap}"
DOMAIN="${DOMAIN:-}"
WEB_PORT="${WEB_PORT:-8765}"
ENABLE_HTTPS="${ENABLE_HTTPS:-1}"
SETUP_SWAP="${SETUP_SWAP:-1}"

export DEBIAN_FRONTEND=noninteractive

echo "==> Packages (Python 3.12, FFmpeg, Nginx, Myanmar fonts)"
apt-get update
apt-get install -y ca-certificates curl git rsync ffmpeg fonts-noto-core fontconfig \
  nginx certbot python3-certbot-nginx
apt-get install -y fonts-sil-padauk || echo "Padauk font package was skipped; Noto Sans Myanmar is enough."

if ! command -v python3.12 >/dev/null 2>&1; then
  # Ubuntu 24.04 already has Python 3.12. Ubuntu 22.04 needs the deadsnakes PPA.
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
  if [[ "${VERSION_ID:-}" == "22.04" ]]; then
    apt-get install -y software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update
  fi
  apt-get install -y python3.12 python3.12-venv python3.12-dev
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR"

if [[ "$SRC" != "$APP_DIR" ]]; then
  echo "==> Copying the app to $APP_DIR"
  rsync -a --delete \
    --exclude .git \
    --exclude .venv \
    --exclude .env \
    --exclude web_data \
    --exclude jobs \
    --exclude logs \
    --exclude .cache \
    --exclude __pycache__ \
    --exclude "*.pyc" \
    "$SRC"/ "$APP_DIR"/
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Python packages and Playwright Chromium"
sudo -u "$APP_USER" python3.12 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -U pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/.cache/ms-playwright" "$APP_DIR/.cache/huggingface" \
  "$APP_DIR/web_data" "$APP_DIR/jobs" "$APP_DIR/logs"
sudo -u "$APP_USER" env PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.cache/ms-playwright" \
  "$APP_DIR/.venv/bin/python" -m playwright install chromium
"$APP_DIR/.venv/bin/python" -m playwright install-deps chromium
fc-cache -f >/dev/null || true

if [[ "$SETUP_SWAP" == "1" ]] && ! swapon --show | grep -q .; then
  echo "==> Adding 2 GB of swap (helps an 8 GB server when Whisper loads)"
  if [[ ! -f /swapfile ]]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile || true
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Settings file (.env)"
APP_DIR="$APP_DIR" WEB_PASSWORD="${WEB_PASSWORD:-}" GEMINI_API_KEY="${GEMINI_API_KEY:-}" \
  python3.12 - <<'PY'
import os
import re
import secrets
from pathlib import Path

app = Path(os.environ["APP_DIR"])
env_path = app / ".env"
example = app / ".env.example"
text = env_path.read_text(encoding="utf-8") if env_path.is_file() else example.read_text(encoding="utf-8")

def quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise SystemExit("Passwords and keys must be a single line.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

def current(key: str) -> str:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.M)
    if not match:
        return ""
    raw = match.group(1).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        raw = raw[1:-1]
    return raw

def upsert(key: str, value: str) -> None:
    global text
    line = f"{key}={quote(value)}"
    pattern = re.compile(rf"^(?:#\s*)?{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        text = pattern.sub(line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"

generated = ""
password = os.environ.get("WEB_PASSWORD", "")
if password:
    upsert("WEB_PASSWORD", password)
elif not current("WEB_PASSWORD"):
    generated = secrets.token_urlsafe(12)
    upsert("WEB_PASSWORD", generated)

gemini = os.environ.get("GEMINI_API_KEY", "")
if gemini:
    upsert("GEMINI_API_KEY", gemini)

env_path.write_text(text, encoding="utf-8")
env_path.chmod(0o600)
marker = app / ".generated-password"
if generated:
    marker.write_text(generated + "\n", encoding="utf-8")
    marker.chmod(0o600)
else:
    marker.unlink(missing_ok=True)
PY
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
if [[ -f "$APP_DIR/.generated-password" ]]; then
  chown "$APP_USER:$APP_USER" "$APP_DIR/.generated-password"
fi

echo "==> systemd service"
sed -e "s|__USER__|$APP_USER|g" -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__PORT__|$WEB_PORT|g" \
  "$APP_DIR/deploy/recap-web.service" > /etc/systemd/system/recap-web.service
systemctl daemon-reload
systemctl enable recap-web
systemctl restart recap-web

if [[ -n "$DOMAIN" ]]; then
  echo "==> Nginx for $DOMAIN"
  sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__PORT__|$WEB_PORT|g" \
    "$APP_DIR/deploy/nginx-recap.conf.template" > /etc/nginx/sites-available/recap-web
  ln -sfn /etc/nginx/sites-available/recap-web /etc/nginx/sites-enabled/recap-web
  if [[ -e /etc/nginx/sites-enabled/default ]]; then
    rm -f /etc/nginx/sites-enabled/default
  fi
  nginx -t
  systemctl reload nginx

  if [[ "$ENABLE_HTTPS" == "1" ]]; then
    echo "==> Let's Encrypt certificate"
    cert_args=(--nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect)
    if [[ -n "${CERTBOT_EMAIL:-}" ]]; then
      cert_args+=(--email "$CERTBOT_EMAIL")
    else
      cert_args+=(--register-unsafely-without-email)
      echo "CERTBOT_EMAIL was not set. Let's Encrypt will not have an address for expiry notices."
    fi
    if certbot "${cert_args[@]}"; then
      APP_DIR="$APP_DIR" python3.12 - <<'PY'
import os
import re
from pathlib import Path
path = Path(os.environ["APP_DIR"]) / ".env"
text = path.read_text(encoding="utf-8")
line = 'WEB_COOKIE_SECURE="1"'
pattern = re.compile(r"^(?:#\s*)?WEB_COOKIE_SECURE=.*$", re.M)
text = pattern.sub(line, text, count=1) if pattern.search(text) else text.rstrip() + "\n" + line + "\n"
path.write_text(text, encoding="utf-8")
PY
      chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
      systemctl restart recap-web
      echo "HTTPS is on. Open https://$DOMAIN"
    else
      echo "HTTPS is not ready yet. Point the domain's A record at this server's IP, wait a few minutes, then run:"
      echo "  sudo certbot --nginx -d $DOMAIN"
    fi
  fi
else
  echo "No DOMAIN was set, so Nginx was not configured."
  echo "The app is only on http://127.0.0.1:$WEB_PORT until you add a site. See DEPLOY.md."
fi

echo "==> Health check"
sleep 2
if curl -fsS "http://127.0.0.1:${WEB_PORT}/healthz"; then
  echo
  echo "The web app is running."
else
  echo "The service did not answer. Last log lines:"
  journalctl -u recap-web -n 40 --no-pager || true
  exit 1
fi

echo
if [[ -f "$APP_DIR/.generated-password" ]]; then
  echo "Save this login password. It was generated because WEB_PASSWORD was not set:"
  echo "  $(cat "$APP_DIR/.generated-password")"
  echo "ဒီ စကားဝှက်ကို မှတ်ထားပါ။ WEB_PASSWORD မပေးလို့ script က ဖန်တီးထားတာပါ။"
  rm -f "$APP_DIR/.generated-password"
else
  echo "Login password: the WEB_PASSWORD you passed in (it is stored in $APP_DIR/.env)."
fi
if ! APP_DIR="$APP_DIR" python3.12 - <<'PY'
import os
import re
from pathlib import Path
text = Path(os.environ["APP_DIR"], ".env").read_text(encoding="utf-8")
match = re.search(r"^GEMINI_API_KEY=(.*)$", text, re.M)
value = match.group(1).strip().strip('"').strip("'") if match else ""
raise SystemExit(0 if value else 1)
PY
then
  echo "Gemini key is still empty. Edit $APP_DIR/.env and set GEMINI_API_KEY, then: sudo systemctl restart recap-web"
  echo "Gemini key မထည့်ရသေးပါ။ .env ကို ပြင်ပြီး service ကို restart လုပ်ပါ။"
fi
echo "Logs: sudo journalctl -u recap-web -f"
echo "Done."
