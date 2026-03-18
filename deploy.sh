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
cp "$REPO_DIR/app.py"              "$INSTALL_DIR/app.py"
cp "$REPO_DIR/cron_expire.py"      "$INSTALL_DIR/cron_expire.py"
cp "$REPO_DIR/restore_iptables.py" "$INSTALL_DIR/restore_iptables.py"
cp "$REPO_DIR/migrate_vpn_type.py"  "$INSTALL_DIR/migrate_vpn_type.py"
cp "$REPO_DIR/import_pivpn_peers.py" "$INSTALL_DIR/import_pivpn_peers.py"

# 2. Copier les templates
mkdir -p "$INSTALL_DIR/templates"
cp "$REPO_DIR/templates/"*.html   "$INSTALL_DIR/templates/"

# 3. Mettre à jour les dépendances Python si requirements.txt a changé
"$INSTALL_DIR/venv/bin/pip" install -q -r "$REPO_DIR/requirements.txt"

# 4. Redémarrer le service
systemctl restart vpn-billing

echo ""
echo "✅ Mise à jour terminée !"
echo ""
systemctl status vpn-billing --no-pager -l
