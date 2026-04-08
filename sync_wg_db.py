#!/usr/bin/env python3
"""
sync_wg_db.py — Audit de sécurité + restauration iptables.

RÔLE 1 — SÉCURITÉ (critique) :
  Détecte et bloque tout peer actif (actif=1) appartenant à un utilisateur
  dont l'abonnement est expiré, suspendu ou inexistant.
  Ferme la faille "nouveau peer bypasse le blocage d'un ancien peer".

RÔLE 2 — FIABILITÉ :
  Ré-applique les règles iptables DROP pour tous les peers suspendus
  dans les deux containers après un redémarrage ou perte de règles.

LANCEMENT RECOMMANDÉ (crontab -e) :
  @reboot    sleep 15 && python3 /opt/vpn-billing/sync_wg_db.py >> /var/log/vpn_sync.log 2>&1
  0 * * * *  python3 /opt/vpn-billing/sync_wg_db.py >> /var/log/vpn_sync.log 2>&1
"""

import sqlite3
import subprocess
from datetime import date, datetime

DB_PATH = "/opt/vpn-billing/vpn_billing.db"

# Les deux containers AmneziaWG
CONTAINERS = {
    'amnezia-awg':  'wg0',
    'amnezia-awg2': 'awg0',
}


# ─── Helpers iptables ─────────────────────────────────────────────────────────

def container_running(name):
    r = subprocess.run(
        ['docker', 'inspect', '-f', '{{.State.Running}}', name],
        capture_output=True, text=True
    )
    return r.stdout.strip() == 'true'


def _rule_exists(container, direction, bare_ip):
    r = subprocess.run(
        ['docker', 'exec', container, 'iptables',
         '-C', 'FORWARD', direction, bare_ip, '-j', 'DROP'],
        capture_output=True
    )
    return r.returncode == 0


def _rule_exists_host(direction, bare_ip):
    r = subprocess.run(
        ['iptables', '-C', 'FORWARD', direction, bare_ip, '-j', 'DROP'],
        capture_output=True
    )
    return r.returncode == 0


def apply_drop(container, ip_vpn):
    """
    Ajoute les règles iptables DROP (source + destination) pour l'IP donnée.
    Vérifie l'existence avant d'insérer pour éviter les doublons.
    Retourne le nombre de règles nouvellement ajoutées.
    """
    bare = ip_vpn.split('/')[0]
    added = 0
    for direction in ['-s', '-d']:
        if not _rule_exists(container, direction, bare):
            r = subprocess.run(
                ['docker', 'exec', container, 'iptables',
                 '-I', 'FORWARD', direction, bare, '-j', 'DROP'],
                capture_output=True, text=True
            )
            if r.returncode == 0:
                added += 1
    return added


def apply_drop_host(ip_vpn):
    """Même chose pour les peers PiVPN (iptables sur le host, pas docker exec)."""
    bare = ip_vpn.split('/')[0]
    added = 0
    for direction in ['-s', '-d']:
        if not _rule_exists_host(direction, bare):
            r = subprocess.run(
                ['iptables', '-I', 'FORWARD', direction, bare, '-j', 'DROP'],
                capture_output=True, text=True
            )
            if r.returncode == 0:
                added += 1
    return added


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ts    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = date.today().isoformat()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print(f"\n[{ts}] ══════════════════════════════════")
    print(f"[{ts}]  sync_wg_db.py — audit + restore  ")
    print(f"[{ts}] ══════════════════════════════════")

    # ─────────────────────────────────────────────────────────────────────────
    # ÉTAPE 1 — SÉCURITÉ
    # Trouver les peers actifs (actif=1) dont l'abonnement n'est plus valide.
    # Cas typique : admin crée un nouvel appareil pour un user expiré → l'IP
    # du nouveau peer n'a aucune règle DROP → le client passe librement.
    # ─────────────────────────────────────────────────────────────────────────
    print(f"[{ts}] Étape 1 : audit abonnements expirés...")

    leaked = conn.execute("""
        SELECT p.id, p.ip_vpn, p.label, p.container, p.vpn_type,
               u.id AS user_id, u.nom,
               a.statut AS abo_statut, a.date_fin
        FROM peers p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN abonnements a ON a.user_id = p.user_id
        WHERE p.actif = 1
          AND u.is_banned = 0
          AND (
              a.id IS NULL
              OR a.statut IN ('suspendu', 'expire')
              OR (a.statut = 'actif' AND a.date_fin IS NOT NULL AND a.date_fin < ?)
          )
    """, (today,)).fetchall()

    blocked_leaked = 0
    for peer in leaked:
        container = peer['container'] or 'amnezia-awg'
        ip        = peer['ip_vpn'] or ''
        if not ip:
            continue

        if peer['vpn_type'] == 'pivpn':
            n = apply_drop_host(ip)
        else:
            if not container_running(container):
                container = 'amnezia-awg'
            n = apply_drop(container, ip)

        conn.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer['id'],))
        raison = peer['abo_statut'] or 'sans abonnement'
        print(
            f"[{ts}] 🔒 BLOQUÉ (abo {raison}) : "
            f"{peer['nom']} / {peer['label']} ({ip}) [{container}] "
            f"— {n} règle(s) ajoutée(s)"
        )
        blocked_leaked += 1

    if blocked_leaked:
        conn.commit()
        print(f"[{ts}] ⚠  {blocked_leaked} peer(s) avec abo invalide bloqué(s) !")
    else:
        print(f"[{ts}] ✅ Aucun peer actif avec abo expiré.")

    # ─────────────────────────────────────────────────────────────────────────
    # ÉTAPE 2 — FIABILITÉ
    # Ré-appliquer les DROP manquants pour tous les peers déjà suspendus.
    # S'exécute après un redémarrage container/host où les règles sont perdues.
    # ─────────────────────────────────────────────────────────────────────────
    print(f"[{ts}] Étape 2 : restauration des DROP manquants...")

    suspended = conn.execute("""
        SELECT id, ip_vpn, label, container, vpn_type
        FROM peers WHERE actif = 0
    """).fetchall()

    restored = 0
    for peer in suspended:
        ip        = peer['ip_vpn'] or ''
        container = peer['container'] or 'amnezia-awg'
        if not ip:
            continue

        if peer['vpn_type'] == 'pivpn':
            n = apply_drop_host(ip)
        else:
            if not container_running(container):
                continue  # Container arrêté, on ne peut rien faire
            n = apply_drop(container, ip)

        if n > 0:
            print(f"[{ts}] ✅ DROP restauré : {peer['label']} ({ip}) [{container}]")
            restored += 1

    print(f"[{ts}] Étape 2 terminée — {restored} règle(s) manquante(s) restaurée(s).")
    print(f"[{ts}] ══════════════════════════════════\n")

    conn.close()


if __name__ == '__main__':
    main()
