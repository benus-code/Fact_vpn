#!/usr/bin/env python3
"""
app.py — Portail de facturation VPN (AmneziaWG / WireGuard)
Lancer : python3 app.py
"""

import os
import sqlite3
import hashlib
import subprocess
import urllib.request
import json
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, g, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash as wz_check

app = Flask(__name__)
app.secret_key = os.environ.get("VPN_SECRET_KEY", "CHANGE_CE_SECRET_EN_PROD_SVP")

DB_PATH      = "/opt/vpn-billing/vpn_billing.db"
CONTAINER    = "amnezia-awg"
WG_INTERFACE = "wg0"

# Valeurs par défaut — insérées en BDD si absentes
SETTINGS_DEFAULTS = {
    "beneficiaire":      "Чеганг Анжес Уилфрид",
    "telephone":         "+7 996 637-23-58",
    "banque":            "Тбанк",
    "montant":           "100",
    "reference":         "VPN + твоё имя",
    "telegram_bot_token": "",
    "telegram_chat_id":   "",
    "support_telegram":   "",   # ex: https://t.me/tonpseudo
    "support_whatsapp":   "",   # ex: +7 996 637-23-58
}

# ─── DB helpers ───────────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def get_settings():
    """Dict des paramètres de paiement/config depuis la BDD."""
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {row["key"]: row["value"] for row in rows}

# ─── Migration douce au démarrage ─────────────────────────────────────────────
def init_app_db():
    """Crée tables/colonnes manquantes sans toucher aux données existantes."""
    conn = sqlite3.connect(DB_PATH)

    # Table settings
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    for key, value in SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )

    # Colonnes optionnelles sur users
    for col in ["whatsapp", "telegram"]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass

    # Colonne date_ajout sur peers
    try:
        conn.execute("ALTER TABLE peers ADD COLUMN date_ajout DATE")
        conn.execute("UPDATE peers SET date_ajout = DATE('now') WHERE date_ajout IS NULL")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

# ─── Auth helpers ─────────────────────────────────────────────────────────────
def hash_password(p):
    return generate_password_hash(p)

def verify_password(stored, provided):
    """Vérifie le mot de passe. Compatible avec les anciens hash SHA-256."""
    # Ancien hash SHA-256 brut (64 caractères hexadécimaux)
    if len(stored) == 64 and all(c in "0123456789abcdef" for c in stored):
        return stored == hashlib.sha256(provided.encode()).hexdigest()
    return wz_check(stored, provided)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Accès réservé à l'administrateur.", "danger")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated

