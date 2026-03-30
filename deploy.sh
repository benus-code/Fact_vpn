#!/bin/bash
# =============================================================================
# deploy.sh — Mise à jour du VPN Billing sur le VPS
# Lancer depuis /opt/vpn-billing (en root) : bash deploy.sh
# =============================================================================
set -e

INSTALL_DIR="/opt/vpn-billing"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"   # dossier où est ce script (repo git)

echo "=== Mise à jour VPN Billing ==="
echo "    Source : $REPO_DIR"
echo "    Dest   : $INSTALL_DIR"
echo ""

# 1. Copier les fichiers Python
cp "$REPO_DIR/app.py"             "$INSTALL_DIR/app.py"
cp "$REPO_DIR/cron_expire.py"     "$INSTALL_DIR/cron_expire.py"
cp "$REPO_DIR/restore_iptables.py" "$INSTALL_DIR/restore_iptables.py"
cp "$REPO_DIR/status_bot.py"      "$INSTALL_DIR/status_bot.py"
cp "$REPO_DIR/monitoring_vpn.py"  "$INSTALL_DIR/monitoring_vpn.py"
cp "$REPO_DIR/setup_reminder_timer.sh" "$INSTALL_DIR/setup_reminder_timer.sh" 2>/dev/null || true

# 2. Copier les templates
mkdir -p "$INSTALL_DIR/templates"
cp "$REPO_DIR/templates/"*.html   "$INSTALL_DIR/templates/"

# 3. Mettre à jour les dépendances Python si requirements.txt a changé
"$INSTALL_DIR/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"

# 4. Redémarrer le service principal
systemctl restart vpn-billing

# 5. Redémarrer les services canal de statut (s'ils sont installés)
for svc in sp-status-bot sp-monitoring; do
    if systemctl is-enabled "$svc" &>/dev/null; then
        systemctl restart "$svc"
        echo "🔄 $svc redémarré"
    fi
done

# Redémarrer le timer de rappels si installé
if systemctl is-enabled renewal-reminder.timer &>/dev/null; then
    systemctl restart renewal-reminder.timer
    echo "🔄 renewal-reminder.timer redémarré"
fi

echo ""
echo "✅ Mise à jour terminée !"
echo ""
systemctl status vpn-billing --no-pager -l
