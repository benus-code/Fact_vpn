#!/usr/bin/env python3
"""
access_daemon.py — Daemon de contrôle d'accès dynamique VPN

Source de vérité : docker exec wg show <iface> dump (runtime, pubkey→IP)
Application      : iptables HOST FORWARD DROP (sans wg set, sans restart)

Cycle toutes les INTERVAL secondes :
  1. wg show dump  → {pubkey: vpn_ip}  (IP actuelle temps-réel)
  2. BD            → set de pubkeys à bloquer (expirés + bannis + actif=0)
  3. Réconciliation iptables HOST :
       blocked   + règle manquante  → ajout DROP
       non-bloqué + règle présente  → suppression DROP
       règle sur IP absente de WG   → nettoyage (IP obsolète / peer supprimé)

RÈGLES ABSOLUES (cf. vpn_utils.py) :
  - Jamais wg set / wg syncconf
  - Jamais docker restart
  - Ne pas toucher aux peers PiVPN (10.211.76.x)
  - Ne modifier aucun fichier existant
"""

import logging
import logging.handlers
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import date


# ─── Configuration ────────────────────────────────────────────────────────────

DB_PATH  = '/opt/vpn-billing/vpn_billing.db'
INTERVAL = 30    # secondes entre chaque cycle
LOG_PATH = '/var/log/vpn_access_daemon.log'
PID_FILE = '/var/run/vpn_access_daemon.pid'

# Containers WireGuard à surveiller
WG_CONTAINERS = [
    ('amnezia-awg',  'wg0'),
    ('amnezia-awg2', 'awg0'),
]

# Préfixes IPs JAMAIS touchés (gérés séparément)
PROTECTED_PREFIXES = ('10.211.76.',)

# Préfixes IPs gérés par ce daemon (pour détecter les règles obsolètes)
MANAGED_PREFIXES = ('10.8.1.', '10.8.2.')


# ─── Logging ──────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    fmt = logging.Formatter(
        '%(asctime)s [access_daemon] %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Fichier avec rotation (5 MB × 3 fichiers)
    fh = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Stdout pour journald / supervision manuelle
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


logger = logging.getLogger(__name__)


# ─── WireGuard dump ───────────────────────────────────────────────────────────

def _parse_wg_dump(output: str) -> dict:
    """
    Parse la sortie machine-readable de 'wg show <iface> dump'.
    Format tabulé : pubkey  preshared  endpoint  allowed_ips  hs  rx  tx  ka
    Première ligne = infos serveur → ignorée.
    Retourne {pubkey: bare_ip}.
    """
    wg_map = {}
    lines = output.strip().split('\n')
    for line in lines[1:]:          # sauter la ligne serveur
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        pubkey      = parts[0].strip()
        allowed_ips = parts[3].strip()   # ex: "10.8.1.5/32" ou "(none)"
        if not pubkey or allowed_ips in ('', '(none)'):
            continue
        bare_ip = allowed_ips.split('/')[0]
        # Ne garder que les IPs réelles gérées (exclut blackhole 192.0.2.x etc.)
        if bare_ip and any(bare_ip.startswith(p) for p in MANAGED_PREFIXES):
            wg_map[pubkey] = bare_ip
    return wg_map


