#!/usr/bin/env python3
"""
reconcile_peers.py
Réconcilie les clés publiques manquantes en BD avec le serveur WireGuard.
Fait correspondre par IP VPN. À exécuter UNE SEULE FOIS.
"""
import subprocess
import sqlite3
import sys
from datetime import datetime

DB = '/opt/vpn-billing/vpn_billing.db'
CONTAINER = 'amnezia-awg'
INTERFACE = 'wg0'

def get_server_peers() -> dict:
    """Retourne {ip: pubkey} depuis wg show."""
    result = subprocess.run(
        ['docker', 'exec', CONTAINER, 'wg', 'show', INTERFACE, 'allowed-ips'],
        capture_output=True, text=True, timeout=15
    )
    peers = {}
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) == 2:
            pubkey, allowed = parts
            # Extraire l'IP sans le /32
            ip = allowed.split('/')[0]
            peers[ip] = pubkey
    return peers

def reconcile():
    server_peers = get_server_peers()
    print(f"Serveur: {len(server_peers)} peers trouvés")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Récupérer les peers BD avec clé MANUAL en 10.8.1.x
    manual_peers = conn.execute("""
        SELECT p.id, p.public_key, p.ip_vpn, u.nom
        FROM peers p
        JOIN users u ON u.id = p.user_id
        WHERE p.public_key LIKE 'MANUAL%'
        AND p.ip_vpn LIKE '10.8.1.%'
    """).fetchall()

    print(f"\nPeers BD avec clé MANUAL à réconcilier: {len(manual_peers)}")
    print("=" * 70)

    updated = 0
    not_found = 0

    for peer in manual_peers:
        ip = peer['ip_vpn']
        if ip in server_peers:
            real_pubkey = server_peers[ip]
            # Mettre à jour la clé publique en BD
            conn.execute(
                "UPDATE peers SET public_key = ? WHERE id = ?",
                (real_pubkey, peer['id'])
            )
            print(f"OK  | peer_id:{peer['id']:3} | {ip:15} | "
                  f"{peer['nom']:<20} | {real_pubkey[:30]}...")
            updated += 1
        else:
            print(f"NOT FOUND | peer_id:{peer['id']:3} | "
                  f"{ip:15} | {peer['nom']}")
            not_found += 1

    # Identifier les peers sur le serveur sans entrée BD (hors 10.211.76.x)
    bd_ips = {r[0] for r in conn.execute(
        "SELECT ip_vpn FROM peers WHERE ip_vpn LIKE '10.8.1.%'"
    ).fetchall()}

    print(f"\nPeers sur le serveur sans entrée BD:")
    orphelins_serveur = []
    for ip, pubkey in sorted(server_peers.items()):
        if ip not in bd_ips and ip.startswith('10.8.1.'):
            print(f"ORPHELIN | {ip:15} | {pubkey[:40]}...")
            orphelins_serveur.append((ip, pubkey))

    conn.commit()
    conn.close()

    print(f"\n{'='*70}")
    print(f"Mis à jour : {updated}")
    print(f"Non trouvés sur serveur : {not_found}")
    print(f"Orphelins serveur (à créer manuellement) : {len(orphelins_serveur)}")

    return orphelins_serveur

if __name__ == '__main__':
    print("=== RÉCONCILIATION BD ↔ SERVEUR ===")
    print("Mode simulation (pas de modification BD)..." if '--dry-run' in sys.argv else "")
    orphelins = reconcile()
    if orphelins:
        print("\nPeers orphelins sur le serveur — associez-les manuellement:")
        for ip, pk in orphelins:
            print(f"  {ip:15} → {pk}")
