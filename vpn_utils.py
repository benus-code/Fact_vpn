#!/usr/bin/env python3
"""
vpn_utils.py — Utilitaires VPN centralisés
Supporte AWG 1.x (amnezia-awg / wg0) et AWG 2.0 (amnezia-awg2 / awg0)

RÈGLES ABSOLUES :
- wg show (pas awg show — absent du container)
- Jamais wg set / wg syncconf → coupe l'interface AmneziaWG
- Keepalive = écriture fichier uniquement, actif après docker restart
"""

import re
import sqlite3
import subprocess
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "/opt/vpn-billing/vpn_billing.db"

# ─── Configuration des containers ─────────────────────────────────────────────

CONTAINERS = {
    'amnezia-awg': {
        'container':   'amnezia-awg',
        'interface':   'wg0',
        'config_path': '/opt/amnezia/awg/wg0.conf',
        'label':       'AWG 1.x',
        'badge_class': 'awg1',
        'port':        34882,
    },
    'amnezia-awg2': {
        'container':   'amnezia-awg2',
        'interface':   'awg0',
        'config_path': '/opt/amnezia/awg/awg0.conf',
        'label':       'AWG 2.0',
        'badge_class': 'awg2',
        'port':        36071,
    },
}

DEFAULT_CONTAINER = 'amnezia-awg'


def get_container_cfg(container_name: str) -> dict:
    return CONTAINERS.get(container_name, CONTAINERS[DEFAULT_CONTAINER])


# ─── Docker helpers ────────────────────────────────────────────────────────────

def docker_run(container: str, *args, timeout=15) -> subprocess.CompletedProcess:
    """Exécute une commande dans un container Docker."""
    return subprocess.run(
        ['docker', 'exec', container] + list(args),
        capture_output=True, text=True, timeout=timeout
    )


def docker_run_stdin(container: str, cmd_str: str,
                     stdin_data: str, timeout=10) -> subprocess.CompletedProcess:
    """Exécute une commande sh -c avec données sur stdin."""
    return subprocess.run(
        ['docker', 'exec', '-i', container, 'sh', '-c', cmd_str],
        input=stdin_data, capture_output=True, text=True, timeout=timeout
    )


def container_is_running(container: str) -> bool:
    """Vérifie si un container Docker tourne."""
    try:
        r = subprocess.run(
            ['docker', 'inspect', '--format={{.State.Status}}', container],
            capture_output=True, text=True, timeout=5
        )
        return r.returncode == 0 and r.stdout.strip() == 'running'
    except Exception:
        return False


# ─── Parsing wg show ──────────────────────────────────────────────────────────

def _parse_hs_age(hs_str: str) -> int:
    """'2 minutes, 30 seconds ago' → âge en secondes."""
    total = 0
    for val, unit in re.findall(r'(\d+)\s+(second|minute|hour|day|week)s?', hs_str):
        val = int(val)
        if   'second' in unit: total += val
        elif 'minute' in unit: total += val * 60
        elif 'hour'   in unit: total += val * 3600
        elif 'day'    in unit: total += val * 86400
        elif 'week'   in unit: total += val * 604800
    return total


def _parse_transfer_bytes(s: str) -> int:
    """'1.23 MiB' → bytes."""
    m = re.match(r'([\d.]+)\s*(B|KiB|MiB|GiB|TiB)', s.strip())
    if not m:
        return 0
    v    = float(m.group(1))
    mult = {'B': 1, 'KiB': 1024, 'MiB': 1024**2,
            'GiB': 1024**3, 'TiB': 1024**4}
    return int(v * mult.get(m.group(2), 1))


def parse_wg_show(output: str) -> dict:
    """
    Parse la sortie lisible de 'wg show'.
    Retourne un dict keyed par bare IP : {
        public_key, handshake_ts, handshake_age_s,
        rx, tx, keepalive, endpoint
    }
    """
    by_ip  = {}
    cur    = {}
    now_ts = int(datetime.utcnow().timestamp())

    for line in output.splitlines():
        s = line.strip()
        if s.startswith('peer:'):
            if cur.get('ip'):
                by_ip[cur['ip']] = cur
            cur = {'public_key': s.split(':', 1)[1].strip()}

        elif cur:
            if s.startswith('allowed ips:'):
                raw = s.split(':', 1)[1].strip().split(',')[0].split('/')[0].strip()
                cur['ip'] = raw

            elif s.startswith('latest handshake:'):
                hs_str = s.split(':', 1)[1].strip()
                age    = _parse_hs_age(hs_str)
                cur['handshake_age_s'] = age
                cur['handshake_ts']    = now_ts - age if age else 0

            elif s.startswith('transfer:'):
                seg = s.split(':', 1)[1]
                rx = tx = 0
                for part in seg.split(','):
                    part = part.strip()
                    if 'received' in part:
                        rx = _parse_transfer_bytes(part.replace('received', '').strip())
                    elif 'sent' in part:
                        tx = _parse_transfer_bytes(part.replace('sent', '').strip())
                cur['rx'] = rx
                cur['tx'] = tx

            elif s.startswith('endpoint:'):
                cur['endpoint'] = s.split(':', 1)[1].strip()

            elif s.startswith('persistent keepalive:'):
                m = re.search(r'every (\d+)', s)
                cur['keepalive'] = int(m.group(1)) if m else 0

    if cur.get('ip'):
        by_ip[cur['ip']] = cur

    return by_ip


