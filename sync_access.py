#!/usr/bin/env python3
"""
sync_access.py — Cron wrapper pour la synchronisation WireGuard AllowedIPs.

Synchronise TOUS les peers BD avec le serveur WireGuard :
  - Abonnement actif   → AllowedIPs normale  (restore_peer)
  - Abonnement expiré  → AllowedIPs blackhole (suspend_peer)
  - Peer banni         → AllowedIPs blackhole (suspend_peer)

Idempotent : ne touche que les peers dont l'état a changé.

Crontab recommandé :
  */5 * * * * cd /opt/vpn-billing && /opt/vpn-billing/venv/bin/python3 sync_access.py >> /var/log/vpn_sync.log 2>&1
"""

import logging
import sys
from datetime import datetime

from vpn_access import sync_all_peers

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [sync_access] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger(__name__)


def main():
    start = datetime.now()
    logger.info("=== Démarrage sync WireGuard AllowedIPs ===")

    try:
        results = sync_all_peers()
        elapsed = (datetime.now() - start).total_seconds()
        logger.info(
            f"Terminé en {elapsed:.1f}s — "
            f"suspendu:{results['suspended']} "
            f"restauré:{results['restored']} "
            f"ignoré:{results['skipped']} "
            f"erreur:{results['errors']} "
            f"sans_clé:{results['no_key']}"
        )
        if results['errors'] > 0:
            logger.warning(f"{results['errors']} peer(s) en erreur — vérifier les logs vpn_access")
        return 0
    except Exception as e:
        logger.error(f"Erreur fatale sync_access: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
