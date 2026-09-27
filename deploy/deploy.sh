#!/usr/bin/env bash
# Copy the app to the server and (re)start it. Run on your Mac from the project root:
#
#   deploy/deploy.sh ubuntu@<server-ip> ~/.ssh/<key>.pem            # update the code, rebuild, restart
#   deploy/deploy.sh ubuntu@<server-ip> ~/.ssh/<key>.pem --first    # first time: also server setup,
#                                                                   # API keys and DOMAIN
#
# --first copies patient-nlp/.env to the server as deploy/app.env (readable only by the
# server user) and sets DOMAIN to <server-ip with dashes>.sslip.io unless DOMAIN is set:
#   DOMAIN=intake.example.com deploy/deploy.sh ubuntu@... key.pem --first
set -euo pipefail

HOST=${1:?usage: deploy/deploy.sh user@host key.pem [--first]}
KEY=${2:?usage: deploy/deploy.sh user@host key.pem [--first]}
FIRST=${3:-}
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new)
REMOTE_DIR=case_taking

cd "$(dirname "$0")/.."

echo "==> Copying code to $HOST:~/$REMOTE_DIR"
"${SSH[@]}" "$HOST" "mkdir -p ~/$REMOTE_DIR"
if ! "${SSH[@]}" "$HOST" "command -v rsync >/dev/null"; then
  "${SSH[@]}" "$HOST" "sudo apt-get update -qq && sudo apt-get install -y -qq rsync"
fi
rsync -az --delete -e "ssh -i $KEY" \
  --exclude ".venv" --exclude "node_modules" --exclude "dist" --exclude "__pycache__" \
  --exclude ".env" --exclude "app.env" --exclude "*.log" --exclude "*.png" \
  --include "/backend/***" --include "/patient-nlp/***" --include "/frontend/***" \
  --include "/deploy/***" --include "/.dockerignore" --exclude "*" \
  ./ "$HOST:~/$REMOTE_DIR/"

if [ "$FIRST" = "--first" ]; then
  echo "==> Server setup (Docker, swap)"
  "${SSH[@]}" "$HOST" "bash ~/$REMOTE_DIR/deploy/setup_server.sh"

  echo "==> API keys -> ~/$REMOTE_DIR/deploy/app.env (chmod 600)"
  "${SSH[@]}" "$HOST" "umask 077 && cat > ~/$REMOTE_DIR/deploy/app.env" < patient-nlp/.env

  if [ -z "${DOMAIN:-}" ]; then
    IP=$("${SSH[@]}" "$HOST" "curl -s https://checkip.amazonaws.com")
    DOMAIN="${IP//./-}.sslip.io"
  fi
  echo "==> DOMAIN=$DOMAIN"
  "${SSH[@]}" "$HOST" "echo DOMAIN=$DOMAIN > ~/$REMOTE_DIR/deploy/.env"
fi

echo "==> Building and starting (first build takes a few minutes)"
"${SSH[@]}" "$HOST" "cd ~/$REMOTE_DIR/deploy && sudo docker compose up -d --build && sudo docker image prune -f >/dev/null"

DOMAIN_NOW=$("${SSH[@]}" "$HOST" "cut -d= -f2 ~/$REMOTE_DIR/deploy/.env")
echo
echo "Done: https://$DOMAIN_NOW"
echo "Logs: ssh -i $KEY $HOST 'cd ~/$REMOTE_DIR/deploy && sudo docker compose logs -f app'"