# ─── Statut de tous les peers ──────────────────────────────────────────────────

def get_peers_status(container_name: str) -> dict:
    """
    Retourne le statut live de tous les peers d'un container.
    Clé = bare IP. Silencieux si container absent.
    """
    if not container_is_running(container_name):
        return {}
    try:
        r = docker_run(container_name, 'wg', 'show', timeout=10)
        if r.returncode != 0:
            return {}
        return parse_wg_show(r.stdout)
    except Exception as e:
        logger.warning(f"[vpn_utils] get_peers_status({container_name}): {e}")
        return {}


def get_all_peers_status() -> dict:
    """
    Statut live de TOUS les peers des deux containers.
    Clé = bare IP. Inclut 'container' dans chaque entrée.
    """
    result = {}
    for cname in CONTAINERS:
        data = get_peers_status(cname)
        for ip, info in data.items():
            info['container'] = cname
            result[ip] = info
    return result


# ─── Formatage ────────────────────────────────────────────────────────────────

def format_bytes(b: int) -> str:
    """1536000 → '1.5 Mo'"""
    if b is None:
        return '—'
    for unit in ['o', 'Ko', 'Mo', 'Go', 'To']:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} Po"


def peer_status_label(handshake_ts: int) -> dict:
    """
    Retourne label + classe CSS selon l'âge du handshake.
    dot_class : 'green' | 'amber' | 'red' | 'gray'
    """
    if not handshake_ts:
        return {'label': 'Jamais connecté', 'dot': 'gray',  'badge': 'secondary'}
    age = int(datetime.utcnow().timestamp()) - handshake_ts
    if age < 180:
        return {'label': 'Connecté',  'dot': 'green', 'badge': 'success'}
    elif age < 1800:
        return {'label': 'Inactif',   'dot': 'amber', 'badge': 'warning'}
    else:
        return {'label': 'Hors ligne','dot': 'red',   'badge': 'danger'}


def format_age(seconds: int) -> str:
    """3750 → '1h02'  /  45 → '45s'  /  None → '—'"""
    if seconds is None:
        return '—'
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    h   = seconds // 3600
    m   = (seconds % 3600) // 60
    return f"{h}h{m:02d}"


# ─── Keepalive — écriture fichier uniquement ─────────────────────────────────
#
# IMPORTANT : wg syncconf / wg set coupent l'interface AmneziaWG.
# La modification du fichier de config suffit ; elle sera active
# après : docker restart <container>
# ─────────────────────────────────────────────────────────────────────────────

def apply_keepalive(peer_ip: str, container_name: str, interval: int = 25) -> dict:
    """
    Écrit PersistentKeepalive dans le fichier de config du container.
    N'applique PAS en live (wg syncconf/set interdit sur AmneziaWG).
    Actif après : docker restart <container>
    """
    cfg     = get_container_cfg(container_name)
    bare_ip = peer_ip.split('/')[0]

    try:
        # 1. Lire config
        r = docker_run(cfg['container'], 'cat', cfg['config_path'])
        if r.returncode != 0:
            return {'success': False,
                    'message': f"Lecture {cfg['config_path']} : {r.stderr.strip()}"}

        config = r.stdout
        if f"AllowedIPs = {bare_ip}/32" not in config:
            return {'success': False,
                    'message': f"Peer {bare_ip} introuvable dans {cfg['config_path']}"}

        # 2. Patcher en mémoire : KA après AllowedIPs, supprimer doublon
        lines     = config.split('\n')
        new_lines = []
        skip_ka   = False

        for line in lines:
            if line.strip() in ('[Peer]', '[Interface]'):
                skip_ka = False
            if f"AllowedIPs = {bare_ip}/32" in line:
                new_lines.append(line)
                new_lines.append(f"PersistentKeepalive = {interval}")
                skip_ka = True
                continue
            if skip_ka and line.strip().startswith('PersistentKeepalive'):
                skip_ka = False
                continue
            new_lines.append(line)

        # 3. Réécrire
        r2 = docker_run_stdin(
            cfg['container'],
            f"cat > {cfg['config_path']}",
            '\n'.join(new_lines)
        )
        if r2.returncode != 0:
            return {'success': False,
                    'message': f"Écriture : {r2.stderr.strip()}"}

        return {'success': True,
                'message': (f"PersistentKeepalive = {interval}s enregistré "
                            f"pour {bare_ip}. "
                            f"Actif après : docker restart {cfg['container']}")}

    except subprocess.TimeoutExpired:
        return {'success': False, 'message': 'Timeout — container ne répond pas'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ─── Logs admin ───────────────────────────────────────────────────────────────

def log_admin_action(action: str, detail: str = None,
                     admin_id: int = None, target_user_id: int = None,
                     ip_source: str = None, db_path: str = DB_PATH):
    """Enregistre une action admin dans logs_admin."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            INSERT INTO logs_admin
                (action, detail, admin_id, target_user_id, ip_source)
            VALUES (?, ?, ?, ?, ?)
        """, (action, detail, admin_id, target_user_id, ip_source))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[vpn_utils] log_admin_action: {e}")


