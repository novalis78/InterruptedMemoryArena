#!/bin/bash
# One-time setup of ima-bench (run as root). Installs the agents system-wide, read-only for the agent users.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl git ripgrep xz-utils unzip jq build-essential python3-venv python3-pip iptables ca-certificates >/dev/null

# --- users: one server user, eight agent slots -------------------------------------------------------------
id ima >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/ima --shell /usr/sbin/nologin ima
for i in 1 2 3 4 5 6 7 8; do id ag$i >/dev/null 2>&1 || useradd --create-home --shell /bin/bash ag$i; chmod 700 /home/ag$i; done
install -d -m 750 -o ima -g ima /opt/ima /var/lib/ima/chains
install -d -m 700 -o root -g root /etc/ima

# --- PicoClaw (pinned) -----------------------------------------------------------------------------
PICO=v0.3.1
if [ ! -x /opt/picoclaw/picoclaw ]; then
  mkdir -p /opt/picoclaw && curl -fsSL https://github.com/sipeed/picoclaw/releases/download/$PICO/picoclaw_Linux_x86_64.tar.gz | tar -xz -C /opt/picoclaw
fi

# --- Hermes v0.23.1 (pinned), installed under /opt/hermes-home ---------------------------------------
if [ ! -x /opt/hermes-home/.local/bin/hermes ]; then
  mkdir -p /opt/hermes-home
  HOME=/opt/hermes-home bash -c 'curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup --branch v0.23.1' > /var/log/hermes-install.log 2>&1
fi

# --- server venv (aiohttp for the model proxy) -----------------------------------------------------------------
[ -x /opt/ima/venv/bin/python ] || python3 -m venv /opt/ima/venv
/opt/ima/venv/bin/pip install -q aiohttp

chmod -R a+rX /opt/picoclaw /opt/hermes-home
echo "picoclaw $(/opt/picoclaw/picoclaw version 2>/dev/null | head -1) | hermes $(HOME=/opt/hermes-home /opt/hermes-home/.local/bin/hermes --version 2>/dev/null | head -1)"
