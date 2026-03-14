#!/usr/bin/env python3
"""
cron_expire.py — Désactive automatiquement les peers dont l'abonnement a expiré.
Ajouter dans crontab :  0 * * * * python3 /opt/vpn-billing/cron_expire.py >> /var/log/vpn_expire.log 2>&1
"""

import sqlite3
import subprocess
from datetime import date

DB_PATH      = "/opt/vpn-billing/vpn_billing.db"
CONTAINER    = "amnezia-awg"
WG_INTERFACE = "wg0"

def main():
    now = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Cherche les abonnements expirés avec des peers encore actifs
    expired = c.execute("""
        SELECT p.id, p.public_key, p.ip_vpn, p.label, u.nom, a.date_fin
        FROM peers p
        JOIN abonnements a ON a.user_id = p.user_id
        JOIN users u ON u.id = p.user_id
        WHERE p.actif = 1
          AND a.date_fin IS NOT NULL
          AND a.date_fin < ?
    """, (now,)).fetchall()

    if not expired:
        print(f"[{now}] Aucun peer à désactiver.")
        conn.close()
        return

    for peer in expired:
        try:
            result = subprocess.run(
                ["docker", "exec", CONTAINER, "wg", "set", WG_INTERFACE,
                 "peer", peer["public_key"], "remove"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                c.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer["id"],))
                print(f"[{now}] ✅ Désactivé : {peer['nom']} / {peer['label']} ({peer['ip_vpn']}) — expiré le {peer['date_fin']}")
            else:
                print(f"[{now}] ⚠  Erreur WG pour {peer['nom']} / {peer['label']} : {result.stderr.strip()}")
        except Exception as e:
            print(f"[{now}] ❌ Exception pour {peer['nom']} : {e}")

    # Met à jour le statut des abonnements expirés
    c.execute("""
        UPDATE abonnements SET statut = 'expire'
        WHERE date_fin < ? AND statut = 'actif'
    """, (now,))

    conn.commit()
    conn.close()
    print(f"[{now}] Terminé — {len(expired)} peer(s) traité(s).")

if __name__ == "__main__":
    main()