# ─── Alertes keepalive ────────────────────────────────────────────────────────

def get_keepalive_alerts(db_path: str = DB_PATH) -> list:
    """
    Retourne les peers actifs (handshake < 10min) sans keepalive configuré.
    """
    all_status = get_all_peers_status()
    alerts     = []
    now_ts     = int(datetime.utcnow().timestamp())

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    peers = conn.execute("""
        SELECT p.id, p.ip_vpn, p.label, p.container,
               u.nom as user_nom, u.id as user_id
        FROM peers p
        JOIN users u ON u.id = p.user_id
        WHERE p.actif = 1
    """).fetchall()
    conn.close()

    for p in peers:
        bare_ip = p['ip_vpn'].split('/')[0]
        live    = all_status.get(bare_ip, {})
        ka      = live.get('keepalive', 0)
        hs_ts   = live.get('handshake_ts', 0)
        age     = (now_ts - hs_ts) if hs_ts else None

        if ka == 0 and age is not None and age < 600:
            alerts.append({
                'peer_id':   p['id'],
                'peer_ip':   bare_ip,
                'label':     p['label'],
                'user_nom':  p['user_nom'],
                'user_id':   p['user_id'],
                'container': p['container'] or DEFAULT_CONTAINER,
                'age_s':     age,
            })

    return alerts


# ─── KPIs dashboard ───────────────────────────────────────────────────────────

def get_kpis(db_path: str = DB_PATH) -> dict:
    """Métriques principales pour la vue d'ensemble."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total_clients = conn.execute(
        "SELECT COUNT(*) FROM users WHERE is_admin = 0"
    ).fetchone()[0]

    mrr = conn.execute("""
        SELECT COALESCE(SUM(montant), 0)
        FROM abonnements
        WHERE statut = 'actif' AND date_fin >= date('now')
    """).fetchone()[0]

    expiring_7d = conn.execute("""
        SELECT COUNT(*) FROM abonnements
        WHERE statut = 'actif'
          AND date_fin BETWEEN date('now') AND date('now', '+7 days')
    """).fetchone()[0]

    pending = conn.execute("""
        SELECT COUNT(*) FROM users u
        JOIN abonnements a ON a.user_id = u.id
        WHERE u.is_admin = 0 AND a.statut = 'en_attente'
    """).fetchone()[0]

    unread_alerts = conn.execute(
        "SELECT COUNT(*) FROM alertes WHERE lu = 0"
    ).fetchone()[0]

    mrr_history = conn.execute("""
        SELECT strftime('%Y-%m', date_paiement) as mois,
               SUM(montant) as total
        FROM paiements
        WHERE valide = 1
        GROUP BY mois
        ORDER BY mois DESC
        LIMIT 6
    """).fetchall()

    conn.close()

    # Peers connectés maintenant (handshake < 3min)
    all_status    = get_all_peers_status()
    now_ts        = int(datetime.utcnow().timestamp())
    actifs_now    = sum(
        1 for d in all_status.values()
        if d.get('handshake_ts') and (now_ts - d['handshake_ts']) < 180
    )

    return {
        'total_clients': total_clients,
        'actifs_now':    actifs_now,
        'mrr':           int(mrr),
        'expiring_7d':   expiring_7d,
        'pending':       pending,
        'unread_alerts': unread_alerts,
        'mrr_history':   [dict(r) for r in mrr_history],
    }
