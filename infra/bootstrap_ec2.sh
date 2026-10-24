#!/usr/bin/env bash
# One-time bootstrap for the AWS EC2 box (Ubuntu 24.04, ap-south-1).
# Run as the default 'ubuntu' user:  bash infra/bootstrap_ec2.sh
# Security group must allow inbound 22 (your IP only), 80 and 443. No UDP needed (Plivo terminates media).
set -euo pipefail

sudo timedatectl set-timezone Asia/Kolkata

sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git ufw unattended-upgrades jq

# Docker Engine + compose plugin from Docker's official apt repo
if ! command -v docker >/dev/null; then
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

# Host firewall (defence in depth on top of the security group)
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# 4 GB swap: protects against OOM during LibreOffice renders on small instances
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "Bootstrap done. Log out/in once so the docker group applies, then: cd infra && cp .env.example .env && docker compose up -d --build"
