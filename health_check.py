#!/usr/bin/env python3
"""
health_check.py
Vérifie la santé de tous les services critiques.
Envoie une alerte Telegram si quelque chose ne va pas.
Lancé toutes les 10 minutes via cron.

Cron : */10 * * * * /opt/vpn-billing/venv/bin/python3 /opt/vpn-billing/health_check.py >> /var/log/vpn_health.log 2>&1
"""
import json
import socket
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime

DB = '/opt/vpn-billing/vpn_billing.db'


def get_telegram_config():
    """Lit le token et chat_id Telegram depuis settings."""
    try:
        conn = sqlite3.connect(DB, timeout=5)
        rows = dict(conn.execute(
            "SELECT key, value FROM settings WHERE key IN "
            "('telegram_bot_token','telegram_chat_id')"
        ).fetchall())
        conn.close()
        return (rows.get('telegram_bot_token', '') or '').strip(), \
               (rows.get('telegram_chat_id', '') or '').strip()
    except Exception:
        return '', ''


def send_alert(message: str):
    """Envoie une alerte Telegram (n'attrape jamais — log si fail)."""
    token, chat_id = get_telegram_config()
    if not token or not chat_id:
        print(f"[ALERT] {message}")
        return
    try:
        data = json.dumps({
            'chat_id':    chat_id,
            'text':       f"🚨 VPN ALERT\n{message}",
            'parse_mode': 'HTML',
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={'Content-Type': 'application/json'},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[ALERT FAILED] {e}")


def check_docker_container(name: str) -> bool:
    """True si le container Docker tourne."""
    try:
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Running}}', name],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == 'true'
    except Exception:
        return False


def check_service(name: str) -> bool:
    """True si le service systemd est actif."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', '--quiet', name],
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def check_service_failed(name: str) -> bool:
    """True si le service systemd est en état failed."""
    try:
        result = subprocess.run(
            ['systemctl', 'is-failed', name],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == 'failed'
    except Exception:
        return False


def check_dns() -> bool:
    """True si la résolution DNS du SMTP fonctionne."""
    try:
        socket.getaddrinfo('smtp-relay.brevo.com', 587)
        return True
    except Exception:
        return False


def check_db() -> bool:
    """True si la BD est lisible."""
    try:
        conn = sqlite3.connect(DB, timeout=5)
        conn.execute("SELECT COUNT(*) FROM users")
        conn.close()
        return True
    except Exception:
        return False


def check_disk_usage(mount: str = '/opt') -> int:
    """Retourne le pourcentage d'utilisation, ou -1 en cas d'erreur."""
    try:
        result = subprocess.run(
            ['df', '-h', mount],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n')[1:]:
            parts = line.split()
            if parts and parts[-1] == mount:
                return int(parts[4].replace('%', ''))
    except Exception:
        pass
    return -1


def main():
    alerts = []
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    # Containers Docker AmneziaWG
    for container in ['amnezia-awg', 'amnezia-awg2']:
        if not check_docker_container(container):
            alerts.append(f"❌ Container {container} arrêté !")

    # Services systemd
    # renewal-reminder est un oneshot — on vérifie l'état failed
    if check_service_failed('renewal-reminder'):
        alerts.append("❌ Service renewal-reminder en erreur !")

    for service in ['vpn-billing', 'vpn-access-daemon']:
        if not check_service(service):
            alerts.append(f"❌ Service {service} arrêté !")

    # DNS (nécessaire pour les emails Brevo)
    if not check_dns():
        alerts.append("❌ DNS cassé — emails impossibles !")

    # Base de données
    if not check_db():
        alerts.append("❌ Base de données inaccessible !")

    # Espace disque
    usage = check_disk_usage('/opt')
    if usage > 85:
        alerts.append(f"⚠️ Disque /opt à {usage}% — attention !")

    if alerts:
        msg = f"[{now}]\n" + "\n".join(alerts)
        send_alert(msg)
        print(f"ALERTES ENVOYÉES:\n{msg}")
        sys.exit(1)
    else:
        print(f"[{now}] Tous les services OK ✅")


if __name__ == '__main__':
    main()
