#!/usr/bin/env python3
"""
vpn_ctl.py — Contrôle d'accès VPN en ligne de commande

Usage:
  python3 vpn_ctl.py block <user_id>
  python3 vpn_ctl.py unblock <user_id>
  python3 vpn_ctl.py status <user_id>
  python3 vpn_ctl.py sync
"""
import sys
import sqlite3
import subprocess
from access_daemon import build_wg_map, block_ip, unblock_ip, sync_once

DB = '/opt/vpn-billing/vpn_billing.db'

def get_user_peers(user_id: int) -> list:
    conn = sqlite3.connect(DB)
    peers = conn.execute(
        "SELECT public_key, ip_vpn, label FROM peers WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    conn.close()
    return peers

def cmd_block(user_id: int):
    wg_map = build_wg_map()
    peers = get_user_peers(user_id)
    for pk, ip_bd, label in peers:
        ip_real = wg_map.get(pk, ip_bd)
        if ip_real:
            ok = block_ip(ip_real)
            print(f"{'OK' if ok else 'FAIL'} — bloqué: {label} ({ip_real})")
        else:
            print(f"SKIP — pas d'IP pour {label}")

def cmd_unblock(user_id: int):
    wg_map = build_wg_map()
    peers = get_user_peers(user_id)
    for pk, ip_bd, label in peers:
        ip_real = wg_map.get(pk, ip_bd)
        if ip_real:
            ok = unblock_ip(ip_real)
            print(f"{'OK' if ok else 'FAIL'} — débloqué: {label} ({ip_real})")

def cmd_status(user_id: int):
    wg_map = build_wg_map()
    peers = get_user_peers(user_id)
    print(f"User #{user_id} — {len(peers)} peer(s):")
    for pk, ip_bd, label in peers:
        ip_real = wg_map.get(pk, '?')
        # Vérifier règle iptables
        check = subprocess.run(
            ['iptables', '-C', 'FORWARD', '-s', ip_real,
             '-j', 'DROP', '-m', 'comment', '--comment', 'vpn-daemon'],
            capture_output=True
        )
        blocked = check.returncode == 0
        status = "BLOQUÉ" if blocked else "ACTIF"
        print(f"  {label:<20} | IP BD:{ip_bd} | IP réelle:{ip_real} | {status}")

def cmd_sync():
    print("Synchronisation en cours...")
    stats = sync_once()
    print(f"Résultat: {stats}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'sync':
        cmd_sync()
    elif cmd in ('block', 'unblock', 'status') and len(sys.argv) == 3:
        uid = int(sys.argv[2])
        {'block': cmd_block, 'unblock': cmd_unblock,
         'status': cmd_status}[cmd](uid)
    else:
        print(__doc__)
        sys.exit(1)
