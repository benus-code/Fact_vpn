#!/usr/bin/env python3
"""
status_bot.py — Bot Telegram pour publier les statuts du service SP Network.

Commandes disponibles (admin uniquement) :
  /status               → affiche le statut actuel dans ce chat
  /statut_ok            → publie "Tout fonctionne normalement" sur le canal
  /statut_maintenance   → publie une annonce de maintenance planifiée
  /statut_panne         → publie une alerte d'incident en cours
  /statut_resolu        → publie un message de résolution d'incident
  /statut_custom <texte>→ publie un message personnalisé sur le canal

Fonctionnement :
  - Long-polling (getUpdates) — pas de webhook nécessaire
  - Lit la config dans la base SQLite du billing (même DB que app.py)
  - Seul l'admin (admin_telegram_id) peut envoyer des commandes
  - Les messages sont publiés sur telegram_channel_id
  - Le statut courant est sauvegardé dans monitoring_state.json

Lancer en prod : systemctl start sp-status-bot
"""

import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

# ─── Config ────────────────────────────────────────────────────────────────────
DB_PATH    = os.path.join(os.path.dirname(__file__), "billing.db")
STATE_FILE = os.path.join(os.path.dirname(__file__), "monitoring_state.json")

STATUTS = {
    "ok":          "🟢",
    "maintenance": "🟡",
    "panne":       "🔴",
    "inconnu":     "⚪",
}

# ─── DB helpers ────────────────────────────────────────────────────────────────
def get_settings():
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}

# ─── État monitoring ───────────────────────────────────────────────────────────
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

# ─── Telegram helpers ──────────────────────────────────────────────────────────
def tg_call(token, method, payload):
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[TG] HTTP {e.code} {method}: {body}", flush=True)
        return None
    except Exception as e:
        print(f"[TG] Error {method}: {e}", flush=True)
        return None

def send_to_channel(token, channel_id, text):
    return tg_call(token, "sendMessage", {
        "chat_id":    channel_id,
        "text":       text,
        "parse_mode": "HTML",
    })

def reply(token, chat_id, text):
    return tg_call(token, "sendMessage", {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
    })

# ─── Messages prédéfinis ───────────────────────────────────────────────────────
def now_str():
    return datetime.now().strftime("%d/%m/%Y %H:%M")

def msg_ok():
    return (
        "🟢 <b>SP Network — Tout fonctionne normalement</b>\n\n"
        "✅ Le service est pleinement opérationnel.\n"
        f"<i>Mise à jour : {now_str()}</i>"
    )

def msg_maintenance():
    return (
        "🟡 <b>SP Network — Maintenance planifiée</b>\n\n"
        "⚙️ Une opération de maintenance est en cours.\n"
        "Le service sera temporairement indisponible.\n"
        "Nous ferons notre possible pour minimiser l'interruption.\n\n"
        f"<i>Début : {now_str()}</i>"
    )

def msg_panne():
    return (
        "🔴 <b>SP Network — Incident en cours</b>\n\n"
        "⚠️ Nous avons détecté une perturbation du service.\n"
        "Nos équipes travaillent à la résolution.\n"
        "Merci de votre patience.\n\n"
        f"<i>Signalé le : {now_str()}</i>"
    )

def msg_resolu():
    return (
        "✅ <b>SP Network — Incident résolu</b>\n\n"
        "Le service est de nouveau pleinement opérationnel.\n"
        "Merci pour votre patience.\n\n"
        f"<i>Résolu le : {now_str()}</i>"
    )

