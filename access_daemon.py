#!/usr/bin/env python3
"""
access_daemon.py
Daemon de contrôle d'accès VPN par clé publique.

Principe :
- Source de vérité : wg show dump (IP actuelle par pubkey)
- Blocage : iptables DROP sur IP actuelle
- Sync toutes les 30 secondes
- Idempotent : ne recrée pas les règles déjà correctes
- Ne touche JAMAIS aux containers ni aux configs WireGuard
"""
import subprocess
import sqlite3
import logging
import time
import sys
import signal
from datetime import datetime
from typing import Dict, Set, Optional

# ── Configuration ──────────────────────────────────────────────────
DB_PATH     = '/opt/vpn-billing/vpn_billing.db'
SYNC_INTERVAL = 30  # secondes
LOG_FILE    = '/var/log/vpn_access_daemon.log'

CONTAINERS = [
    {'name': 'amnezia-awg',  'interface': 'wg0'},
    {'name': 'amnezia-awg2', 'interface': 'awg0'},
]

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [ACCESS-DAEMON] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ── Arrêt propre ───────────────────────────────────────────────────
running = True

def handle_signal(sig, frame):
    global running
    logger.info(f"Signal {sig} reçu — arrêt propre...")
    running = False

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ══════════════════════════════════════════════════════════════════
# PARTIE 1 : LECTURE ÉTAT WIREGUARD (source de vérité)
# ══════════════════════════════════════════════════════════════════

