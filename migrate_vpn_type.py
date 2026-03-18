#!/usr/bin/env python3
"""
migrate_vpn_type.py — Met à jour la colonne vpn_type des peers existants.

Règle de classification (basée sur le suffixe du label) :
  - _pc, _mac → pivpn (ordinateur)
  - tout le reste  → amnezia (smartphone) — valeur déjà par défaut

Usage : python3 /opt/vpn-billing/migrate_vpn_type.py [--dry-run]
"""

import sqlite3
import sys

DB_PATH = "/opt/vpn-billing/vpn_billing.db"

# Suffixes de label qui indiquent un pair PiVPN (PC/Mac)
PIVPN_SUFFIXES = ("_pc", "_mac")


def classify(label: str) -> str:
    label_lower = label.lower()
    for suffix in PIVPN_SUFFIXES:
        if label_lower.endswith(suffix):
            return "pivpn"
    return "amnezia"


def main():
    dry_run = "--dry-run" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    peers = conn.execute("SELECT id, label, vpn_type FROM peers").fetchall()

    if not peers:
        print("Aucun peer trouvé dans la base.")
        conn.close()
        return

    updates = []
    for p in peers:
        new_type = classify(p["label"])
        old_type = p["vpn_type"] or "amnezia"
        updates.append((p["id"], p["label"], old_type, new_type))

    # Affichage du plan
    pivpn_count  = sum(1 for _, _, _, nt in updates if nt == "pivpn")
    amnezia_count = len(updates) - pivpn_count
    print(f"{'[DRY-RUN] ' if dry_run else ''}Peers trouvés : {len(updates)}")
    print(f"  → {pivpn_count} peer(s) classés PiVPN (PC)")
    print(f"  → {amnezia_count} peer(s) classés Amnezia (mobile)\n")

    changes = [(uid, label, old, new) for uid, label, old, new in updates if old != new]
    no_changes = [(uid, label, old, new) for uid, label, old, new in updates if old == new]

    if changes:
        print(f"{'[DRY-RUN] ' if dry_run else ''}Changements à appliquer ({len(changes)}) :")
        for uid, label, old, new in changes:
            print(f"  id={uid:3d}  {label:<30s}  {old} → {new}")
    else:
        print("Aucun changement nécessaire — tous les peers sont déjà correctement classés.")

    if no_changes:
        print(f"\nDéjà corrects ({len(no_changes)}) :")
        for uid, label, old, new in no_changes:
            print(f"  id={uid:3d}  {label:<30s}  {new}")

    if dry_run:
        print("\n[DRY-RUN] Aucune modification effectuée. Relancez sans --dry-run pour appliquer.")
        conn.close()
        return

    if not changes:
        conn.close()
        return

    # Application
    for uid, label, old, new in changes:
        conn.execute("UPDATE peers SET vpn_type = ? WHERE id = ?", (new, uid))
    conn.commit()
    conn.close()
    print(f"\n✅ {len(changes)} peer(s) mis à jour avec succès.")


if __name__ == "__main__":
    main()
