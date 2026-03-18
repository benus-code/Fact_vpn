#!/usr/bin/env python3
"""
import_pivpn_peers.py — Importe les clients PiVPN existants dans la DB du portail.

Lit chaque .conf dans PIVPN_CONFIGS_DIR, extrait le label (nom de fichier sans .conf)
et l'IP VPN (ligne "Address = x.x.x.x/32"), puis insère un peer vpn_type='pivpn'
en tentant de l'associer à un utilisateur existant.

Règle de matching user :
  Le label est comparé (sans le suffixe _pc/_mac) au champ 'nom' ou 'username' des users.
  Ex : "angess_pc" → cherche user dont le nom contient "angess".
  Si plusieurs matchs ou aucun → peer créé SANS user (user_id = NULL) → à assigner manuellement.

Usage :
  python3 import_pivpn_peers.py [--dry-run]
"""

import os
import re
import sqlite3
import sys
from datetime import date

DB_PATH            = "/opt/vpn-billing/vpn_billing.db"
PIVPN_CONFIGS_DIR  = "/home/benus/configs"
DRY_RUN            = "--dry-run" in sys.argv

# Suffixes à retirer pour trouver la racine du nom
REMOVE_SUFFIXES = ["_pc", "_mac", "_2", "_3", "_4"]


def parse_conf(path):
    """Retourne (ip_vpn, public_key) extraits du fichier .conf client PiVPN."""
    ip_vpn     = None
    public_key = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            m = re.match(r'^Address\s*=\s*([\d.]+)', line)
            if m:
                ip_vpn = m.group(1)
            m = re.match(r'^PublicKey\s*=\s*(\S+)', line)
            if m:
                public_key = m.group(1)
    return ip_vpn, public_key


def root_name(label):
    """Retire les suffixes courants pour obtenir la racine du prénom."""
    low = label.lower()
    for s in REMOVE_SUFFIXES:
        if low.endswith(s):
            low = low[: -len(s)]
            break
    return low


def find_user(conn, label):
    """Retourne user_id si exactement 1 user matche, sinon None."""
    root = root_name(label)
    users = conn.execute(
        "SELECT id, nom, username FROM users WHERE nom IS NOT NULL"
    ).fetchall()

    matches = []
    for u in users:
        nom_low  = (u["nom"]      or "").lower()
        user_low = (u["username"] or "").lower()
        if root in nom_low or root in user_low or nom_low in root or user_low in root:
            matches.append(u)

    if len(matches) == 1:
        return matches[0]["id"], matches[0]["nom"]
    return None, None


def main():
    if not os.path.isdir(PIVPN_CONFIGS_DIR):
        print(f"❌ Dossier introuvable : {PIVPN_CONFIGS_DIR}")
        sys.exit(1)

    conf_files = sorted(
        f for f in os.listdir(PIVPN_CONFIGS_DIR) if f.endswith(".conf")
    )
    if not conf_files:
        print(f"Aucun fichier .conf dans {PIVPN_CONFIGS_DIR}")
        sys.exit(0)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    today = date.today().isoformat()
    inserted = skipped = unmatched = 0
    unmatched_list = []

    print(f"{'[DRY-RUN] ' if DRY_RUN else ''}Fichiers .conf trouvés : {len(conf_files)}\n")

    for fname in conf_files:
        label = fname[:-5]   # retire .conf
        path  = os.path.join(PIVPN_CONFIGS_DIR, fname)

        # Vérifier si déjà en DB
        existing = conn.execute(
            "SELECT id FROM peers WHERE label = ?", (label,)
        ).fetchone()
        if existing:
            print(f"  ⏭  {label:<30s} déjà en DB (id={existing['id']})")
            skipped += 1
            continue

        ip_vpn, public_key = parse_conf(path)
        if not ip_vpn:
            print(f"  ⚠️  {label:<30s} impossible de lire l'IP dans le .conf")
            skipped += 1
            continue

        user_id, user_nom = find_user(conn, label)
        match_str = f"→ user #{user_id} ({user_nom})" if user_id else "→ ⚠️  SANS USER (à assigner)"

        print(f"  ➕  {label:<30s}  {ip_vpn:<16s}  {match_str}")

        if user_id is None:
            unmatched += 1
            unmatched_list.append(label)

        if not DRY_RUN:
            conn.execute(
                """INSERT INTO peers
                   (user_id, label, public_key, ip_vpn, actif, date_ajout, vpn_type)
                   VALUES (?, ?, ?, ?, 1, ?, 'pivpn')""",
                (user_id, label, public_key or "", ip_vpn, today),
            )
        inserted += 1

    if not DRY_RUN:
        conn.commit()
    conn.close()

    print()
    if DRY_RUN:
        print(f"[DRY-RUN] {inserted} peer(s) seraient importés, {skipped} ignorés.")
    else:
        print(f"✅ {inserted} peer(s) importés, {skipped} ignorés.")

    if unmatched_list:
        print(f"\n⚠️  {unmatched} peer(s) sans user associé — à assigner via l'admin :")
        for lbl in unmatched_list:
            print(f"   - {lbl}")
        print("\nSQL pour assigner manuellement :")
        print("  UPDATE peers SET user_id = <ID_USER> WHERE label = '<LABEL>';")


if __name__ == "__main__":
    main()
