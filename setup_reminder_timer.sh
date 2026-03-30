#!/bin/bash
# =============================================================================
# setup_reminder_timer.sh — Installe le timer systemd pour les rappels
#
# Lance cron_expire.py toutes les heures :
#   • Désactive les peers expirés
#   • Envoie rappels J-3 et J-1 (une seule fois chacun, flags en DB)
#
# Lancer une seule fois en root :
#   bash setup_reminder_timer.sh
# =============================================================================
set -e

INSTALL_DIR="/opt/vpn-billing"
VENV_PYTHON="$INSTALL_DIR/venv/bin/python3"

echo "=== Installation renewal-reminder timer ==="

# ─── Service ──────────────────────────────────────────────────────────────────
cat > /etc/systemd/system/renewal-reminder.service << EOF
[Unit]
Description=SP Network — Rappels expiration et désactivation peers
After=network.target

[Service]
Type=oneshot
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_PYTHON $INSTALL_DIR/cron_expire.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=renewal-reminder
EOF

echo "✅ renewal-reminder.service créé"

# ─── Timer (toutes les heures) ────────────────────────────────────────────────
cat > /etc/systemd/system/renewal-reminder.timer << EOF
[Unit]
Description=Rappels renouvellement SP Network

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo "✅ renewal-reminder.timer créé"

# ─── Désactiver l'ancien cron si présent ─────────────────────────────────────
if crontab -l 2>/dev/null | grep -q cron_expire; then
    echo "⚠️  Un cron existant pour cron_expire.py a été détecté."
    echo "   Supprimez-le manuellement avec : crontab -e"
    echo "   (ligne : 0 8 * * * python3 /opt/vpn-billing/cron_expire.py ...)"
fi

# ─── Activer et démarrer ──────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable renewal-reminder.timer
systemctl start renewal-reminder.timer

echo ""
echo "=== Timer activé ==="
systemctl status renewal-reminder.timer --no-pager -l
echo ""
echo "Prochain déclenchement :"
systemctl list-timers renewal-reminder.timer --no-pager
echo ""
echo "Pour voir les logs : journalctl -u renewal-reminder.service"
