#!/bin/bash
# =============================================================================
# setup_status_channel.sh — Installe les services systemd pour le canal de statut
#
# Ce script crée deux services systemd :
#   • sp-status-bot   : bot Telegram qui reçoit vos commandes /statut_*
#   • sp-monitoring   : surveillance automatique du service amnezia-awg
#
# Lancer une seule fois en root depuis /opt/vpn-billing :
#   bash setup_status_channel.sh
# =============================================================================
set -e

INSTALL_DIR="/opt/vpn-billing"
VENV_PYTHON="$INSTALL_DIR/venv/bin/python3"

echo "=== Installation des services canal de statut ==="
echo ""

# ─── 1. sp-status-bot ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/sp-status-bot.service << EOF
[Unit]
Description=SP Network — Bot Telegram commandes statut
After=network.target vpn-billing.service
Wants=vpn-billing.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_PYTHON $INSTALL_DIR/status_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sp-status-bot

[Install]
WantedBy=multi-user.target
EOF

echo "✅ /etc/systemd/system/sp-status-bot.service créé"

# ─── 2. sp-monitoring ─────────────────────────────────────────────────────────
cat > /etc/systemd/system/sp-monitoring.service << EOF
[Unit]
Description=SP Network — Surveillance automatique VPN
After=network.target amnezia-awg.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_PYTHON $INSTALL_DIR/monitoring_vpn.py
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal
SyslogIdentifier=sp-monitoring

[Install]
WantedBy=multi-user.target
EOF

echo "✅ /etc/systemd/system/sp-monitoring.service créé"

# ─── 3. Activer et démarrer ───────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable sp-status-bot sp-monitoring
systemctl restart sp-status-bot sp-monitoring

echo ""
echo "=== Services démarrés ==="
echo ""
systemctl status sp-status-bot --no-pager -l
echo ""
systemctl status sp-monitoring --no-pager -l
echo ""
echo "───────────────────────────────────────────────────────────"
echo "Pour voir les logs en temps réel :"
echo "  journalctl -fu sp-status-bot"
echo "  journalctl -fu sp-monitoring"
echo ""
echo "Prérequis dans les paramètres du billing (admin) :"
echo "  • telegram_bot_token   → token du bot BotFather"
echo "  • telegram_channel_id  → @handle ou ID du canal public"
echo "  • admin_telegram_id    → votre ID Telegram personnel"
echo ""
echo "Trouvez votre ID Telegram en envoyant un message à @userinfobot"
