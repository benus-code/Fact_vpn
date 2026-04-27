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

# 1. Copier les fichiers Python (seulement si repo ≠ dossier d'installation)
if [ "$REPO_DIR" != "$INSTALL_DIR" ]; then
  cp "$REPO_DIR/app.py"               "$INSTALL_DIR/app.py"
  cp "$REPO_DIR/cron_expire.py"       "$INSTALL_DIR/cron_expire.py"
  cp "$REPO_DIR/restore_iptables.py"  "$INSTALL_DIR/restore_iptables.py"
  cp "$REPO_DIR/migrate_vpn_type.py"  "$INSTALL_DIR/migrate_vpn_type.py"
  cp "$REPO_DIR/import_pivpn_peers.py" "$INSTALL_DIR/import_pivpn_peers.py"

  # 2. Copier les templates
  mkdir -p "$INSTALL_DIR/templates"
  cp "$REPO_DIR/templates/"*.html   "$INSTALL_DIR/templates/"

  # 3. Mettre à jour les dépendances Python
  "$INSTALL_DIR/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"
else
  echo "    (repo = install dir, copie ignorée)"
  "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
fi

# 4. Installer / mettre à jour le timer systemd (2x/jour)
cp "$REPO_DIR/renewal-reminder.service" /etc/systemd/system/renewal-reminder.service
cp "$REPO_DIR/renewal-reminder.timer"   /etc/systemd/system/renewal-reminder.timer
systemctl daemon-reload
systemctl enable --now renewal-reminder.timer

# 5. Supprimer l'ancienne entrée cron si elle existe
(crontab -l 2>/dev/null | grep -v "cron_expire.py") | crontab - 2>/dev/null || true

# 6. Redémarrer le service Flask
systemctl restart vpn-billing

echo ""
echo "✅ Mise à jour terminée !"
echo ""
systemctl status vpn-billing --no-pager -l
echo ""
systemctl list-timers renewal-reminder.timer --no-pager