# ─── Telegram notifications ───────────────────────────────────────────────────
def notify_telegram(message):
    """Envoie un message via bot Telegram. Silencieux si non configuré."""
    s = get_settings()
    token   = s.get("telegram_bot_token", "").strip()
    chat_id = s.get("telegram_chat_id", "").strip()
    if not token or not chat_id:
        return
    try:
        data = json.dumps({
            "chat_id": chat_id,
            "text":    message,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        app.logger.warning(f"Telegram notify failed: {e}")

# ─── iptables helpers ─────────────────────────────────────────────────────────
def iptables_block_peer(ip_vpn):
    ip = ip_vpn.split("/")[0]
    try:
        subprocess.run(
            ["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True
        )
        subprocess.run(
            ["docker", "exec", CONTAINER, "iptables", "-I", "FORWARD", "-d", ip, "-j", "DROP"],
            check=True, capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        app.logger.error(f"iptables_block_peer failed: {e.stderr}")
        return False

def iptables_unblock_peer(ip_vpn):
    ip = ip_vpn.split("/")[0]
    for direction in ["-s", "-d"]:
        try:
            subprocess.run(
                ["docker", "exec", CONTAINER, "iptables", "-D", "FORWARD", direction, ip, "-j", "DROP"],
                check=True, capture_output=True
            )
        except subprocess.CalledProcessError:
            pass
    return True

# ─── VPN health helpers ────────────────────────────────────────────────────────
import re as _re

def _parse_handshake_duration(text):
    """Convertit 'X minutes, Y seconds ago' en secondes. Retourne None si absent."""
    if not text:
        return None
    total = 0
    for val, unit in _re.findall(r'(\d+)\s+(year|month|week|day|hour|minute|second)', text):
        v = int(val)
        if   'year'   in unit: total += v * 31536000
        elif 'month'  in unit: total += v * 2592000
        elif 'week'   in unit: total += v * 604800
        elif 'day'    in unit: total += v * 86400
        elif 'hour'   in unit: total += v * 3600
        elif 'minute' in unit: total += v * 60
        elif 'second' in unit: total += v
    return total if total > 0 else None

def parse_wg_output(output):
    """Parse la sortie de 'wg show wg0'.
    Retourne { '10.8.1.x': { pubkey, handshake_secs, transfer_rx, transfer_tx, keepalive } }
    """
    peers = {}
    current = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("peer:"):
            pubkey = line.split(":", 1)[1].strip()
            current = {"pubkey": pubkey, "allowed_ip": None,
                       "handshake_secs": None, "transfer_rx": "",
                       "transfer_tx": "", "keepalive": 0}
        elif current is None:
            continue
        elif line.startswith("allowed ips:"):
            cidr = line.split(":", 1)[1].strip().split(",")[0].strip()
            current["allowed_ip"] = cidr.split("/")[0]
        elif line.startswith("latest handshake:"):
            current["handshake_secs"] = _parse_handshake_duration(
                line.split(":", 1)[1].strip()
            )
        elif line.startswith("transfer:"):
            parts = line.split(":", 1)[1].strip()
            m = _re.match(r'(.+?)\s+received,\s+(.+?)\s+sent', parts)
            if m:
                current["transfer_rx"] = m.group(1)
                current["transfer_tx"] = m.group(2)
        elif line.startswith("persistent keepalive:"):
            m = _re.search(r'every (\d+)', line)
            current["keepalive"] = int(m.group(1)) if m else 0
        elif line == "" and current and current["allowed_ip"]:
            peers[current["allowed_ip"]] = current
            current = None
    if current and current.get("allowed_ip"):
        peers[current["allowed_ip"]] = current
    return peers

def get_server_health():
    """Retourne CPU %, RAM %, erreurs wg0 — stdlib uniquement, sans psutil."""
    import time as _time
    health = {"cpu_pct": "N/A", "ram_pct": "N/A",
              "ram_used": "N/A", "ram_total": "N/A", "wg0_errors": "N/A"}
    try:
        def _read_cpu():
            with open("/proc/stat") as f:
                parts = f.readline().split()
            idle  = int(parts[4])
            total = sum(int(x) for x in parts[1:])
            return idle, total
        i1, t1 = _read_cpu()
        _time.sleep(0.1)
        i2, t2 = _read_cpu()
        health["cpu_pct"] = round(100 * (1 - (i2 - i1) / max(t2 - t1, 1)), 1)
    except Exception:
        pass
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k.strip()] = int(v.split()[0])
        total_kb = mem.get("MemTotal", 1)
        avail_kb = mem.get("MemAvailable", total_kb)
        used_kb  = total_kb - avail_kb
        health["ram_pct"]   = round(100 * used_kb / total_kb, 1)
        health["ram_used"]  = f"{used_kb // 1024} Mo"
        health["ram_total"] = f"{total_kb // 1024} Mo"
    except Exception:
        pass
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if "wg0" in line:
                    fields = line.split(":")[1].split()
                    health["wg0_errors"] = int(fields[2]) + int(fields[10])
                    break
    except Exception:
        pass
    return health

# ─── Routes publiques ─────────────────────────────────────────────────────────
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("admin_panel") if session.get("is_admin") else url_for("dashboard"))
    return render_template("landing.html", bank=get_settings())

@app.route("/guide")
def guide():
    return render_template("guide.html", bank=get_settings())

@app.route("/inscription", methods=["GET", "POST"])
def inscription():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        nom      = request.form["nom"].strip()
        email    = request.form["email"].strip().lower()
        mdp      = request.form["password"]
        confirm  = request.form["confirm"]
        whatsapp = request.form.get("whatsapp", "").strip()
        telegram = request.form.get("telegram", "").strip()
        if mdp != confirm:
            flash("Les mots de passe ne correspondent pas.", "danger")
            return render_template("inscription.html", bank=get_settings())
        if len(mdp) < 6:
            flash("Le mot de passe doit faire au moins 6 caractères.", "danger")
            return render_template("inscription.html", bank=get_settings())
        db = get_db()
        if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            flash("Cet email est déjà utilisé.", "danger")
            return render_template("inscription.html", bank=get_settings())
        db.execute(
            "INSERT INTO users (nom, email, password_hash, is_admin, whatsapp, telegram) VALUES (?, ?, ?, 0, ?, ?)",
            (nom, email, hash_password(mdp), whatsapp or None, telegram or None)
        )
        db.commit()
        new_user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        db.execute(
            "INSERT INTO abonnements (user_id, montant, statut) VALUES (?, 100, 'en_attente')",
            (new_user["id"],)
        )
        db.commit()
        # Notification Telegram à l'admin
        contacts = []
        if whatsapp: contacts.append(f"WhatsApp: {whatsapp}")
        if telegram: contacts.append(f"Telegram: {telegram}")
        contact_str = " | ".join(contacts) if contacts else "aucun contact fourni"
        notify_telegram(
            f"🆕 <b>Nouvelle inscription</b>\n"
            f"Nom : {nom}\nEmail : {email}\n{contact_str}"
        )
        flash("✅ Demande envoyée ! L'administrateur activera votre accès après réception du paiement.", "success")
        return redirect(url_for("login"))
    return render_template("inscription.html", bank=get_settings())

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        mdp   = request.form["password"]
        db    = get_db()
        user  = db.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user and verify_password(user["password_hash"], mdp):
            # Migration transparente des anciens hash SHA-256
            if len(user["password_hash"]) == 64:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (hash_password(mdp), user["id"]))
                db.commit()
            session["user_id"]  = user["id"]
            session["user_nom"] = user["nom"]
            session["is_admin"] = bool(user["is_admin"])
            return redirect(url_for("admin_panel") if user["is_admin"] else url_for("dashboard"))
        flash("Email ou mot de passe incorrect.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ─── Portail utilisateur ──────────────────────────────────────────────────────
@app.route("/dashboard")
@login_required
def dashboard():
    db   = get_db()
    uid  = session["user_id"]
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    abo  = db.execute("SELECT * FROM abonnements WHERE user_id = ?", (uid,)).fetchone()
    peers = db.execute("SELECT * FROM peers WHERE user_id = ? ORDER BY date_ajout, id", (uid,)).fetchall()
    paiements = db.execute(
        "SELECT * FROM paiements WHERE user_id = ? ORDER BY date_paiement DESC LIMIT 5",
        (uid,)
    ).fetchall()

    statut_color = "success"
    jours_restants = None
    if abo and abo["date_fin"]:
        df = date.fromisoformat(abo["date_fin"])
        jours_restants = (df - date.today()).days
        if jours_restants < 0:
            statut_color = "danger"
        elif jours_restants <= 7:
            statut_color = "warning"

    return render_template("dashboard.html",
        user=user, abo=abo, peers=peers,
        paiements=paiements, bank=get_settings(),
        statut_color=statut_color, jours_restants=jours_restants,
        today=date.today()
    )

@app.route("/changer_mdp", methods=["POST"])
@login_required
def changer_mdp():
    uid     = session["user_id"]
    ancien  = request.form["ancien"]
    nouveau = request.form["nouveau"]
    confirm = request.form["confirm"]
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not verify_password(user["password_hash"], ancien):
        flash("Ancien mot de passe incorrect.", "danger")
    elif nouveau != confirm:
        flash("Les nouveaux mots de passe ne correspondent pas.", "danger")
    elif len(nouveau) < 6:
        flash("Le mot de passe doit faire au moins 6 caractères.", "danger")
    else:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (hash_password(nouveau), uid))
        db.commit()
        flash("Mot de passe mis à jour avec succès.", "success")
    return redirect(url_for("dashboard"))

# ─── Panel Admin ──────────────────────────────────────────────────────────────
@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    db = get_db()
    users = db.execute("""
        SELECT u.id, u.nom, u.email,
               a.date_debut, a.date_fin, a.montant, a.statut,
               COUNT(p.id) as nb_peers,
               SUM(CASE WHEN p.actif=1 THEN 1 ELSE 0 END) as peers_actifs
        FROM users u
        LEFT JOIN abonnements a ON a.user_id = u.id
        LEFT JOIN peers p ON p.user_id = u.id
        WHERE u.is_admin = 0
        GROUP BY u.id
        ORDER BY u.nom
    """).fetchall()

    paiements_en_attente = db.execute("""
        SELECT p.*, u.nom
        FROM paiements p
        JOIN users u ON u.id = p.user_id
        WHERE p.valide = 0
        ORDER BY p.date_paiement DESC
    """).fetchall()

    demandes = db.execute("""
        SELECT u.id, u.nom, u.email, u.whatsapp, u.telegram, u.created_at
        FROM users u
        JOIN abonnements a ON a.user_id = u.id
        WHERE u.is_admin = 0 AND a.statut = 'en_attente'
        ORDER BY u.created_at DESC
    """).fetchall()

    return render_template("admin.html",
        users=users,
        paiements_en_attente=paiements_en_attente,
        demandes=demandes,
        settings=get_settings(),
        today=date.today()
    )

@app.route("/admin/vpn-health")
@login_required
@admin_required
def admin_vpn_health():
    wg_data  = {}
    wg_error = None
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER, "wg", "show", WG_INTERFACE],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            wg_data = parse_wg_output(result.stdout)
        else:
            wg_error = result.stderr.strip() or "Commande wg show échouée"
    except Exception as e:
        wg_error = str(e)

    db = get_db()
    db_peers = db.execute("""
        SELECT p.id, p.label, p.ip_vpn, p.public_key, p.actif,
               u.nom AS user_nom
        FROM peers p
        JOIN users u ON u.id = p.user_id
        ORDER BY u.nom, p.label
    """).fetchall()

    peers_view      = []
    connected_count = 0
    for p in db_peers:
        ip  = p["ip_vpn"].split("/")[0]
        wg  = wg_data.get(ip, {})
        secs = wg.get("handshake_secs")

        if secs is None:
            status = "jamais"; badge_color = "secondary"
        elif secs < 180:
            status = "connecte"; badge_color = "success"; connected_count += 1
        elif secs < 600:
            status = "inactif"; badge_color = "warning"
        else:
            status = "inactif"; badge_color = "danger"

        if secs is None:
            hs_color = "secondary"; hs_label = "Jamais"
        elif secs < 60:
            hs_color = "success";  hs_label = f"{secs}s"
        elif secs < 180:
            hs_color = "success";  hs_label = f"{secs // 60}min {secs % 60}s"
        elif secs < 600:
            hs_color = "warning";  hs_label = f"{secs // 60}min"
        elif secs < 3600:
            hs_color = "danger";   hs_label = f"{secs // 60}min"
        else:
            hs_color = "danger";   hs_label = f"{secs // 3600}h"

        keepalive     = wg.get("keepalive", 0)
        warn_keepalive = (keepalive == 0 and secs is not None)
        has_real_key  = not p["public_key"].startswith("MANUAL_")

        peers_view.append({
            "id":           p["id"],
            "label":        p["label"],
            "user_nom":     p["user_nom"],
            "ip_vpn":       ip,
            "public_key":   p["public_key"],
            "has_real_key": has_real_key,
            "actif":        p["actif"],
            "status":       status,
            "badge_color":  badge_color,
            "hs_color":     hs_color,
            "hs_label":     hs_label,
            "transfer_rx":  wg.get("transfer_rx") or "—",
            "transfer_tx":  wg.get("transfer_tx") or "—",
            "keepalive":    keepalive,
            "warn_keepalive": warn_keepalive,
        })

    server_health = get_server_health()
    server_health["connected_count"] = connected_count
    server_health["total_peers"]     = len(db_peers)

    return render_template("admin_vpn_health.html",
        peers=peers_view,
        server_health=server_health,
        wg_error=wg_error,
    )

