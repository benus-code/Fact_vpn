#!/usr/bin/env python3
"""
Cron toutes les 15 min : détecte les transitions vers état 'problem'
et envoie une alerte Telegram à l'admin pour chaque nouveau client en difficulté.
"""
import sys
import sqlite3
import json
import urllib.request

sys.path.insert(0, "/opt/vpn-billing")
import monitoring
from monitoring import (
    DB, get_all_connection_health,
    CONN_PROBLEM, PROBLEM_NEVER, PROBLEM_DEAD, PROBLEM_BLOCKED,
)

# ── Init table d'état ────────────────────────────────────────────────────────
conn = sqlite3.connect(DB)
conn.execute("""
    CREATE TABLE IF NOT EXISTS connection_health_state (
        public_key         TEXT PRIMARY KEY,
        last_state         TEXT,
        last_sub_code      TEXT,
        first_seen_problem TIMESTAMP,
        last_alert_sent    TIMESTAMP,
        updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# ── État actuel ───────────────────────────────────────────────────────────────
current = get_all_connection_health()

# ── État précédent ────────────────────────────────────────────────────────────
cur = conn.cursor()
cur.execute("SELECT public_key, last_state, last_sub_code FROM connection_health_state")
previous = {r[0]: {"state": r[1], "sub_code": r[2]} for r in cur.fetchall()}

# ── Détecte transitions vers 'problem' ───────────────────────────────────────
transitions = []
for pubkey, (state, sub, age, label) in current.items():
    prev = previous.get(pubkey)
    became_problem = state == CONN_PROBLEM and (not prev or prev["state"] != CONN_PROBLEM)
    sub_changed = (state == CONN_PROBLEM and prev and prev["state"] == CONN_PROBLEM
                   and prev["sub_code"] != sub)
    if became_problem or sub_changed:
        transitions.append((pubkey, state, sub, label))

# ── Config Telegram ───────────────────────────────────────────────────────────
cur.execute("SELECT key, value FROM settings WHERE key IN ('telegram_bot_token', 'telegram_chat_id')")
cfg = dict(cur.fetchall())
token      = cfg.get("telegram_bot_token", "").strip()
admin_chat = cfg.get("telegram_chat_id", "").strip()

# ── Site URL pour le lien de l'alerte ────────────────────────────────────────
cur.execute("SELECT value FROM settings WHERE key='site_url'")
row = cur.fetchone()
site_url = (row[0].strip().rstrip("/") if row and row[0] else "").rstrip("/")
monitoring_url = f"{site_url}/admin/monitoring" if site_url else "/admin/monitoring"

# ── Envoie alerte groupée si transitions + config présente ───────────────────
sent_ok = False
if transitions and token and admin_chat:
    pubkeys = [t[0] for t in transitions]
    placeholders = ",".join("?" * len(pubkeys))
    cur.execute(
        f"SELECT p.public_key, u.nom, u.email FROM peers p "
        f"LEFT JOIN users u ON u.id = p.user_id WHERE p.public_key IN ({placeholders})",
        pubkeys,
    )
    name_map = {r[0]: (r[1] or "?", r[2] or "") for r in cur.fetchall()}

    sub_emoji    = {PROBLEM_NEVER: "🆕", PROBLEM_BLOCKED: "🚫", PROBLEM_DEAD: "💀"}
    sub_label_fr = {
        PROBLEM_NEVER:   "jamais connecté (onboarding à vérifier)",
        PROBLEM_BLOCKED: "tunnel actif mais aucun trafic (DPI/firewall ?)",
        PROBLEM_DEAD:    "déconnecté depuis plus de 7 jours",
    }

    lines = ["🚨 *VPN Privé · Connexions en difficulté*", ""]
    for pubkey, state, sub, label in transitions[:20]:
        nom, email = name_map.get(pubkey, ("?", ""))
        emoji = sub_emoji.get(sub, "⚠️")
        lines.append(f"{emoji} *{nom}* — {sub_label_fr.get(sub, label)}")
        if email:
            lines.append(f"   `{email}`")
    if len(transitions) > 20:
        lines.append(f"\n_... et {len(transitions) - 20} autres_")
    lines.append(f"\n🔗 {monitoring_url}")
    msg = "\n".join(lines)

    try:
        data = json.dumps({
            "chat_id": admin_chat,
            "text": msg,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            sent_ok = resp.status == 200
    except Exception as e:
        print(f"[telegram_error] {e}")

# ── Met à jour la table d'état ────────────────────────────────────────────────
for pubkey, (state, sub, age, label) in current.items():
    cur.execute("""
        INSERT INTO connection_health_state
            (public_key, last_state, last_sub_code, first_seen_problem, last_alert_sent, updated_at)
        VALUES (?, ?, ?,
            CASE WHEN ? = 'problem' THEN datetime('now') ELSE NULL END,
            CASE WHEN ? THEN datetime('now') ELSE NULL END,
            datetime('now'))
        ON CONFLICT(public_key) DO UPDATE SET
            last_state    = excluded.last_state,
            last_sub_code = excluded.last_sub_code,
            first_seen_problem = CASE
                WHEN excluded.last_state = 'problem' AND last_state != 'problem' THEN datetime('now')
                WHEN excluded.last_state != 'problem' THEN NULL
                ELSE first_seen_problem
            END,
            last_alert_sent = CASE WHEN ? THEN datetime('now') ELSE last_alert_sent END,
            updated_at = datetime('now')
    """, (pubkey, state, sub, state, sent_ok, sent_ok))

conn.commit()
conn.close()
print(f"health_alert: {len(current)} peers, {len(transitions)} transitions, telegram_sent={sent_ok}")