# ─── Traitement des commandes ──────────────────────────────────────────────────
def handle_update(update, token, channel_id, admin_id):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return

    from_id  = str(msg.get("from", {}).get("id", ""))
    chat_id  = msg.get("chat", {}).get("id")
    text     = (msg.get("text") or "").strip()

    if not text.startswith("/"):
        return

    # Seul l'admin peut envoyer des commandes
    if from_id != str(admin_id):
        reply(token, chat_id, "⛔ Commandes réservées à l'administrateur.")
        return

    state = load_state()
    cmd   = text.split()[0].lower().rstrip("@" + "abcdefghijklmnopqrstuvwxyz_0123456789")

    if cmd == "/status":
        emoji  = STATUTS.get(state.get("statut", "inconnu"), "⚪")
        depuis = state.get("depuis") or "—"
        reply(token, chat_id,
              f"{emoji} Statut actuel : <b>{state.get('statut', 'inconnu')}</b>\n"
              f"Depuis : {depuis}\n\n"
              f"Canal : <code>{channel_id}</code>")

    elif cmd == "/statut_ok":
        send_to_channel(token, channel_id, msg_ok())
        state.update({"statut": "ok", "depuis": now_str(), "dernier_update": now_str()})
        save_state(state)
        reply(token, chat_id, "✅ Message 'OK' publié sur le canal.")

    elif cmd == "/statut_maintenance":
        send_to_channel(token, channel_id, msg_maintenance())
        state.update({"statut": "maintenance", "depuis": now_str(), "dernier_update": now_str()})
        save_state(state)
        reply(token, chat_id, "🟡 Message 'Maintenance' publié sur le canal.")

    elif cmd == "/statut_panne":
        send_to_channel(token, channel_id, msg_panne())
        state.update({"statut": "panne", "depuis": now_str(), "dernier_update": now_str()})
        save_state(state)
        reply(token, chat_id, "🔴 Message 'Panne' publié sur le canal.")

    elif cmd == "/statut_resolu":
        send_to_channel(token, channel_id, msg_resolu())
        state.update({"statut": "ok", "depuis": now_str(), "dernier_update": now_str()})
        save_state(state)
        reply(token, chat_id, "✅ Message 'Résolu' publié sur le canal.")

    elif cmd == "/statut_custom":
        custom_text = text[len("/statut_custom"):].strip()
        if not custom_text:
            reply(token, chat_id, "⚠️ Usage : /statut_custom <votre message>")
            return
        full_msg = (
            f"📢 <b>SP Network</b>\n\n"
            f"{custom_text}\n\n"
            f"<i>{now_str()}</i>"
        )
        send_to_channel(token, channel_id, full_msg)
        state["dernier_update"] = now_str()
        save_state(state)
        reply(token, chat_id, "✅ Message personnalisé publié sur le canal.")

    else:
        reply(token, chat_id,
              "Commandes disponibles :\n"
              "/status — statut actuel\n"
              "/statut_ok — service opérationnel\n"
              "/statut_maintenance — maintenance planifiée\n"
              "/statut_panne — incident en cours\n"
              "/statut_resolu — incident résolu\n"
              "/statut_custom &lt;texte&gt; — message libre")

# ─── Long-polling loop ─────────────────────────────────────────────────────────
def run():
    s = get_settings()
    token      = s.get("telegram_bot_token", "").strip()
    channel_id = s.get("telegram_channel_id", "").strip()
    admin_id   = s.get("admin_telegram_id", "").strip()

    if not token:
        print("[status_bot] telegram_bot_token non configuré — arrêt.", flush=True)
        sys.exit(1)
    if not channel_id:
        print("[status_bot] telegram_channel_id non configuré — arrêt.", flush=True)
        sys.exit(1)
    if not admin_id:
        print("[status_bot] admin_telegram_id non configuré — arrêt.", flush=True)
        sys.exit(1)

    print(f"[status_bot] Démarré. Canal={channel_id} Admin={admin_id}", flush=True)

    offset = 0
    while True:
        # Recharge la config à chaque itération (permet changement en live)
        s          = get_settings()
        token      = s.get("telegram_bot_token", "").strip()
        channel_id = s.get("telegram_channel_id", "").strip()
        admin_id   = s.get("admin_telegram_id", "").strip()

        if not token or not channel_id or not admin_id:
            time.sleep(30)
            continue

        result = tg_call(token, "getUpdates", {
            "offset":  offset,
            "timeout": 30,
            "allowed_updates": ["message"],
        })

        if result and result.get("ok"):
            for update in result.get("result", []):
                uid = update.get("update_id", 0)
                if uid >= offset:
                    offset = uid + 1
                try:
                    handle_update(update, token, channel_id, admin_id)
                except Exception as e:
                    print(f"[status_bot] Erreur traitement update {uid}: {e}", flush=True)
        else:
            time.sleep(5)

if __name__ == "__main__":
    run()
