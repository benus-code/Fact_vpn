#!/bin/bash
# setup_metrics_timer.sh — Installe le timer systemd pour vpn_metrics.py (toutes les 60s)

set -e
INSTALL_DIR="/opt/vpn-billing"
PYTHON="$(which python3)"

cat > /etc/systemd/system/vpn-metrics.service << EOF
[Unit]
Description=VPN peer metrics collector (one-shot)
After=network.target

[Service]
Type=oneshot
ExecStart=${PYTHON} ${INSTALL_DIR}/vpn_metrics.py
WorkingDirectory=${INSTALL_DIR}
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/vpn-metrics.timer << EOF
[Unit]
Description=Run VPN metrics collector every 60s
After=network.target

[Timer]
OnBootSec=60s
OnUnitActiveSec=60s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now vpn-metrics.timer

echo "✅ Timer vpn-metrics installé et démarré."
systemctl list-timers vpn-metrics.timer --no-pager