@app.route("/admin/vpn-health/set-keepalive", methods=["POST"])
@login_required
@admin_required
def admin_set_keepalive():
    pubkey = request.form.get("pubkey", "").strip()
    if not pubkey or pubkey.startswith("MANUAL_"):
        return jsonify({"ok": False, "error": "Clé publique invalide"}), 400
    try:
        result = subprocess.run(
            ["docker", "exec", CONTAINER, "wg", "set", WG_INTERFACE,
             "peer", pubkey, "persistent-keepalive", "25"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": result.stderr.strip()}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/admin/settings/update", methods=["POST"])
@login_required
@admin_required
def admin_update_settings():
    db = get_db()
    for key in ["beneficiaire", "telephone", "banque", "montant", "reference",
                "telegram_bot_token", "telegram_chat_id",
                "support_telegram", "support_whatsapp"]:
        value = request.form.get(key, "").strip()
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    db.commit()
    flash("✅ Paramètres mis à jour.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/user/<int:uid>")
@login_required
@admin_required
def admin_user_detail(uid):
    db = get_db()
    user  = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    abo   = db.execute("SELECT * FROM abonnements WHERE user_id = ?", (uid,)).fetchone()
    peers = db.execute(
        "SELECT * FROM peers WHERE user_id = ? ORDER BY date_ajout, id", (uid,)
    ).fetchall()
    histo = db.execute(
        "SELECT * FROM paiements WHERE user_id = ? ORDER BY date_paiement DESC",
        (uid,)
    ).fetchall()
    return render_template("admin_user.html",
        user=user, abo=abo, peers=peers, histo=histo, today=date.today()
    )

# ─── Gestion des peers ────────────────────────────────────────────────────────
@app.route("/admin/peer/ajouter", methods=["POST"])
@login_required
@admin_required
def admin_ajouter_peer():
    uid        = int(request.form["user_id"])
    label      = request.form["label"].strip()
    ip_vpn     = request.form["ip_vpn"].strip()
    date_ajout = request.form.get("date_ajout") or date.today().isoformat()
    db = get_db()
    # Vérifie que l'IP n'est pas déjà utilisée
    if db.execute("SELECT id FROM peers WHERE ip_vpn = ?", (ip_vpn,)).fetchone():
        flash(f"L'IP {ip_vpn} est déjà attribuée à un autre appareil.", "danger")
        return redirect(url_for("admin_user_detail", uid=uid))
    # public_key = placeholder unique (portail sans échange WireGuard)
    public_key = f"MANUAL_{ip_vpn}"
    db.execute(
        "INSERT INTO peers (user_id, label, public_key, ip_vpn, actif, date_ajout) VALUES (?, ?, ?, ?, 1, ?)",
        (uid, label, public_key, ip_vpn, date_ajout)
    )
    db.commit()
    user = db.execute("SELECT nom FROM users WHERE id = ?", (uid,)).fetchone()
    flash(f"✅ Appareil « {label} » ({ip_vpn}) ajouté pour {user['nom']}.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))

@app.route("/admin/peer/supprimer/<int:peer_id>", methods=["POST"])
@login_required
@admin_required
def admin_supprimer_peer(peer_id):
    db   = get_db()
    peer = db.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if peer:
        uid = peer["user_id"]
        iptables_unblock_peer(peer["ip_vpn"])  # s'assure que la règle DROP est retirée
        db.execute("DELETE FROM peers WHERE id = ?", (peer_id,))
        db.commit()
        flash(f"Appareil {peer['label']} ({peer['ip_vpn']}) supprimé.", "warning")
        return redirect(url_for("admin_user_detail", uid=uid))
    return redirect(url_for("admin_panel"))

@app.route("/admin/peer/suspendre/<int:peer_id>", methods=["POST"])
@login_required
@admin_required
def admin_suspendre_peer(peer_id):
    db   = get_db()
    peer = db.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if peer:
        ok = iptables_block_peer(peer["ip_vpn"])
        db.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer_id,))
        db.commit()
        flash(f"Peer {peer['label']} ({peer['ip_vpn']}) suspendu {'✅' if ok else '⚠ (erreur iptables)'}.", "warning")
    return redirect(request.referrer or url_for("admin_panel"))

