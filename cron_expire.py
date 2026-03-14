#!/usr/bin/env python3
"""
cron_expire.py — Désactive automatiquement les peers dont l'abonnement a expiré.
Ajouter dans crontab :  0 * * * * python3 /opt/vpn-billing/cron_expire.py >> /var/log/vpn_expire.log 2>&1
"""

import sqlite3
import subprocess
from datetime import date

DB_PATH   = "/opt/vpn-billing/vpn_billing.db"
CONTAINER = "amnezia-awg"

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
            ip = peer["ip_vpn"].split("/")[0]
            r1 = subprocess.run(
                ["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"],
                capture_output=True, text=True
            )
            r2 = subprocess.run(
                ["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-d", ip, "-j", "DROP"],
                capture_output=True, text=True
            )
            if r1.returncode == 0 and r2.returncode == 0:
                c.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer["id"],))
                print(f"[{now}] ✅ Désactivé : {peer['nom']} / {peer['label']} ({peer['ip_vpn']}) — expiré le {peer['date_fin']}")
            else:
                err = r1.stderr.strip() or r2.stderr.strip()
                print(f"[{now}] ⚠  Erreur iptables pour {peer['nom']} / {peer['label']} : {err}")
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
