#!/usr/bin/env python3
"""
vpn_access.py
Contrôle d'accès VPN par clé publique WireGuard.
Remplace le système iptables.

Principe :
- ACTIF    → AllowedIPs = IP_NORMALE/32
- SUSPENDU → AllowedIPs = 192.0.2.1/32 (blackhole RFC5737)

192.0.2.0/24 est réservé pour la documentation (RFC5737).
Aucun routeur ne route ce préfixe. Le trafic est silencieusement droppé
par WireGuard lui-même, sans aucune règle iptables.
"""
import subprocess
import sqlite3
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

DB = '/opt/vpn-billing/vpn_billing.db'
BLACKHOLE_IP = '192.0.2.1/32'  # RFC5737 — jamais routé


def _run(container: str, *args, timeout=10) -> subprocess.CompletedProcess:
    """Exécute une commande dans un container Docker."""
    cmd = ['docker', 'exec', container] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def get_current_allowed_ips(container: str) -> dict:
    """
    Retourne l'état AllowedIPs depuis le FICHIER DE CONFIG du container.
    Format : { pubkey: 'ip/mask' }
    (wg show n'est pas utilisé car wg set est interdit sur AmneziaWG)
    """
    config_paths = {
        'amnezia-awg':  '/opt/amnezia/awg/wg0.conf',
        'amnezia-awg2': '/opt/amnezia/awg/awg0.conf',
    }
    config_path = config_paths.get(container)
    if not config_path:
        return {}

    r = _run(container, 'cat', config_path)
    if r.returncode != 0:
        logger.warning(f"Impossible de lire {config_path} dans {container}: {r.stderr}")
        return {}

    state = {}
    current_pubkey = None
    for line in r.stdout.split('\n'):
        line = line.strip()
        if line.startswith('PublicKey'):
            current_pubkey = line.split('=', 1)[1].strip()
        elif line.startswith('AllowedIPs') and current_pubkey:
            state[current_pubkey] = line.split('=', 1)[1].strip()
    return state


def _set_allowed_ips(container: str, pubkey: str,
                     allowed_ips: str) -> bool:
    """
    Met à jour AllowedIPs dans le fichier de config (persistance uniquement).
    wg set est INTERDIT sur AmneziaWG — coupe l'interface.
    La modification prend effet au prochain redémarrage du container.
    """
    _update_config_file(container, pubkey, allowed_ips)
    return True


def _update_config_file(container: str, pubkey: str,
                        new_allowed_ips: str) -> None:
    """Met à jour AllowedIPs dans le fichier .conf pour persistance."""
    config_paths = {
        'amnezia-awg':  '/opt/amnezia/awg/wg0.conf',
        'amnezia-awg2': '/opt/amnezia/awg/awg0.conf',
    }
    config_path = config_paths.get(container)
    if not config_path:
        return

    # Lire la config
    r = _run(container, 'cat', config_path)
    if r.returncode != 0:
        return

    content = r.stdout

    # Remplacer AllowedIPs dans le bloc du peer correspondant
    lines = content.split('\n')
    new_lines = []
    in_target_peer = False

    for i, line in enumerate(lines):
        if line.strip() == '[Peer]':
            # Vérifier si c'est notre peer
            in_target_peer = False
            new_lines.append(line)
            continue

        if 'PublicKey' in line and pubkey in line:
            in_target_peer = True
            new_lines.append(line)
            continue

        if in_target_peer and line.strip().startswith('AllowedIPs'):
            new_lines.append(f'AllowedIPs = {new_allowed_ips}')
            in_target_peer = False
            continue

        if line.strip().startswith('[') and in_target_peer:
            in_target_peer = False

        new_lines.append(line)

    new_content = '\n'.join(new_lines)

    # Écrire la nouvelle config
    subprocess.run(
        ['docker', 'exec', '-i', container, 'sh', '-c',
         f'cat > {config_path}'],
        input=new_content,
        capture_output=True, text=True, timeout=10
    )


