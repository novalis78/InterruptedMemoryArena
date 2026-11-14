#!/bin/bash
# Install the trip server, the model proxy and the network fence on ima-bench (run as root from /tmp/ima-deploy).
set -euo pipefail
D=/tmp/ima-deploy
install -d -m 750 -o ima -g ima /opt/ima/data /var/lib/ima/chains /var/lib/ima/keys /var/lib/ima/runs
cp -r $D/travel_db /opt/ima/data/ && cp $D/arena.json /opt/ima/data/arena.json
install -m 640 -o root -g ima $D/trip_server.py $D/llm_proxy.py /opt/ima/
chown -R ima:ima /opt/ima/data && chmod -R o-rwx /opt/ima /var/lib/ima
install -m 755 $D/trip /usr/local/bin/trip
if [ -f $D/openrouter.key ]; then install -m 640 -o root -g ima $D/openrouter.key /etc/ima/openrouter.key; shred -u $D/openrouter.key; fi
chmod 750 /etc/ima && chgrp ima /etc/ima

cat > /etc/systemd/system/ima-trip.service <<'EOF'
[Unit]
Description=IMA trip server
After=network.target
[Service]
User=ima
ExecStart=/usr/bin/python3 /opt/ima/trip_server.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/ima-proxy.service <<'EOF'
[Unit]
Description=IMA model proxy
After=network.target
[Service]
User=ima
Environment=IMA_RUN_CAP=95 IMA_CHAIN_CAP=4
ExecStart=/opt/ima/venv/bin/python /opt/ima/llm_proxy.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF
chown -R ima:ima /opt/ima/venv
systemctl daemon-reload
systemctl enable --now ima-trip ima-proxy >/dev/null 2>&1
systemctl restart ima-trip ima-proxy

# Network fence: the agent users may only talk to the trip server and the model proxy on 127.0.0.1.
iptables -N IMA_AGENTS 2>/dev/null || iptables -F IMA_AGENTS
iptables -A IMA_AGENTS -o lo -d 127.0.0.1 -p tcp -m multiport --dports 8401,8402 -j ACCEPT
iptables -A IMA_AGENTS -o lo -j ACCEPT -m comment --comment "loopback for tools agents start themselves"
iptables -A IMA_AGENTS -j REJECT
ip6tables -N IMA_AGENTS 2>/dev/null || ip6tables -F IMA_AGENTS
ip6tables -A IMA_AGENTS -o lo -j ACCEPT
ip6tables -A IMA_AGENTS -j REJECT
for i in 1 2 3 4 5 6 7 8; do
  u=$(id -u ag$i)
  iptables -C OUTPUT -m owner --uid-owner $u -j IMA_AGENTS 2>/dev/null || iptables -A OUTPUT -m owner --uid-owner $u -j IMA_AGENTS
  ip6tables -C OUTPUT -m owner --uid-owner $u -j IMA_AGENTS 2>/dev/null || ip6tables -A OUTPUT -m owner --uid-owner $u -j IMA_AGENTS
done
# Other users' processes (prompts in argv) are invisible to the agents.
mount -o remount,hidepid=2 /proc || true
grep -q hidepid /etc/fstab || echo "proc /proc proc defaults,hidepid=2 0 0" >> /etc/fstab
sleep 2; systemctl is-active ima-trip ima-proxy