@app.route("/admin/peer/reactiver/<int:peer_id>", methods=["POST"])
@login_required
@admin_required
def admin_reactiver_peer(peer_id):
    db   = get_db()
    peer = db.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if peer:
        ok = iptables_unblock_peer(peer["ip_vpn"])
        db.execute("UPDATE peers SET actif = 1 WHERE id = ?", (peer_id,))
        db.commit()
        flash(f"Peer {peer['label']} ({peer['ip_vpn']}) réactivé {'✅' if ok else '⚠ (erreur iptables)'}.", "success")
    return redirect(request.referrer or url_for("admin_panel"))

@app.route("/admin/user/suspendre_tout/<int:uid>", methods=["POST"])
@login_required
@admin_required
def admin_suspendre_tout(uid):
    db    = get_db()
    peers = db.execute("SELECT * FROM peers WHERE user_id = ? AND actif = 1", (uid,)).fetchall()
    for peer in peers:
        iptables_block_peer(peer["ip_vpn"])
        db.execute("UPDATE peers SET actif = 0 WHERE id = ?", (peer["id"],))
    db.execute("UPDATE abonnements SET statut = 'suspendu' WHERE user_id = ?", (uid,))
    db.commit()
    user = db.execute("SELECT nom FROM users WHERE id = ?", (uid,)).fetchone()
    flash(f"Tous les accès de {user['nom']} ont été suspendus.", "warning")
    return redirect(url_for("admin_user_detail", uid=uid))

