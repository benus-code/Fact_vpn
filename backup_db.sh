#!/bin/bash
# backup_db.sh — Sauvegarde quotidienne de la base de données VPN
# Usage : /opt/vpn-billing/backup_db.sh
# Cron  : 0 3 * * * /opt/vpn-billing/backup_db.sh >> /var/log/vpn_backup.log 2>&1

set -u

DB="/opt/vpn-billing/vpn_billing.db"
BACKUP_DIR="/opt/vpn-billing/backups"
DATE=$(date +%Y-%m-%d_%H-%M)
BACKUP_FILE="$BACKUP_DIR/vpn_billing_$DATE.db"
KEEP_DAYS=30  # Garder 30 jours de backups

# Créer le dossier si nécessaire
mkdir -p "$BACKUP_DIR"

# Vérifier que la BD existe
if [ ! -f "$DB" ]; then
    echo "[$(date)] ERREUR — base introuvable : $DB"
    exit 1
fi

# Backup propre SQLite (évite la corruption pendant écriture concurrente)
sqlite3 "$DB" ".backup '$BACKUP_FILE'"

if [ $? -eq 0 ] && [ -f "$BACKUP_FILE" ]; then
    SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
    echo "[$(date)] Backup OK — $BACKUP_FILE ($SIZE)"

    # Compresser le backup
    gzip "$BACKUP_FILE"
    echo "[$(date)] Compressé — ${BACKUP_FILE}.gz"

    # Supprimer les backups de plus de KEEP_DAYS jours
    find "$BACKUP_DIR" -name "vpn_billing_*.db.gz" -mtime +$KEEP_DAYS -delete
    COUNT=$(ls "$BACKUP_DIR"/vpn_billing_*.db.gz 2>/dev/null | wc -l)
    echo "[$(date)] Backups conservés : $COUNT"
else
    echo "[$(date)] ERREUR backup — vérifier la BD"
    exit 1
fi