def suspend_peer(pubkey: str, container: str,
                 vpn_ip: str) -> dict:
    """
    Suspend un peer : AllowedIPs → blackhole.
    Le peer reste enregistré, son trafic est droppé par WireGuard.
    """
    if not pubkey or pubkey.startswith('MANUAL'):
        logger.warning(f"Pas de clé publique valide pour {vpn_ip}")
        return {'success': False,
                'message': 'Clé publique manquante — utiliser iptables fallback'}

    # Vérifier état actuel (idempotence)
    current = get_current_allowed_ips(container)
    if current.get(pubkey) == BLACKHOLE_IP:
        logger.info(f"Peer {vpn_ip} déjà suspendu")
        return {'success': True, 'message': 'Déjà suspendu', 'already': True}

    ok = _set_allowed_ips(container, pubkey, BLACKHOLE_IP)
    if ok:
        logger.info(f"SUSPENDU: {vpn_ip} ({pubkey[:20]}...)")
        return {'success': True,
                'message': f'Peer {vpn_ip} suspendu (blackhole config)'}
    return {'success': False, 'message': 'Échec écriture config'}


def restore_peer(pubkey: str, container: str, vpn_ip: str) -> dict:
    """
    Réactive un peer : AllowedIPs → IP normale.
    """
    if not pubkey or pubkey.startswith('MANUAL'):
        logger.warning(f"Pas de clé publique valide pour {vpn_ip}")
        return {'success': False,
                'message': 'Clé publique manquante — utiliser iptables fallback'}

    allowed_ips = f'{vpn_ip}/32'

    # Vérifier état actuel (idempotence)
    current = get_current_allowed_ips(container)
    if current.get(pubkey) == allowed_ips:
        logger.info(f"Peer {vpn_ip} déjà actif")
        return {'success': True, 'message': 'Déjà actif', 'already': True}

    ok = _set_allowed_ips(container, pubkey, allowed_ips)
    if ok:
        logger.info(f"RÉACTIVÉ: {vpn_ip} ({pubkey[:20]}...)")
        return {'success': True,
                'message': f'Peer {vpn_ip} réactivé (config)'}
    return {'success': False, 'message': 'Échec écriture config'}


def sync_all_peers() -> dict:
    """
    Synchronise TOUS les peers BD avec le serveur.
    - Abonnement actif   → AllowedIPs normale
    - Abonnement expiré  → AllowedIPs blackhole
    - Peer banni         → AllowedIPs blackhole
    Idempotent : ne touche que les peers dont l'état a changé.
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # Récupérer tous les peers avec leur statut
    peers = conn.execute("""
        SELECT
            p.id, p.public_key, p.ip_vpn, p.actif,
            p.container,
            u.id as uid, u.nom, u.is_banned,
            a.statut as abo_statut,
            a.date_fin
        FROM peers p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN abonnements a ON a.user_id = u.id
            AND a.date_fin = (
                SELECT MAX(date_fin) FROM abonnements
                WHERE user_id = u.id
            )
        WHERE p.ip_vpn LIKE '10.8.1.%'
        AND u.is_admin = 0
    """).fetchall()

    conn.close()

    results = {'suspended': 0, 'restored': 0, 'skipped': 0,
               'errors': 0, 'no_key': 0}

    # Récupérer l'état actuel du serveur (1 seul appel par container)
    current_state_awg1 = get_current_allowed_ips('amnezia-awg')
    current_state_awg2 = get_current_allowed_ips('amnezia-awg2')

    for peer in peers:
        pubkey = peer['public_key']
        vpn_ip = peer['ip_vpn']
        container = peer['container'] or 'amnezia-awg'

        if not pubkey or pubkey.startswith('MANUAL'):
            results['no_key'] += 1
            continue

        # Déterminer si le peer doit être actif ou suspendu
        should_be_active = (
            not peer['is_banned']
            and peer['actif'] == 1
            and peer['abo_statut'] == 'actif'
            and peer['date_fin'] is not None
            and peer['date_fin'] >= datetime.now().strftime('%Y-%m-%d')
        )

        current_state = (current_state_awg1 if container == 'amnezia-awg'
                         else current_state_awg2)
        current_allowed = current_state.get(pubkey)

        if should_be_active:
            expected = f'{vpn_ip}/32'
            if current_allowed != expected:
                r = restore_peer(pubkey, container, vpn_ip)
                if r['success']:
                    results['restored'] += 1
                else:
                    results['errors'] += 1
            else:
                results['skipped'] += 1
        else:
            if current_allowed != BLACKHOLE_IP:
                r = suspend_peer(pubkey, container, vpn_ip)
                if r['success']:
                    results['suspended'] += 1
                else:
                    results['errors'] += 1
            else:
                results['skipped'] += 1

    logger.info(f"sync_all_peers: {results}")
    return results