def build_wg_map() -> Dict[str, str]:
    """
    Retourne {pubkey: ip_actuelle} depuis wg show dump.
    Source de vérité runtime — indépendant des fichiers .conf
    """
    mapping = {}

    for container in CONTAINERS:
        try:
            result = subprocess.run(
                ['docker', 'exec', container['name'],
                 'wg', 'show', container['interface'], 'dump'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                logger.warning(
                    f"wg dump échoué sur {container['name']}: "
                    f"{result.stderr.strip()}"
                )
                continue

            lines = result.stdout.strip().split('\n')
            # Ligne 0 = interface (ignorer)
            for line in lines[1:]:
                if not line.strip():
                    continue
                parts = line.split('\t')
                # Format : pubkey preshared endpoint allowed_ips ...
                if len(parts) < 4:
                    continue
                pubkey      = parts[0].strip()
                allowed_ips = parts[3].strip()

                if allowed_ips and allowed_ips != '(none)':
                    # Extraire IP depuis "10.8.1.X/32"
                    ip = allowed_ips.split('/')[0]
                    if ip and ip != '(none)':
                        mapping[pubkey] = ip

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout wg dump sur {container['name']}")
        except Exception as e:
            logger.error(f"Erreur wg dump {container['name']}: {e}")

    logger.debug(f"wg_map: {len(mapping)} peers actifs")
    return mapping


# ══════════════════════════════════════════════════════════════════
# PARTIE 2 : LECTURE ÉTAT ABONNEMENTS (base de données)
# ══════════════════════════════════════════════════════════════════

def get_blocked_pubkeys() -> Set[str]:
    """
    Retourne l'ensemble des pubkeys qui doivent être bloquées.
    Critères : abonnement expiré OU banni OU peer inactif
    Exclut : PiVPN (10.211.76.x) — géré séparément
    """
    blocked = set()
    today = datetime.now().strftime('%Y-%m-%d')

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT DISTINCT p.public_key
            FROM peers p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN abonnements a ON a.user_id = u.id
                AND a.date_fin = (
                    SELECT MAX(date_fin) FROM abonnements
                    WHERE user_id = u.id
                )
            WHERE
                u.is_admin = 0
                AND p.public_key IS NOT NULL
                AND p.public_key != ''
                AND p.public_key NOT LIKE 'MANUAL%'
                AND p.ip_vpn NOT LIKE '10.211.76.%'
                AND (
                    u.is_banned = 1
                    OR p.actif = 0
                    OR a.date_fin IS NULL
                    OR a.date_fin < ?
                    OR a.statut != 'actif'
                )
        """, (today,)).fetchall()

        blocked = {r['public_key'] for r in rows}
        conn.close()

    except Exception as e:
        logger.error(f"Erreur lecture BD: {e}")

    logger.debug(f"Pubkeys à bloquer: {len(blocked)}")
    return blocked


def get_active_pubkeys() -> Set[str]:
    """
    Retourne l'ensemble des pubkeys qui doivent être actives.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    active = set()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT DISTINCT p.public_key
            FROM peers p
            JOIN users u ON u.id = p.user_id
            JOIN abonnements a ON a.user_id = u.id
                AND a.date_fin = (
                    SELECT MAX(date_fin) FROM abonnements
                    WHERE user_id = u.id
                )
            WHERE
                u.is_admin = 0
                AND u.is_banned = 0
                AND p.actif = 1
                AND p.public_key IS NOT NULL
                AND p.public_key NOT LIKE 'MANUAL%'
                AND p.ip_vpn NOT LIKE '10.211.76.%'
                AND a.statut = 'actif'
                AND a.date_fin >= ?
        """, (today,)).fetchall()

        active = {r['public_key'] for r in rows}
        conn.close()

    except Exception as e:
        logger.error(f"Erreur lecture BD actifs: {e}")

    return active


# ══════════════════════════════════════════════════════════════════
# PARTIE 3 : GESTION IPTABLES
# ══════════════════════════════════════════════════════════════════

def get_current_blocked_ips() -> Set[str]:
    """
    Retourne les IPs actuellement bloquées par iptables FORWARD.
    Identifie les règles posées par ce daemon via commentaire.
    """
    blocked = set()
    try:
        result = subprocess.run(
            ['iptables', '-L', 'FORWARD', '-n', '--line-numbers'],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n'):
            # Nos règles ont le commentaire "vpn-daemon"
            if 'DROP' in line and 'vpn-daemon' in line:
                parts = line.split()
                for part in parts:
                    if part.startswith('10.8.'):
                        blocked.add(part)
    except Exception as e:
        logger.error(f"Erreur lecture iptables: {e}")
    return blocked


def block_ip(ip: str) -> bool:
    """Bloque une IP avec commentaire daemon."""
    try:
        # Vérifier si la règle existe déjà
        check = subprocess.run(
            ['iptables', '-C', 'FORWARD', '-s', ip, '-j', 'DROP',
             '-m', 'comment', '--comment', 'vpn-daemon'],
            capture_output=True, timeout=5
        )
        if check.returncode == 0:
            return True  # Déjà bloquée

        # Ajouter la règle source
        subprocess.run(
            ['iptables', '-I', 'FORWARD', '-s', ip, '-j', 'DROP',
             '-m', 'comment', '--comment', 'vpn-daemon'],
            check=True, capture_output=True, timeout=5
        )
        # Ajouter la règle destination
        subprocess.run(
            ['iptables', '-I', 'FORWARD', '-d', ip, '-j', 'DROP',
             '-m', 'comment', '--comment', 'vpn-daemon'],
            check=True, capture_output=True, timeout=5
        )
        logger.info(f"BLOQUÉ: {ip}")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"iptables block failed {ip}: {e}")
        return False


def unblock_ip(ip: str) -> bool:
    """Supprime les règles DROP pour une IP."""
    try:
        for direction in ['-s', '-d']:
            # Supprimer toutes les règles DROP pour cette IP (avec commentaire)
            while True:
                result = subprocess.run(
                    ['iptables', '-D', 'FORWARD', direction, ip,
                     '-j', 'DROP',
                     '-m', 'comment', '--comment', 'vpn-daemon'],
                    capture_output=True, timeout=5
                )
                if result.returncode != 0:
                    break  # Plus de règle à supprimer
        logger.info(f"DÉBLOQUÉ: {ip}")
        return True

    except Exception as e:
        logger.error(f"iptables unblock failed {ip}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# PARTIE 4 : SYNCHRONISATION PRINCIPALE
# ══════════════════════════════════════════════════════════════════

def sync_once() -> dict:
    """
    Un cycle de synchronisation complet.
    Retourne les statistiques du cycle.
    """
    stats = {
        'blocked': 0, 'unblocked': 0,
        'already_ok': 0, 'no_ip': 0, 'errors': 0
    }

    # 1. Construire le mapping pubkey → IP actuelle
    wg_map = build_wg_map()
    if not wg_map:
        logger.warning("wg_map vide — skip ce cycle")
        return stats

    # 2. Lire l'état BD
    blocked_keys = get_blocked_pubkeys()
    active_keys  = get_active_pubkeys()

    # 3. Appliquer le blocage
    for pubkey in blocked_keys:
        ip = wg_map.get(pubkey)
        if not ip:
            stats['no_ip'] += 1
            continue
        if block_ip(ip):
            stats['blocked'] += 1
        else:
            stats['errors'] += 1

    # 4. Lever le blocage pour les actifs
    for pubkey in active_keys:
        ip = wg_map.get(pubkey)
        if not ip:
            continue
        # Vérifier si une règle DROP existe pour cette IP
        check_src = subprocess.run(
            ['iptables', '-C', 'FORWARD', '-s', ip, '-j', 'DROP',
             '-m', 'comment', '--comment', 'vpn-daemon'],
            capture_output=True, timeout=5
        )
        if check_src.returncode == 0:
            # Règle existe → la supprimer
            if unblock_ip(ip):
                stats['unblocked'] += 1
        else:
            stats['already_ok'] += 1

    return stats


# ══════════════════════════════════════════════════════════════════
# PARTIE 5 : BOUCLE PRINCIPALE
# ══════════════════════════════════════════════════════════════════

def main():
    logger.info("=" * 60)
    logger.info("VPN Access Daemon démarré")
    logger.info(f"Interval: {SYNC_INTERVAL}s | DB: {DB_PATH}")
    logger.info("=" * 60)

    cycle = 0
    while running:
        cycle += 1
        try:
            stats = sync_once()
            if stats['blocked'] > 0 or stats['unblocked'] > 0:
                logger.info(
                    f"Cycle #{cycle} — "
                    f"bloqué:{stats['blocked']} "
                    f"débloqué:{stats['unblocked']} "
                    f"ok:{stats['already_ok']} "
                    f"sans_ip:{stats['no_ip']} "
                    f"erreurs:{stats['errors']}"
                )
        except Exception as e:
            logger.error(f"Erreur cycle #{cycle}: {e}", exc_info=True)

        # Attendre avant le prochain cycle
        for _ in range(SYNC_INTERVAL):
            if not running:
                break
            time.sleep(1)

    logger.info("Daemon arrêté proprement.")


if __name__ == '__main__':
    main()
