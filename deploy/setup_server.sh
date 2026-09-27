#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu EC2 server (24.04 or later): Docker + Compose, and a swap
# file so building the image cannot run the 4 GB machine out of memory.
#
# Run on the server (deploy.sh copies it there):  bash ~/case_taking/deploy/setup_server.sh
set -euo pipefail

sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 docker-buildx rsync
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

# 2 GB of swap, or 1 GB on a small disk (the default 8 GB EBS volume)
SWAP_SIZE=2G
if [ "$(df --output=size -BG / | tail -1 | tr -dc 0-9)" -lt 15 ]; then SWAP_SIZE=1G; fi
if [ ! -f /swapfile ]; then
  sudo fallocate -l "$SWAP_SIZE" /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab
fi

echo
echo "Docker is installed. Log out and back in (or run: newgrp docker) before using docker without sudo."
