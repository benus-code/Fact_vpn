#!/usr/bin/env python3
"""
monitoring_vpn.py — Surveillance automatique du service SP Network (amnezia-awg).

Vérifie toutes les 5 minutes si le service systemd `amnezia-awg` est actif.
En cas de panne ou de rétablissement, publie automatiquement sur le canal Telegram
configuré dans les paramètres du billing.

Anti-spam : un seul message par incident (grâce à monitoring_state.json).

Lancer en prod : systemctl start sp-monitoring
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

# ─── Config ────────────────────────────────────────────────────────────────────
DB_PATH      = os.path.join(os.path.dirname(__file__), "vpn_billing.db")
STATE_FILE   = os.path.join(os.path.dirname(__file__), "monitoring_state.json")
SERVICE_NAME = "amnezia-awg"
CHECK_INTERVAL = 300  # secondes (5 minutes)

# ─── DB helpers ────────────────────────────────────────────────────────────────
def get_settings():
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}

# ─── État ──────────────────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"statut": "inconnu", "depuis": None, "dernier_update": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ─── Vérification service ──────────────────────────────────────────────────────
def check_service():
    """Retourne True si le conteneur Docker amnezia-awg tourne, False sinon."""
    try:
        # Essai 1 : conteneur Docker
        result = subprocess.run(
            ["docker", "inspect", "--format={{.State.Status}}", SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip() == "running"
    except Exception:
        pass
    try:
        # Essai 2 : service systemd (fallback)
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() == "active"
    except Exception as e:
        print(f"[monitoring] Erreur vérification service: {e}", flush=True)
        return False

# ─── Telegram ──────────────────────────────────────────────────────────────────
def send_to_channel(token, channel_id, text):
    data = json.dumps({
        "chat_id":    channel_id,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[monitoring] TG HTTP {e.code}: {e.read().decode(errors='replace')}", flush=True)
    except Exception as e:
        print(f"[monitoring] TG erreur: {e}", flush=True)
    return None

def also_notify_admin(token, admin_chat_id, text):
    """Notifie aussi le chat privé admin (telegram_chat_id)."""
    if not admin_chat_id:
        return
    send_to_channel(token, admin_chat_id, text)

# ─── Messages ──────────────────────────────────────────────────────────────────
def now_str():
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def msg_panne_auto():
    return (
        "🔴 <b>SP Network — Incident détecté</b>\n\n"
        "⚠️ Le service est actuellement indisponible.\n"
        "Nos équipes ont été alertées automatiquement.\n"
        "Merci de votre patience.\n\n"
        f"<i>Détecté le : {now_str()}</i>"
    )

def msg_retablissement_auto(depuis):
    duree = ""
    if depuis:
        try:
            debut = datetime.strptime(depuis, "%d/%m/%Y %H:%M")
            delta = datetime.now() - debut
            minutes = int(delta.total_seconds() // 60)
            if minutes < 60:
                duree = f" (durée : {minutes} min)"
            else:
                heures = minutes // 60
                duree = f" (durée : {heures}h{minutes % 60:02d})"
        except Exception:
            pass
    return (
        "🟢 <b>SP Network — Service rétabli</b>\n\n"
        f"✅ Le service est de nouveau opérationnel{duree}.\n"
        "Merci pour votre patience.\n\n"
        f"<i>Rétabli le : {now_str()}</i>"
    )

# ─── Boucle principale ─────────────────────────────────────────────────────────
def run():
    print(f"[monitoring] Démarré. Surveillance de '{SERVICE_NAME}' toutes les {CHECK_INTERVAL}s.", flush=True)

    while True:
        s          = get_settings()
        token      = s.get("telegram_bot_token", "").strip()
        channel_id = s.get("telegram_channel_id", "").strip()
        admin_chat  = s.get("telegram_chat_id", "").strip()

        if not token or not channel_id:
            print("[monitoring] Telegram non configuré — vérification ignorée.", flush=True)
            time.sleep(CHECK_INTERVAL)
            continue

        state   = load_state()
        actif   = check_service()
        statut  = state.get("statut", "inconnu")

        if not actif and statut != "panne":
            # Transition OK → panne
            print(f"[monitoring] PANNE détectée à {now_str()}", flush=True)
            send_to_channel(token, channel_id, msg_panne_auto())
            also_notify_admin(token, admin_chat,
                f"🚨 <b>ALERTE</b> : le service {SERVICE_NAME} est tombé à {now_str()}")
            state.update({"statut": "panne", "depuis": now_str(), "dernier_update": now_str()})
            save_state(state)

        elif actif and statut == "panne":
            # Transition panne → OK
            print(f"[monitoring] RÉTABLISSEMENT à {now_str()}", flush=True)
            send_to_channel(token, channel_id, msg_retablissement_auto(state.get("depuis")))
            also_notify_admin(token, admin_chat,
                f"✅ <b>RÉTABLI</b> : le service {SERVICE_NAME} est de nouveau actif à {now_str()}")
            state.update({"statut": "ok", "depuis": now_str(), "dernier_update": now_str()})
            save_state(state)

        elif actif and statut == "inconnu":
            # Premier démarrage : initialiser l'état
            state.update({"statut": "ok", "depuis": now_str(), "dernier_update": now_str()})
            save_state(state)
            print(f"[monitoring] État initialisé : OK à {now_str()}", flush=True)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
