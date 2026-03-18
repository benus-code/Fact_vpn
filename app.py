#!/usr/bin/env python3
"""
app.py — Portail de facturation VPN (AmneziaWG / WireGuard)
Lancer : python3 app.py
"""

import os
import secrets
import sqlite3
import hashlib
import subprocess
import urllib.request
import json
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, g, Response)
from werkzeug.security import generate_password_hash, check_password_hash as wz_check

app = Flask(__name__)
app.secret_key = os.environ.get("VPN_SECRET_KEY", "CHANGE_CE_SECRET_EN_PROD_SVP")

DB_PATH       = "/opt/vpn-billing/vpn_billing.db"
CONTAINER     = "amnezia-awg"
WG_INTERFACE  = "wg0"
PIVPN_CONFIGS = "/home/benus/configs"

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
    "smtp_email":         "benuslavision@gmail.com",
    "smtp_password":      "",   # Mot de passe d'application Gmail
    "site_url":           "",   # ex: https://vpn.mondomaine.com (sans slash final)
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

    # Colonne vpn_type sur peers (amnezia=smartphone, pivpn=PC)
    try:
        conn.execute("ALTER TABLE peers ADD COLUMN vpn_type TEXT DEFAULT 'amnezia'")
    except sqlite3.OperationalError:
        pass

    # Table pour les tokens de réinitialisation de mot de passe
    conn.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            token      TEXT    NOT NULL UNIQUE,
            expires_at TEXT    NOT NULL
        )
    """)

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

def send_email(to_email, subject, body_html):
    """Envoie via Gmail SMTP. Ignore tout domaine .local* et les configs vides."""
    if not to_email or '@' not in to_email:
        return False
    domain = to_email.split('@', 1)[1].lower()
    if '.local' in domain or '.' not in domain:
        return False
    s = get_settings()
    addr = s.get('smtp_email', '').strip()
    pwd  = s.get('smtp_password', '').strip()
    if not addr or not pwd:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"VPN Privé <{addr}>"
        msg['To']      = to_email
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=ctx, timeout=15) as srv:
            srv.login(addr, pwd)
            srv.sendmail(addr, to_email, msg.as_string())
        return True
    except Exception as e:
        app.logger.error(f"send_email → {to_email}: {e}")
        return False

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
    """Débloque un peer Amnezia (via docker exec)."""
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

def iptables_block_host(ip_vpn):
    """Bloque un peer PiVPN par iptables sur le host (pas Docker)."""
    ip = ip_vpn.split("/")[0]
    try:
        subprocess.run(["iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"], check=True, capture_output=True)
        subprocess.run(["iptables", "-I", "FORWARD", "-d", ip, "-j", "DROP"], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        app.logger.error(f"iptables_block_host failed: {e.stderr}")
        return False

def iptables_unblock_host(ip_vpn):
    """Débloque un peer PiVPN (iptables host)."""
    ip = ip_vpn.split("/")[0]
    for direction in ["-s", "-d"]:
        try:
            subprocess.run(["iptables", "-D", "FORWARD", direction, ip, "-j", "DROP"], check=True, capture_output=True)
        except subprocess.CalledProcessError:
            pass
    return True

def pivpn_get_config(label):
    """Retourne le contenu du fichier .conf PiVPN, ou None si introuvable."""
    path = os.path.join(PIVPN_CONFIGS, f"{label}.conf")
    try:
        with open(path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return None

def block_peer(peer):
    """Dispatch : bloque selon le type VPN du peer."""
    if peer["vpn_type"] == "pivpn":
        return iptables_block_host(peer["ip_vpn"])
    return iptables_block_peer(peer["ip_vpn"])

def unblock_peer(peer):
    """Dispatch : débloque selon le type VPN du peer."""
    if peer["vpn_type"] == "pivpn":
        return iptables_unblock_host(peer["ip_vpn"])
    return iptables_unblock_peer(peer["ip_vpn"])

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
        forfait  = request.form.get("forfait", "mobile")
        prix_forfait = {"mobile": 149, "ordinateur": 249, "complet": 349}.get(forfait, 149)
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
            "INSERT INTO abonnements (user_id, montant, statut) VALUES (?, ?, 'en_attente')",
            (new_user["id"], prix_forfait)
        )
        db.commit()
        # Notification Telegram à l'admin
        contacts = []
        if whatsapp: contacts.append(f"WhatsApp: {whatsapp}")
        if telegram: contacts.append(f"Telegram: {telegram}")
        contact_str = " | ".join(contacts) if contacts else "aucun contact fourni"
        notify_telegram(
            f"🆕 <b>Nouvelle inscription</b>\n"
            f"Nom : {nom}\nEmail : {email}\nForfait : {forfait.capitalize()} ({prix_forfait} ₽)\n{contact_str}"
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

@app.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def mot_de_passe_oublie():
    sent = False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        db    = get_db()
        user  = db.execute("SELECT * FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
        # Toujours afficher le même message (pas de fuite d'info)
        sent = True
        if user:
            domain = email.split("@", 1)[1] if "@" in email else ""
            if ".local" not in domain and "." in domain:
                token   = secrets.token_urlsafe(32)
                expires = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                db.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
                db.execute(
                    "INSERT INTO password_resets (user_id, token, expires_at) VALUES (?, ?, ?)",
                    (user["id"], token, expires)
                )
                db.commit()
                site_url  = get_settings().get("site_url", "").rstrip("/")
                if not site_url:
                    site_url = request.host_url.rstrip("/")
                reset_url = f"{site_url}/reset-mdp/{token}"
                html = (
                    f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
                    f"<h2 style='color:#e94560'>🔐 Réinitialisation de mot de passe</h2>"
                    f"<p>Bonjour <strong>{user['nom']}</strong>,</p>"
                    f"<p>Vous avez demandé à réinitialiser votre mot de passe. Cliquez sur le lien ci-dessous :</p>"
                    f"<p style='text-align:center;margin:28px 0'>"
                    f"<a href='{reset_url}' style='background:#e94560;color:#fff;padding:12px 28px;"
                    f"border-radius:6px;text-decoration:none;font-weight:700;'>Choisir un nouveau mot de passe</a></p>"
                    f"<p style='color:#888;font-size:.85rem'>Ce lien est valable <strong>1 heure</strong>. "
                    f"Si vous n'avez pas fait cette demande, ignorez cet email.</p>"
                    f"<hr><small style='color:#aaa'>VPN Privé — Service personnel</small></div>"
                )
                send_email(email, "🔐 Réinitialisation de votre mot de passe VPN", html)
    return render_template("mot_de_passe_oublie.html", sent=sent)

@app.route("/reset-mdp/<token>", methods=["GET", "POST"])
def reset_mdp(token):
    db  = get_db()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    row = db.execute(
        "SELECT * FROM password_resets WHERE token = ? AND expires_at > ?", (token, now)
    ).fetchone()
    if not row:
        flash("Ce lien est invalide ou expiré. Faites une nouvelle demande.", "danger")
        return redirect(url_for("mot_de_passe_oublie"))

    if request.method == "POST":
        mdp1 = request.form.get("password", "")
        mdp2 = request.form.get("password_confirm", "")
        if len(mdp1) < 6:
            flash("Le mot de passe doit contenir au moins 6 caractères.", "danger")
            return render_template("reset_mdp.html", token=token)
        if mdp1 != mdp2:
            flash("Les deux mots de passe ne correspondent pas.", "danger")
            return render_template("reset_mdp.html", token=token)
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(mdp1), row["user_id"]))
        db.execute("DELETE FROM password_resets WHERE token = ?", (token,))
        db.commit()
        flash("Mot de passe mis à jour ! Vous pouvez vous connecter.", "success")
        return redirect(url_for("login"))

    return render_template("reset_mdp.html", token=token)

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

@app.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    if session.get("is_admin"):
        return redirect(url_for("admin_panel"))
    uid = session["user_id"]
    db  = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if request.method == "POST":
        nom      = request.form.get("nom", "").strip()
        email    = request.form.get("email", "").strip().lower()
        whatsapp = request.form.get("whatsapp", "").strip()
        telegram = request.form.get("telegram", "").strip()
        if not nom or not email:
            flash("Nom et email sont requis.", "danger")
            return render_template("profil.html", user=user)
        if db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (email, uid)).fetchone():
            flash("Cet email est déjà utilisé.", "danger")
            return render_template("profil.html", user=user)
        db.execute(
            "UPDATE users SET nom=?, email=?, whatsapp=?, telegram=? WHERE id=?",
            (nom, email, whatsapp or None, telegram or None, uid)
        )
        db.commit()
        session["user_nom"] = nom
        flash("✅ Informations mises à jour.", "success")
        return redirect(url_for("profil"))
    return render_template("profil.html", user=user)

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

@app.route("/admin/settings/update", methods=["POST"])
@login_required
@admin_required
def admin_update_settings():
    db = get_db()
    for key in ["beneficiaire", "telephone", "banque", "montant", "reference",
                "telegram_bot_token", "telegram_chat_id",
                "support_telegram", "support_whatsapp",
                "smtp_email", "smtp_password", "site_url"]:
        value = request.form.get(key, "").strip()
        db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
    db.commit()
    flash("✅ Paramètres mis à jour.", "success")
    return redirect(url_for("admin_panel"))

@app.route("/admin/test-email", methods=["POST"])
@admin_required
def admin_test_email():
    """Envoie un email de test à l'adresse SMTP configurée pour vérifier la connexion."""
    s    = get_settings()
    addr = s.get("smtp_email", "").strip()
    pwd  = s.get("smtp_password", "").strip()
    if not addr or not pwd:
        flash("❌ Adresse Gmail ou mot de passe d'application non configurés.", "danger")
        return redirect(url_for("admin_panel"))
    html = (
        "<div style='font-family:sans-serif'>"
        "<h2 style='color:#e94560'>✅ Test SMTP réussi</h2>"
        "<p>Si vous recevez cet email, la configuration Gmail est correcte.</p>"
        "</div>"
    )
    ok = send_email(addr, "🔧 Test SMTP — VPN Privé", html)
    if ok:
        flash(f"✅ Email de test envoyé à {addr}. Vérifiez votre boîte (et spams).", "success")
    else:
        flash("❌ Échec de l'envoi. Vérifiez le mot de passe d'application et que le port 465 n'est pas bloqué.", "danger")
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
@app.route("/admin/peer/config/<int:peer_id>")
@login_required
@admin_required
def admin_peer_config(peer_id):
    """Télécharge le fichier .conf PiVPN d'un peer."""
    peer = get_db().execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if not peer or peer["vpn_type"] != "pivpn":
        flash("Config disponible uniquement pour les peers PiVPN.", "danger")
        return redirect(request.referrer or url_for("admin_panel"))
    config = pivpn_get_config(peer["label"])
    if not config:
        flash(f"Fichier {peer['label']}.conf introuvable dans {PIVPN_CONFIGS}.", "danger")
        return redirect(request.referrer or url_for("admin_panel"))
    return Response(
        config,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={peer['label']}.conf"}
    )