def get_wg_map() -> dict:
    """
    Interroge TOUS les containers AWG via wg show dump.
    Retourne {pubkey: vpn_ip} — IPs protégées exclues.
    """
    wg_map = {}
    for container, interface in WG_CONTAINERS:
        try:
            r = subprocess.run(
                ['docker', 'exec', container, 'wg', 'show', interface, 'dump'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                logger.warning(
                    f"wg show {container}/{interface} dump → "
                    f"code={r.returncode} {r.stderr.strip()}"
                )
                continue
            parsed = _parse_wg_dump(r.stdout)
            # Exclure IPs protégées
            for pk, ip in parsed.items():
                if not any(ip.startswith(p) for p in PROTECTED_PREFIXES):
                    wg_map[pk] = ip
            logger.debug(f"{container}/{interface}: {len(parsed)} peers lus")
        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout wg show dump {container}/{interface}")
        except Exception as exc:
            logger.warning(f"wg show dump {container}/{interface}: {exc}")
    return wg_map


# ─── Base de données ──────────────────────────────────────────────────────────

def get_blocked_pubkeys() -> set:
    """
    Retourne les clés publiques qui doivent être BLOQUÉES :
      - utilisateur banni   (is_banned = 1)
      - peer désactivé      (actif = 0)
      - aucun abonnement actif valide (NOT EXISTS)

    Exclut les admins et les clés MANUAL_*.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        today = date.today().isoformat()

        rows = conn.execute("""
            SELECT DISTINCT p.public_key
            FROM peers p
            JOIN users u ON u.id = p.user_id
            WHERE u.is_admin = 0
              AND p.public_key IS NOT NULL
              AND p.public_key NOT LIKE 'MANUAL%'
              AND (
                  u.is_banned = 1
                  OR p.actif = 0
                  OR NOT EXISTS (
                      SELECT 1 FROM abonnements a
                      WHERE a.user_id = u.id
                        AND a.statut  = 'actif'
                        AND (a.date_fin IS NULL OR a.date_fin >= ?)
                  )
              )
        """, (today,)).fetchall()
        conn.close()
        return {r['public_key'] for r in rows}
    except Exception as exc:
        logger.error(f"DB get_blocked_pubkeys: {exc}")
        return set()   # fail-open : ne rien bloquer si DB inaccessible


# ─── iptables HOST ────────────────────────────────────────────────────────────

def _ipt(*args) -> int:
    """Lance iptables sur le HOST. Retourne le returncode."""
    try:
        r = subprocess.run(
            ['iptables'] + list(args),
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode
    except Exception as exc:
        logger.error(f"iptables {' '.join(args)}: {exc}")
        return -1


def _rule_exists(flag: str, ip: str) -> bool:
    """-C FORWARD → True si la règle DROP existe."""
    return _ipt('-C', 'FORWARD', flag, ip, '-j', 'DROP') == 0


def _add_drop(ip: str) -> bool:
    """Insère les règles DROP -s et -d pour ip. Idempotent."""
    ok = True
    for flag in ('-s', '-d'):
        if not _rule_exists(flag, ip):
            if _ipt('-I', 'FORWARD', flag, ip, '-j', 'DROP') != 0:
                logger.error(f"Échec ajout DROP {flag} {ip}")
                ok = False
    return ok


def _remove_drop(ip: str) -> None:
    """Supprime TOUTES les règles DROP -s/-d pour ip (doublons inclus)."""
    for flag in ('-s', '-d'):
        while _ipt('-D', 'FORWARD', flag, ip, '-j', 'DROP') == 0:
            pass   # répéter jusqu'à "no such rule"


def _get_current_drop_ips() -> set:
    """
    Parse 'iptables -L FORWARD -n' et retourne les IPs VPN
    qui ont actuellement une règle DROP (source ou destination).
    Format des lignes DROP :
      DROP  all  --  <source>  <dest>
    """
    try:
        r = subprocess.run(
            ['iptables', '-L', 'FORWARD', '-n'],
            capture_output=True, text=True, timeout=5,
        )
        drop_ips = set()
        for line in r.stdout.split('\n'):
            if not line.startswith('DROP'):
                continue
            parts = line.split()
            # parts : [DROP, prot, opt, source, dest, ...]
            if len(parts) < 5:
                continue
            for ip_field in (parts[3], parts[4]):
                bare = ip_field.split('/')[0]
                if any(bare.startswith(p) for p in MANAGED_PREFIXES):
                    drop_ips.add(bare)
        return drop_ips
    except Exception as exc:
        logger.error(f"iptables -L FORWARD -n: {exc}")
        return set()


# ─── Cycle de synchronisation ─────────────────────────────────────────────────

def sync_once() -> dict:
    """
    Un cycle complet de réconciliation.
    Retourne un dict de statistiques.
    """
    stats = {
        'blocked':   0,   # règles DROP ajoutées
        'unblocked': 0,   # règles DROP supprimées (user réactivé)
        'cleaned':   0,   # règles DROP supprimées (IP obsolète)
        'errors':    0,
        'skipped':   0,   # aucun changement nécessaire
    }

    # ── 1. État runtime WireGuard ─────────────────────────────────────────────
    wg_map = get_wg_map()   # {pubkey: vpn_ip}
    if not wg_map:
        logger.warning("wg_map vide — containers down ou aucun peer ?")
        return stats

    # ── 2. Pubkeys à bloquer selon la BD ─────────────────────────────────────
    blocked_pubkeys = get_blocked_pubkeys()

    # ── 3. Calcul des IPs cibles ──────────────────────────────────────────────
    ips_to_block = set()
    ips_to_allow = set()
    for pubkey, vpn_ip in wg_map.items():
        if pubkey in blocked_pubkeys:
            ips_to_block.add(vpn_ip)
        else:
            ips_to_allow.add(vpn_ip)

    # ── 4. Ajouter les règles manquantes ──────────────────────────────────────
    for ip in ips_to_block:
        if not _rule_exists('-s', ip):
            if _add_drop(ip):
                logger.info(f"BLOQUÉ   {ip}")
                stats['blocked'] += 1
            else:
                stats['errors'] += 1
        else:
            stats['skipped'] += 1

    # ── 5. Supprimer les règles pour IPs redevenues actives ───────────────────
    for ip in ips_to_allow:
        if _rule_exists('-s', ip):
            _remove_drop(ip)
            logger.info(f"DÉBLOQUÉ {ip}")
            stats['unblocked'] += 1
        else:
            stats['skipped'] += 1

    # ── 6. Nettoyer les règles sur IPs absentes de WireGuard ──────────────────
    # (ex : peer supprimé, IP réattribuée à un autre user)
    all_wg_ips    = set(wg_map.values())
    current_drops = _get_current_drop_ips()
    for ip in current_drops:
        if ip not in all_wg_ips:
            _remove_drop(ip)
            logger.info(f"NETTOYÉ  {ip} (absent de WireGuard)")
            stats['cleaned'] += 1

    return stats


# ─── Daemon ───────────────────────────────────────────────────────────────────

_running = True


def _on_signal(signum, _frame) -> None:
    global _running
    logger.info(f"Signal {signum} reçu — arrêt propre en cours")
    _running = False


def _write_pid() -> None:
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as exc:
        logger.warning(f"PID file non écrit : {exc}")


def _remove_pid() -> None:
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def main() -> int:
    setup_logging()
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    logger.info("=" * 60)
    logger.info("access_daemon démarré")
    logger.info(f"Containers : {[c for c, _ in WG_CONTAINERS]}")
    logger.info(f"Intervalle : {INTERVAL}s   DB : {DB_PATH}")
    logger.info("=" * 60)
    _write_pid()

    cycle = 0
    try:
        while _running:
            cycle += 1
            try:
                stats = sync_once()
                changed = stats['blocked'] + stats['unblocked'] + stats['cleaned']
                if changed or stats['errors']:
                    logger.info(
                        f"Cycle #{cycle:04d} — "
                        f"bloqué:{stats['blocked']}  "
                        f"débloqué:{stats['unblocked']}  "
                        f"nettoyé:{stats['cleaned']}  "
                        f"erreur:{stats['errors']}"
                    )
                else:
                    logger.debug(
                        f"Cycle #{cycle:04d} — "
                        f"aucun changement ({stats['skipped']} peers OK)"
                    )
            except Exception as exc:
                logger.error(
                    f"Cycle #{cycle:04d} — erreur inattendue: {exc}",
                    exc_info=True,
                )

            # Attente interruptible seconde par seconde
            for _ in range(INTERVAL):
                if not _running:
                    break
                time.sleep(1)
    finally:
        _remove_pid()
        logger.info("access_daemon arrêté proprement")

    return 0


if __name__ == '__main__':
    sys.exit(main())
