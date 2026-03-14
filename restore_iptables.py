#!/usr/bin/env python3
"""
restore_iptables.py — Restaure les règles iptables DROP pour les peers suspendus.

À lancer au démarrage du serveur (avant le service Flask) pour rétablir
les blocages après un redémarrage du container Docker amnezia-awg.

Exemple crontab :  @reboot python3 /opt/vpn-billing/restore_iptables.py >> /var/log/vpn_restore.log 2>&1
Exemple systemd :  ExecStartPre=/usr/bin/python3 /opt/vpn-billing/restore_iptables.py
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

    suspended = c.execute(
        "SELECT id, ip_vpn, label FROM peers WHERE actif = 0"
    ).fetchall()
    conn.close()

    if not suspended:
        print(f"[{now}] Aucun peer suspendu à restaurer.")
        return

    ok_count = 0
    for peer in suspended:
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
            ok_count += 1
            print(f"[{now}] ✅ Règle DROP restaurée : {peer['label']} ({ip})")
        else:
            err = r1.stderr.strip() or r2.stderr.strip()
            print(f"[{now}] ⚠  Erreur pour {peer['label']} ({ip}) : {err}")

    print(f"[{now}] Terminé — {ok_count}/{len(suspended)} peer(s) restauré(s).")


if __name__ == "__main__":
    main()