@app.route("/admin/peer/ajouter", methods=["POST"])
@login_required
@admin_required
def admin_ajouter_peer():
    uid        = int(request.form["user_id"])
    label      = request.form["label"].strip()
    ip_vpn     = request.form["ip_vpn"].strip()
    date_ajout = request.form.get("date_ajout") or date.today().isoformat()
    vpn_type   = request.form.get("vpn_type", "amnezia")
    db = get_db()
    if db.execute("SELECT id FROM peers WHERE ip_vpn = ?", (ip_vpn,)).fetchone():
        flash(f"L'IP {ip_vpn} est déjà attribuée à un autre appareil.", "danger")
        return redirect(url_for("admin_user_detail", uid=uid))
    public_key = f"MANUAL_{ip_vpn}"
    db.execute(
        "INSERT INTO peers (user_id, label, public_key, ip_vpn, actif, date_ajout, vpn_type) VALUES (?, ?, ?, ?, 1, ?, ?)",
        (uid, label, public_key, ip_vpn, date_ajout, vpn_type)
    )
    db.commit()
    user = db.execute("SELECT nom FROM users WHERE id = ?", (uid,)).fetchone()
    type_label = "PiVPN (PC)" if vpn_type == "pivpn" else "Amnezia (mobile)"
    flash(f"✅ Appareil « {label} » ({ip_vpn}) [{type_label}] ajouté pour {user['nom']}.", "success")
    return redirect(url_for("admin_user_detail", uid=uid))

