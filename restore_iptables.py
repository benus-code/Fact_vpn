#!/usr/bin/env python3
"""
restore_iptables.py — Restaure les règles iptables DROP au démarrage.

Ce script est un alias de sync_wg_db.py au démarrage.
Il délègue directement à sync_wg_db qui fait les deux étapes :
  1. Bloque les peers actifs avec abo expiré (sécurité)
  2. Restaure les DROP manquants pour tous les peers suspendus (fiabilité)

Crontab recommandé (remplace l'ancienne ligne) :
  @reboot  sleep 15 && python3 /opt/vpn-billing/restore_iptables.py >> /var/log/vpn_restore.log 2>&1
"""

import sync_wg_db

if __name__ == "__main__":
    sync_wg_db.main()