@app.route("/admin/user/creer", methods=["POST"])
@login_required
@admin_required
def admin_creer_user():
    nom      = request.form["nom"].strip()
    email    = request.form["email"].strip().lower()
    mdp      = request.form["password"]
    whatsapp = request.form.get("whatsapp", "").strip()
    telegram = request.form.get("telegram", "").strip()
    db = get_db()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        flash(f"L'email {email} est déjà utilisé.", "danger")
        return redirect(url_for("admin_panel"))
    db.execute(
        "INSERT INTO users (nom, email, password_hash, is_admin, whatsapp, telegram) VALUES (?, ?, ?, 0, ?, ?)",
        (nom, email, hash_password(mdp), whatsapp or None, telegram or None)
    )
    db.commit()
    new_user = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    db.execute(
        "INSERT INTO abonnements (user_id, montant, statut) VALUES (?, 100, 'expire')",
        (new_user["id"],)
    )
    db.commit()
    flash(f"✅ Utilisateur {nom} créé avec succès.", "success")
    return redirect(url_for("admin_user_detail", uid=new_user["id"]))

@app.route("/admin/paiement/ajouter", methods=["POST"])
@login_required
@admin_required
def admin_ajouter_paiement():
    uid     = int(request.form["user_id"])
    montant = float(request.form["montant"])
    mois    = int(request.form["mois"])
    note    = request.form.get("note", "")
    db = get_db()

    db.execute("""
        INSERT INTO paiements (user_id, montant, mois_prolonges, note, valide)
        VALUES (?, ?, ?, ?, 1)
    """, (uid, montant, mois, note))

    abo = db.execute("SELECT * FROM abonnements WHERE user_id = ?", (uid,)).fetchone()
    if abo and abo["date_fin"]:
        base = max(date.fromisoformat(abo["date_fin"]), date.today())
    else:
        base = date.today()
    nouvelle_fin = base + timedelta(days=30 * mois)

    db.execute("""
        UPDATE abonnements
        SET date_fin = ?, date_debut = COALESCE(date_debut, ?), statut = 'actif'
        WHERE user_id = ?
    """, (nouvelle_fin.isoformat(), date.today().isoformat(), uid))

    peers = db.execute("SELECT * FROM peers WHERE user_id = ?", (uid,)).fetchall()
    for peer in peers:
        if not peer["actif"]:
            iptables_unblock_peer(peer["ip_vpn"])
            db.execute("UPDATE peers SET actif = 1 WHERE id = ?", (peer["id"],))

    db.commit()
    user = db.execute("SELECT nom FROM users WHERE id = ?", (uid,)).fetchone()
    flash(f"✅ Paiement enregistré pour {user['nom']} — abonnement jusqu'au {nouvelle_fin.strftime('%d/%m/%Y')}.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))

@app.route("/admin/abonnement/modifier", methods=["POST"])
@login_required
@admin_required
def admin_modifier_abo():
    uid      = int(request.form["user_id"])
    date_fin = request.form["date_fin"]
    montant  = float(request.form["montant"])
    db = get_db()
    db.execute("""
        UPDATE abonnements SET date_fin = ?, montant = ?,
               date_debut = COALESCE(date_debut, ?), statut = 'actif'
        WHERE user_id = ?
    """, (date_fin, montant, date.today().isoformat(), uid))
    db.commit()
    flash("Abonnement mis à jour.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_app_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