@app.route("/admin/peer/supprimer/<int:peer_id>", methods=["POST"])
@login_required
@admin_required
def admin_supprimer_peer(peer_id):
    db   = get_db()
    peer = db.execute("SELECT * FROM peers WHERE id = ?", (peer_id,)).fetchone()
    if peer:
        uid = peer["user_id"]
        unblock_peer(peer)  # s'assure que la règle DROP est retirée
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
        ok = block_peer(peer)
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
        ok = unblock_peer(peer)
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
        block_peer(peer)
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
            unblock_peer(peer)
            db.execute("UPDATE peers SET actif = 1 WHERE id = ?", (peer["id"],))

    db.commit()
    user_data = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    flash(f"✅ Paiement enregistré pour {user_data['nom']} — abonnement jusqu'au {nouvelle_fin.strftime('%d/%m/%Y')}.", "success")
    # Email de bienvenue / renouvellement
    html_welcome = (
        f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
        f"<h2 style='color:#e94560'>✅ Votre accès VPN est actif !</h2>"
        f"<p>Bonjour <strong>{user_data['nom']}</strong>,</p>"
        f"<p>Votre abonnement est actif jusqu'au <strong>{nouvelle_fin.strftime('%d/%m/%Y')}</strong>.</p>"
        f"<p>Connectez-vous à votre espace pour consulter vos appareils et coordonnées de paiement.</p>"
        f"<p>Besoin d'aide pour l'installation ? Consultez notre guide en vous connectant.</p>"
        f"<hr><small style='color:#888'>VPN Privé — Service personnel</small></div>"
    )
    send_email(user_data['email'], "✅ Votre accès VPN est actif !", html_welcome)
    notify_telegram(
        f"💳 <b>Paiement activé</b>\nUser : {user_data['nom']}\n"
        f"Fin : {nouvelle_fin.strftime('%d/%m/%Y')}"
    )
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

@app.route("/admin/broadcast", methods=["POST"])
@login_required
@admin_required
def admin_broadcast():
    subject = request.form.get("subject", "").strip() or "Information VPN"
    message = request.form.get("message", "").strip()
    channel = request.form.get("channel", "email")
    if not message:
        flash("Message vide.", "danger")
        return redirect(url_for("admin_panel"))
    db = get_db()
    users = db.execute("SELECT * FROM users WHERE is_admin = 0").fetchall()
    sent_email = 0
    for u in users:
        if channel in ("email", "both"):
            html = (
                f"<div style='font-family:sans-serif;max-width:520px;margin:0 auto'>"
                f"<p>Bonjour <strong>{u['nom']}</strong>,</p>"
                f"<p>{message.replace(chr(10), '<br>')}</p>"
                f"<hr><small style='color:#888'>VPN Privé — Service personnel</small></div>"
            )
            if send_email(u["email"], subject, html):
                sent_email += 1
    if channel in ("telegram", "both"):
        notify_telegram(f"📢 <b>Broadcast</b>\n{message}")
    flash(f"✅ Envoyé à {sent_email} abonné(s) par email.", "success")
    return redirect(url_for("admin_panel"))

# ─── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_app_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
